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
PROFILE: dict[str, object] = {}


def _brief(source_record_id: int) -> dict[str, object]:
    return {
        "source_record_ids": [source_record_id],
        "creative_intent": "围绕午夜回航建立目标无关的推理卷宗。",
        "reasoning_proposition": "是谁修改了航行记录，回航保护机制因何触发？",
        "resolution_mode": "author_anchored",
        "author_answer": "大副修改了记录，欠压保护触发了回航。",
        "author_anchors": [
            {
                "anchor_id": "anchor_api_first_officer",
                "statement": "大副修改了航行记录。",
            }
        ],
        "boundary_text": "必须保持唯一因果答案。",
        "creative_constraints": [
            {
                "constraint_id": "constraint_api_unique",
                "statement": "因果答案必须唯一。",
                "strength": "hard",
            }
        ],
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

        deepseek_saved = client.put(
            "/api/v1/settings/provider",
            headers=_identity(actor_id),
            json={
                "provider": "deepseek",
                "api_key": "sk-deepseek-api-secret",
                "model_id": "deepseek-v4-flash",
                "model_is_custom": False,
            },
        )
        assert deepseek_saved.status_code == 200
        assert deepseek_saved.json()["provider"] == "deepseek"
        assert deepseek_saved.json()["masked_api_key"].endswith("cret")
        openai_setting = client.get(
            "/api/v1/settings/provider?provider=openai",
            headers=_identity(actor_id),
        )
        assert openai_setting.json()["model_id"] == "gpt-5.6-sol"

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

        source_response = client.post(
            f"/api/v1/projects/{project_id}/sources",
            headers=_identity(actor_id),
            json={
                "source_kind": "human_original",
                "content_text": "一艘渡轮每天午夜会重新驶回同一座码头。",
            },
        )
        assert source_response.status_code == 201
        source = source_response.json()
        listed_sources = client.get(
            f"/api/v1/projects/{project_id}/sources",
            headers=_identity(actor_id),
        )
        assert listed_sources.json() == [source]

        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="api-test-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        polish_queued = client.post(
            f"/api/v1/projects/{project_id}/tasks/brief-polish",
            headers=_identity(actor_id),
            json={
                "source_record_id": source["source_record_id"],
                "provider": "deepseek",
            },
        )
        assert polish_queued.status_code == 202
        assert worker.run_once() is True
        latest_polish = client.get(
            f"/api/v1/projects/{project_id}/tasks/latest?task_type=brief_polish",
            headers=_identity(actor_id),
        )
        assert latest_polish.status_code == 200
        assert latest_polish.json()["result"]["proposal_source_record"]["source_kind"] == (
            "agent_polish_proposal"
        )

        updated = client.put(
            f"/api/v1/projects/{project_id}/brief",
            headers=_identity(actor_id),
            json={
                "expected_revision": 1,
                "content": _brief(source["source_record_id"]),
            },
        )
        assert updated.status_code == 200
        extract_queued = client.post(
            f"/api/v1/projects/{project_id}/tasks/brief-anchor-extract",
            headers=_identity(actor_id),
            json={
                "expected_brief_revision": updated.json()["draft_revision"],
                "provider": "deepseek",
            },
        )
        assert extract_queued.status_code == 202
        assert worker.run_once() is True
        extract_task = client.get(
            (
                f"/api/v1/projects/{project_id}/tasks/"
                f"{extract_queued.json()['task_run_id']}"
            ),
            headers=_identity(actor_id),
        )
        assert extract_task.json()["result"]["author_anchors"]
        assert extract_task.json()["input_brief_revision"] == updated.json()["draft_revision"]

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
                "provider": "deepseek",
            },
        )
        assert queued.status_code == 202
        assert queued.json()["provider"] == "deepseek"
        assert queued.json()["model_id"] == "deepseek-v4-flash"
        task_id = queued.json()["task_run_id"]

        assert worker.run_once() is True

        task = client.get(
            f"/api/v1/projects/{project_id}/tasks/{task_id}", headers=_identity(actor_id)
        )
        assert task.status_code == 200
        assert task.json()["status"] == "succeeded"
        assert task.json()["result"]["snapshot_id"] == task.json()["result_snapshot_id"]

        stream = client.get(
            f"/api/v1/projects/{project_id}/tasks/{task_id}/stream",
            headers={**_identity(actor_id), "Last-Event-ID": "1"},
        )
        assert stream.status_code == 200
        assert "id: 1\n" not in stream.text
        assert "event: task.succeeded" in stream.text
        assert "sk-test-api-secret" not in stream.text
        assert "sk-deepseek-api-secret" not in stream.text
        assert "chain_of_thought" not in stream.text

        invalid_cursor = client.get(
            f"/api/v1/projects/{project_id}/tasks/{task_id}/stream",
            headers={**_identity(actor_id), "Last-Event-ID": "bad"},
        )
        assert invalid_cursor.status_code == 422

        draft = client.get(f"/api/v1/projects/{project_id}/draft", headers=_identity(actor_id))
        assert draft.json()["revision"] == 2
        assert draft.json()["content"]["schema_version"] == "1.0"
