"""Real-PostgreSQL API acceptance test for the Agent generation golden path."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from application_services_test_support import _clear_projects_before_downgrade
from casefile.agent_runtime import FakeProvider
from casefile.agent_runtime.brief_to_draft_v8.workflow import run_v8_generation
from casefile.agent_runtime.brief_to_draft_v11.workflow import run_v11_generation
from casefile.agent_runtime.credentials import generate_master_key
from casefile.agent_runtime.models import (
    CaseFileChatCandidate,
    CaseFileChatRequest,
    CaseFileChatResult,
    GenerationRequest,
    GenerationResult,
)
from casefile.agent_runtime.providers import _add_fake_v10_matrix_plan, _fake_v8_output
from casefile.api.app import create_app
from casefile.contracts import ContractValidationError
from casefile.data_postgres.models import TaskAttempt
from casefile.worker.runtime import Worker, WorkerConfig
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROFILE: dict[str, object] = {}


class ApiChatProvider(FakeProvider):
    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        assert request.prompt_version == "casefile-chat-v1"
        resolution = request.casefile["resolution_specs"][0]
        return CaseFileChatResult(
            candidate=CaseFileChatCandidate.model_validate(
                {
                    "answer": "我已阅读完整卷宗，并提出一条可审阅说明。",
                    "referenced_object_ids": [resolution["id"]],
                    "suggestions": [
                        {
                            "object_id": resolution["id"],
                            "path": "/description",
                            "value_json": json.dumps(
                                "用于解释午夜回航原因的核心命题。",
                                ensure_ascii=False,
                            ),
                            "reason": "让结论规格更容易被作者理解。",
                        }
                    ],
                }
            ),
            usage={
                "requests": 1,
                "input_tokens": 5,
                "output_tokens": 5,
                "total_tokens": 10,
            },
        )


class RecoverableV8Provider(FakeProvider):
    def __init__(self) -> None:
        self.fail_evidence = True

    def generate(self, request: GenerationRequest) -> GenerationResult:
        async def call_component(
            _instructions: str,
            _input_text: str,
            output_type: type[BaseModel],
            stage: str,
            component_id: str,
            schema_id: str,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            request.emit(
                "agent.model_call.started",
                stage,
                {
                    "component_id": component_id,
                    "schema_id": schema_id,
                    "attempt_no": 1,
                    "protocol": "fake_strict",
                },
            )
            if component_id == "evidence_logic" and self.fail_evidence:
                issue = {
                    "code": "missing",
                    "path": "/claims/0/statement",
                    "message": "缺少必填字段。",
                }
                request.emit(
                    "agent.model_call.failed",
                    stage,
                    {
                        "component_id": component_id,
                        "schema_id": schema_id,
                        "attempt_no": 1,
                        "protocol": "fake_strict",
                        "failure_layer": "pydantic",
                        "issues": [issue],
                    },
                )
                raise ContractValidationError([issue])
            output = _fake_v8_output(output_type)
            if request.prompt_version in {"brief-to-draft-v10", "brief-to-draft-v11"}:
                _add_fake_v10_matrix_plan(output_type, output)
            if component_id == "resolution_governance":
                output["resolution_specs"][0]["conclusion_mode"] = request.brief["conclusion_mode"]
            request.emit(
                "agent.model_call.completed",
                stage,
                {
                    "component_id": component_id,
                    "schema_id": schema_id,
                    "attempt_no": 1,
                    "protocol": "fake_strict",
                    "usage": {},
                },
            )
            return output, {}

        runner = (
            run_v11_generation
            if request.prompt_version == "brief-to-draft-v11"
            else run_v8_generation
        )
        return asyncio.run(runner(request, call_component=call_component))


def _brief(source_record_id: int) -> dict[str, object]:
    return {
        "source_record_ids": [source_record_id],
        "creative_intent": "围绕午夜回航建立目标无关的推理卷宗。",
        "reasoning_proposition": "是谁修改了航行记录，回航保护机制因何触发？",
        "resolution_mode": "author_anchored",
        "conclusion_mode": "unique",
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
        _clear_projects_before_downgrade(database_url)
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
            _clear_projects_before_downgrade(database_url)
            command.downgrade(config, "base")


def _identity(actor_id: int) -> dict[str, str]:
    return {"X-CaseFile-User-Id": str(actor_id)}


def test_provider_setting_delete_roundtrip(
    api_database: tuple[str, Engine, int, str],
) -> None:
    database_url, _engine, actor_id, master_key = api_database
    app = create_app(database_url)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}), TestClient(app) as client:
        saved = client.put(
            "/api/v1/settings/provider",
            headers=_identity(actor_id),
            json={
                "provider": "deepseek",
                "api_key": "sk-deepseek-api-secret",
                "model_id": "deepseek-v4-flash",
                "model_is_custom": False,
            },
        )
        assert saved.status_code == 200
        assert saved.json()["masked_api_key"].endswith("cret")

        deleted = client.delete(
            "/api/v1/settings/provider?provider=deepseek",
            headers=_identity(actor_id),
        )
        assert deleted.status_code == 204
        assert deleted.content == b""
        assert (
            client.get(
                "/api/v1/settings/provider?provider=deepseek",
                headers=_identity(actor_id),
            ).json()
            is None
        )

        restored = client.put(
            "/api/v1/settings/provider",
            headers=_identity(actor_id),
            json={
                "provider": "deepseek",
                "api_key": "sk-deepseek-api-restored",
                "model_id": "deepseek-v4-flash",
                "model_is_custom": False,
            },
        )
        assert restored.status_code == 200
        assert restored.json()["config_version"] == 3
        assert restored.json()["masked_api_key"].endswith("ored")


def test_settings_brief_generation_sse_and_completion_gate(
    api_database: tuple[str, Engine, int, str],
) -> None:
    database_url, engine, actor_id, master_key = api_database
    app = create_app(database_url)
    with (
        patch.dict(
            os.environ,
            {
                "CASEFILE_MASTER_KEY": master_key,
            },
        ),
        TestClient(app) as client,
    ):
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
        removed_draft_routes = (
            ("POST", f"/api/v1/projects/{project_id}/draft/entities"),
            ("GET", f"/api/v1/projects/{project_id}/draft/entities"),
            ("GET", f"/api/v1/projects/{project_id}/draft/entities/entity_removed"),
            ("PUT", f"/api/v1/projects/{project_id}/draft/entities/entity_removed"),
            ("DELETE", f"/api/v1/projects/{project_id}/draft/entities/entity_removed"),
            (
                "PUT",
                f"/api/v1/projects/{project_id}/draft/entities/entity_removed/adjacent-locations",
            ),
            ("POST", f"/api/v1/projects/{project_id}/draft/events"),
            ("GET", f"/api/v1/projects/{project_id}/draft/events"),
            ("GET", f"/api/v1/projects/{project_id}/draft/events/event_removed"),
            ("PUT", f"/api/v1/projects/{project_id}/draft/events/event_removed"),
            ("DELETE", f"/api/v1/projects/{project_id}/draft/events/event_removed"),
            ("PUT", f"/api/v1/projects/{project_id}/draft/events/event_removed/actors"),
        )
        for method, path in removed_draft_routes:
            response = client.request(
                method,
                path,
                headers=_identity(actor_id),
                json={} if method in {"POST", "PUT"} else None,
            )
            assert response.status_code == 404, (method, path, response.text)

        empty_draft = client.get(
            f"/api/v1/projects/{project_id}/draft",
            headers=_identity(actor_id),
        )
        assert empty_draft.status_code == 200
        assert empty_draft.json()["content"] is None
        empty_exposure_plan = client.get(
            f"/api/v1/projects/{project_id}/draft/exposure-plan",
            headers=_identity(actor_id),
        )
        assert empty_exposure_plan.status_code == 200
        assert empty_exposure_plan.json()["draft_id"] == empty_draft.json()["draft_id"]
        assert empty_exposure_plan.json()["revision"] == 0
        assert empty_exposure_plan.json()["entries"] == []

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
                "polish_mode": "rewrite",
            },
        )
        assert polish_queued.status_code == 202
        assert worker.run_once() is True
        latest_polish = client.get(
            f"/api/v1/projects/{project_id}/tasks/latest?task_type=brief_polish",
            headers=_identity(actor_id),
        )
        assert latest_polish.status_code == 200
        assert latest_polish.json()["result"]["polish_mode"] == "rewrite"
        assert latest_polish.json()["result"]["introduced_details"] == []
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
            (f"/api/v1/projects/{project_id}/tasks/{extract_queued.json()['task_run_id']}"),
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

        invalid_strategy = client.post(
            f"/api/v1/projects/{project_id}/tasks/generate",
            headers=_identity(actor_id),
            json={
                "brief_version_id": confirmed.json()["brief_version_id"],
                "expected_draft_id": empty_draft.json()["draft_id"],
                "expected_draft_revision": 1,
                "provider": "deepseek",
                "candidate_strategy": "not_a_strategy",
            },
        )
        assert invalid_strategy.status_code == 422

        queued = client.post(
            f"/api/v1/projects/{project_id}/tasks/generate",
            headers=_identity(actor_id),
            json={
                "brief_version_id": confirmed.json()["brief_version_id"],
                "expected_draft_id": empty_draft.json()["draft_id"],
                "expected_draft_revision": 1,
                "provider": "deepseek",
                "candidate_strategy": "structure_first",
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
        with factory() as diagnostic_session:
            diagnostic_attempt = diagnostic_session.scalar(
                select(TaskAttempt).where(TaskAttempt.task_run_id == task_id)
            )
        assert task.status_code == 200
        assert task.json()["status"] == "succeeded", json.dumps(
            {
                "task": task.json(),
                "attempt_error": (
                    diagnostic_attempt.error_details_jsonb if diagnostic_attempt else None
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        assert task.json()["failure"] is None
        assert task.json()["candidate_strategy"] == "structure_first"
        assert [step["component_id"] for step in task.json()["component_steps"]] == [
            "context_pack_builder",
            "case_blueprint_planner",
            "temporal_structure_planner",
            "story_world",
            "evidence_logic",
            "evidence_matrix",
            "resolution_governance",
            "reference_linker",
            "casefile_compiler",
            "quality_repair_gate",
        ]
        assert task.json()["result_snapshot_id"] is None
        assert task.json()["result"]["title"]
        assert task.json()["result"]["candidate_strategy"] == "structure_first"
        assert task.json()["result"]["candidate_strategy_version"] == "candidate-strategy-v1"

        recoverable_provider = RecoverableV8Provider()
        recovery_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="api-recovery-worker"),
            provider_factory=lambda _task: recoverable_provider,
        )
        with patch(
            "casefile.application.workflow_service.prompt_version_for_task",
            return_value="brief-to-draft-v11",
        ):
            recoverable_queued = client.post(
                f"/api/v1/projects/{project_id}/tasks/generate",
                headers=_identity(actor_id),
                json={
                    "brief_version_id": confirmed.json()["brief_version_id"],
                    "expected_draft_id": empty_draft.json()["draft_id"],
                    "expected_draft_revision": 1,
                    "provider": "deepseek",
                    "candidate_strategy": "atmosphere_first",
                },
            )
        assert recoverable_queued.status_code == 202
        recoverable_task_id = recoverable_queued.json()["task_run_id"]
        assert recovery_worker.run_once() is True
        failed_task = client.get(
            f"/api/v1/projects/{project_id}/tasks/{recoverable_task_id}",
            headers=_identity(actor_id),
        ).json()
        assert failed_task["status"] == "failed"
        assert any(
            step["component_id"] == "evidence_logic"
            and step["status"] == "failed"
            and step["failure_layer"] == "structured_output"
            for step in failed_task["component_steps"]
        )

        recoverable_provider.fail_evidence = False
        resumed = client.post(
            f"/api/v1/projects/{project_id}/tasks/{recoverable_task_id}/resume",
            headers=_identity(actor_id),
            json={
                "expected_draft_id": empty_draft.json()["draft_id"],
                "expected_draft_revision": 1,
                "expected_brief_revision": updated.json()["draft_revision"],
            },
        )
        assert resumed.status_code == 202
        assert resumed.json()["attempt_count"] == 1
        assert recovery_worker.run_once() is True
        recovered_task = client.get(
            f"/api/v1/projects/{project_id}/tasks/{recoverable_task_id}",
            headers=_identity(actor_id),
        ).json()
        assert recovered_task["status"] == "succeeded"
        assert recovered_task["attempt_count"] == 2
        assert recovered_task["prompt_version"] == "brief-to-draft-v11"
        second_attempt_steps = [
            step for step in recovered_task["component_steps"] if step["attempt_no"] == 2
        ]
        assert {
            step["component_id"] for step in second_attempt_steps if step["status"] == "reused"
        } >= {"case_blueprint_planner", "story_world", "resolution_governance"}
        assert any(
            step["component_id"] == "evidence_logic" and step["status"] == "succeeded"
            for step in second_attempt_steps
        )

        stale_provider = RecoverableV8Provider()
        stale_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="api-stale-worker"),
            provider_factory=lambda _task: stale_provider,
        )
        stale_queued = client.post(
            f"/api/v1/projects/{project_id}/tasks/generate",
            headers=_identity(actor_id),
            json={
                "brief_version_id": confirmed.json()["brief_version_id"],
                "expected_draft_id": empty_draft.json()["draft_id"],
                "expected_draft_revision": 1,
                "provider": "deepseek",
                "candidate_strategy": "reasoning_first",
            },
        )
        assert stale_queued.status_code == 202
        stale_task_id = stale_queued.json()["task_run_id"]
        assert stale_worker.run_once() is True

        candidates = client.get(
            f"/api/v1/projects/{project_id}/draft-candidates",
            headers=_identity(actor_id),
        )
        assert candidates.status_code == 200
        assert {candidate["task_run_id"] for candidate in candidates.json()} == {
            task_id,
            recoverable_task_id,
        }
        structure_candidate = next(
            candidate
            for candidate in candidates.json()
            if candidate["candidate_strategy"] == "structure_first"
        )
        assert structure_candidate["can_adopt"] is True
        assert structure_candidate["candidate_strategy_label"] == "结构优先"
        empty_draft = client.get(
            f"/api/v1/projects/{project_id}/draft",
            headers=_identity(actor_id),
        )
        assert empty_draft.json()["content"] is None
        preview = client.get(
            f"/api/v1/projects/{project_id}/draft-candidates/{task_id}",
            headers=_identity(actor_id),
        )
        assert preview.status_code == 200
        assert preview.json()["task_run_id"] == task_id
        assert preview.json()["preview"] is True
        assert preview.json()["read_only"] is True
        assert preview.json()["content"]["title"] == structure_candidate["title"]
        assert preview.json()["content_hash"] == structure_candidate["content_hash"]
        foreign_actor_id = actor_id + 1
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, display_name, status) "
                    "VALUES (:id, 'Foreign preview actor', 'active')"
                ),
                {"id": foreign_actor_id},
            )
        foreign_preview = client.get(
            f"/api/v1/projects/{project_id}/draft-candidates/{task_id}",
            headers=_identity(foreign_actor_id),
        )
        assert foreign_preview.status_code == 404
        assert foreign_preview.json()["code"] == "not_found"
        draft_after_preview = client.get(
            f"/api/v1/projects/{project_id}/draft",
            headers=_identity(actor_id),
        )
        assert draft_after_preview.json() == empty_draft.json()
        adopted = client.post(
            f"/api/v1/projects/{project_id}/draft-candidates/{task_id}/adopt",
            headers=_identity(actor_id),
            json={"expected_current_draft_id": empty_draft.json()["draft_id"]},
        )
        assert adopted.status_code == 200
        assert adopted.json()["adopted"] is True
        adopted_task = client.get(
            f"/api/v1/projects/{project_id}/tasks/{task_id}",
            headers=_identity(actor_id),
        )
        assert adopted_task.json()["result_snapshot_id"] is not None
        stale_resume = client.post(
            f"/api/v1/projects/{project_id}/tasks/{stale_task_id}/resume",
            headers=_identity(actor_id),
            json={
                "expected_draft_id": empty_draft.json()["draft_id"],
                "expected_draft_revision": 2,
                "expected_brief_revision": updated.json()["draft_revision"],
            },
        )
        assert stale_resume.status_code == 409
        assert stale_resume.json()["code"] == "task_resume_draft_stale"

        current_brief = client.get(
            f"/api/v1/projects/{project_id}/brief",
            headers=_identity(actor_id),
        ).json()
        revised_content = _brief(source["source_record_id"])
        revised_content["creative_intent"] = "用新的创作方向验证旧候选只读边界。"
        revised_brief = client.put(
            f"/api/v1/projects/{project_id}/brief",
            headers=_identity(actor_id),
            json={
                "expected_revision": current_brief["draft_revision"],
                "content": revised_content,
            },
        )
        assert revised_brief.status_code == 200
        reconfirmed = client.post(
            f"/api/v1/projects/{project_id}/brief/confirm",
            headers=_identity(actor_id),
            json={"expected_revision": revised_brief.json()["draft_revision"]},
        )
        assert reconfirmed.status_code == 201
        candidate_history = client.get(
            f"/api/v1/projects/{project_id}/draft-candidates",
            headers=_identity(actor_id),
        ).json()
        old_current = next(
            candidate for candidate in candidate_history if candidate["task_run_id"] == task_id
        )
        assert old_current["is_current"] is True
        assert old_current["is_current_brief"] is False
        stale_candidate = next(
            candidate
            for candidate in candidate_history
            if candidate["task_run_id"] == recoverable_task_id
        )
        assert stale_candidate["can_adopt"] is False
        stale_adoption = client.post(
            (f"/api/v1/projects/{project_id}/draft-candidates/{recoverable_task_id}/adopt"),
            headers=_identity(actor_id),
            json={"expected_current_draft_id": empty_draft.json()["draft_id"]},
        )
        assert stale_adoption.status_code == 409
        assert stale_adoption.json()["code"] == "candidate_brief_stale"

        stream = client.get(
            f"/api/v1/projects/{project_id}/tasks/{task_id}/stream",
            headers={**_identity(actor_id), "Last-Event-ID": "1"},
        )
        assert stream.status_code == 200
        assert "id: 1\n" not in stream.text
        assert "event: task.succeeded" in stream.text
        assert "event: agent.step.completed" in stream.text
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
        assert draft.json()["content"]["schema_version"] == "2.0"

        event_id = draft.json()["content"]["events"][0]["id"]
        time_preview = client.post(
            f"/api/v1/projects/{project_id}/draft/events/{event_id}/time-preview",
            headers=_identity(actor_id),
            json={
                "expected_draft_id": adopted.json()["draft_id"],
                "expected_revision": 2,
                "proposed_time": {
                    "kind": "exact",
                    "value": "2042-06-01T20:15",
                    "precision": "minute",
                },
            },
        )
        assert time_preview.status_code == 200
        assert time_preview.json()["can_confirm"] is True
        assert time_preview.json()["draft_id"] == adopted.json()["draft_id"]
        assert time_preview.json()["base_revision"] == 2
        assert (
            client.get(f"/api/v1/projects/{project_id}/draft", headers=_identity(actor_id)).json()[
                "revision"
            ]
            == 2
        )

        invalid_time_preview = client.post(
            f"/api/v1/projects/{project_id}/draft/events/{event_id}/time-preview",
            headers=_identity(actor_id),
            json={
                "expected_draft_id": adopted.json()["draft_id"],
                "expected_revision": 2,
                "proposed_time": {
                    "kind": "range",
                    "start": "2042-06-01T20:20",
                    "end": "2042-06-01T20:10",
                    "precision": "minute",
                },
            },
        )
        assert invalid_time_preview.status_code == 200
        assert invalid_time_preview.json()["can_confirm"] is False
        assert invalid_time_preview.json()["validation"]["issues"][0]["code"] == (
            "invalid_time_range"
        )

        factual_events = draft.json()["content"]["events"]
        exposure_entries = [
            {
                "entry_key": f"exposure_{event['id']}",
                "title": event["title"],
                "note": None,
                "refs": [{"object_type": "event", "object_id": event["id"]}],
            }
            for event in reversed(factual_events)
        ]
        exposure_plan = client.put(
            f"/api/v1/projects/{project_id}/draft/exposure-plan",
            headers=_identity(actor_id),
            json={
                "expected_draft_id": adopted.json()["draft_id"],
                "expected_revision": 0,
                "entries": exposure_entries,
            },
        )
        assert exposure_plan.status_code == 200, exposure_plan.text
        assert exposure_plan.headers["X-CaseFile-Exposure-Plan-Revision"] == "1"
        assert exposure_plan.json()["revision"] == 1
        assert [item["refs"][0]["object_id"] for item in exposure_plan.json()["entries"]] == [
            event["id"] for event in reversed(factual_events)
        ]
        current_exposure_plan = client.get(
            f"/api/v1/projects/{project_id}/draft/exposure-plan",
            headers=_identity(actor_id),
        )
        assert current_exposure_plan.json() == exposure_plan.json()
        unchanged_draft = client.get(
            f"/api/v1/projects/{project_id}/draft",
            headers=_identity(actor_id),
        ).json()
        assert unchanged_draft["revision"] == 2
        assert [event["time"] for event in unchanged_draft["content"]["events"]] == [
            event["time"] for event in factual_events
        ]
        stale_exposure_plan = client.put(
            f"/api/v1/projects/{project_id}/draft/exposure-plan",
            headers=_identity(actor_id),
            json={
                "expected_draft_id": adopted.json()["draft_id"],
                "expected_revision": 0,
                "entries": exposure_entries,
            },
        )
        assert stale_exposure_plan.status_code == 409
        assert stale_exposure_plan.json()["code"] == "exposure_plan_revision_conflict"
        with engine.connect() as connection:
            counts = {
                table_name: connection.scalar(
                    text(f"SELECT count(*) FROM {table_name} WHERE project_id = :project_id"),
                    {"project_id": project_id},
                )
                for table_name in (
                    "exposure_plans",
                    "exposure_plan_revisions",
                    "exposure_plan_entries",
                    "exposure_plan_entry_refs",
                )
            }
            assert counts == {
                "exposure_plans": 1,
                "exposure_plan_revisions": 1,
                "exposure_plan_entries": len(exposure_entries),
                "exposure_plan_entry_refs": len(exposure_entries),
            }

        workbench_context = client.get(
            f"/api/v1/projects/{project_id}/workbench-context",
            headers=_identity(actor_id),
        )
        assert workbench_context.status_code == 200
        context = workbench_context.json()
        assert context["draft_revision"] == 2
        assert context["validation"] == {
            "status": "passed",
            "validator": "casefile.contracts.validate_casefile",
            "schema_version": "2.0",
            "issue_count": 0,
            "issues": [],
            "reason": None,
        }
        assert context["sources"][0]["trace_id"] == (f"source_records:{source['source_record_id']}")
        assert context["sources"][0]["content_text"] == ("一艘渡轮每天午夜会重新驶回同一座码头。")
        adoption_fact = next(
            entry
            for entry in context["audit_entries"]
            if entry["action"] == "agent_adopt_brief_candidate"
        )
        assert adoption_fact["source_table"] == "draft_operations"
        assert adoption_fact["details"]["result_revision"] == 2
        exposure_fact = next(
            entry
            for entry in context["audit_entries"]
            if entry["action"] == "exposure_plan.revised"
        )
        assert exposure_fact["source_table"] == "audit_events"
        assert exposure_fact["details"]["revision"] == 1

        thread_response = client.post(
            f"/api/v1/projects/{project_id}/agent/threads",
            headers=_identity(actor_id),
            json={
                "expected_draft_id": adopted.json()["draft_id"],
                "expected_draft_revision": 2,
            },
        )
        assert thread_response.status_code == 201
        thread_id = thread_response.json()["thread_id"]
        queued_chat = client.post(
            f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages",
            headers=_identity(actor_id),
            json={
                "expected_draft_id": adopted.json()["draft_id"],
                "expected_draft_revision": 2,
                "content": "请通读整个卷宗并给出一条可审阅建议。",
                "provider": "deepseek",
            },
        )
        assert queued_chat.status_code == 202
        chat_task_id = queued_chat.json()["task"]["task_run_id"]
        with engine.connect() as connection:
            stored_prompt_version = connection.scalar(
                text("SELECT prompt_version FROM task_runs WHERE id = :task_run_id"),
                {"task_run_id": chat_task_id},
            )
        assert stored_prompt_version == "casefile-chat-v1"
        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="api-chat-worker"),
            provider_factory=lambda _task: ApiChatProvider(),
        )
        assert chat_worker.run_once() is True

        message_response = client.get(
            f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages",
            headers=_identity(actor_id),
        )
        assert message_response.status_code == 200
        messages = message_response.json()
        assert [message["role"] for message in messages] == ["user", "assistant"]
        assert messages[-1]["task"]["task_run_id"] == chat_task_id
        patch_set = messages[-1]["patch_set"]
        assert patch_set["status"] == "pending"
        assert patch_set["operations"][0]["object_type"] == "resolution_spec"
        assert patch_set["operations"][0]["field_path"] == "/description"

        applied = client.post(
            (f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set['patch_set_id']}/apply"),
            headers=_identity(actor_id),
            json={
                "expected_draft_id": adopted.json()["draft_id"],
                "expected_revision": 2,
                "operation_ids": None,
            },
        )
        assert applied.status_code == 200
        assert applied.json()["status"] == "applied"
        assert applied.json()["draft_revision"] == 3

        undone = client.post(
            (f"/api/v1/projects/{project_id}/agent/patch-sets/{patch_set['patch_set_id']}/undo"),
            headers=_identity(actor_id),
            json={
                "expected_draft_id": adopted.json()["draft_id"],
                "expected_revision": 3,
            },
        )
        assert undone.status_code == 200
        assert undone.json()["status"] == "undone"
        assert undone.json()["draft_revision"] == 4

        metrics = client.get(
            f"/api/v1/projects/{project_id}/a-path-metrics",
            headers=_identity(actor_id),
        )
        assert metrics.status_code == 200
        metrics_body = metrics.json()
        assert metrics_body["version"] == "a-path-funnel-v1"
        assert metrics_body["funnel"]["task_runs"] >= 3
        assert metrics_body["funnel"]["generated_candidates"] >= 2
        assert metrics_body["funnel"]["adopted_candidates"] == 1
        assert metrics_body["funnel"]["post_adoption_edited_candidates"] == 1
        assert metrics_body["post_adoption"]["adoption_operations"] == 1
        assert metrics_body["post_adoption"]["edit_operations"] >= 2
        assert metrics_body["usage_observations"]["task_attempts"] >= 3
        foreign_metrics = client.get(
            f"/api/v1/projects/{project_id}/a-path-metrics",
            headers=_identity(foreign_actor_id),
        )
        assert foreign_metrics.status_code == 404
        assert foreign_metrics.json()["code"] == "not_found"

        rejected_chat = client.post(
            f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages",
            headers=_identity(actor_id),
            json={
                "expected_draft_id": adopted.json()["draft_id"],
                "expected_draft_revision": 4,
                "content": "再给一条建议，这次我会整批不采用。",
            },
        )
        assert rejected_chat.status_code == 202
        assert chat_worker.run_once() is True
        latest_messages = client.get(
            f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages",
            headers=_identity(actor_id),
        ).json()
        rejected_patch = latest_messages[-1]["patch_set"]
        rejected = client.post(
            (
                f"/api/v1/projects/{project_id}/agent/patch-sets/"
                f"{rejected_patch['patch_set_id']}/apply"
            ),
            headers=_identity(actor_id),
            json={
                "expected_draft_id": adopted.json()["draft_id"],
                "expected_revision": 4,
                "operation_ids": [],
            },
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["draft_revision"] == 4

        updated_thread = client.patch(
            f"/api/v1/projects/{project_id}/agent/threads/{thread_id}",
            headers=_identity(actor_id),
            json={
                "expected_draft_id": adopted.json()["draft_id"],
                "expected_draft_revision": 4,
                "title": "午夜回航审阅",
                "is_pinned": True,
                "archived": True,
            },
        )
        assert updated_thread.status_code == 200
        assert updated_thread.json()["status"] == "archived"
        assert updated_thread.json()["is_pinned"] is True
        archived_send = client.post(
            f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages",
            headers=_identity(actor_id),
            json={
                "expected_draft_id": adopted.json()["draft_id"],
                "expected_draft_revision": 4,
                "content": "归档线程不能继续发送。",
            },
        )
        assert archived_send.status_code == 409
