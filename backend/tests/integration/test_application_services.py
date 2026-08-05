"""PostgreSQL integration tests for the Brief-to-Draft application workflow."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
import rfc8785
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
from casefile.application.errors import ApplicationError
from casefile.application.services import CaseFileService
from casefile.application.snapshot import casefile_content_hash
from casefile.application.v1_editing import V1EditingService
from casefile.application.workflow_service import WorkflowService
from casefile.contracts import ContractValidationError, validate_casefile
from casefile.data_postgres.models import (
    BriefVersion,
    CaseFileObject,
    DraftOperation,
    DraftSnapshot,
    TaskAttempt,
    TaskEvent,
    TaskRun,
    UserProviderSetting,
)
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine, create_engine, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
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


@pytest.fixture
def workflow_database() -> Iterator[tuple[Engine, int, str]]:
    database_url = _test_database_url()
    config = _alembic_config(database_url)
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
                            "VALUES ('Workflow Owner') RETURNING id"
                        )
                    ).scalar_one()
                )
            yield engine, actor_id, master_key
        finally:
            engine.dispose()
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
            expected_draft_revision=1,
        )
    return project_id, int(task["task_run_id"])


def _adopt_candidate(
    engine: Engine,
    actor_id: int,
    project_id: int,
    task_run_id: int,
    *,
    expected_revision: int = 1,
) -> dict[str, object]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        return WorkflowService(session).adopt_generation_candidate(
            actor_id,
            project_id,
            task_run_id,
            expected_draft_revision=expected_revision,
        )


def test_provider_credential_deletion_blocks_active_tasks_and_erases_material(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        with patch(
            "casefile.application.workflow_service.prompt_version_for_task",
            return_value="brief-to-draft-v3",
        ):
            _project_id, task_run_id = _prepare_task(engine, actor_id)
        with factory() as session, pytest.raises(ApplicationError) as active:
            WorkflowService(session).delete_provider_setting(actor_id)
        assert active.value.code == "provider_credential_in_use"
        assert active.value.details["active_task_count"] == 1

        with factory() as session, session.begin():
            session.execute(
                update(TaskRun)
                .where(TaskRun.id == task_run_id)
                .values(
                    status="failed",
                    stage="failed",
                    completed_at=func.now(),
                    error_code="test_task_finished",
                )
            )

        with factory() as session:
            setting_before = session.scalar(
                select(UserProviderSetting).where(
                    UserProviderSetting.user_id == actor_id,
                    UserProviderSetting.provider == "openai",
                )
            )
            assert setting_before is not None
            setting_id = setting_before.id

        with factory() as session:
            WorkflowService(session).delete_provider_setting(actor_id)

        with factory() as session:
            deleted = session.get(UserProviderSetting, setting_id)
            task = session.get(TaskRun, task_run_id)
            assert deleted is not None
            assert deleted.credential_status == "deleted"
            assert deleted.credential_deleted_at is not None
            assert deleted.secret_ciphertext is None
            assert deleted.secret_nonce is None
            assert deleted.key_version is None
            assert deleted.secret_last_four is None
            assert task is not None and task.provider_setting_id == setting_id

        with factory() as session:
            assert WorkflowService(session).get_provider_setting(actor_id) is None

        with factory() as session:
            restored = WorkflowService(session).save_provider_setting(
                actor_id,
                api_key="sk-test-restored-secret",
                model_id="gpt-5.6-sol",
                model_is_custom=False,
            )
            assert restored["config_version"] == 3
            assert restored["masked_api_key"].endswith("cret")

        with factory() as session:
            restored_row = session.get(UserProviderSetting, setting_id)
            assert restored_row is not None
            assert restored_row.credential_status == "unverified"
            assert restored_row.credential_deleted_at is None
            assert restored_row.secret_ciphertext is not None


def test_brief_service_enforces_root_schema_conditions(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, _master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        project = CaseFileService(session).create_project(
            actor_id,
            ProjectCreate(title="Brief 条件门禁", description=None, profile=PROFILE),
        )
    project_id = int(project["id"])
    with factory() as session:
        source = WorkflowService(session).create_source(
            actor_id,
            project_id,
            source_kind="human_original",
            content_text="一份用于验证 Brief 条件语义的原稿。",
            parent_source_record_id=None,
        )
    source_record_id = int(source["source_record_id"])
    with factory() as session, pytest.raises(ApplicationError) as parented_original:
        WorkflowService(session).create_source(
            actor_id,
            project_id,
            source_kind="human_original",
            content_text="原稿不能伪装成另一份来源的子修订。",
            parent_source_record_id=source_record_id,
        )
    assert parented_original.value.code == "source_parent_forbidden"

    anchored_without_answer = _brief(source_record_id)
    anchored_without_answer["author_answer"] = None
    open_with_hidden_answer = _brief(source_record_id)
    open_with_hidden_answer["resolution_mode"] = "open"
    duplicate_sources = _brief(source_record_id)
    duplicate_sources["source_record_ids"] = [source_record_id, source_record_id]
    invalid_cases = (
        (anchored_without_answer, "brief_author_answer_required"),
        (open_with_hidden_answer, "brief_resolution_mode_conflict"),
        (duplicate_sources, "brief_source_record_duplicate"),
    )

    for content, expected_code in invalid_cases:
        with factory() as session, pytest.raises(ApplicationError) as invalid:
            WorkflowService(session).update_brief(
                actor_id,
                project_id,
                expected_revision=1,
                content=content,
            )
        assert invalid.value.code == expected_code

    with factory() as session:
        assert WorkflowService(session).get_brief(actor_id, project_id)["draft_revision"] == 1


def test_fake_worker_persists_candidate_then_adopts_exact_roundtrip_snapshot(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="test-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert worker.run_once() is True
        assert worker.run_once() is False

        with factory() as session:
            workflow = WorkflowService(session)
            task = workflow.get_task(actor_id, project_id, task_run_id)
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            events = workflow.list_task_events(actor_id, project_id, task_run_id)
            error_details = session.scalar(
                select(TaskRun.error_details_jsonb).where(TaskRun.id == task_run_id)
            )

        assert task["status"] == "succeeded", (task, events, error_details)
        assert task["result_snapshot_id"] is None
        assert task["usage"]["tools"]["execution_success_rate"] == 1.0
        assert draft["revision"] == 1
        assert draft["content"] is None
        assert [event["sequence_no"] for event in events] == list(
            range(1, len(events) + 1)
        )
        assert events[-1]["event_type"] == "task.succeeded"

        adopted = _adopt_candidate(
            engine,
            actor_id,
            project_id,
            task_run_id,
        )
        assert adopted["adopted"] is True
        with factory() as session:
            workflow = WorkflowService(session)
            task = workflow.get_task(actor_id, project_id, task_run_id)
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            candidates = workflow.list_generation_candidates(actor_id, project_id)
        assert task["result_snapshot_id"] is not None
        assert draft["revision"] == 2
        assert draft["content"]["title"] == "围绕午夜回航建立目标无关的推理卷宗。"
        assert candidates[0]["is_current"] is True
        assert candidates[0]["is_adopted"] is True

        with engine.connect() as connection:
            ciphertext = connection.execute(
                select(UserProviderSetting.secret_ciphertext).where(
                    UserProviderSetting.user_id == actor_id
                )
            ).scalar_one()
            snapshot_json, snapshot_hash = connection.execute(
                select(DraftSnapshot.snapshot_jsonb, DraftSnapshot.content_hash).where(
                    DraftSnapshot.id == task["result_snapshot_id"]
                )
            ).one()
        assert b"sk-test-workflow-secret" not in bytes(ciphertext)
        assert snapshot_json == draft["content"]
        assert snapshot_hash == casefile_content_hash(draft["content"])


def test_adoption_preserves_reference_free_knowledge_state_slots(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="empty-knowledge-state-worker"),
            provider_factory=lambda _task: EmptyKnowledgeStateProvider(),
        )
        assert worker.run_once() is True

        with factory() as session:
            candidate = session.scalar(
                select(TaskAttempt.candidate_jsonb).where(TaskAttempt.task_run_id == task_run_id)
            )
        assert isinstance(candidate, dict)

        _adopt_candidate(engine, actor_id, project_id, task_run_id)

        with factory() as session:
            content = CaseFileService(session).get_draft(actor_id, project_id)["content"]
        assert content == candidate
        assert content["entities"][0]["knowledge_states"][-1] == {
            "as_of_event_ref": None,
            "knows_refs": [],
            "believes_refs": [],
            "false_belief_refs": [],
        }


def test_same_brief_generates_multiple_candidates_and_explicit_adoption_replaces_draft(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, first_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="multi-candidate-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert worker.run_once() is True

        with factory() as session:
            workflow = WorkflowService(session)
            brief = workflow.get_brief(actor_id, project_id)
            second = workflow.create_generation_task(
                actor_id,
                project_id,
                brief_version_id=brief["current_version_id"],
                expected_draft_revision=1,
            )
        second_task_id = int(second["task_run_id"])
        assert worker.run_once() is True

        with factory() as session:
            workflow = WorkflowService(session)
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            candidates = workflow.list_generation_candidates(actor_id, project_id)
        assert draft["revision"] == 1
        assert draft["content"] is None
        assert [item["task_run_id"] for item in candidates] == [
            second_task_id,
            first_task_id,
        ]
        assert all(item["can_adopt"] for item in candidates)

        _adopt_candidate(
            engine,
            actor_id,
            project_id,
            second_task_id,
            expected_revision=1,
        )
        with factory() as session:
            workflow = WorkflowService(session)
            brief = workflow.get_brief(actor_id, project_id)
            third = workflow.create_generation_task(
                actor_id,
                project_id,
                brief_version_id=brief["current_version_id"],
                expected_draft_revision=2,
            )
        third_task_id = int(third["task_run_id"])
        assert worker.run_once() is True

        with factory() as session:
            unchanged = CaseFileService(session).get_draft(actor_id, project_id)
        assert unchanged["revision"] == 2
        second_resolution_id = unchanged["content"]["resolution_specs"][0]["id"]

        _adopt_candidate(
            engine,
            actor_id,
            project_id,
            third_task_id,
            expected_revision=2,
        )
        with factory() as session:
            workflow = WorkflowService(session)
            replaced = CaseFileService(session).get_draft(actor_id, project_id)
            candidates = workflow.list_generation_candidates(actor_id, project_id)
            active_count = session.scalar(
                select(func.count(CaseFileObject.id)).where(
                    CaseFileObject.project_id == project_id,
                    CaseFileObject.deleted_at.is_(None),
                )
            )
            archived_count = session.scalar(
                select(func.count(CaseFileObject.id)).where(
                    CaseFileObject.project_id == project_id,
                    CaseFileObject.deleted_at.is_not(None),
                )
            )
            operation_types = list(
                session.scalars(
                    select(DraftOperation.operation_type)
                    .where(DraftOperation.project_id == project_id)
                    .order_by(DraftOperation.sequence_no)
                )
            )
        assert replaced["revision"] == 3
        assert replaced["content"]["resolution_specs"][0]["id"] != second_resolution_id
        assert active_count and active_count > 0
        assert archived_count and archived_count > 0
        assert operation_types == [
            "agent_adopt_brief_candidate",
            "agent_adopt_brief_candidate",
        ]
        assert candidates[0]["task_run_id"] == third_task_id
        assert candidates[0]["is_current"] is True
        assert candidates[1]["task_run_id"] == second_task_id
        assert candidates[1]["is_adopted"] is True
        assert candidates[2]["task_run_id"] == first_task_id
        assert candidates[2]["can_adopt"] is True

        with engine.begin() as connection, pytest.raises(DBAPIError):
            connection.execute(
                update(TaskAttempt)
                .where(TaskAttempt.task_run_id == first_task_id)
                .values(candidate_jsonb={"tampered": True})
            )


def test_worker_repairs_structural_output_with_actionable_feedback(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    provider = StructuralFailureProvider(failures_before_success=1)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="repair-success-worker"),
            provider_factory=lambda _task: provider,
        )

        assert worker.run_once() is True

        with factory() as session:
            workflow = WorkflowService(session)
            task = workflow.get_task(actor_id, project_id, task_run_id)
            events = workflow.list_task_events(actor_id, project_id, task_run_id)
            attempt = session.scalar(
                select(TaskAttempt).where(TaskAttempt.task_run_id == task_run_id)
            )

        assert task["status"] == "succeeded"
        assert task["failure"] is None
        assert provider.calls == 2
        assert provider.feedback[0] == ()
        assert provider.feedback[1][0]["issues"][0]["path"] == "/events/0/time"
        assert attempt is not None
        assert attempt.validation_errors_jsonb == [
            {
                "repair_no": 0,
                "issues": [
                    {
                        "code": "schema_invalid",
                        "path": "/events/0/time",
                        "message": "字段类型应为 'object'",
                    }
                ],
            }
        ]
        failed_event = next(
            event for event in events if event["event_type"] == "validation.failed"
        )
        assert failed_event["payload"]["issues"][0]["message"] == "字段类型应为 'object'"
        assert any(event["event_type"] == "model.repair_started" for event in events)
        assert "author-secret-value" not in repr(events)


def test_worker_exhausts_structural_repairs_without_persisting_candidate(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    provider = StructuralFailureProvider(failures_before_success=99)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="repair-failure-worker"),
            provider_factory=lambda _task: provider,
        )

        assert worker.run_once() is True

        with factory() as session:
            workflow = WorkflowService(session)
            task = workflow.get_task(actor_id, project_id, task_run_id)
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            events = workflow.list_task_events(actor_id, project_id, task_run_id)
            attempt = session.scalar(
                select(TaskAttempt).where(TaskAttempt.task_run_id == task_run_id)
            )

        assert task["status"] == "failed"
        assert task["error_code"] == "candidate_validation_failed"
        assert task["failure"] == {
            "code": "candidate_validation_failed",
            "message": "模型输出未通过 CaseFile 结构校验，已停止写入 Draft。",
            "retryable": True,
            "issues": [
                {
                    "code": "schema_invalid",
                    "path": "/events/0/time",
                    "message": "字段类型应为 'object'",
                },
            ],
        }
        assert provider.calls == 3
        assert attempt is not None
        assert attempt.candidate_jsonb is None
        assert len(attempt.validation_errors_jsonb) == 3
        assert draft["content"] is None
        assert events[-1]["event_type"] == "task.failed"
        assert events[-1]["payload"]["failure"] == task["failure"]
        assert "author-secret-value" not in repr(task)
        assert "author-secret-value" not in repr(events)


def test_source_polish_extract_recovery_and_human_confirmation(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        project = CaseFileService(session).create_project(
            actor_id,
            ProjectCreate(title="来源闭环", description=None, profile={}),
        )
    project_id = int(project["id"])

    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        with factory() as session:
            workflow = WorkflowService(session)
            workflow.save_provider_setting(
                actor_id,
                provider="deepseek",
                api_key="sk-test-source-workflow-secret",
                model_id="deepseek-v4-flash",
                model_is_custom=False,
            )
            original = workflow.create_source(
                actor_id,
                project_id,
                source_kind="human_original",
                content_text="原稿必须完整保留；这句话不能被候选覆盖。",
                parent_source_record_id=None,
            )
            polish_task = workflow.create_polish_task(
                actor_id,
                project_id,
                source_record_id=original["source_record_id"],
                provider="deepseek",
                polish_mode="proofread",
            )

        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="brief-aux-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert worker.run_once() is True

        with factory() as session:
            workflow = WorkflowService(session)
            polished = workflow.get_task(
                actor_id,
                project_id,
                polish_task["task_run_id"],
            )
            latest_polish = workflow.get_latest_task(
                actor_id,
                project_id,
                task_type="brief_polish",
            )
            sources = workflow.list_sources(actor_id, project_id)

        assert polished["status"] == "succeeded"
        assert polished["result"]["input_hash"] == polished["input_hash"]
        assert polished["result"]["polish_mode"] == "proofread"
        assert polished["result"]["introduced_details"] == []
        proposal = polished["result"]["proposal_source_record"]
        assert proposal["source_kind"] == "agent_polish_proposal"
        assert proposal["parent_source_record_id"] == original["source_record_id"]
        assert proposal["generated_by_task_run_id"] == polish_task["task_run_id"]
        assert latest_polish is not None
        assert latest_polish["task_run_id"] == polish_task["task_run_id"]
        assert next(
            item for item in sources if item["source_record_id"] == original["source_record_id"]
        )["content_text"] == "原稿必须完整保留；这句话不能被候选覆盖。"

        unreviewed = _brief(original["source_record_id"])
        unreviewed["author_anchors"] = []
        unreviewed["creative_constraints"] = []
        with factory() as session:
            workflow = WorkflowService(session)
            saved = workflow.update_brief(
                actor_id,
                project_id,
                expected_revision=1,
                content=unreviewed,
            )
            with pytest.raises(ApplicationError) as not_reviewed:
                workflow.confirm_brief(
                    actor_id,
                    project_id,
                    expected_revision=saved["draft_revision"],
                )
        assert not_reviewed.value.code == "brief_author_anchors_required"

        with factory() as session:
            extract_task = WorkflowService(session).create_anchor_extract_task(
                actor_id,
                project_id,
                expected_brief_revision=saved["draft_revision"],
                provider="deepseek",
            )
        assert worker.run_once() is True

        with factory() as session:
            workflow = WorkflowService(session)
            extracted = workflow.get_task(
                actor_id,
                project_id,
                extract_task["task_run_id"],
            )
            assert extracted["result"]["input_hash"] == extracted["input_hash"]
            reviewed = dict(unreviewed)
            reviewed["author_anchors"] = [
                {
                    "anchor_id": f"anchor_task_{extract_task['task_run_id']}_{index:02d}",
                    "statement": item["statement"],
                }
                for index, item in enumerate(
                    extracted["result"]["author_anchors"],
                    start=1,
                )
            ]
            reviewed["creative_constraints"] = [
                {
                    "constraint_id": (
                        f"constraint_task_{extract_task['task_run_id']}_{index:02d}"
                    ),
                    "statement": item["statement"],
                    "strength": item["suggested_strength"],
                }
                for index, item in enumerate(
                    extracted["result"]["creative_constraints"],
                    start=1,
                )
            ]
            saved_reviewed = workflow.update_brief(
                actor_id,
                project_id,
                expected_revision=saved["draft_revision"],
                content=reviewed,
            )
            confirmed = workflow.confirm_brief(
                actor_id,
                project_id,
                expected_revision=saved_reviewed["draft_revision"],
            )
        assert confirmed["content"]["author_anchors"]
        assert confirmed["content"]["creative_constraints"]


def test_brief_updates_clear_current_version_and_reject_foreign_sources(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        first = CaseFileService(session).create_project(
            actor_id,
            ProjectCreate(title="第一卷", description=None, profile={}),
        )
    with factory() as session:
        second = CaseFileService(session).create_project(
            actor_id,
            ProjectCreate(title="第二卷", description=None, profile={}),
        )
    first_id = int(first["id"])
    second_id = int(second["id"])
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        with factory() as session:
            workflow = WorkflowService(session)
            workflow.save_provider_setting(
                actor_id,
                api_key="sk-test-version-secret",
                model_id="gpt-5.6-sol",
                model_is_custom=False,
            )
            first_source = workflow.create_source(
                actor_id,
                first_id,
                source_kind="human_original",
                content_text="第一卷原稿",
                parent_source_record_id=None,
            )
            foreign_source = workflow.create_source(
                actor_id,
                second_id,
                source_kind="human_original",
                content_text="第二卷原稿",
                parent_source_record_id=None,
            )
            saved = workflow.update_brief(
                actor_id,
                first_id,
                expected_revision=1,
                content=_brief(first_source["source_record_id"]),
            )
            confirmed = workflow.confirm_brief(
                actor_id,
                first_id,
                expected_revision=saved["draft_revision"],
            )

        changed = _brief(first_source["source_record_id"])
        changed["reasoning_proposition"] = "修改后的推理命题。"
        with factory() as session:
            workflow = WorkflowService(session)
            updated = workflow.update_brief(
                actor_id,
                first_id,
                expected_revision=saved["draft_revision"],
                content=changed,
            )
            assert updated["current_version_id"] is None
            with pytest.raises(ApplicationError) as stale_version:
                workflow.create_generation_task(
                    actor_id,
                    first_id,
                    brief_version_id=confirmed["brief_version_id"],
                    expected_draft_revision=1,
                )
        assert stale_version.value.code == "brief_version_not_current"

        foreign = _brief(foreign_source["source_record_id"])
        with factory() as session, pytest.raises(ApplicationError) as wrong_source:
            WorkflowService(session).update_brief(
                actor_id,
                first_id,
                expected_revision=updated["draft_revision"],
                content=foreign,
            )
        assert wrong_source.value.code == "brief_source_invalid"


def test_expired_lease_creates_a_new_attempt(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        _, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        first = Worker(factory, config=WorkerConfig(worker_id="worker-a", lease_seconds=1))
        assert first._claim_next() is not None
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE task_runs SET lease_expires_at = "
                    "CURRENT_TIMESTAMP - INTERVAL '1 second'"
                )
            )
        second = Worker(factory, config=WorkerConfig(worker_id="worker-b", lease_seconds=60))
        claimed = second._claim_next()
        assert claimed is not None
        with factory() as session:
            attempts = list(
                session.scalars(
                    select(TaskAttempt)
                    .where(TaskAttempt.task_run_id == task_run_id)
                    .order_by(TaskAttempt.attempt_no)
                )
            )
        assert [attempt.status for attempt in attempts] == ["failed", "running"]
        assert attempts[0].error_code == "worker_lease_expired"


def test_worker_rejects_rotated_provider_configuration(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        with factory() as session:
            rotated = WorkflowService(session).save_provider_setting(
                actor_id,
                api_key="sk-test-rotated-secret",
                model_id="gpt-5.6-sol",
                model_is_custom=False,
            )
        assert rotated["config_version"] == 2

        provider_called = False

        def provider_factory(_task: TaskRun) -> FakeProvider:
            nonlocal provider_called
            provider_called = True
            return FakeProvider()

        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="rotated-provider-test"),
            provider_factory=provider_factory,
        )
        assert worker.run_once() is True
        assert provider_called is False
        with factory() as session:
            task = WorkflowService(session).get_task(actor_id, project_id, task_run_id)
        assert task["status"] == "failed"
        assert task["error_code"] == "generation_failed"


def test_confirmed_brief_and_task_events_are_database_immutable(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        _, task_run_id = _prepare_task(engine, actor_id)
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text("UPDATE brief_versions SET version_no = 9"))
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text("UPDATE task_events SET stage = 'changed' WHERE task_run_id = :task_id"),
                {"task_id": task_run_id},
            )
        with engine.connect() as connection:
            assert connection.execute(select(BriefVersion.version_no)).scalar_one() == 1
            assert connection.execute(select(TaskEvent.stage)).scalar_one() == "queued"


def test_v1_editing_updates_supported_objects_and_preserves_contract(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="editing-test-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert worker.run_once() is True
        _adopt_candidate(engine, actor_id, project_id, task_run_id)

        with factory() as session:
            service = CaseFileService(session)
            initial = service.get_draft(actor_id, project_id)
            task = WorkflowService(session).get_task(actor_id, project_id, task_run_id)
            error_details = session.scalar(
                select(TaskRun.error_details_jsonb).where(TaskRun.id == task_run_id)
            )
            attempt_candidate = session.scalar(
                select(TaskAttempt.candidate_jsonb).where(TaskAttempt.task_run_id == task_run_id)
            )
            assert task["status"] == "succeeded", (
                task,
                error_details,
                attempt_candidate is not None,
            )
            assert initial["revision"] == 2
            content = initial["content"]
            entity_id = content["entities"][0]["id"]
            location_id = content["locations"][0]["id"]
            event_id = content["events"][0]["id"]
            resolution_id = content["resolution_specs"][0]["id"]

        with factory() as session:
            entity, revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                entity_id,
                expected_revision=2,
                changes={
                    "name": "Edited investigator",
                    "description": "Edited entity description",
                    "traits": ["careful", "persistent"],
                },
            )
            assert revision == 3
            assert entity["name"] == "Edited investigator"
            assert entity["description"] == "Edited entity description"
            assert entity["traits"] == ["careful", "persistent"]

        with factory() as session:
            location, revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                location_id,
                expected_revision=3,
                changes={
                    "name": "Edited laboratory",
                    "description": "Edited location description",
                },
            )
            assert revision == 4
            assert location["name"] == "Edited laboratory"
            assert location["description"] == "Edited location description"

        with factory() as session:
            event, revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                event_id,
                expected_revision=4,
                changes={
                    "title": "Edited restart event",
                    "description": "Edited event description",
                },
            )
            assert revision == 5
            assert event["title"] == "Edited restart event"
            assert event["description"] == "Edited event description"

        with factory() as session:
            resolution, revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                resolution_id,
                expected_revision=5,
                changes={
                    "title": "Edited core resolution",
                    "reasoning_question": "Which evidence establishes the restart cause?",
                },
            )
            assert revision == 6
            assert resolution["title"] == "Edited core resolution"
            assert (
                resolution["reasoning_question"]
                == "Which evidence establishes the restart cause?"
            )

        with factory() as session:
            final = CaseFileService(session).get_draft(actor_id, project_id)
            assert final["revision"] == 6
            assert final["content"]["resolution_specs"][0] == resolution
            validate_casefile(final["content"])

        with factory() as session, pytest.raises(ApplicationError) as field_read_only:
            V1EditingService(session).patch_object(
                actor_id,
                project_id,
                entity_id,
                expected_revision=6,
                changes={"id": "ent_replaced"},
            )
        assert field_read_only.value.code == "field_read_only"

        with factory() as session, pytest.raises(ApplicationError) as conflict:
            V1EditingService(session).patch_object(
                actor_id,
                project_id,
                entity_id,
                expected_revision=4,
                changes={"name": "Stale edit"},
            )
        assert conflict.value.code == "draft_revision_conflict"


def test_v1_editing_supports_all_eleven_object_collections(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="all-object-editing-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert worker.run_once() is True
        _adopt_candidate(engine, actor_id, project_id, task_run_id)

        with factory() as session:
            content = CaseFileService(session).get_draft(actor_id, project_id)["content"]
        edits = [
            ("resolution_specs", {"title": "更新后的核心结论"}),
            ("entities", {"aliases": ["林博士", "研究员"]}),
            ("relationships", {"relationship_type": "updated_relation"}),
            ("locations", {"access_rules": ["仅授权人员可进入"]}),
            ("events", {"title": "更新后的第七次重启"}),
            ("information_units", {"content": "更新后的日志内容。"}),
            ("claims", {"statement": "更新后的备用系统触发主张。"}),
            ("hypotheses", {"proposition": "更新后的原因假设。"}),
            ("reasoning_paths", {"description": "更新后的推理路径说明。"}),
            ("constraints", {"statement": "更新后的硬约束。"}),
            ("structure_locks", {"reason": "更新后的锁定原因。"}),
        ]
        revision = 2
        for collection, changes in edits:
            with factory() as session:
                _updated, next_revision = V1EditingService(session).patch_object(
                    actor_id,
                    project_id,
                    content[collection][0]["id"],
                    expected_revision=revision,
                    changes=changes,
                )
            assert next_revision == revision + 1
            revision = next_revision

        entity_refs = [
            {"object_type": "entity", "object_id": entity["id"]}
            for entity in content["entities"]
        ]
        with factory() as session:
            event, next_revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                content["events"][0]["id"],
                expected_revision=revision,
                changes={"participant_refs": entity_refs},
            )
            assert event["participant_refs"] == entity_refs
            assert next_revision == revision + 1
            revision = next_revision

        with factory() as session, pytest.raises(ApplicationError) as knowledge_state:
            V1EditingService(session).patch_object(
                actor_id,
                project_id,
                content["entities"][0]["id"],
                expected_revision=revision,
                changes={"knowledge_states": []},
            )
        assert knowledge_state.value.code == "field_read_only"

        with factory() as session:
            final = CaseFileService(session).get_draft(actor_id, project_id)
            assert final["revision"] == revision
            validate_casefile(final["content"])
            edit_operations = list(
                session.scalars(
                    select(DraftOperation).where(
                        DraftOperation.project_id == project_id,
                        DraftOperation.operation_type == "replace",
                    )
                )
            )
            assert len(edit_operations) == len(edits) + 1
            assert {
                operation.result_revision - operation.base_revision
                for operation in edit_operations
            } == {1}


def test_agent_chat_persists_reviewable_batch_and_atomic_apply_undo(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    provider = ChatSuggestionProvider()
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        generation_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="chat-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        _adopt_candidate(engine, actor_id, project_id, generation_task_id)

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(actor_id, project_id, title=None)
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                content="请通读整个卷宗并给出可以审阅的修改建议。",
            )
            chat_task_id = int(queued["task"]["task_run_id"])
            frozen_input = session.scalar(
                select(TaskRun.input_jsonb).where(TaskRun.id == chat_task_id)
            )
            assert set(frozen_input) == {"casefile", "history", "message"}
            assert frozen_input["history"] == []
            assert frozen_input["casefile"]["events"]

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="chat-suggestion-worker"),
            provider_factory=lambda _task: provider,
        )
        assert chat_worker.run_once() is True
        assert len(provider.requests) == 1
        assert provider.requests[0].message == "请通读整个卷宗并给出可以审阅的修改建议。"

        with factory() as session:
            workflow = WorkflowService(session)
            messages = workflow.list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )
            assert [message["role"] for message in messages] == ["user", "assistant"]
            assistant = messages[-1]
            assert assistant["status"] == "completed"
            assert assistant["referenced_object_ids"]
            patch_set = assistant["patch_set"]
            assert patch_set["status"] == "pending"
            assert len(patch_set["operations"]) == 2
            operation_ids = [
                operation["operation_id"] for operation in patch_set["operations"]
            ]
            applied = workflow.apply_agent_patch_set(
                actor_id,
                project_id,
                patch_set["patch_set_id"],
                expected_revision=2,
                operation_ids=operation_ids,
            )
            assert applied["draft_revision"] == 3
            assert applied["status"] == "applied"
            assert {
                issue["rule_id"] for issue in applied["validator_issues"]
            } == {"CF-W-CLAIM-001"}

        with factory() as session:
            applied_draft = CaseFileService(session).get_draft(actor_id, project_id)
            assert applied_draft["revision"] == 3
            assert (
                applied_draft["content"]["entities"][0]["description"]
                == "负责追查午夜重启原因的研究员。"
            )
            assert applied_draft["content"]["claims"][0]["support_refs"] == []
            operation_types = list(
                session.scalars(
                    select(DraftOperation.operation_type)
                    .where(
                        DraftOperation.project_id == project_id,
                        DraftOperation.operation_type.in_(
                            ("agent_patch_apply", "agent_patch_undo")
                        ),
                    )
                    .order_by(DraftOperation.sequence_no)
                )
            )
            assert operation_types == ["agent_patch_apply"]

        with factory() as session:
            undone = WorkflowService(session).undo_agent_patch_set(
                actor_id,
                project_id,
                patch_set["patch_set_id"],
                expected_revision=3,
            )
            assert undone["draft_revision"] == 4
            assert undone["status"] == "undone"

        with factory() as session:
            restored = CaseFileService(session).get_draft(actor_id, project_id)
            assert restored["revision"] == 4
            assert "description" not in restored["content"]["entities"][0]
            assert len(restored["content"]["claims"][0]["support_refs"]) == 1
            operation_types = list(
                session.scalars(
                    select(DraftOperation.operation_type)
                    .where(
                        DraftOperation.project_id == project_id,
                        DraftOperation.operation_type.in_(
                            ("agent_patch_apply", "agent_patch_undo")
                        ),
                    )
                    .order_by(DraftOperation.sequence_no)
                )
            )
            assert operation_types == ["agent_patch_apply", "agent_patch_undo"]


def test_agent_chat_marks_result_stale_after_concurrent_manual_edit(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    provider = ChatSuggestionProvider()
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        Worker(
            factory,
            config=WorkerConfig(worker_id="stale-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        _adopt_candidate(engine, actor_id, project_id, generation_task_id)

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(actor_id, project_id, title="并发编辑")
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                content="请在后台分析，我会继续编辑。",
            )
            chat_task_id = int(queued["task"]["task_run_id"])
            frozen_revision = queued["task"]["input_draft_revision"]
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            entity_id = draft["content"]["entities"][0]["id"]

        with factory() as session:
            _entity, edited_revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                entity_id,
                expected_revision=frozen_revision,
                changes={"description": "用户在 Agent 运行期间补充的说明。"},
            )
            assert edited_revision == frozen_revision + 1

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="stale-chat-worker"),
            provider_factory=lambda _task: provider,
        )
        assert chat_worker.run_once() is True

        with factory() as session:
            workflow = WorkflowService(session)
            task = workflow.get_task(actor_id, project_id, chat_task_id)
            messages = workflow.list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )
            assistant = messages[-1]
            assert task["status"] == "succeeded"
            assert assistant["content"]
            assert assistant["patch_set"]["status"] == "stale"
            with pytest.raises(ApplicationError) as stale_apply:
                workflow.apply_agent_patch_set(
                    actor_id,
                    project_id,
                    assistant["patch_set"]["patch_set_id"],
                    expected_revision=edited_revision,
                    operation_ids=None,
                )
            assert stale_apply.value.code == "agent_patch_not_pending"


def test_agent_patch_structural_failure_rolls_back_entire_batch(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    provider = ChatSuggestionProvider(invalid_time=True)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        Worker(
            factory,
            config=WorkerConfig(worker_id="invalid-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(actor_id, project_id, title=None)
            workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                content="提出一条会触发结构门禁的建议。",
            )
        Worker(
            factory,
            config=WorkerConfig(worker_id="invalid-chat-worker"),
            provider_factory=lambda _task: provider,
        ).run_once()

        with factory() as session:
            workflow = WorkflowService(session)
            patch_set = workflow.list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )[-1]["patch_set"]
            operation_id = patch_set["operations"][0]["operation_id"]
            with pytest.raises(ContractValidationError):
                workflow.apply_agent_patch_set(
                    actor_id,
                    project_id,
                    patch_set["patch_set_id"],
                    expected_revision=2,
                    operation_ids=[operation_id],
                )

        with factory() as session:
            unchanged = CaseFileService(session).get_draft(actor_id, project_id)
            assert unchanged["revision"] == 2
            assert (
                unchanged["content"]["events"][0]["time"]["end"]
                == "2042-06-01T20:03:00+08:00"
            )
            messages = WorkflowService(session).list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )
            assert messages[-1]["patch_set"]["status"] == "pending"


def test_agent_collaboration_freezes_and_reviews_atomic_patch_batches(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        generation_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="agent-collaboration-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        _adopt_candidate(engine, actor_id, project_id, generation_task_id)

        with factory() as session:
            generated_task = WorkflowService(session).get_task(
                actor_id,
                project_id,
                generation_task_id,
            )
            initial_draft = CaseFileService(session).get_draft(actor_id, project_id)
        assert generated_task["status"] == "succeeded"
        assert initial_draft["revision"] == 2

        entity = initial_draft["content"]["entities"][0]
        location = initial_draft["content"]["locations"][0]
        event = initial_draft["content"]["events"][0]
        entity_id = entity["id"]
        location_id = location["id"]
        event_id = event["id"]

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                title="核对关键对象",
            )
            sent = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                content="请逐项建议调整研究员、实验室和重启事件。",
            )
        first_chat_task_id = int(sent["task"]["task_run_id"])

        with factory() as session:
            frozen_input, input_hash, input_draft_revision = session.execute(
                select(
                    TaskRun.input_jsonb,
                    TaskRun.input_hash,
                    TaskRun.input_draft_revision,
                ).where(TaskRun.id == first_chat_task_id)
            ).one()
        assert set(frozen_input) == {"casefile", "history", "message"}
        assert frozen_input["casefile"] == initial_draft["content"]
        assert frozen_input["history"] == []
        assert frozen_input["message"] == "请逐项建议调整研究员、实验室和重启事件。"
        assert input_draft_revision == 2
        assert input_hash == hashlib.sha256(rfc8785.dumps(frozen_input)).hexdigest()

        chat_claimer = Worker(
            factory,
            config=WorkerConfig(worker_id="agent-collaboration-completion-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        first_claim = chat_claimer._claim_next()
        assert first_claim is not None
        assert first_claim[0] == first_chat_task_id

        with factory() as session:
            first_completion = WorkflowService(session).complete_chat_task(
                first_chat_task_id,
                first_claim[1],
                answer="我整理了三个互相独立、可逐项审阅的修改建议。",
                referenced_object_ids=[entity_id, location_id, entity_id],
                suggestions=[
                    {
                        "object_id": entity_id,
                        "path": "/name",
                        "value": "林首席研究员",
                        "reason": "明确人物在调查中的职责。",
                    },
                    {
                        "object_id": location_id,
                        "path": "/name",
                        "value": "中央实验室",
                        "reason": "统一地点称谓。",
                    },
                    {
                        "object_id": event_id,
                        "path": "/title",
                        "value": "不应采纳的重启标题",
                        "reason": "演示逐项拒绝。",
                    },
                ],
                usage={
                    "requests": 1,
                    "input_tokens": 120,
                    "output_tokens": 40,
                    "total_tokens": 160,
                },
            )

        first_message = first_completion["message"]
        first_patch = first_message["patch_set"]
        assert first_message["status"] == "completed"
        assert first_message["referenced_object_ids"] == [entity_id, location_id]
        assert first_patch["status"] == "pending"
        assert first_patch["base_draft_revision"] == 2
        assert [
            (operation["object_id"], operation["field_path"], operation["decision"])
            for operation in first_patch["operations"]
        ] == [
            (entity_id, "/name", "pending"),
            (location_id, "/name", "pending"),
            (event_id, "/title", "pending"),
        ]

        with factory() as session:
            second_sent = WorkflowService(session).send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                content="再给重启事件补一个候选标题。",
            )
        second_chat_task_id = int(second_sent["task"]["task_run_id"])
        with factory() as session:
            second_frozen_input = session.scalar(
                select(TaskRun.input_jsonb).where(TaskRun.id == second_chat_task_id)
            )
        assert second_frozen_input is not None
        assert second_frozen_input["history"] == [
            {
                "role": "user",
                "content": "请逐项建议调整研究员、实验室和重启事件。",
            },
            {
                "role": "assistant",
                "content": "我整理了三个互相独立、可逐项审阅的修改建议。",
            },
        ]

        second_claim = chat_claimer._claim_next()
        assert second_claim is not None
        assert second_claim[0] == second_chat_task_id
        with factory() as session:
            second_completion = WorkflowService(session).complete_chat_task(
                second_chat_task_id,
                second_claim[1],
                answer="补充了一条事件标题候选。",
                referenced_object_ids=[event_id],
                suggestions=[
                    {
                        "object_id": event_id,
                        "path": "/title",
                        "value": "系统重启与回航保护触发",
                        "reason": "让时间线标题直接表达关键事实。",
                    }
                ],
                usage={
                    "requests": 1,
                    "input_tokens": 80,
                    "output_tokens": 20,
                    "total_tokens": 100,
                },
            )
        second_patch = second_completion["message"]["patch_set"]
        assert second_patch["status"] == "pending"
        assert second_patch["base_draft_revision"] == 2

        first_patch_id = int(first_patch["patch_set_id"])
        selected_operation_ids = [
            int(operation["operation_id"])
            for operation in first_patch["operations"][:2]
        ]
        with factory() as session:
            rejected = WorkflowService(session).apply_agent_patch_set(
                actor_id,
                project_id,
                int(second_patch["patch_set_id"]),
                expected_revision=2,
                operation_ids=[],
            )
        assert rejected["status"] == "rejected"
        assert rejected["draft_revision"] == 2
        assert [operation["decision"] for operation in rejected["operations"]] == [
            "rejected"
        ]
        with factory() as session:
            unchanged = CaseFileService(session).get_draft(actor_id, project_id)
        assert unchanged["revision"] == 2

        with factory() as session:
            applied = WorkflowService(session).apply_agent_patch_set(
                actor_id,
                project_id,
                first_patch_id,
                expected_revision=2,
                operation_ids=selected_operation_ids,
            )
        assert applied["draft_revision"] == 3
        assert applied["status"] == "applied"
        assert [operation["decision"] for operation in applied["operations"]] == [
            "accepted",
            "accepted",
            "rejected",
        ]

        with factory() as session:
            applied_draft = CaseFileService(session).get_draft(actor_id, project_id)
            apply_operations = list(
                session.scalars(
                    select(DraftOperation).where(
                        DraftOperation.operation_type == "agent_patch_apply"
                    )
                )
            )
        assert applied_draft["revision"] == 3
        assert next(
            item for item in applied_draft["content"]["entities"] if item["id"] == entity_id
        )["name"] == "林首席研究员"
        assert next(
            item
            for item in applied_draft["content"]["locations"]
            if item["id"] == location_id
        )["name"] == "中央实验室"
        assert next(
            item for item in applied_draft["content"]["events"] if item["id"] == event_id
        )["title"] == event["title"]
        assert len(apply_operations) == 1
        assert (
            apply_operations[0].base_revision,
            apply_operations[0].result_revision,
        ) == (2, 3)

        with factory() as session:
            undone = WorkflowService(session).undo_agent_patch_set(
                actor_id,
                project_id,
                first_patch_id,
                expected_revision=3,
            )
        assert undone["draft_revision"] == 4
        assert undone["status"] == "undone"

        with factory() as session:
            undone_draft = CaseFileService(session).get_draft(actor_id, project_id)
            undo_operations = list(
                session.scalars(
                    select(DraftOperation).where(
                        DraftOperation.operation_type == "agent_patch_undo"
                    )
                )
            )
        assert undone_draft["revision"] == 4
        assert next(
            item for item in undone_draft["content"]["entities"] if item["id"] == entity_id
        )["name"] == entity["name"]
        assert next(
            item
            for item in undone_draft["content"]["locations"]
            if item["id"] == location_id
        )["name"] == location["name"]
        assert len(undo_operations) == 1
        assert (
            undo_operations[0].base_revision,
            undo_operations[0].result_revision,
        ) == (3, 4)

        with factory() as session, pytest.raises(ApplicationError) as rejected_patch:
            WorkflowService(session).apply_agent_patch_set(
                actor_id,
                project_id,
                int(second_patch["patch_set_id"]),
                expected_revision=4,
                operation_ids=None,
            )
        assert rejected_patch.value.code == "agent_patch_not_pending"

        with factory() as session:
            final_draft = CaseFileService(session).get_draft(actor_id, project_id)
            messages = WorkflowService(session).list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )
        assert final_draft["revision"] == 4
        second_assistant = next(
            message
            for message in messages
            if message["task"] is not None
            and message["task"]["task_run_id"] == second_chat_task_id
        )
        assert second_assistant["patch_set"]["status"] == "rejected"
        assert second_assistant["patch_set"]["is_stale"] is False
