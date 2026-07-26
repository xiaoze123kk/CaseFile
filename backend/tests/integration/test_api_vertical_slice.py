"""Real-PostgreSQL API acceptance test for the first CaseFile vertical slice."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from casefile.api.app import create_app
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.postgres
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _database_url() -> str:
    value = os.getenv("CASEFILE_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CASEFILE_TEST_DATABASE_URL is not configured")
    if not (make_url(value).database or "").endswith("_test"):
        pytest.fail("Refusing API integration test against a non-_test database")
    return value


def _config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT / "src"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="module")
def api_database() -> Iterator[tuple[str, Engine]]:
    database_url = _database_url()
    config = _config(database_url)
    engine = create_engine(database_url)
    with patch.dict(os.environ, {"DATABASE_URL": database_url}):
        command.downgrade(config, "base")
        command.upgrade(config, "head")
    try:
        yield database_url, engine
    finally:
        engine.dispose()
        with patch.dict(os.environ, {"DATABASE_URL": database_url}):
            command.downgrade(config, "base")


def _create_user(engine: Engine, name: str, *, status: str = "active") -> int:
    with engine.begin() as connection:
        return int(
            connection.execute(
                text(
                    "INSERT INTO users (display_name, status) "
                    "VALUES (:name, :status) RETURNING id"
                ),
                {"name": name, "status": status},
            ).scalar_one()
        )


def _identity(user_id: int, revision: int | None = None) -> dict[str, str]:
    headers = {"X-CaseFile-User-Id": str(user_id)}
    if revision is not None:
        headers["X-CaseFile-Base-Revision"] = str(revision)
    return headers


def _person(name: str) -> dict[str, object]:
    return {
        "entity_kind": "person",
        "name": name,
        "description": None,
        "traits": [],
        "attributes": {},
        "confidence": 1.0,
        "person": {"role": "调查员", "background": None},
    }


def _location(name: str) -> dict[str, object]:
    return {
        "entity_kind": "location",
        "name": name,
        "description": None,
        "traits": [],
        "attributes": {},
        "confidence": 1.0,
        "location": {"geo": {}, "movement_rules": {}},
    }


def test_project_edit_snapshot_isolation_conflict_and_archive(
    api_database: tuple[str, Engine],
) -> None:
    database_url, engine = api_database
    owner_id = _create_user(engine, "API Owner")
    other_id = _create_user(engine, "Other User")
    disabled_id = _create_user(engine, "Disabled User", status="disabled")
    app = create_app(database_url)

    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/projects").status_code == 401
        assert client.get("/api/v1/projects", headers=_identity(999999)).status_code == 401
        assert client.get("/api/v1/projects", headers=_identity(disabled_id)).status_code == 401
        missing_route = client.get("/missing")
        assert missing_route.status_code == 404
        assert set(missing_route.json()) == {"code", "message", "details"}

        created = client.post(
            "/api/v1/projects",
            headers=_identity(owner_id),
            json={"title": "重启事件", "description": None, "profile": {}},
        )
        assert created.status_code == 201
        project_id = created.json()["id"]
        assert created.json()["draft"]["revision"] == 1
        updated_project = client.patch(
            f"/api/v1/projects/{project_id}",
            headers=_identity(owner_id),
            json={"title": "重启事件（修订）"},
        )
        assert updated_project.status_code == 200
        assert updated_project.json()["title"] == "重启事件（修订）"
        listed_projects = client.get("/api/v1/projects", headers=_identity(owner_id))
        assert [item["id"] for item in listed_projects.json()] == [project_id]

        missing_base = client.post(
            f"/api/v1/projects/{project_id}/draft/entities",
            headers=_identity(owner_id),
            json=_person("无 revision"),
        )
        assert missing_base.status_code == 428
        assert missing_base.json()["code"] == "base_revision_required"

        person = client.post(
            f"/api/v1/projects/{project_id}/draft/entities",
            headers=_identity(owner_id, 1),
            json=_person("调查员"),
        )
        assert person.status_code == 201
        person_id = person.json()["object_id"]
        assert person.headers["X-CaseFile-Draft-Revision"] == "2"

        first_location = client.post(
            f"/api/v1/projects/{project_id}/draft/entities",
            headers=_identity(owner_id, 2),
            json=_location("控制室"),
        )
        assert first_location.status_code == 201
        first_location_id = first_location.json()["object_id"]

        second_location = client.post(
            f"/api/v1/projects/{project_id}/draft/entities",
            headers=_identity(owner_id, 3),
            json=_location("机房"),
        )
        assert second_location.status_code == 201
        second_location_id = second_location.json()["object_id"]

        wrong_type = client.put(
            f"/api/v1/projects/{project_id}/draft/entities/{first_location_id}/adjacent-locations",
            headers=_identity(owner_id, 4),
            json={"object_ids": [person_id]},
        )
        assert wrong_type.status_code == 422
        assert wrong_type.json()["code"] == "invalid_reference"

        adjacency = client.put(
            f"/api/v1/projects/{project_id}/draft/entities/{first_location_id}/adjacent-locations",
            headers=_identity(owner_id, 4),
            json={"object_ids": [second_location_id]},
        )
        assert adjacency.status_code == 200
        assert adjacency.json()["location"]["adjacent_location_object_ids"] == [
            second_location_id
        ]

        event_payload = {
            "title": "系统重启",
            "summary": None,
            "start_time": {"minute": 0},
            "end_time": None,
            "narrative_order": 1,
            "narrative_phase_object_id": None,
            "location_object_id": first_location_id,
            "visibility": "restricted",
            "truth_status": "true",
            "confidence": 1.0,
        }
        event = client.post(
            f"/api/v1/projects/{project_id}/draft/events",
            headers=_identity(owner_id, 5),
            json=event_payload,
        )
        assert event.status_code == 201
        event_id = event.json()["object_id"]

        dangling_actor = client.put(
            f"/api/v1/projects/{project_id}/draft/events/{event_id}/actors",
            headers=_identity(owner_id, 6),
            json={"object_ids": ["entity_missing"]},
        )
        assert dangling_actor.status_code == 422
        assert dangling_actor.json()["code"] == "invalid_reference"

        actors = client.put(
            f"/api/v1/projects/{project_id}/draft/events/{event_id}/actors",
            headers=_identity(owner_id, 6),
            json={"object_ids": [person_id]},
        )
        assert actors.status_code == 200
        assert actors.json()["actor_object_ids"] == [person_id]
        entities = client.get(
            f"/api/v1/projects/{project_id}/draft/entities", headers=_identity(owner_id)
        )
        assert len(entities.json()) == 3
        events = client.get(
            f"/api/v1/projects/{project_id}/draft/events", headers=_identity(owner_id)
        )
        assert [item["object_id"] for item in events.json()] == [event_id]

        draft = client.get(
            f"/api/v1/projects/{project_id}/draft", headers=_identity(owner_id)
        )
        assert draft.status_code == 200
        assert draft.json()["revision"] == 7
        assert draft.json()["content"]["events"][0]["location_object_id"] == first_location_id

        stale = client.put(
            f"/api/v1/projects/{project_id}/draft/events/{event_id}",
            headers=_identity(owner_id, 6),
            json=event_payload,
        )
        assert stale.status_code == 409
        assert stale.json()["code"] == "draft_revision_conflict"

        cross_user = client.get(
            f"/api/v1/projects/{project_id}", headers=_identity(other_id)
        )
        assert cross_user.status_code == 404
        cross_user_write = client.put(
            f"/api/v1/projects/{project_id}/draft/events/{event_id}/actors",
            headers=_identity(other_id, 7),
            json={"object_ids": []},
        )
        assert cross_user_write.status_code == 404

        in_use = client.delete(
            f"/api/v1/projects/{project_id}/draft/entities/{second_location_id}",
            headers=_identity(owner_id, 7),
        )
        assert in_use.status_code == 409
        assert in_use.json()["code"] == "object_in_use"

        snapshot = client.post(
            f"/api/v1/projects/{project_id}/draft/snapshots",
            headers=_identity(owner_id, 7),
        )
        assert snapshot.status_code == 201
        snapshot_id = snapshot.json()["id"]
        repeated = client.post(
            f"/api/v1/projects/{project_id}/draft/snapshots",
            headers=_identity(owner_id, 7),
        )
        assert repeated.status_code == 200
        assert repeated.json()["id"] == snapshot_id
        assert repeated.json()["content_hash"] == snapshot.json()["content_hash"]

        fetched = client.get(
            f"/api/v1/projects/{project_id}/draft/snapshots/{snapshot_id}",
            headers=_identity(owner_id),
        )
        assert fetched.status_code == 200
        assert fetched.json()["content"] == snapshot.json()["content"]
        listed_snapshots = client.get(
            f"/api/v1/projects/{project_id}/draft/snapshots",
            headers=_identity(owner_id),
        )
        assert [item["id"] for item in listed_snapshots.json()] == [snapshot_id]

        deleted_event = client.delete(
            f"/api/v1/projects/{project_id}/draft/events/{event_id}",
            headers=_identity(owner_id, 7),
        )
        assert deleted_event.status_code == 204
        assert deleted_event.headers["X-CaseFile-Draft-Revision"] == "8"
        assert (
            client.get(
                f"/api/v1/projects/{project_id}/draft/events/{event_id}",
                headers=_identity(owner_id),
            ).status_code
            == 404
        )
        with engine.connect() as connection:
            soft_deleted = connection.execute(
                text(
                    """
                    SELECT object.deleted_at IS NOT NULL,
                           event.location_id,
                           event.narrative_phase_id,
                           (SELECT count(*) FROM casefile_refs ref
                             WHERE ref.from_object_id = object.id)
                      FROM casefile_objects object
                      JOIN events event ON event.object_registry_id = object.id
                     WHERE object.object_id = :object_id
                    """
                ),
                {"object_id": event_id},
            ).one()
        assert tuple(soft_deleted) == (True, None, None, 0)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO casefile_objects (
                        project_id, casefile_id, draft_id, object_id, object_type,
                        revision, source_jsonb, confirmation_status
                    )
                    SELECT project_id, casefile_id, draft_id, object_id, object_type,
                           1, '{}'::jsonb, 'user_confirmed'
                      FROM casefile_objects
                     WHERE object_id = :object_id
                    """
                ),
                {"object_id": event_id},
            )

        archived = client.post(
            f"/api/v1/projects/{project_id}/archive", headers=_identity(owner_id)
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "archived"
        rejected = client.put(
            f"/api/v1/projects/{project_id}/draft/events/{event_id}/actors",
            headers=_identity(owner_id, 8),
            json={"object_ids": []},
        )
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "project_archived"
        rejected_snapshot = client.post(
            f"/api/v1/projects/{project_id}/draft/snapshots",
            headers=_identity(owner_id, 8),
        )
        assert rejected_snapshot.status_code == 409
        assert rejected_snapshot.json()["code"] == "project_archived"
