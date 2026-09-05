"""Task HTTP projections must satisfy the shared contract without changing JSON."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from casefile.api.app import create_app
from casefile.api.dependencies import get_actor_user_id, get_session
from casefile.application.workflow_views import task_view
from casefile_contracts import TaskRun
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource


def projected_task(task_type: str, status: str) -> dict:
    task = SimpleNamespace(
        id=21, project_id=8, task_type=task_type, prompt_version="v22", status=status,
        stage="completed", provider=None if task_type == "novel_compile" else "deepseek",
        model_id=None if task_type == "novel_compile" else "deepseek-v4-pro",
        input_draft_revision=4, input_brief_revision=None, input_source_record_id=None,
        brief_intake_id=None, input_brief_intake_revision=None,
        base_brief_intake_candidate_id=None, agent_thread_id=None, input_message_id=None,
        output_message_id=None, input_hash="a" * 64, attempt_count=1,
        input_jsonb={"goal_session": {"goal_id": 3, "goal_revision": 2}},
        usage_jsonb={"total_tokens": 120}, result_snapshot_id=None,
        result_jsonb={"answer": "测试结果"} if status == "succeeded" else None,
        error_code=None, error_details_jsonb={}, component_step_runs=[],
        created_at=datetime(2026, 9, 5, tzinfo=UTC), updated_at=None,
    )
    return task_view(task)


@pytest.mark.parametrize("task_type", ["brief_polish", "casefile_chat", "novel_compile"])
@pytest.mark.parametrize(
    "status", ["queued", "running", "cancelling", "succeeded", "failed", "cancelled"],
)
def test_task_projection_matches_generated_contract(task_type: str, status: str) -> None:
    raw = projected_task(task_type, status)
    parsed = TaskRun.model_validate(raw)
    assert set(parsed.model_dump(exclude_unset=True)) == set(raw)
    schema = json.loads(
        (Path(__file__).resolve().parents[3] / "contracts/schemas/task/task.schema.json")
        .read_text(encoding="utf-8")
    )
    registry = Registry().with_resource(schema["$id"], Resource.from_contents(schema))
    Draft202012Validator(
        {"$ref": schema["$id"] + "#/$defs/TaskRun"}, registry=registry,
    ).validate(raw)
    assert raw["created_at"] == "2026-09-05T00:00:00+00:00"
    assert raw["updated_at"] is None


def test_task_http_preserves_payload_and_latest_query_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = projected_task("brief_polish", "succeeded")
    service = SimpleNamespace(
        require_generic_task_access=lambda *args: None,
        require_generic_task_type=lambda *args: None,
        get_task=lambda *args: payload,
        cancel_task=lambda *args: payload,
        get_latest_task=lambda *args, **kwargs: payload,
    )
    monkeypatch.setattr("casefile.api.workflow.WorkflowService", lambda session: service)
    app = create_app(verify_database=False)
    app.dependency_overrides[get_actor_user_id] = lambda: 1
    app.dependency_overrides[get_session] = lambda: None
    client = TestClient(app)
    assert client.get("/api/v1/projects/8/tasks/21").json() == payload
    cancelled = client.post("/api/v1/projects/8/tasks/21/cancel")
    assert cancelled.status_code == 202
    assert cancelled.json() == payload
    latest_url = "/api/v1/projects/8/tasks/latest"
    assert client.get(latest_url, params={"task_type": "brief_polish"}).json() == payload
    service.get_latest_task = lambda *args, **kwargs: None
    assert client.get(latest_url, params={"task_type": "brief_polish"}).json() is None
    for unsupported in ("reverse_parse", "novel_compile", "idea_generation"):
        assert client.get(latest_url, params={"task_type": unsupported}).status_code == 422


def test_task_routes_document_the_shared_response_contract() -> None:
    schema = create_app(verify_database=False).openapi()
    base = "/api/v1/projects/{project_id}/tasks/"
    for suffix in (
        "generate", "brief-polish", "brief-anchor-extract", "brief-strategy-options",
        "brief-intake-questions", "brief-intake-synthesize", "{task_run_id}/cancel",
        "{task_run_id}/resume", "{task_run_id}",
    ):
        method, status = ("get", "200") if suffix == "{task_run_id}" else ("post", "202")
        response = schema["paths"][base + suffix][method]["responses"][status]
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/TaskRun"
        }
    latest = schema["paths"][base + "latest"]["get"]["responses"]["200"]
    model = latest["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
    assert schema["components"]["schemas"][model]["anyOf"] == [
        {"$ref": "#/components/schemas/TaskRun"}, {"type": "null"}
    ]
