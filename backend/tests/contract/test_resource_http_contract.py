"""Project and Brief view contracts preserve existing HTTP representations."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from casefile.api.app import create_app
from casefile.api.dependencies import get_actor_user_id, get_session
from casefile.application.services import _project_view
from casefile.application.workflow_views import brief_version_view, brief_view
from casefile_contracts import BriefVersionView, BriefView, ProjectView
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("archived", [False, True])
def test_resource_projections_and_http_preserve_json(
    archived: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 9, 5, tzinfo=UTC)
    project = jsonable_encoder(_project_view(SimpleNamespace(
        project=SimpleNamespace(
            id=1, title="卷宗", description=None, profile_jsonb={"custom": [1, "保留"]},
            status="archived" if archived else "active", archived_at=now if archived else None,
            created_at=now, updated_at=now,
        ),
        casefile=SimpleNamespace(id=2, current_draft_id=3),
        draft=SimpleNamespace(
            id=3, title="工作稿", revision=1, schema_version="2.0", status="active",
        ),
    )))
    content = json.loads((ROOT / "fixtures/benchmark/brief_to_draft.json").read_text(
        encoding="utf-8",
    ))["brief"]
    brief = brief_view(SimpleNamespace(
        id=4, public_id="brief_test", draft_revision=1, draft_jsonb=content if archived else {},
        current_version_id=5 if archived else None, updated_at=now,
    ))
    if archived:
        brief["current_version_no"] = 1
    version = brief_version_view(SimpleNamespace(
        id=5, brief_id=4, version_no=1, content_jsonb=content,
        content_hash="a" * 64, confirmed_at=now,
    ), "brief_test")
    registry = Registry()
    for path in (ROOT / "contracts/schemas").rglob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(schema["$id"], Resource.from_contents(schema))
    for model, raw, schema_path in (
        (ProjectView, project, "project/project.schema.json"),
        (BriefView, brief, "brief/brief-views.schema.json"),
        (BriefVersionView, version, "brief/brief-views.schema.json"),
    ):
        assert set(model.model_validate(raw).model_dump(exclude_unset=True)) == set(raw)
        Draft202012Validator({
            "$ref": f"https://casefile.local/schemas/v2/{schema_path}#/$defs/{model.__name__}",
        }, registry=registry).validate(raw)
    monkeypatch.setattr("casefile.api.app.CaseFileService", lambda session: SimpleNamespace(
        get_project=lambda *args: project, list_projects=lambda *args: [project],
        archive_project=lambda *args: project, unarchive_project=lambda *args: project,
    ))
    monkeypatch.setattr("casefile.api.workflow.WorkflowService", lambda session: SimpleNamespace(
        get_brief=lambda *args: brief, confirm_brief=lambda *args, **kwargs: version,
    ))
    app = create_app(verify_database=False)
    app.dependency_overrides[get_actor_user_id] = lambda: 1
    app.dependency_overrides[get_session] = lambda: None
    client = TestClient(app)
    assert client.get("/api/v1/projects").json() == [project]
    assert client.get("/api/v1/projects/1").json() == project
    for action in ("archive", "unarchive"):
        assert client.post(f"/api/v1/projects/1/{action}").json() == project
    assert client.get("/api/v1/projects/1/brief").json() == brief
    response = client.post("/api/v1/projects/1/brief/confirm", json={"expected_revision": 1})
    assert response.status_code == 201
    assert response.json() == version
    assert brief["updated_at"] == version["confirmed_at"] == "2026-09-05T00:00:00+00:00"


def test_resource_routes_publish_generated_contracts() -> None:
    schema = create_app(verify_database=False).openapi()
    for path, method, status, model in (
        ("/projects", "post", "201", "ProjectView"),
        ("/projects/{project_id}", "get", "200", "ProjectView"),
        ("/projects/{project_id}", "patch", "200", "ProjectView"),
        ("/projects/{project_id}/archive", "post", "200", "ProjectView"),
        ("/projects/{project_id}/unarchive", "post", "200", "ProjectView"),
        ("/projects/{project_id}/brief", "get", "200", "BriefView"),
        ("/projects/{project_id}/brief", "put", "200", "BriefView"),
        ("/projects/{project_id}/brief/confirm", "post", "201", "BriefVersionView"),
    ):
        response = schema["paths"]["/api/v1" + path][method]["responses"][status]
        assert response["content"]["application/json"]["schema"]["$ref"] == (
            "#/components/schemas/" + model
        )
