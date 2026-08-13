"""Shared PostgreSQL fixtures and providers for application-service integration tests."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from casefile.agent_runtime import FakeProvider
from casefile.agent_runtime.credentials import generate_master_key
from casefile.agent_runtime.models import (
    CaseFileChatCandidate,
    CaseFileChatRequest,
    CaseFileChatResult,
    GenerationRequest,
    GenerationResult,
    ToolMetrics,
)
from casefile.application.commands import ProjectCreate
from casefile.application.services import CaseFileService
from casefile.application.workflow_service import WorkflowService
from casefile.contracts import ContractValidationError, validate_casefile
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROFILE: dict[str, object] = {}


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
                "anchor_id": "anchor_first_officer",
                "statement": "大副修改了航行记录。",
            },
            {
                "anchor_id": "anchor_voltage_guard",
                "statement": "欠压保护触发了回航。",
            },
        ],
        "boundary_text": "必须保持唯一因果答案。",
        "creative_constraints": [
            {
                "constraint_id": "constraint_unique_cause",
                "statement": "因果答案必须唯一。",
                "strength": "hard",
            }
        ],
    }


class RichFixtureProvider:
    """Return a deterministic, fully populated v1 fixture for editing tests."""

    def generate(self, request: GenerationRequest) -> GenerationResult:
        fixture_path = BACKEND_ROOT.parent / "fixtures" / "casefiles" / "restart_loop.casefile.json"
        candidate = json.loads(fixture_path.read_text(encoding="utf-8"))
        candidate["casefile_id"] = request.casefile_id
        candidate["version"] = {
            "version_id": request.version_id,
            "version_no": request.version_no,
            "parent_version_id": request.parent_version_id,
        }
        candidate["brief_ref"] = {
            "brief_id": request.brief_id,
            "version": request.brief_version,
        }
        for constraint in candidate["constraints"]:
            for scope_ref in constraint["scope_refs"]:
                if scope_ref["object_type"] == "casefile":
                    scope_ref["object_id"] = request.casefile_id
        validate_casefile(candidate)
        return GenerationResult(
            candidate=candidate,
            usage={"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            tools=ToolMetrics(calls=1, valid_calls=1, successful_calls=1, adopted_results=1),
        )


class ConclusionFixtureProvider(RichFixtureProvider):
    """Add one valid proposed conclusion to the editing fixture."""

    def generate(self, request: GenerationRequest) -> GenerationResult:
        result = super().generate(request)
        result.candidate["reasoning_paths"][0]["target_ref"] = {
            "object_type": "claim",
            "object_id": "claim_backup_trigger",
        }
        result.candidate["resolution_specs"][0]["conclusion"] = {
            "outcome": "answer",
            "review_status": "proposed",
            "summary": "备用系统依据安全规则主动触发了主系统重启。",
            "values": [
                {
                    "slot_id": "slot_root_cause",
                    "value": {
                        "object_type": "claim",
                        "object_id": "claim_backup_trigger",
                    },
                }
            ],
            "selected_hypothesis_refs": [
                {
                    "object_type": "hypothesis",
                    "object_id": "hyp_automatic_restart",
                }
            ],
            "supporting_reasoning_path_refs": [
                {
                    "object_type": "reasoning_path",
                    "object_id": "path_causal_restart",
                }
            ],
            "rationale": "重启日志、关键主张和自动安全重启路径形成一致依据。",
            "unresolved_gaps": [],
        }
        validate_casefile(result.candidate)
        return result


class EmptyKnowledgeStateProvider(RichFixtureProvider):
    """Add a valid knowledge-state slot that deliberately has no ObjectRefs."""

    def generate(self, request: GenerationRequest) -> GenerationResult:
        result = super().generate(request)
        result.candidate["entities"][0]["knowledge_states"].append(
            {
                "as_of_event_ref": None,
                "knows_refs": [],
                "believes_refs": [],
                "false_belief_refs": [],
            }
        )
        validate_casefile(result.candidate)
        return result


class StructuralFailureProvider(FakeProvider):
    """Fail deterministically before optionally returning a valid candidate."""

    def __init__(self, failures_before_success: int) -> None:
        self.failures_before_success = failures_before_success
        self.calls = 0
        self.feedback: list[tuple[dict[str, object], ...]] = []

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls += 1
        self.feedback.append(request.repair_feedback)
        if self.calls <= self.failures_before_success:
            raise ContractValidationError(
                [
                    {
                        "code": "schema_invalid",
                        "path": "/events/0/time",
                        "message": "'author-secret-value' is not of type 'object'",
                    }
                ]
            )
        return super().generate(request)


class ChatSuggestionProvider(FakeProvider):
    """Return deterministic, reviewable workbench suggestions."""

    def __init__(self, *, invalid_time: bool = False) -> None:
        self.invalid_time = invalid_time
        self.requests: list[CaseFileChatRequest] = []

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        self.requests.append(request)
        entity = request.casefile["entities"][0]
        event = request.casefile["events"][0]
        claim = request.casefile["claims"][0]
        suggestions = (
            [
                {
                    "object_id": event["id"],
                    "path": "/time/end",
                    "value_json": json.dumps(
                        "2042-06-01T19:59:00+08:00",
                        ensure_ascii=False,
                    ),
                    "reason": "验证结构门禁不会接受倒置时间。",
                }
            ]
            if self.invalid_time
            else [
                {
                    "object_id": entity["id"],
                    "path": "/description",
                    "value_json": json.dumps(
                        "负责追查午夜重启原因的研究员。",
                        ensure_ascii=False,
                    ),
                    "reason": "补充人物在卷宗中的职责。",
                },
                {
                    "object_id": claim["id"],
                    "path": "/support_refs",
                    "value_json": "[]",
                    "reason": "暴露关键主张缺少支撑的语义警告。",
                },
            ]
        )
        candidate = CaseFileChatCandidate.model_validate(
            {
                "answer": "我已通读完整卷宗，并整理出可逐项审阅的建议。",
                "referenced_object_ids": [entity["id"], event["id"]],
                "suggestions": suggestions,
            }
        )
        return CaseFileChatResult(
            candidate=candidate,
            usage={
                "requests": 1,
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
        )


def _test_database_url() -> str:
    value = os.getenv("CASEFILE_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CASEFILE_TEST_DATABASE_URL is not configured")
    if not (make_url(value).database or "").endswith("_test"):
        pytest.fail("CASEFILE_TEST_DATABASE_URL must point to a disposable *_test database")
    return value


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT / "src"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _clear_projects_before_downgrade(database_url: str) -> None:
    """Remove test aggregates that cannot be represented by an older migration."""

    engine = create_engine(database_url)
    try:
        if "projects" not in inspect(engine).get_table_names():
            return
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE projects CASCADE"))
    finally:
        engine.dispose()


@pytest.fixture
def workflow_database() -> Iterator[tuple[Engine, int, str]]:
    database_url = _test_database_url()
    config = _alembic_config(database_url)
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
                        text(
                            "INSERT INTO users (display_name) "
                            "VALUES ('Workflow Owner') RETURNING id"
                        )
                    ).scalar_one()
                )
            yield engine, actor_id, master_key
        finally:
            engine.dispose()
            _clear_projects_before_downgrade(database_url)
            command.downgrade(config, "base")


def _prepare_task(engine: Engine, actor_id: int) -> tuple[int, int]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        project = CaseFileService(session).create_project(
            actor_id,
            ProjectCreate(title="午夜回航", description=None, profile=PROFILE),
        )
    project_id = int(project["id"])
    with factory() as session:
        workflow = WorkflowService(session)
        empty_draft = CaseFileService(session).get_draft(actor_id, project_id)
        assert empty_draft["content"] is None
        setting = workflow.save_provider_setting(
            actor_id,
            api_key="sk-test-workflow-secret",
            model_id="gpt-5.6-sol",
            model_is_custom=False,
        )
        assert setting["masked_api_key"].endswith("cret")
        source = workflow.create_source(
            actor_id,
            project_id,
            source_kind="human_original",
            content_text="一艘渡轮每天午夜会重新驶回同一座码头。",
            parent_source_record_id=None,
        )
        updated = workflow.update_brief(
            actor_id,
            project_id,
            expected_revision=1,
            content=_brief(source["source_record_id"]),
        )
        confirmed = workflow.confirm_brief(
            actor_id,
            project_id,
            expected_revision=updated["draft_revision"],
        )
        task = workflow.create_generation_task(
            actor_id,
            project_id,
            brief_version_id=confirmed["brief_version_id"],
            expected_draft_id=empty_draft["draft_id"],
            expected_draft_revision=1,
        )
    return project_id, int(task["task_run_id"])


def _draft_revision_and_content(
    engine: Engine,
    actor_id: int,
    project_id: int,
) -> tuple[int, object]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        draft = CaseFileService(session).get_draft(actor_id, project_id)
    return int(draft["revision"]), draft["content"]


def _adopt_candidate(
    engine: Engine,
    actor_id: int,
    project_id: int,
    task_run_id: int,
    *,
    expected_current_draft_id: int | None = None,
) -> dict[str, object]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        if expected_current_draft_id is None:
            current = CaseFileService(session).get_draft(actor_id, project_id)
            expected_current_draft_id = int(current["draft_id"])
        return WorkflowService(session).adopt_generation_candidate(
            actor_id,
            project_id,
            task_run_id,
            expected_current_draft_id=expected_current_draft_id,
        )
