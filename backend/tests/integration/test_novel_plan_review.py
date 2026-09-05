"""Recommendation and exact approved plan continuation through real API/Worker."""

from unittest.mock import patch

import pytest
from casefile.api.app import create_app
from casefile.data_postgres.models import AgentModelCall, CompileArtifact, TaskRun
from casefile_contracts import NovelRecommendation
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from test_prose_shadow_runtime import _prepare, _providers, _result, _run

pytestmark = pytest.mark.postgres


def test_truncated_planner_output_is_persisted_as_failed_not_recoverable(workflow_database):
    from casefile.agent_runtime import FakeProvider
    from casefile.agent_runtime.story_planner import CompilerProviderOutputError
    from casefile.worker.runtime import Worker, WorkerConfig

    _, _, key = workflow_database
    factory, _, plan_run, _ = _prepare(workflow_database, planning_only=True)

    class TruncatedProvider(FakeProvider):
        def propose_skeleton(self, request):
            raise CompilerProviderOutputError(
                "compiler_model_output_truncated", '{"scenes":[', {"output_tokens": 8192}, "length"
            )

    with patch.dict("os.environ", {"CASEFILE_MASTER_KEY": key}):
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="truncated-compiler-test"),
            provider_factory=lambda _: TruncatedProvider(),
        )
        assert worker.run_once(task_run_id=plan_run["task_run_id"])
    with factory() as session:
        task = session.get(TaskRun, plan_run["task_run_id"])
        call = session.scalar(select(AgentModelCall).where(AgentModelCall.task_run_id == task.id))
        assert task.error_code == "compiler_model_output_truncated"
        assert call.status == "failed"
        assert call.raw_output_text == '{"scenes":['
        assert call.parse_status == "length"
        assert call.usage_jsonb["output_tokens"] == 8192


def test_reviewed_plan_is_reused_without_replanning(workflow_database):
    engine, actor, key = workflow_database
    factory, project, plan_run, draft = _prepare(workflow_database, planning_only=True)
    _run(factory, plan_run, _providers(), key)
    with factory() as session:
        task = session.get(TaskRun, plan_run["task_run_id"])
        artifacts = list(
            session.scalars(
                select(CompileArtifact).where(
                    CompileArtifact.compile_run_id == plan_run["compile_run_id"]
                )
            )
        )
    assert task.status == "succeeded", task.error_code
    assert not any(a.artifact_kind == "novel_candidate" for a in artifacts)
    plan = next(a for a in artifacts if a.artifact_kind == "novel_plan")
    body = {
        "mode": "preview",
        "expected_draft_id": draft["draft_id"],
        "expected_draft_revision": draft["revision"],
        "planner_provider": "deepseek",
        "compiler_profile_version_id": plan_run["compiler_profile_version_id"],
        "prose_renderer_shadow": True,
        "approved_plan_run_id": plan_run["compile_run_id"],
    }
    with TestClient(create_app(engine.url.render_as_string(hide_password=False))) as client:
        response = client.post(
            f"/api/v1/projects/{project}/compile-runs",
            json=body,
            headers={"X-CaseFile-User-Id": str(actor)},
        )
    assert response.status_code == 201, response.text
    prose_run = response.json()
    _run(factory, prose_run, _providers(), key)
    final, _, artifacts = _result(factory, prose_run)
    assert final.status == "succeeded", final.error_code
    assert final.input_jsonb["approved_novel_plan"]["content_hash"] == plan.content_hash
    assert (
        next(a for a in artifacts if a.artifact_kind == "novel_plan").content_hash
        == plan.content_hash
    )
    with factory() as session:
        calls = list(
            session.scalars(select(AgentModelCall).where(AgentModelCall.task_run_id == final.id))
        )
        assert not any(call.prompt_component_id == "story_planner" for call in calls)
    assert any(a.artifact_kind == "novel_candidate" for a in artifacts)


def test_recommendation_is_owned_revision_checked_and_has_no_fake_fallback(workflow_database):
    (
        engine,
        actor,
        _,
    ) = workflow_database
    _, project, _, draft = _prepare(workflow_database, planning_only=True)
    with engine.begin() as connection:
        other_actor = connection.execute(
            text("INSERT INTO users (display_name) VALUES ('Other author') RETURNING id")
        ).scalar_one()
    rec = NovelRecommendation(
        concept="一口气读完的渡轮谜案",
        rationale="围绕渡轮回航的单一谜题组织调查与揭晓。",
        chapters=2,
        scenes=4,
        style="克制，重视观察和验证。",
    )
    url = f"/api/v1/projects/{project}/novel-recommendation"
    body = {
        "expected_draft_id": draft["draft_id"],
        "expected_draft_revision": draft["revision"],
        "preferences": "紧凑",
    }
    with (
        TestClient(create_app(engine.url.render_as_string(hide_password=False))) as client,
        patch(
            "casefile.application.compiler.recommendation.recommend_novel", return_value=(rec, {})
        ) as provider,
    ):
        headers = {"X-CaseFile-User-Id": str(actor)}
        assert (
            client.post(
                url, json=body, headers={"X-CaseFile-User-Id": str(other_actor)}
            ).status_code
            == 404
        )
        provider.assert_not_called()
        assert (
            client.post(
                url,
                json={**body, "expected_draft_revision": draft["revision"] + 1},
                headers=headers,
            ).status_code
            == 409
        )
        provider.assert_not_called()
        assert client.post(url, json=body, headers=headers).json() == rec.model_dump()
        provider.side_effect = RuntimeError("private provider diagnostic")
        failed = client.post(url, json=body, headers=headers)
        assert failed.status_code == 502
        assert "private provider diagnostic" not in failed.text
        assert "concept" not in failed.json()
