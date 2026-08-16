"""Real-PostgreSQL acceptance test for the recoverable Brief Intake loop."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
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


def _database_url() -> str:
    value = os.getenv("CASEFILE_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CASEFILE_TEST_DATABASE_URL is not configured")
    if not (make_url(value).database or "").endswith("_test"):
        pytest.fail("CASEFILE_TEST_DATABASE_URL must use a disposable *_test database")
    return value


def _config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT / "src"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture
def intake_database() -> Iterator[tuple[str, Engine, int, int, str]]:
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
                        text(
                            "INSERT INTO users (display_name) "
                            "VALUES ('Intake Owner') RETURNING id"
                        )
                    ).scalar_one()
                )
                stranger_id = int(
                    connection.execute(
                        text(
                            "INSERT INTO users (display_name) "
                            "VALUES ('Other Owner') RETURNING id"
                        )
                    ).scalar_one()
                )
            yield database_url, engine, actor_id, stranger_id, master_key
        finally:
            engine.dispose()
            command.downgrade(config, "base")


def _identity(actor_id: int) -> dict[str, str]:
    return {"X-CaseFile-User-Id": str(actor_id)}


def _manual_candidate(concept: str) -> dict[str, object]:
    return {
        "concept": concept,
        "core_selling_points": ["三份可靠记录互相冲突", "作者保留最终事实决定权"],
        "content_outline": ["建立时间矛盾", "交叉验证来源", "揭示记录改写"],
        "reasoning_goal": "解释同一事件为何在三份记录中拥有不同发生时间。",
        "resolution_mode": "agent_proposed",
        "conclusion_mode": "undetermined",
        "author_answer": None,
        "constraints": [
            {
                "constraint_key": "constraint_keep_archive",
                "category": "must_keep",
                "statement": "必须保留记录被人为改写这一事实。",
                "strength": "hard",
                "confirmed": True,
                "source": "user_confirmed",
            }
        ],
        "pending_decisions": [
            {
                "decision_key": "decision_supporting_cast",
                "prompt": "是否合并次要证人？",
                "impact": "只影响角色规模，不改变核心解答。",
                "source": "unresolved",
            }
        ],
        "scope_estimate": "中篇，4 名核心角色。",
        "risk_notes": ["时间线信息密度较高。"],
        "field_sources": {
            "concept": "user_confirmed",
            "core_selling_points": "agent_suggestion",
            "content_outline": "agent_suggestion",
            "reasoning_goal": "user_confirmed",
            "resolution_mode": "user_confirmed",
            "conclusion_mode": "user_confirmed",
            "author_answer": "unresolved",
            "constraints": "user_confirmed",
            "scope_estimate": "agent_suggestion",
            "risk_notes": "agent_suggestion",
        },
    }


def test_brief_intake_recovers_questions_candidates_and_adopts_to_brief(
    intake_database: tuple[str, Engine, int, int, str],
) -> None:
    database_url, engine, actor_id, stranger_id, master_key = intake_database
    app = create_app(database_url)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}), TestClient(app) as client:
        setting = client.put(
            "/api/v1/settings/provider",
            headers=_identity(actor_id),
            json={
                "api_key": "sk-intake-test-secret",
                "model_id": "gpt-5.6-sol",
                "model_is_custom": False,
            },
        )
        assert setting.status_code == 200
        project = client.post(
            "/api/v1/projects",
            headers=_identity(actor_id),
            json={"title": "不存在的时间", "description": None, "profile": {}},
        )
        assert project.status_code == 201
        project_id = project.json()["id"]

        intake = client.get(
            f"/api/v1/projects/{project_id}/brief-intake",
            headers=_identity(actor_id),
        )
        assert intake.status_code == 200
        assert intake.json()["stage"] == "idea"
        assert intake.json()["revision"] == 1
        assert intake.json()["current_source"] is None

        source = client.put(
            f"/api/v1/projects/{project_id}/brief-intake/source",
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 1,
                "content_text": "一名档案员发现三份可靠证词都指向一段不存在的时间。",
            },
        )
        assert source.status_code == 200
        assert source.json()["revision"] == 2
        assert source.json()["current_source"]["source_kind"] == "human_original"
        source_id = source.json()["current_source"]["source_record_id"]

        conflict = client.put(
            f"/api/v1/projects/{project_id}/brief-intake/source",
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 1,
                "content_text": "过时页面试图覆盖原稿。",
            },
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "brief_intake_revision_conflict"

        queued_questions = client.post(
            f"/api/v1/projects/{project_id}/tasks/brief-intake-questions",
            headers=_identity(actor_id),
            json={"expected_intake_revision": 2, "provider": "openai"},
        )
        assert queued_questions.status_code == 202
        assert queued_questions.json()["input_brief_intake_revision"] == 2
        assert queued_questions.json()["input_source_record_id"] == source_id

        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="brief-intake-test-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert worker.run_once() is True

        recovered = client.get(
            f"/api/v1/projects/{project_id}/brief-intake",
            headers=_identity(actor_id),
        ).json()
        assert recovered["revision"] == 4
        assert recovered["stage"] == "questions"
        assert len(recovered["questions"]) == 2
        hard_question = next(item for item in recovered["questions"] if item["required"])
        optional_question = next(
            item for item in recovered["questions"] if not item["required"]
        )

        rejected_pending = client.patch(
            (
                f"/api/v1/projects/{project_id}/brief-intake/questions/"
                f"{hard_question['question_key']}"
            ),
            headers=_identity(actor_id),
            json={"expected_intake_revision": 4, "answer_mode": "pending"},
        )
        assert rejected_pending.status_code == 422
        assert rejected_pending.json()["code"] == "brief_intake_required_question_pending"

        answered = client.patch(
            (
                f"/api/v1/projects/{project_id}/brief-intake/questions/"
                f"{hard_question['question_key']}"
            ),
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 4,
                "answer_mode": "suggestion",
                "suggestion_index": 0,
            },
        )
        assert answered.status_code == 200
        assert answered.json()["revision"] == 5
        pending = client.patch(
            (
                f"/api/v1/projects/{project_id}/brief-intake/questions/"
                f"{optional_question['question_key']}"
            ),
            headers=_identity(actor_id),
            json={"expected_intake_revision": 5, "answer_mode": "pending"},
        )
        assert pending.status_code == 200
        assert pending.json()["hard_questions_resolved"] is True
        assert pending.json()["revision"] == 6

        queued_synthesis = client.post(
            f"/api/v1/projects/{project_id}/tasks/brief-intake-synthesize",
            headers=_identity(actor_id),
            json={"expected_intake_revision": 6, "provider": "openai"},
        )
        assert queued_synthesis.status_code == 202
        assert worker.run_once() is True
        synthesized = client.get(
            f"/api/v1/projects/{project_id}/brief-intake",
            headers=_identity(actor_id),
        ).json()
        assert synthesized["revision"] == 8
        assert synthesized["stage"] == "confirmation"
        first_candidate = next(
            item for item in synthesized["candidates"] if item["is_current"]
        )
        assert first_candidate["origin"] == "agent_synthesis"
        assert first_candidate["content"]["pending_decisions"]

        saved = client.post(
            (
                f"/api/v1/projects/{project_id}/brief-intake/candidates/"
                f"{first_candidate['candidate_id']}/save"
            ),
            headers=_identity(actor_id),
            json={"expected_intake_revision": 8},
        )
        assert saved.status_code == 200
        assert saved.json()["revision"] == 9

        manual = client.post(
            f"/api/v1/projects/{project_id}/brief-intake/candidates",
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 9,
                "parent_candidate_id": first_candidate["candidate_id"],
                "content": _manual_candidate("人工表单修改后的概念"),
                "activate": True,
            },
        )
        assert manual.status_code == 201
        assert manual.json()["revision"] == 10
        manual_candidate_id = manual.json()["current_candidate_id"]

        abandoned = client.post(
            (
                f"/api/v1/projects/{project_id}/brief-intake/candidates/"
                f"{first_candidate['candidate_id']}/activate"
            ),
            headers=_identity(actor_id),
            json={"expected_intake_revision": 10},
        )
        assert abandoned.status_code == 200
        assert abandoned.json()["revision"] == 11
        assert abandoned.json()["current_candidate_id"] == first_candidate["candidate_id"]

        dialogue = client.post(
            f"/api/v1/projects/{project_id}/tasks/brief-intake-synthesize",
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 11,
                "provider": "openai",
                "base_candidate_id": first_candidate["candidate_id"],
                "instruction": "把内容骨架压缩为三个阶段，其他已确认内容不变。",
            },
        )
        assert dialogue.status_code == 202
        assert worker.run_once() is True
        revised = client.get(
            f"/api/v1/projects/{project_id}/brief-intake",
            headers=_identity(actor_id),
        ).json()
        assert revised["revision"] == 13
        dialogue_candidate = next(
            item for item in revised["candidates"] if item["is_current"]
        )
        assert dialogue_candidate["origin"] == "dialogue_revision"
        assert dialogue_candidate["parent_candidate_id"] == first_candidate["candidate_id"]
        assert any(
            item["candidate_id"] == manual_candidate_id for item in revised["candidates"]
        )

        brief_before = client.get(
            f"/api/v1/projects/{project_id}/brief", headers=_identity(actor_id)
        ).json()
        stale_adopt = client.post(
            (
                f"/api/v1/projects/{project_id}/brief-intake/candidates/"
                f"{dialogue_candidate['candidate_id']}/adopt"
            ),
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 13,
                "expected_brief_revision": brief_before["draft_revision"] + 1,
            },
        )
        assert stale_adopt.status_code == 409
        assert stale_adopt.json()["code"] == "brief_revision_conflict"

        adopted = client.post(
            (
                f"/api/v1/projects/{project_id}/brief-intake/candidates/"
                f"{dialogue_candidate['candidate_id']}/adopt"
            ),
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 13,
                "expected_brief_revision": brief_before["draft_revision"],
            },
        )
        assert adopted.status_code == 200
        assert adopted.json()["intake"]["stage"] == "brief_review"
        assert adopted.json()["intake"]["revision"] == 14
        brief = adopted.json()["brief"]
        assert brief["draft_revision"] == 2
        assert brief["content"]["creative_intent"] == dialogue_candidate["content"]["concept"]
        assert brief["content"]["source_record_ids"] == [source_id]
        assert brief["content"]["author_anchors"] == []
        assert brief["content"]["creative_constraints"] == []
        assert brief["content"]["core_selling_points"]

        formal = client.get(
            f"/api/v1/projects/{project_id}/brief", headers=_identity(actor_id)
        )
        assert formal.status_code == 200
        assert formal.json()["content"] == brief["content"]
        forbidden = client.get(
            f"/api/v1/projects/{project_id}/brief-intake",
            headers=_identity(stranger_id),
        )
        assert forbidden.status_code == 404

        candidate_id = dialogue_candidate["candidate_id"]
        question_id = hard_question["question_key"]
        with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE brief_intake_candidates "
                    "SET content_jsonb = content_jsonb || '{\"tampered\": true}'::jsonb "
                    "WHERE id = :candidate_id"
                ),
                {"candidate_id": candidate_id},
            )
        with pytest.raises(sa.exc.DBAPIError), engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE brief_intake_questions SET prompt = 'tampered' "
                    "WHERE intake_id = :intake_id AND question_key = :question_key"
                ),
                {
                    "intake_id": adopted.json()["intake"]["brief_intake_id"],
                    "question_key": question_id,
                },
            )


def test_brief_intake_appends_optional_question_batches(
    intake_database: tuple[str, Engine, int, int, str],
) -> None:
    database_url, engine, actor_id, _stranger_id, master_key = intake_database
    app = create_app(database_url)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}), TestClient(app) as client:
        setting = client.put(
            "/api/v1/settings/provider",
            headers=_identity(actor_id),
            json={
                "api_key": "sk-intake-additional-test",
                "model_id": "gpt-5.6-sol",
                "model_is_custom": False,
            },
        )
        assert setting.status_code == 200
        project = client.post(
            "/api/v1/projects",
            headers=_identity(actor_id),
            json={"title": "追加追问", "description": None, "profile": {}},
        )
        project_id = project.json()["id"]
        source = client.put(
            f"/api/v1/projects/{project_id}/brief-intake/source",
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 1,
                "content_text": "三份独立档案都记录了同一段不存在的时间。",
            },
        )
        assert source.status_code == 200

        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="brief-intake-additional-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )

        initial = client.post(
            f"/api/v1/projects/{project_id}/tasks/brief-intake-questions",
            headers=_identity(actor_id),
            json={"expected_intake_revision": 2, "provider": "openai"},
        )
        assert initial.status_code == 202
        assert worker.run_once() is True
        first_batch = client.get(
            f"/api/v1/projects/{project_id}/brief-intake",
            headers=_identity(actor_id),
        ).json()
        assert len(first_batch["questions"]) == 2

        additional = client.post(
            f"/api/v1/projects/{project_id}/tasks/brief-intake-questions",
            headers=_identity(actor_id),
            json={"expected_intake_revision": 4, "provider": "openai"},
        )
        assert additional.status_code == 202
        assert worker.run_once() is True
        combined = client.get(
            f"/api/v1/projects/{project_id}/brief-intake",
            headers=_identity(actor_id),
        ).json()

        assert combined["revision"] == 6
        assert len(combined["questions"]) == 4
        assert [question["ordinal"] for question in combined["questions"]] == [1, 2, 3, 4]
        assert len({question["question_key"] for question in combined["questions"]}) == 4
        assert sum(1 for question in combined["questions"] if question["required"]) == 1
        assert [question["prompt"] for question in combined["questions"][:2]] == [
            question["prompt"] for question in first_batch["questions"]
        ]
        assert all(
            not question["required"] for question in combined["questions"][2:]
        )


def test_brief_intake_archives_stale_tasks_and_allows_manual_recovery(
    intake_database: tuple[str, Engine, int, int, str],
) -> None:
    database_url, engine, actor_id, _stranger_id, master_key = intake_database
    app = create_app(database_url)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}), TestClient(app) as client:
        setting = client.put(
            "/api/v1/settings/provider",
            headers=_identity(actor_id),
            json={
                "api_key": "sk-intake-stale-secret",
                "model_id": "gpt-5.6-sol",
                "model_is_custom": False,
            },
        )
        assert setting.status_code == 200
        project = client.post(
            "/api/v1/projects",
            headers=_identity(actor_id),
            json={"title": "过时任务", "description": None, "profile": {}},
        )
        project_id = project.json()["id"]
        source = client.put(
            f"/api/v1/projects/{project_id}/brief-intake/source",
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 1,
                "content_text": "一份记录声称钟楼在建成前就已经敲响。",
            },
        )
        assert source.status_code == 200
        queued_questions = client.post(
            f"/api/v1/projects/{project_id}/tasks/brief-intake-questions",
            headers=_identity(actor_id),
            json={"expected_intake_revision": 2, "provider": "openai"},
        )
        assert queued_questions.status_code == 202

        revised_source = client.put(
            f"/api/v1/projects/{project_id}/brief-intake/source",
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 3,
                "content_text": "一份原始记录声称钟楼在奠基前就已经敲响。",
            },
        )
        assert revised_source.status_code == 200
        assert revised_source.json()["revision"] == 4

        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="brief-intake-stale-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert worker.run_once() is True
        stale_questions = client.get(
            (
                f"/api/v1/projects/{project_id}/tasks/"
                f"{queued_questions.json()['task_run_id']}"
            ),
            headers=_identity(actor_id),
        ).json()
        assert stale_questions["status"] == "succeeded"
        assert stale_questions["result"]["stale"] is True
        recovered = client.get(
            f"/api/v1/projects/{project_id}/brief-intake",
            headers=_identity(actor_id),
        ).json()
        assert recovered["revision"] == 4
        assert recovered["stage"] == "idea"
        assert recovered["questions"] == []

        current_questions = client.post(
            f"/api/v1/projects/{project_id}/tasks/brief-intake-questions",
            headers=_identity(actor_id),
            json={"expected_intake_revision": 4, "provider": "openai"},
        )
        assert current_questions.status_code == 202
        assert worker.run_once() is True
        recovered = client.get(
            f"/api/v1/projects/{project_id}/brief-intake",
            headers=_identity(actor_id),
        ).json()
        assert recovered["revision"] == 6
        hard_question = next(item for item in recovered["questions"] if item["required"])
        optional_question = next(
            item for item in recovered["questions"] if not item["required"]
        )
        answered = client.patch(
            (
                f"/api/v1/projects/{project_id}/brief-intake/questions/"
                f"{hard_question['question_key']}"
            ),
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 6,
                "answer_mode": "suggestion",
                "suggestion_index": 0,
            },
        )
        assert answered.status_code == 200
        pending = client.patch(
            (
                f"/api/v1/projects/{project_id}/brief-intake/questions/"
                f"{optional_question['question_key']}"
            ),
            headers=_identity(actor_id),
            json={"expected_intake_revision": 7, "answer_mode": "pending"},
        )
        assert pending.status_code == 200
        queued_synthesis = client.post(
            f"/api/v1/projects/{project_id}/tasks/brief-intake-synthesize",
            headers=_identity(actor_id),
            json={"expected_intake_revision": 8, "provider": "openai"},
        )
        assert queued_synthesis.status_code == 202

        changed_answer = client.patch(
            (
                f"/api/v1/projects/{project_id}/brief-intake/questions/"
                f"{optional_question['question_key']}"
            ),
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 9,
                "answer_mode": "answer",
                "answer_text": "短篇，三名核心角色。",
            },
        )
        assert changed_answer.status_code == 200
        assert worker.run_once() is True
        stale_synthesis = client.get(
            (
                f"/api/v1/projects/{project_id}/tasks/"
                f"{queued_synthesis.json()['task_run_id']}"
            ),
            headers=_identity(actor_id),
        ).json()
        assert stale_synthesis["status"] == "succeeded"
        assert stale_synthesis["result"]["stale"] is True

        recovered = client.get(
            f"/api/v1/projects/{project_id}/brief-intake",
            headers=_identity(actor_id),
        ).json()
        assert recovered["revision"] == 10
        assert recovered["current_candidate_id"] is None
        assert len(recovered["candidates"]) == 1
        stale_candidate = recovered["candidates"][0]
        assert stale_candidate["is_stale"] is True
        assert stale_candidate["can_activate"] is False
        rejected_activation = client.post(
            (
                f"/api/v1/projects/{project_id}/brief-intake/candidates/"
                f"{stale_candidate['candidate_id']}/activate"
            ),
            headers=_identity(actor_id),
            json={"expected_intake_revision": 10},
        )
        assert rejected_activation.status_code == 409
        assert rejected_activation.json()["code"] == "brief_intake_candidate_stale"

        manual = client.post(
            f"/api/v1/projects/{project_id}/brief-intake/candidates",
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 10,
                "parent_candidate_id": stale_candidate["candidate_id"],
                "content": _manual_candidate("人工接管后的当前概念"),
                "activate": True,
            },
        )
        assert manual.status_code == 201
        assert manual.json()["stage"] == "confirmation"
        assert manual.json()["revision"] == 11


def test_brief_intake_initializes_legacy_projects_once_and_keeps_review_closed(
    intake_database: tuple[str, Engine, int, int, str],
) -> None:
    database_url, _engine, actor_id, _stranger_id, master_key = intake_database
    app = create_app(database_url)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}), TestClient(app) as client:
        project = client.post(
            "/api/v1/projects",
            headers=_identity(actor_id),
            json={"title": "旧版卷宗", "description": None, "profile": {}},
        )
        project_id = project.json()["id"]
        source = client.post(
            f"/api/v1/projects/{project_id}/sources",
            headers=_identity(actor_id),
            json={
                "source_kind": "human_original",
                "content_text": "旧流程已经保存的原始想法。",
            },
        )
        source_id = source.json()["source_record_id"]
        brief = client.put(
            f"/api/v1/projects/{project_id}/brief",
            headers=_identity(actor_id),
            json={
                "expected_revision": 1,
                "content": {
                    "source_record_ids": [source_id],
                    "creative_intent": "恢复旧流程中的正式简报草稿。",
                    "reasoning_proposition": "验证旧项目不会被 Intake 回写。",
                    "resolution_mode": "open",
                    "conclusion_mode": "open_interpretation",
                    "author_answer": None,
                    "author_anchors": [],
                    "boundary_text": None,
                    "creative_constraints": [],
                },
            },
        )
        assert brief.status_code == 200

        first = client.get(
            f"/api/v1/projects/{project_id}/brief-intake",
            headers=_identity(actor_id),
        )
        second = client.get(
            f"/api/v1/projects/{project_id}/brief-intake",
            headers=_identity(actor_id),
        )
        assert first.status_code == second.status_code == 200
        assert first.json()["brief_intake_id"] == second.json()["brief_intake_id"]
        assert first.json()["revision"] == second.json()["revision"] == 1
        assert first.json()["stage"] == second.json()["stage"] == "brief_review"
        assert first.json()["current_source"]["source_record_id"] == source_id

        blocked = client.put(
            f"/api/v1/projects/{project_id}/brief-intake/source",
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 1,
                "content_text": "不能从正式审阅倒退回建案中心。",
            },
        )
        assert blocked.status_code == 409
        assert blocked.json()["code"] == "brief_intake_already_adopted"
        assert blocked.json()["message"] == "当前建案已进入正式创作简报审阅，不能再回退修改。"

        source_only_project = client.post(
            "/api/v1/projects",
            headers=_identity(actor_id),
            json={"title": "仅有来源", "description": None, "profile": {}},
        ).json()["id"]
        source_only = client.post(
            f"/api/v1/projects/{source_only_project}/sources",
            headers=_identity(actor_id),
            json={
                "source_kind": "human_original",
                "content_text": "旧流程只保存了原稿，还没有 Brief。",
            },
        ).json()
        recovered_source_only = client.get(
            f"/api/v1/projects/{source_only_project}/brief-intake",
            headers=_identity(actor_id),
        ).json()
        assert recovered_source_only["stage"] == "idea"
        assert recovered_source_only["current_source"]["source_record_id"] == (
            source_only["source_record_id"]
        )


def test_brief_intake_reopens_for_revision_and_adopts_a_new_version(
    intake_database: tuple[str, Engine, int, int, str],
) -> None:
    database_url, _engine, actor_id, _stranger_id, master_key = intake_database
    app = create_app(database_url)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}), TestClient(app) as client:
        project = client.post(
            "/api/v1/projects",
            headers=_identity(actor_id),
            json={"title": "简报修订回归", "description": None, "profile": {}},
        )
        project_id = project.json()["id"]
        source = client.post(
            f"/api/v1/projects/{project_id}/sources",
            headers=_identity(actor_id),
            json={
                "source_kind": "human_original",
                "content_text": "修订前的最初想法。",
            },
        ).json()
        source_id = source["source_record_id"]
        brief = client.put(
            f"/api/v1/projects/{project_id}/brief",
            headers=_identity(actor_id),
            json={
                "expected_revision": 1,
                "content": {
                    "source_record_ids": [source_id],
                    "creative_intent": "修订前的创作意图。",
                    "reasoning_proposition": "修订前的核心命题。",
                    "resolution_mode": "open",
                    "conclusion_mode": "open_interpretation",
                    "author_answer": None,
                    "author_anchors": [],
                    "boundary_text": None,
                    "creative_constraints": [],
                    "core_selling_points": ["修订前的卖点"],
                    "content_outline": ["修订前的骨架"],
                    "scope_estimate": "短篇",
                    "risk_notes": [],
                },
            },
        )
        assert brief.status_code == 200
        brief_draft_revision = brief.json()["draft_revision"]

        frozen = client.post(
            f"/api/v1/projects/{project_id}/brief/confirm",
            headers=_identity(actor_id),
            json={"expected_revision": brief_draft_revision},
        )
        assert frozen.status_code == 201
        assert frozen.json()["version_no"] == 1

        before_revision = client.get(
            f"/api/v1/projects/{project_id}/brief-intake",
            headers=_identity(actor_id),
        ).json()
        assert before_revision["stage"] == "brief_review"
        assert before_revision["revision"] == 1

        reopened = client.post(
            f"/api/v1/projects/{project_id}/brief-intake/revision",
            headers=_identity(actor_id),
        )
        assert reopened.status_code == 200
        assert reopened.json()["stage"] == "confirmation"
        assert reopened.json()["revision"] == 2

        candidate = client.post(
            f"/api/v1/projects/{project_id}/brief-intake/candidates",
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 2,
                "parent_candidate_id": None,
                "content": _manual_candidate("简报修订后的概念"),
                "activate": True,
            },
        )
        assert candidate.status_code == 201
        assert candidate.json()["stage"] == "confirmation"
        assert candidate.json()["revision"] == 3
        candidate_id = candidate.json()["current_candidate_id"]

        adopted = client.post(
            (
                f"/api/v1/projects/{project_id}/brief-intake/candidates/"
                f"{candidate_id}/adopt"
            ),
            headers=_identity(actor_id),
            json={
                "expected_intake_revision": 3,
                "expected_brief_revision": brief_draft_revision,
            },
        )
        assert adopted.status_code == 200
        assert adopted.json()["intake"]["stage"] == "brief_review"
        revised_brief = adopted.json()["brief"]
        assert revised_brief["draft_revision"] == brief_draft_revision + 1
        assert revised_brief["current_version_id"] is None
        assert revised_brief["content"]["creative_intent"] == "简报修订后的概念"

        # 补上正式审阅要求确认的原子规则，再重新冻结。
        reviewed = client.put(
            f"/api/v1/projects/{project_id}/brief",
            headers=_identity(actor_id),
            json={
                "expected_revision": revised_brief["draft_revision"],
                "content": {
                    **revised_brief["content"],
                    "creative_constraints": [
                        {
                            "constraint_id": "constraint_revision_keep",
                            "statement": "必须保留记录被人为改写这一事实。",
                            "strength": "hard",
                        }
                    ],
                },
            },
        )
        assert reviewed.status_code == 200

        # 旧版本仍留在历史中，重新冻结生成 V2 而不是覆盖 V1。
        refrozen = client.post(
            f"/api/v1/projects/{project_id}/brief/confirm",
            headers=_identity(actor_id),
            json={"expected_revision": reviewed.json()["draft_revision"]},
        )
        assert refrozen.status_code == 201
        assert refrozen.json()["version_no"] == 2
