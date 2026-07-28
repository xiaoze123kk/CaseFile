"""Real-PostgreSQL API acceptance test for the Agent generation golden path."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from casefile.agent_runtime import FakeProvider
from casefile.agent_runtime.credentials import generate_master_key
from casefile.api.app import create_app
from casefile.worker.runtime import Worker, WorkerConfig
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROFILE = {
    "content_type": "interactive_reasoning",
    "target_audience": "adult_general",
    "primary_use_case": "idea_to_playtest",
    "genres": ["mystery"],
    "target_duration_minutes": 90,
    "target_participant_count": 4,
    "difficulty_template": "medium",
    "collaboration_mode": "single_lead_review",
}
BRIEF = {
    "source_text": "一艘渡轮每天午夜会重新驶回同一座码头。",
    "one_line_concept": "玩家需要在重复靠岸前找出让渡轮回航的真实原因。",
    "core_mystery": "是谁修改了航行记录，以及回航是否在保护乘客？",
    "player_goal": "重建最后一小时的航行事实并决定是否终止回航。",
    "gameplay_loop": "调查舱室，交换信息，提出假设，验证记录，做出决定。",
    "constraints": ["真相必须唯一且可由公开线索验证"],
    "open_questions": ["船长缺失的十二分钟记录在哪里？"],
    "project_profile": PROFILE,
}


def _database_url() -> str:
    value = os.getenv("CASEFILE_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CASEFILE_TEST_DATABASE_URL is not configured")
    if not (make_url(value).database or "").endswith("_test"):
        pytest.fail("CASEFILE_TEST_DATABASE_URL must point to a disposable *_test database")
    return value


def _config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT / "src"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture
def api_database() -> Iterator[tuple[str, Engine, int, str]]:
    database_url = _database_url()
    config = _config(database_url)
    master_key = generate_master_key()
    with patch.dict(
        os.environ,
        {"DATABASE_URL": database_url, "CASEFILE_MASTER_KEY": master_key},
    ):
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                actor_id = int(
                    connection.execute(
                        text("INSERT INTO users (display_name) VALUES ('API Owner') RETURNING id")
                    ).scalar_one()
                )
            yield database_url, engine, actor_id, master_key
        finally:
            engine.dispose()
            command.downgrade(config, "base")


def _identity(actor_id: int) -> dict[str, str]:
    return {"X-CaseFile-User-Id": str(actor_id)}


def test_settings_brief_generation_sse_and_completion_gate(
    api_database: tuple[str, Engine, int, str],
) -> None:
    database_url, engine, actor_id, master_key = api_database
    app = create_app(database_url)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}), TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/projects").status_code == 401

        saved = client.put(
            "/api/v1/settings/provider",
            headers=_identity(actor_id),
            json={
                "api_key": "sk-test-api-secret",
                "model_id": "gpt-5.6-sol",
                "model_is_custom": False,
            },
        )
        assert saved.status_code == 200
        assert saved.json()["masked_api_key"].endswith("cret")
        assert "api_key" not in saved.json()

        created = client.post(
            "/api/v1/projects",
            headers=_identity(actor_id),
            json={"title": "午夜回航", "description": None, "profile": PROFILE},
        )
        assert created.status_code == 201
        project_id = created.json()["id"]
        empty = client.get(f"/api/v1/projects/{project_id}/draft", headers=_identity(actor_id))
        assert empty.status_code == 200
        assert empty.json()["content"] is None

        updated = client.put(
            f"/api/v1/projects/{project_id}/brief",
            headers=_identity(actor_id),
            json={"expected_revision": 1, "content": BRIEF},
        )
        assert updated.status_code == 200
        confirmed = client.post(
            f"/api/v1/projects/{project_id}/brief/confirm",
            headers=_identity(actor_id),
            json={"expected_revision": updated.json()["draft_revision"]},
        )
        assert confirmed.status_code == 201

        queued = client.post(
            f"/api/v1/projects/{project_id}/tasks/generate",
            headers=_identity(actor_id),
            json={
                "brief_version_id": confirmed.json()["brief_version_id"],
                "expected_draft_revision": 1,
            },
        )
        assert queued.status_code == 202
        task_id = queued.json()["task_run_id"]

        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="api-test-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert worker.run_once() is True

        task = client.get(
            f"/api/v1/projects/{project_id}/tasks/{task_id}", headers=_identity(actor_id)
        )
        assert task.status_code == 200
        assert task.json()["status"] == "succeeded"

        stream = client.get(
            f"/api/v1/projects/{project_id}/tasks/{task_id}/stream",
            headers={**_identity(actor_id), "Last-Event-ID": "1"},
        )
        assert stream.status_code == 200
        assert "id: 1\n" not in stream.text
        assert "event: task.succeeded" in stream.text
        assert "sk-test-api-secret" not in stream.text
        assert "chain_of_thought" not in stream.text

        invalid_cursor = client.get(
            f"/api/v1/projects/{project_id}/tasks/{task_id}/stream",
            headers={**_identity(actor_id), "Last-Event-ID": "bad"},
        )
        assert invalid_cursor.status_code == 422

        draft = client.get(f"/api/v1/projects/{project_id}/draft", headers=_identity(actor_id))
        assert draft.json()["revision"] == 2
        assert draft.json()["content"]["schema_version"] == "1.0"
