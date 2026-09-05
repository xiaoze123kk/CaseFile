"""Real PostgreSQL, zero-network N4.5 production runtime and recovery evidence."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from casefile.agent_runtime import FakeProvider
from casefile.agent_runtime.prose_judge import FakeProseJudgeProvider
from casefile.agent_runtime.prose_polisher import FakeProsePolisherProvider
from casefile.agent_runtime.prose_quality_critic import FakeProseQualityCriticProvider
from casefile.agent_runtime.prose_rewriter import FakeProseRewriterProvider
from casefile.agent_runtime.prose_writer import FakeProseWriterProvider
from casefile.api.app import create_app
from casefile.application.compiler import CompilerService
from casefile.application.services import CaseFileService
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import AgentModelCall, CompileArtifact, TaskRun, User
from casefile.domain.narrative_compiler import QUALITY_DIMENSIONS, canonical_json_sha256
from casefile.worker.executors.prose_providers import ProseProviders
from casefile.worker.executors.prose_store import ProseStore
from casefile.worker.runtime import Worker, WorkerConfig
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update
from test_narrative_compiler_runtime import _prepare_compilable_project

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[3]


class ScriptedJudge:
    """Protocol test oracle only; this does not evaluate literary semantics."""

    def __init__(self, failures: int = 0, preservation_fail: bool = False) -> None:
        self.failures = failures
        self.preservation_fail = preservation_fail
        self.calls = 0
        self.requests: list[Any] = []

    def judge_scene(self, request: Any) -> Any:
        self.calls += 1
        self.requests.append(request)
        data = request.input_payload["untrusted_data"]
        render = data["render"]
        fail = (
            render["round"] < self.failures
            if render["stage"] != "polished"
            else self.preservation_fail
        )
        evidence_id = request.input_payload["server_evidence_catalog"][0]["evidence_id"]
        checks = data["checklist"]["checks"]
        candidate = {
            "schema_id": "compiler.prose-judge-candidate.v1",
            "assessments": [
                {
                    "check_id": c["check_id"],
                    "verdict": "fail" if fail and i == 0 else "pass",
                    "rationale": "测试判定",
                    "evidence_ids": [evidence_id]
                    if c["polarity"] == "required" and not (fail and i == 0)
                    else [],
                }
                for i, c in enumerate(checks)
            ],
        }
        return FakeProseJudgeProvider(judge_reports=(candidate,)).judge_scene(request)

    def arbitrate_scene(self, request: Any) -> Any:
        raise AssertionError("unanimous fixture must not arbitrate")


def _providers(
    *,
    failures: int = 0,
    preservation_fail: bool = False,
    preference: str = "polished",
    invalid_writer: bool = False,
) -> ProseProviders:
    text = "调查者核对眼前的记录，把已经发生的事实留在笔记中。" * 16
    original = {"schema_id": "compiler.scene-render-candidate.v1", "blocks": [{"text": text}]}
    polished = {
        "schema_id": "compiler.scene-render-candidate.v1",
        "blocks": [{"text": text + "他收起笔记。"}],
    }
    pairwise = tuple(
        {
            "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
            "overall_preference": p,
            "dimension_preferences": [
                {"dimension": d, "preference": p} for d in QUALITY_DIMENSIONS
            ],
        }
        for p in (
            ("b", "a")
            if preference == "polished"
            else ("b", "b")
            if preference == "disagreement"
            else ("tie", "tie")
        )
    )
    return ProseProviders(
        FakeProseWriterProvider(candidates=({"bad": "shape"} if invalid_writer else original,) * 4),
        FakeProseRewriterProvider(candidates=(original,) * 8),
        ScriptedJudge(failures, preservation_fail),
        FakeProseQualityCriticProvider(
            findings_candidates=(
                {"schema_id": "compiler.prose-quality-findings-candidate.v1", "findings": []},
            )
            * 4,
            pairwise_candidates=pairwise * 4,
        ),
        FakeProsePolisherProvider(candidates=(polished,) * 4),
    )


def _prepare(database: tuple[Engine, int, str]) -> tuple[Any, int, dict[str, Any], dict[str, Any]]:
    engine, actor, key = database
    factory, project, draft_id, _ = _prepare_compilable_project(engine, actor, key)
    profile = json.loads(
        (ROOT / "fixtures/compiler/prose_rendering/v1/profile_v2.json").read_text(encoding="utf-8")
    )
    profile["structure"].update(target_chapters=1, target_scenes=2)
    profile["exposure_policy"] = "planner_default"
    profile["prose"]["target_scene_chars"] = {"min": 300, "max": 1200}
    with patch.dict("os.environ", {"CASEFILE_MASTER_KEY": key}), factory() as session:
        WorkflowService(session).save_provider_setting(
            actor,
            provider="deepseek",
            api_key="sk-fake-prose-secret",
            model_id="deepseek-v4-pro",
            model_is_custom=False,
        )
        saved = CompilerService(session).create_profile(
            actor,
            project,
            profile_key="novel.prose",
            name="正文测试",
            schema_id="compiler.novel-profile.v2",
            payload=profile,
        )
        draft = CaseFileService(session).get_draft(actor, project)
    body = {
        "mode": "preview",
        "expected_draft_id": draft_id,
        "expected_draft_revision": draft["revision"],
        "compiler_profile_version_id": saved["current_version_id"],
        "planner_provider": "deepseek",
        "prose_renderer_shadow": True,
    }
    with TestClient(create_app(engine.url.render_as_string(hide_password=False))) as client:
        response = client.post(
            f"/api/v1/projects/{project}/compile-runs",
            headers={"X-CaseFile-User-Id": str(actor)},
            json=body,
        )
    assert response.status_code == 201, response.text
    return factory, project, response.json(), draft


def _run(
    factory: Any,
    run: dict[str, Any],
    providers: ProseProviders,
    key: str,
    worker_id: str = "prose-test",
) -> Worker:
    worker = Worker(
        factory,
        config=WorkerConfig(worker_id=worker_id),
        provider_factory=lambda _: FakeProvider(),
        prose_providers=providers,
    )
    with patch.dict("os.environ", {"CASEFILE_MASTER_KEY": key}):
        assert worker.run_once(task_run_id=run["task_run_id"])
    return worker


def _result(factory: Any, run: dict[str, Any]) -> tuple[TaskRun, dict[str, Any], list[Any]]:
    with factory() as session:
        task = session.get(TaskRun, run["task_run_id"])
        artifacts = list(
            session.scalars(
                select(CompileArtifact).where(
                    CompileArtifact.compile_run_id == run["compile_run_id"]
                )
            )
        )
    assert task is not None
    manifest = next(
        (a.content_jsonb for a in artifacts if a.artifact_key == "compiler.compile_manifest"), None
    )
    assert manifest is not None, (task.status, task.error_code, task.error_details_jsonb)
    return task, manifest, artifacts


@pytest.mark.parametrize(
    "failures,preservation_fail,preference,state",
    [
        (0, False, "polished", "finalized_polished"),
        (1, False, "tie", "finalized_original"),
        (2, False, "polished", "finalized_polished"),
        (3, False, "polished", "semantic_rejected"),
        (0, True, "polished", "finalized_original"),
        (0, False, "disagreement", "finalized_original"),
    ],
)
def test_serial_shadow_and_exact_rollback(
    workflow_database: tuple[Engine, int, str],
    failures: int,
    preservation_fail: bool,
    preference: str,
    state: str,
) -> None:
    _, actor, key = workflow_database
    factory, project, run, before = _prepare(workflow_database)
    providers = _providers(
        failures=failures, preservation_fail=preservation_fail, preference=preference
    )
    _run(factory, run, providers, key)
    task, manifest, artifacts = _result(factory, run)
    assert task.status == "succeeded"
    assert manifest["scenes"][0]["final_state"] == state, manifest
    assert manifest["scenes"][0]["rewrite_count"] == min(failures, 2)
    assert manifest["shadow_status"] == ("semantic_rejected" if failures == 3 else "succeeded")
    assert len(manifest["scenes"]) == (1 if failures == 3 else 2)
    assert all(canonical_json_sha256(a.content_jsonb) == a.content_hash for a in artifacts)
    accepted = {
        a.content_jsonb["scene_id"]: a.content_jsonb
        for a in artifacts
        if a.artifact_kind == "scene_render" and a.content_jsonb["stage"] == "accepted"
    }
    if len(accepted) == 2:
        second = next(
            a.content_jsonb
            for a in artifacts
            if a.artifact_kind == "scene_context" and a.content_jsonb["scene_ordinal"] == 2
        )
        first = next(r for r in accepted.values() if r["scene_ordinal"] == 1)
        assert second["source"]["previous_scene_render_hash"] == canonical_json_sha256(first)
    if state == "finalized_original":
        for render in accepted.values():
            source = next(
                a.content_jsonb
                for a in artifacts
                if a.artifact_kind == "scene_render"
                and a.content_jsonb["scene_id"] == render["scene_id"]
                and a.content_jsonb["stage"] == (f"rewrite_{failures}" if failures else "writer")
            )
            assert render["blocks"] == source["blocks"]
    with factory() as session:
        after = CaseFileService(session).get_draft(actor, project)
        view = CompilerService(session).get_run(actor, project, run["compile_run_id"])
        calls = list(
            session.scalars(
                select(AgentModelCall).where(
                    AgentModelCall.task_run_id == task.id,
                    AgentModelCall.request_fingerprint.is_not(None),
                )
            )
        )
    assert after["revision"] == before["revision"]
    assert view["compilation"]["status"] == "succeeded"
    assert view["prose_shadow"]["is_adopted"] is False
    assert len(calls) == sum(s["physical_request_count"] for s in manifest["scenes"])
    assert all(c.raw_output_text and c.parse_status == "validated" for c in calls)
    assert all(
        c.response_jsonb
        and c.target_schema_id == c.response_jsonb["request_payload"]["output_schema_id"]
        for c in calls
    )
    assert {c.model_id for c in calls} <= {"deepseek-v4-pro", "deepseek-v4-flash"}


def test_protocol_failure_does_not_fail_main_compile(
    workflow_database: tuple[Engine, int, str],
) -> None:
    factory, _, run, _ = _prepare(workflow_database)
    _run(factory, run, _providers(invalid_writer=True), workflow_database[2])
    task, manifest, _ = _result(factory, run)
    assert task.status == "succeeded"
    assert manifest["shadow_status"] == "inconclusive_infrastructure"
    assert len(manifest["scenes"]) == 1
    assert len(manifest["not_run_scene_ids"]) == 1


@pytest.mark.parametrize("response_saved", [True, False])
def test_response_crash_recovery_and_unknown_window(
    workflow_database: tuple[Engine, int, str], response_saved: bool
) -> None:
    factory, _, run, _ = _prepare(workflow_database)
    providers = _providers()
    original = ProseStore.save_response
    crashed = False

    def crash(store: ProseStore, result: Any) -> None:
        nonlocal crashed
        if response_saved:
            usage = {"requests": 1, "input_tokens": 11, "output_tokens": 7, "total_tokens": 18}
            original(
                store,
                replace(
                    result,
                    usage=usage,
                    transport_attempts=(replace(result.transport_attempts[0], usage=usage),),
                ),
            )
        if not crashed:
            crashed = True
            raise SystemExit("simulated process death")
        if not response_saved:
            original(store, result)

    with patch.object(ProseStore, "save_response", crash), pytest.raises(SystemExit):
        _run(factory, run, providers, workflow_database[2])
    with factory() as session, session.begin():
        session.execute(
            update(TaskRun)
            .where(TaskRun.id == run["task_run_id"])
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    _run(factory, run, providers, workflow_database[2], "prose-resumed")
    task, manifest, _ = _result(factory, run)
    assert task.status == "succeeded"
    assert manifest["shadow_status"] == (
        "succeeded" if response_saved else "inconclusive_infrastructure"
    )
    assert providers.writer.call_count == (2 if response_saved else 1)
    if response_saved:
        assert manifest["scenes"][0]["recovered_call_hashes"]
        assert manifest["scenes"][0]["usage"]["total_tokens"] == 18
        assert task.usage_jsonb["total_tokens"] == 18


def test_cancel_after_response_preserves_main_and_audit(
    workflow_database: tuple[Engine, int, str],
) -> None:
    factory, project, run, before = _prepare(workflow_database)
    original = ProseStore.save_response

    def cancel(store: ProseStore, result: Any) -> None:
        original(store, result)
        with factory() as session, session.begin():
            session.execute(
                update(TaskRun).where(TaskRun.id == run["task_run_id"]).values(status="cancelling")
            )

    providers = _providers()
    with patch.object(ProseStore, "save_response", cancel):
        _run(factory, run, providers, workflow_database[2])
    task, manifest, _ = _result(factory, run)
    assert task.status == "cancelled"
    assert manifest["incomplete_reason"] == "compiler_prose_cancelled"
    assert providers.writer.call_count == 1
    with factory() as session:
        view = CompilerService(session).get_run(
            workflow_database[1], project, run["compile_run_id"]
        )
        after = CaseFileService(session).get_draft(workflow_database[1], project)
    assert view["compilation"]["status"] == "succeeded"
    assert after["revision"] == before["revision"]


def test_same_worker_new_attempt_fences_every_old_write(
    workflow_database: tuple[Engine, int, str],
) -> None:
    from casefile.worker.executors.prose_store import ProseLeaseLost

    factory, _, run, _ = _prepare(workflow_database)
    providers = _providers()
    new_worker = Worker(
        factory,
        config=WorkerConfig(worker_id="prose-test"),
        provider_factory=lambda _: FakeProvider(),
        prose_providers=providers,
    )
    claimed: Any = None
    old_store: Any = None
    original = ProseStore.save_response

    def takeover(store: ProseStore, result: Any) -> None:
        nonlocal claimed, old_store
        original(store, result)
        if claimed is not None:
            return
        old_store = store
        with factory() as session, session.begin():
            session.execute(
                update(TaskRun)
                .where(TaskRun.id == run["task_run_id"])
                .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        claimed = new_worker._queue._claim_specific(run["task_run_id"])
        assert isinstance(claimed, tuple)
        for write in (
            lambda: store.save_response(result),
            lambda: store.finish_steps(),
            lambda: store.before_transport(store.current_request, 2),
            lambda: store.artifact(
                "compile_manifest", "compiler.compile_manifest", {}, "prose_manifest"
            ),
        ):
            with pytest.raises(ProseLeaseLost):
                write()

    with patch.object(ProseStore, "save_response", takeover):
        _run(factory, run, providers, workflow_database[2])
    assert old_store is not None and claimed is not None
    with patch.dict("os.environ", {"CASEFILE_MASTER_KEY": workflow_database[2]}):
        new_worker._execute(*claimed)
    task, manifest, _ = _result(factory, run)
    assert task.status == "succeeded" and manifest["shadow_status"] == "succeeded"
    assert providers.writer.call_count == 2


def test_judge_retry_has_two_physical_calls(workflow_database: tuple[Engine, int, str]) -> None:
    from types import SimpleNamespace

    import httpx
    from casefile.agent_runtime.prose_judge import DeepSeekProseJudgeProvider
    from openai import APIConnectionError

    class RetryingJudge(DeepSeekProseJudgeProvider):
        def __init__(self) -> None:
            super().__init__(retry_wait=lambda _: None)
            self.sent = 0
            self.oracle = ScriptedJudge()

        def _create_completion(self, request: Any) -> Any:
            self.sent += 1
            if self.sent == 1:
                raise APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
            result = self.oracle.judge_scene(request)
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8, total_tokens=20),
                choices=[SimpleNamespace(message=SimpleNamespace(content=result.raw_response))],
            )

    factory, _, run, _ = _prepare(workflow_database)
    providers = replace(_providers(), judge=RetryingJudge())
    _run(factory, run, providers, workflow_database[2])
    _, manifest, _ = _result(factory, run)
    assert manifest["shadow_status"] == "succeeded", manifest
    first = manifest["scenes"][0]
    assert first["physical_request_count"] == first["call_count"] + 1
    assert first["unknown_usage_count"] == 1
    assert first["usage"]["total_tokens"] == 80


def test_successful_responses_without_usage_remain_unknown(
    workflow_database: tuple[Engine, int, str],
) -> None:
    from contextlib import ExitStack
    from types import SimpleNamespace

    factory, _, run, _ = _prepare(workflow_database)
    oracle = _providers(failures=1)
    providers = ProseProviders.deepseek()

    def response(method: Any, request: Any) -> Any:
        result = method(request)
        return SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content=result.raw_response))],
        )

    with ExitStack() as stack:
        for provider, method in (
            (providers.writer, oracle.writer.write_scene),
            (providers.rewriter, oracle.rewriter.rewrite_scene),
            (providers.judge, oracle.judge.judge_scene),
            (providers.quality, oracle.quality.assess_quality),
            (providers.polisher, oracle.polisher.polish_scene),
        ):
            stack.enter_context(
                patch.object(
                    provider,
                    "_create_completion",
                    side_effect=lambda request, method=method: response(method, request),
                )
            )
        _run(factory, run, providers, workflow_database[2])
    _, manifest, _ = _result(factory, run)
    assert manifest["shadow_status"] == "succeeded", manifest
    for scene in manifest["scenes"]:
        assert scene["rewrite_count"] == 1
        assert scene["unknown_usage_count"] == scene["physical_request_count"]
    with factory() as session:
        calls = list(
            session.scalars(
                select(AgentModelCall).where(
                    AgentModelCall.task_run_id == run["task_run_id"],
                    AgentModelCall.request_fingerprint.is_not(None),
                )
            )
        )
        assert calls
        assert all(c.usage_jsonb == {"usage_known": False} for c in calls)


def test_api_rejects_invalid_activation_and_hides_calls(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor, _ = workflow_database
    factory, project, run, draft = _prepare(workflow_database)
    with factory() as session:
        profile = CompilerService(session).create_profile(
            actor,
            project,
            profile_key="novel.old",
            name="旧配置",
            schema_id="compiler.novel-profile.v1",
            payload={
                "schema_id": "compiler.novel-profile.v1",
                "structure": {"strategy": "three_act", "target_chapters": 1, "target_scenes": 2},
                "allowed_presentation_modes": ["linear"],
                "exposure_policy": "planner_default",
            },
        )
    body = {
        "mode": "preview",
        "expected_draft_id": draft["draft_id"],
        "expected_draft_revision": draft["revision"],
        "compiler_profile_version_id": run["compiler_profile_version_id"],
        "planner_provider": "deepseek",
        "prose_renderer_shadow": True,
    }
    with factory() as session, session.begin():
        other = User(display_name="另一作者")
        session.add(other)
        session.flush()
        other_actor = other.id
    with TestClient(create_app(engine.url.render_as_string(hide_password=False))) as client:
        for overrides in (
            {"planner_provider": "openai"},
            {"planner_provider": None},
            {"compiler_profile_version_id": profile["current_version_id"]},
            {"mode": "canonical", "canon_version_id": 1},
        ):
            response = client.post(
                f"/api/v1/projects/{project}/compile-runs",
                headers={"X-CaseFile-User-Id": str(actor)},
                json={**body, **overrides},
            )
            assert response.status_code in {404, 422}, response.text
        response = client.get(
            f"/api/v1/projects/{project}/compile-runs/{run['compile_run_id']}",
            headers={"X-CaseFile-User-Id": str(other_actor)},
        )
        assert response.status_code == 404
    assert "raw_response" not in json.dumps(run)


def test_artifact_identity_immutability_and_collision(
    workflow_database: tuple[Engine, int, str],
) -> None:
    from sqlalchemy.exc import DBAPIError, IntegrityError

    factory, _, run, _ = _prepare(workflow_database)
    _run(factory, run, _providers(), workflow_database[2])
    _, _, artifacts = _result(factory, run)
    pairs = [a for a in artifacts if ".quality.pairwise." in a.artifact_key]
    assert len(pairs) == 4
    for original, polished in zip(pairs[::2], pairs[1::2], strict=True):
        assert original.content_hash != polished.content_hash
    sample = pairs[0]
    for key in (sample.artifact_key, sample.artifact_key.replace("original_first", "wrong")):
        with factory() as session, pytest.raises(IntegrityError), session.begin():
            session.add(
                CompileArtifact(
                    project_id=sample.project_id,
                    casefile_id=sample.casefile_id,
                    compile_run_id=sample.compile_run_id,
                    task_run_id=sample.task_run_id,
                    agent_step_run_id=sample.agent_step_run_id,
                    artifact_kind=sample.artifact_kind,
                    artifact_key=key,
                    schema_id=sample.schema_id,
                    content_hash=sample.content_hash,
                    content_jsonb=sample.content_jsonb,
                )
            )
    with factory() as session, pytest.raises(DBAPIError), session.begin():
        session.execute(
            update(CompileArtifact)
            .where(CompileArtifact.id == sample.id)
            .values(content_hash="0" * 64)
        )


def test_runtime_drift_blocks_before_prose_calls(
    workflow_database: tuple[Engine, int, str],
) -> None:
    from casefile.agent_runtime.prose_runtime import prose_runtime_binding

    factory, _, run, _ = _prepare(workflow_database)
    providers = _providers()
    changed = prose_runtime_binding(2)
    changed["version"] = "different-runtime"
    with patch(
        "casefile.worker.executors.prose_shadow.prose_runtime_binding", return_value=changed
    ):
        _run(factory, run, providers, workflow_database[2])
    task, manifest, _ = _result(factory, run)
    assert task.status == "succeeded"
    assert manifest["shadow_status"] == "blocked_precondition"
    assert manifest["scenes"] == []
    assert providers.writer.call_count == 0


def test_artifact_write_failure_recovers_saved_response(
    workflow_database: tuple[Engine, int, str],
) -> None:
    from sqlalchemy.exc import OperationalError

    factory, _, run, _ = _prepare(workflow_database)
    providers = _providers()
    original = ProseStore.artifact
    failed = False

    def fail_write(
        store: ProseStore, kind: str, artifact_key: str, *args: Any, **kwargs: Any
    ) -> Any:
        nonlocal failed
        if artifact_key.endswith(".writer") and not failed:
            failed = True
            raise OperationalError("simulated write failure", None, RuntimeError("offline"))
        return original(store, kind, artifact_key, *args, **kwargs)

    with patch.object(ProseStore, "artifact", fail_write):
        _run(factory, run, providers, workflow_database[2])
    with factory() as session, session.begin():
        task = session.get(TaskRun, run["task_run_id"])
        assert task is not None and task.status == "running"
        task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    _run(factory, run, providers, workflow_database[2], "write-recovery")
    task, manifest, _ = _result(factory, run)
    assert task.status == "succeeded" and manifest["shadow_status"] == "succeeded"
    assert providers.writer.call_count == 2


def test_expired_lease_cancellation_keeps_shadow_manifest(
    workflow_database: tuple[Engine, int, str],
) -> None:
    factory, _, run, _ = _prepare(workflow_database)
    providers = _providers()
    original = ProseStore.save_response

    def crash(store: ProseStore, result: Any) -> None:
        original(store, result)
        raise SystemExit("process died before cancellation")

    with patch.object(ProseStore, "save_response", crash), pytest.raises(SystemExit):
        _run(factory, run, providers, workflow_database[2])
    with factory() as session, session.begin():
        session.execute(
            update(TaskRun)
            .where(TaskRun.id == run["task_run_id"])
            .values(status="cancelling", lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    _run(factory, run, providers, workflow_database[2], "cancellation-reaper")
    task, manifest, _ = _result(factory, run)
    assert task.status == "cancelled"
    assert manifest["shadow_status"] == "inconclusive_infrastructure"
    assert manifest["scenes"][0]["physical_request_count"] == 1
    assert providers.writer.call_count == 1


def test_previous_revision_upgrade_preserves_legacy_run(
    workflow_database: tuple[Engine, int, str],
) -> None:
    from alembic import command
    from application_services_test_support import _alembic_config

    engine, actor, key = workflow_database
    factory, project, draft_id, profile_id = _prepare_compilable_project(engine, actor, key)
    with factory() as session:
        draft = CaseFileService(session).get_draft(actor, project)
        run = CompilerService(session).create_run(
            actor,
            project,
            mode="preview",
            expected_draft_id=draft_id,
            expected_draft_revision=draft["revision"],
            canon_version_id=None,
            exposure_plan_revision_id=None,
            compiler_profile_version_id=profile_id,
        )
    _run(factory, run, _providers(), key)
    with factory() as session:
        original = session.get(TaskRun, run["task_run_id"])
        assert original is not None
        frozen, digest = original.input_jsonb, original.input_hash
    assert "prose_renderer_shadow" not in frozen
    config = _alembic_config(engine.url.render_as_string(hide_password=False))
    command.downgrade(config, "20260903224420")
    command.upgrade(config, "head")
    command.check(config)
    with factory() as session:
        view = CompilerService(session).get_run(actor, project, run["compile_run_id"])
        restored = session.get(TaskRun, run["task_run_id"])
    assert restored is not None and restored.input_jsonb == frozen and restored.input_hash == digest
    assert view["prose_renderer_shadow"] is False
    assert view["prose_shadow"]["status"] == "disabled"


def test_preservation_arbiter_is_persisted_once_per_scene(
    workflow_database: tuple[Engine, int, str],
) -> None:
    class SplitJudge(ScriptedJudge):
        def judge_scene(self, request: Any) -> Any:
            self.preservation_fail = request.role == "adversarial"
            return super().judge_scene(request)

        def arbitrate_scene(self, request: Any) -> Any:
            evidence_id = request.input_payload["server_evidence_catalog"][0]["evidence_id"]
            candidate = {
                "schema_id": "compiler.prose-judge-candidate.v1",
                "assessments": [
                    {
                        "check_id": check,
                        "verdict": "pass",
                        "rationale": "测试仲裁通过。",
                        "evidence_ids": [evidence_id],
                    }
                    for check in request.disputed_check_ids
                ],
            }
            return FakeProseJudgeProvider(arbiter_reports=(candidate,)).arbitrate_scene(request)

    factory, _, run, _ = _prepare(workflow_database)
    _run(factory, run, replace(_providers(), judge=SplitJudge()), workflow_database[2])
    task, manifest, artifacts = _result(factory, run)
    assert task.status == "succeeded" and manifest["shadow_status"] == "succeeded"
    assert all(len(scene["arbiter_report_hashes"]) == 1 for scene in manifest["scenes"])
    assert len([a for a in artifacts if a.artifact_key.endswith(".preservation.arbiter")]) == 2


def test_expired_worker_cannot_write_generic_terminal(
    workflow_database: tuple[Engine, int, str],
) -> None:
    factory, _, run, _ = _prepare(workflow_database)
    worker = Worker(
        factory,
        config=WorkerConfig(worker_id="expired-terminal"),
        provider_factory=lambda _: FakeProvider(),
        prose_providers=_providers(),
    )
    claimed = worker._queue._claim_specific(run["task_run_id"])
    assert isinstance(claimed, tuple)
    task_id, attempt_id = claimed
    with factory() as session, session.begin():
        session.execute(
            update(TaskRun)
            .where(TaskRun.id == task_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    worker._fail(
        task_id,
        attempt_id,
        RuntimeError("late failure"),
        candidate=None,
        usage={},
        validation_errors=[],
        sensitive_values=(),
    )
    with factory() as session, session.begin():
        task = session.get(TaskRun, task_id)
        assert task is not None and task.status == "running"
        task.status = "cancelling"
    assert worker._cancel(task_id, attempt_id, usage={}, validation_errors=[]) is False
    with factory() as session:
        task = session.get(TaskRun, task_id)
        assert task is not None and task.status == "cancelling"
