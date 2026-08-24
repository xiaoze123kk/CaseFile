"""PostgreSQL integration coverage for the N4.1 Compiler runtime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from application_services_test_support import _adopt_candidate, _prepare_task
from casefile.agent_runtime import FakeProvider
from casefile.api.app import create_app
from casefile.application.compiler import CompilerService
from casefile.application.compiler.constants import (
    NARRATIVE_IR_ARTIFACT_KEY,
    NARRATIVE_IR_COMPONENT_ID,
    NARRATIVE_IR_COMPONENT_VERSION,
    NARRATIVE_IR_SCHEMA_ID,
)
from casefile.application.services import CaseFileService
from casefile.application.workflow_views import task_failure_view
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentStepRun,
    CompileArtifact,
    CompileRun,
    TaskAttempt,
    TaskRun,
)
from casefile.domain.narrative_compiler import (
    canonical_json_sha256,
    narrative_ir_component_fingerprint,
    project_narrative_ir_json,
)
from casefile.worker.runtime import Worker, WorkerConfig
from casefile.worker.support import TaskCancellationRequested
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres


def test_compiler_failure_uses_deterministic_public_message() -> None:
    failure = task_failure_view("compiler_snapshot_binding_mismatch")

    assert failure == {
        "code": "compiler_snapshot_binding_mismatch",
        "message": "编译冻结输入校验失败，本次构建已安全停止。",
        "retryable": False,
        "issues": [],
    }


def _prepare_compilable_project(
    engine: Engine, actor_id: int, master_key: str
) -> tuple[sessionmaker, int, int, int]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict("os.environ", {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="compiler-fixture-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert worker.run_once() is True
        _adopt_candidate(engine, actor_id, project_id, generation_task_id)
    with factory() as session:
        draft = CaseFileService(session).get_draft(actor_id, project_id)
        profile = CompilerService(session).create_profile(
            actor_id,
            project_id,
            profile_key="novel.default",
            name="默认小说",
            schema_id="compiler.profile.v1",
            payload={"language": "zh-CN"},
        )
    return (
        factory,
        project_id,
        int(draft["draft_id"]),
        int(profile["current_version_id"]),
    )


def test_providerless_compile_freezes_manifest_and_keeps_draft_unchanged(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory, project_id, draft_id, profile_version_id = _prepare_compilable_project(
        engine, actor_id, master_key
    )
    with factory() as session:
        before = CaseFileService(session).get_draft(actor_id, project_id)
        run = CompilerService(session).create_run(
            actor_id,
            project_id,
            mode="preview",
            expected_draft_id=draft_id,
            expected_draft_revision=int(before["revision"]),
            canon_version_id=None,
            exposure_plan_revision_id=None,
            compiler_profile_version_id=profile_version_id,
        )

    provider_called = False

    def forbidden_provider(_task: TaskRun) -> FakeProvider:
        nonlocal provider_called
        provider_called = True
        raise AssertionError("provider must not be constructed")

    worker = Worker(
        factory,
        config=WorkerConfig(worker_id="compiler-providerless-worker"),
        provider_factory=forbidden_provider,
    )
    assert worker.run_once() is True
    assert provider_called is False

    with factory() as session:
        result = CompilerService(session).get_run(
            actor_id, project_id, int(run["compile_run_id"])
        )
        after = CaseFileService(session).get_draft(actor_id, project_id)
        model_calls = session.scalar(
            select(func.count(AgentModelCall.id)).where(
                AgentModelCall.task_run_id == run["task_run_id"]
            )
        )
        task = session.get(TaskRun, int(run["task_run_id"]))
        artifacts = list(
            session.scalars(
                select(CompileArtifact)
                .where(CompileArtifact.compile_run_id == run["compile_run_id"])
                .order_by(CompileArtifact.id)
            )
        )

    assert result["execution"]["status"] == "succeeded"
    assert len(result["artifacts"]) == 2
    assert model_calls == 0
    assert after["draft_id"] == before["draft_id"]
    assert after["revision"] == before["revision"]
    assert task is not None and task.provider is None and task.model_id is None
    assert [artifact.artifact_kind for artifact in artifacts] == [
        "input_manifest",
        "narrative_ir",
    ]
    assert artifacts[0].content_jsonb == task.input_jsonb
    assert artifacts[0].content_hash == task.input_hash
    assert artifacts[1].content_hash == task.result_jsonb["narrative_ir_hash"]
    assert task.result_jsonb["component_reuse"] == {
        "input_manifest": False,
        "narrative_ir": False,
    }


def test_compile_artifact_is_reused_after_expired_lease(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory, project_id, draft_id, profile_version_id = _prepare_compilable_project(
        engine, actor_id, master_key
    )
    with factory() as session:
        draft = CaseFileService(session).get_draft(actor_id, project_id)
        run = CompilerService(session).create_run(
            actor_id,
            project_id,
            mode="preview",
            expected_draft_id=draft_id,
            expected_draft_revision=int(draft["revision"]),
            canon_version_id=None,
            exposure_plan_revision_id=None,
            compiler_profile_version_id=profile_version_id,
        )

    first = Worker(factory, config=WorkerConfig(worker_id="compiler-crash-worker"))
    claimed = first._claim_next()
    assert isinstance(claimed, tuple)
    task_run_id, attempt_id = claimed
    manifest, detached_run, _snapshot = first._validate_compile_inputs(task_run_id)
    artifact_id, reused = first._materialize_input_manifest(
        task_run_id, attempt_id, detached_run, manifest
    )
    assert reused is False

    with factory() as session, session.begin():
        session.execute(
            update(TaskRun)
            .where(TaskRun.id == task_run_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    second = Worker(factory, config=WorkerConfig(worker_id="compiler-recovery-worker"))
    assert second.run_once() is True
    with factory() as session:
        task = session.get(TaskRun, task_run_id)
        attempts = list(
            session.scalars(
                select(TaskAttempt)
                .where(TaskAttempt.task_run_id == task_run_id)
                .order_by(TaskAttempt.attempt_no)
            )
        )
        steps = list(
            session.scalars(
                select(AgentStepRun)
                .where(AgentStepRun.task_run_id == task_run_id)
                .order_by(AgentStepRun.id)
            )
        )
        artifacts = list(
            session.scalars(
                select(CompileArtifact).where(
                    CompileArtifact.compile_run_id == run["compile_run_id"]
                )
            )
        )

    assert task is not None and task.status == "succeeded"
    assert [attempt.status for attempt in attempts] == ["failed", "succeeded"]
    assert [step.status for step in steps] == ["succeeded", "reused", "succeeded"]
    assert steps[1].resumed_from_step_run_id == steps[0].id
    assert len(artifacts) == 2 and artifacts[0].id == artifact_id
    assert [artifact.artifact_kind for artifact in artifacts] == [
        "input_manifest",
        "narrative_ir",
    ]


def test_both_compiler_artifacts_are_reused_after_completion_crash(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory, project_id, draft_id, profile_version_id = _prepare_compilable_project(
        engine, actor_id, master_key
    )
    with factory() as session:
        draft = CaseFileService(session).get_draft(actor_id, project_id)
        run = CompilerService(session).create_run(
            actor_id,
            project_id,
            mode="preview",
            expected_draft_id=draft_id,
            expected_draft_revision=int(draft["revision"]),
            canon_version_id=None,
            exposure_plan_revision_id=None,
            compiler_profile_version_id=profile_version_id,
        )

    first = Worker(factory, config=WorkerConfig(worker_id="compiler-double-crash-worker"))
    claimed = first._claim_next()
    assert isinstance(claimed, tuple)
    task_run_id, attempt_id = claimed
    manifest, detached_run, snapshot = first._validate_compile_inputs(task_run_id)
    first._materialize_input_manifest(task_run_id, attempt_id, detached_run, manifest)
    fingerprint = narrative_ir_component_fingerprint(snapshot)
    component_hash = canonical_json_sha256(fingerprint)
    narrative_json = project_narrative_ir_json(snapshot)
    narrative_hash = canonical_json_sha256(narrative_json)
    first._materialize_json_artifact_component(
        task_run_id=task_run_id,
        attempt_id=attempt_id,
        run=detached_run,
        component_id=NARRATIVE_IR_COMPONENT_ID,
        component_version=NARRATIVE_IR_COMPONENT_VERSION,
        component_input_hash=component_hash,
        upstream_hashes={"source_snapshot": fingerprint["source_content_hash"]},
        artifact_kind="narrative_ir",
        artifact_key=NARRATIVE_IR_ARTIFACT_KEY,
        schema_id=NARRATIVE_IR_SCHEMA_ID,
        content_hash=narrative_hash,
        content_json=narrative_json,
        event_prefix="compiler.narrative_ir",
        parent_component_id="compiler_input_freeze",
    )
    with factory() as session, session.begin():
        session.execute(
            update(TaskRun)
            .where(TaskRun.id == task_run_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )

    assert Worker(
        factory, config=WorkerConfig(worker_id="compiler-double-recovery-worker")
    ).run_once()
    with factory() as session:
        steps = list(
            session.scalars(
                select(AgentStepRun)
                .where(AgentStepRun.task_run_id == task_run_id)
                .order_by(AgentStepRun.id)
            )
        )
        artifacts = list(
            session.scalars(
                select(CompileArtifact).where(
                    CompileArtifact.compile_run_id == run["compile_run_id"]
                )
            )
        )
    assert [step.status for step in steps] == [
        "succeeded",
        "succeeded",
        "reused",
        "reused",
    ]
    assert steps[2].resumed_from_step_run_id == steps[0].id
    assert steps[3].resumed_from_step_run_id == steps[1].id
    assert len(artifacts) == 2


def test_narrative_ir_write_is_rejected_after_cancellation(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory, project_id, draft_id, profile_version_id = _prepare_compilable_project(
        engine, actor_id, master_key
    )
    with factory() as session:
        draft = CaseFileService(session).get_draft(actor_id, project_id)
        run = CompilerService(session).create_run(
            actor_id,
            project_id,
            mode="preview",
            expected_draft_id=draft_id,
            expected_draft_revision=int(draft["revision"]),
            canon_version_id=None,
            exposure_plan_revision_id=None,
            compiler_profile_version_id=profile_version_id,
        )
    worker = Worker(factory, config=WorkerConfig(worker_id="compiler-cancel-worker"))
    claimed = worker._claim_next()
    assert isinstance(claimed, tuple)
    task_run_id, attempt_id = claimed
    manifest, detached_run, snapshot = worker._validate_compile_inputs(task_run_id)
    worker._materialize_input_manifest(task_run_id, attempt_id, detached_run, manifest)
    fingerprint = narrative_ir_component_fingerprint(snapshot)
    narrative_json = project_narrative_ir_json(snapshot)
    with factory() as session, session.begin():
        session.execute(
            update(TaskRun).where(TaskRun.id == task_run_id).values(status="cancelling")
        )

    with pytest.raises(TaskCancellationRequested):
        worker._materialize_json_artifact_component(
            task_run_id=task_run_id,
            attempt_id=attempt_id,
            run=detached_run,
            component_id=NARRATIVE_IR_COMPONENT_ID,
            component_version=NARRATIVE_IR_COMPONENT_VERSION,
            component_input_hash=canonical_json_sha256(fingerprint),
            upstream_hashes={"source_snapshot": fingerprint["source_content_hash"]},
            artifact_kind="narrative_ir",
            artifact_key=NARRATIVE_IR_ARTIFACT_KEY,
            schema_id=NARRATIVE_IR_SCHEMA_ID,
            content_hash=canonical_json_sha256(narrative_json),
            content_json=narrative_json,
            event_prefix="compiler.narrative_ir",
            parent_component_id="compiler_input_freeze",
        )
    with factory() as session:
        narrative_count = session.scalar(
            select(func.count(CompileArtifact.id)).where(
                CompileArtifact.compile_run_id == run["compile_run_id"],
                CompileArtifact.artifact_kind == "narrative_ir",
            )
        )
    assert narrative_count == 0


def test_task_run_frozen_inputs_reject_tampering(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory, project_id, draft_id, profile_version_id = _prepare_compilable_project(
        engine, actor_id, master_key
    )
    with factory() as session:
        draft = CaseFileService(session).get_draft(actor_id, project_id)
        run = CompilerService(session).create_run(
            actor_id,
            project_id,
            mode="preview",
            expected_draft_id=draft_id,
            expected_draft_revision=int(draft["revision"]),
            canon_version_id=None,
            exposure_plan_revision_id=None,
            compiler_profile_version_id=profile_version_id,
        )
    with pytest.raises(DBAPIError), factory() as session, session.begin():
        session.execute(
            update(TaskRun)
            .where(TaskRun.id == run["task_run_id"])
            .values(input_hash="0" * 64)
        )

    with factory() as session:
        persisted = session.get(TaskRun, int(run["task_run_id"]))
        compile_run = session.scalar(
            select(CompileRun).where(CompileRun.id == run["compile_run_id"])
        )
    assert persisted is not None and compile_run is not None
    assert persisted.input_hash == compile_run.input_hash


def test_compiler_profile_and_run_http_contracts(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    _factory, project_id, draft_id, _profile_version_id = _prepare_compilable_project(
        engine, actor_id, master_key
    )
    headers = {"X-CaseFile-User-Id": str(actor_id)}
    app = create_app(engine.url.render_as_string(hide_password=False))
    with TestClient(app) as client:
        profile_response = client.post(
            f"/api/v1/projects/{project_id}/compiler-profiles",
            headers=headers,
            json={
                "profile_key": "novel.http",
                "name": "接口配置",
                "schema_id": "compiler.profile.v1",
                "payload": {"language": "zh-CN"},
            },
        )
        assert profile_response.status_code == 201, profile_response.text
        profile = profile_response.json()
        draft_response = client.get(
            f"/api/v1/projects/{project_id}/draft", headers=headers
        )
        assert draft_response.status_code == 200
        draft = draft_response.json()
        run_response = client.post(
            f"/api/v1/projects/{project_id}/compile-runs",
            headers=headers,
            json={
                "mode": "preview",
                "expected_draft_id": draft_id,
                "expected_draft_revision": draft["revision"],
                "canon_version_id": None,
                "exposure_plan_revision_id": None,
                "compiler_profile_version_id": profile["current_version_id"],
            },
        )
        assert run_response.status_code == 201, run_response.text
        run = run_response.json()
        assert run["execution"]["provider"] is None
        detail = client.get(
            f"/api/v1/projects/{project_id}/compile-runs/{run['compile_run_id']}",
            headers=headers,
        )
        assert detail.status_code == 200
        assert detail.json()["input_hash"] == run["input_hash"]


def test_artifact_content_api_requires_exact_run_ownership(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory, project_id, draft_id, profile_version_id = _prepare_compilable_project(
        engine, actor_id, master_key
    )
    with factory() as session:
        draft = CaseFileService(session).get_draft(actor_id, project_id)
        run = CompilerService(session).create_run(
            actor_id,
            project_id,
            mode="preview",
            expected_draft_id=draft_id,
            expected_draft_revision=int(draft["revision"]),
            canon_version_id=None,
            exposure_plan_revision_id=None,
            compiler_profile_version_id=profile_version_id,
        )
    assert Worker(
        factory, config=WorkerConfig(worker_id="compiler-artifact-api-worker")
    ).run_once()
    with factory() as session:
        artifact = session.scalar(
            select(CompileArtifact).where(
                CompileArtifact.compile_run_id == run["compile_run_id"],
                CompileArtifact.artifact_kind == "narrative_ir",
            )
        )
    assert artifact is not None

    headers = {"X-CaseFile-User-Id": str(actor_id)}
    app = create_app(engine.url.render_as_string(hide_password=False))
    with TestClient(app) as client:
        foreign_project_response = client.post(
            "/api/v1/projects",
            headers=headers,
            json={"title": "另一项目", "description": None, "profile": {}},
        )
        assert foreign_project_response.status_code == 201
        foreign_project_id = foreign_project_response.json()["id"]
        artifact_path = (
            f"/api/v1/projects/{project_id}/compile-runs/"
            f"{run['compile_run_id']}/artifacts/{artifact.id}"
        )

        response = client.get(
            artifact_path,
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["content_hash"] == artifact.content_hash
        assert response.json()["content"]["schema_id"] == "compiler.narrative-ir.v1"

        assert client.get(artifact_path).status_code == 401
        foreign_project = client.get(
            f"/api/v1/projects/{foreign_project_id}/compile-runs/"
            f"{run['compile_run_id']}/artifacts/{artifact.id}",
            headers=headers,
        )
        assert foreign_project.status_code == 404

        wrong_run = client.get(
            f"/api/v1/projects/{project_id}/compile-runs/999999/artifacts/{artifact.id}",
            headers=headers,
        )
        assert wrong_run.status_code == 404
        wrong_artifact = client.get(
            f"/api/v1/projects/{project_id}/compile-runs/"
            f"{run['compile_run_id']}/artifacts/999999",
            headers=headers,
        )
        assert wrong_artifact.status_code == 404
