"""PostgreSQL integration tests for the Brief-to-Draft application workflow."""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace as dataclasses_replace
from unittest.mock import patch

import pytest
import rfc8785
from application_services_test_support import (
    PROFILE,
    ChatSuggestionProvider,
    ConclusionFixtureProvider,
    EmptyKnowledgeStateProvider,
    RichFixtureProvider,
    StructuralFailureProvider,
    _adopt_candidate,
    _brief,
    _prepare_task,
)
from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from casefile.agent_runtime import FakeProvider
from casefile.agent_runtime.chat_intent import INTENT_ROUTER_VERSION
from casefile.agent_runtime.chat_routing import routing_policy
from casefile.agent_runtime.models import (
    ChatTaskUnderstanding,
    agent_state_to_jsonable,
)
from casefile.application.casefile_v1 import build_casefile_document
from casefile.application.commands import ProjectCreate
from casefile.application.errors import ApplicationError
from casefile.application.services import CaseFileService
from casefile.application.snapshot import casefile_content_hash
from casefile.application.v1_editing import V1EditingService
from casefile.application.workbench_read_model import WorkbenchReadModel
from casefile.application.workflow_service import WorkflowService
from casefile.benchmark.feedback_export import export_feedback_fixtures
from casefile.contracts import ContractValidationError, validate_casefile
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentPatchSet,
    AgentStepRun,
    AuditEvent,
    CaseFileContractRef,
    CaseFileObject,
    DraftOperation,
    DraftSnapshot,
    Location,
    TaskAttempt,
    TaskRun,
    UserProviderSetting,
)
from casefile.data_postgres.repositories import ProjectRepository
from casefile.worker.runtime import Worker, WorkerConfig

pytestmark = pytest.mark.postgres


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
            terminal = workflow.cancel_task(actor_id, project_id, task_run_id)
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            events = workflow.list_task_events(actor_id, project_id, task_run_id)
            error_details = session.scalar(
                select(TaskRun.error_details_jsonb).where(TaskRun.id == task_run_id)
            )

        assert task["status"] == "succeeded", (task, events, error_details)
        assert terminal["status"] == "succeeded"
        assert all(not event["event_type"].startswith("task.cancel") for event in events)
        assert task["result_snapshot_id"] is None
        assert task["usage"]["tools"]["execution_success_rate"] == 1.0
        assert draft["revision"] == 1
        assert draft["content"] is None
        assert [event["sequence_no"] for event in events] == list(range(1, len(events) + 1))
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
        assert draft["content"]["title"] == task["result"]["title"]
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


def test_v11_worker_candidate_adopts_into_workbench_ready_current_draft(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        with patch(
            "casefile.application.workflow_service.prompt_version_for_task",
            return_value="brief-to-draft-v11",
        ):
            project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="v11-workbench-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )

        assert worker.run_once() is True
        with factory() as session:
            workflow = WorkflowService(session)
            task = workflow.get_task(actor_id, project_id, task_run_id)
            draft_before = CaseFileService(session).get_draft(actor_id, project_id)

        assert task["status"] == "succeeded"
        assert task["prompt_version"] == "brief-to-draft-v11"
        assert task["result_snapshot_id"] is None
        assert draft_before["content"] is None
        assert {step["component_id"] for step in task["component_steps"]} == {
            "context_pack_builder",
            "case_blueprint_planner",
            "story_world",
            "evidence_logic",
            "resolution_governance",
            "reference_linker",
            "casefile_compiler",
            "quality_repair_gate",
        }

        _adopt_candidate(engine, actor_id, project_id, task_run_id)

        with factory() as session:
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            context = WorkbenchReadModel(session).get_context(actor_id, project_id)
        content = draft["content"]
        assert content["events"][0]["time"]["kind"] == "exact"
        assert content["locations"][0]["spatial_position"]["coordinate_system"] == ("schematic")
        assert len(content["hypotheses"]) == 2
        assert all(item["evidence_assessments"] for item in content["hypotheses"])
        assert context["draft_id"] == draft["draft_id"]
        assert context["draft_revision"] == 2
        assert context["validation"]["status"] == "passed"


def test_v12_worker_persists_temporal_step_for_workbench_ready_candidate(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        with patch(
            "casefile.application.workflow_service.prompt_version_for_task",
            return_value="brief-to-draft-v12",
        ):
            project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="v12-temporal-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )

        assert worker.run_once() is True
        with factory() as session:
            task = WorkflowService(session).get_task(actor_id, project_id, task_run_id)
            temporal_step = session.scalar(
                select(AgentStepRun).where(
                    AgentStepRun.task_run_id == task_run_id,
                    AgentStepRun.component_id == "temporal_structure_planner",
                )
            )

        assert task["status"] == "succeeded"
        assert task["prompt_version"] == "brief-to-draft-v12"
        assert temporal_step is not None
        assert temporal_step.status == "succeeded"
        assert temporal_step.ir_schema_id == "temporal-plan-v1"
        assert temporal_step.output_jsonb["assignments"][0]["time"]["kind"] == "exact"
        with factory() as session:
            candidate = session.scalar(
                select(TaskAttempt.candidate_jsonb).where(TaskAttempt.task_run_id == task_run_id)
            )
        assert isinstance(candidate, dict)
        assert candidate["events"][0]["time"]["kind"] == "exact"


def test_adoption_roundtrips_evidence_assessment_reference_metadata(
    workflow_database: tuple[Engine, int, str],
) -> None:
    class AssessedFixtureProvider(RichFixtureProvider):
        def generate(self, request):  # type: ignore[no-untyped-def]
            result = super().generate(request)
            result.candidate["hypotheses"][0]["evidence_assessments"] = [
                {
                    "information_ref": {
                        "object_type": "information_unit",
                        "object_id": "info_restart_log",
                    },
                    "effect": "supports",
                    "strength": "strong",
                    "rationale": "重启日志直接记录了触发条件。",
                }
            ]
            validate_casefile(result.candidate)
            return result

    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="assessed-fixture-worker"),
            provider_factory=lambda _task: AssessedFixtureProvider(),
        )
        assert worker.run_once() is True

        with factory() as session:
            candidate = session.scalar(
                select(TaskAttempt.candidate_jsonb).where(TaskAttempt.task_run_id == task_run_id)
            )
        assert isinstance(candidate, dict)
        assert candidate["hypotheses"][0]["evidence_assessments"]

        _adopt_candidate(engine, actor_id, project_id, task_run_id)

        with factory() as session:
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            ref = session.scalar(
                select(CaseFileContractRef).where(
                    CaseFileContractRef.field_path == "/evidence_assessments/0/information_ref"
                )
            )
            snapshot = session.scalar(
                select(DraftSnapshot).where(DraftSnapshot.draft_id == draft["draft_id"])
            )

        assert ref is not None
        assert ref.object_type == "information_unit"
        assert ref.object_id == "info_restart_log"
        assert ref.metadata_jsonb == {
            "effect": "supports",
            "strength": "strong",
            "rationale": "重启日志直接记录了触发条件。",
        }
        assert draft["content"] == candidate
        assert snapshot is not None
        assert snapshot.snapshot_jsonb == candidate
        assert snapshot.content_hash == casefile_content_hash(candidate)


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


def test_same_brief_candidate_strategy_can_regenerate_beyond_two_attempts(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, _first_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="regeneration-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert worker.run_once() is True

        generated: list[int] = []
        for attempt in (1, 2, 3):
            with factory() as session:
                workflow = WorkflowService(session)
                brief = workflow.get_brief(actor_id, project_id)
                draft = CaseFileService(session).get_draft(actor_id, project_id)
                task = workflow.create_generation_task(
                    actor_id,
                    project_id,
                    brief_version_id=brief["current_version_id"],
                    expected_draft_id=draft["draft_id"],
                    expected_draft_revision=1,
                    candidate_strategy="reasoning_first",
                    candidate_strategy_attempt=attempt,
                )
            generated.append(int(task["task_run_id"]))
            assert worker.run_once() is True

        with factory() as session:
            candidates = WorkflowService(session).list_generation_candidates(
                actor_id,
                project_id,
            )
        reasoning = [
            candidate
            for candidate in candidates
            if candidate["candidate_strategy"] == "reasoning_first"
        ]
        assert [int(item["task_run_id"]) for item in reasoning] == generated[::-1]
        assert [item["candidate_strategy_attempt"] for item in reasoning] == [3, 2, 1]


def test_same_brief_candidates_create_independent_switchable_drafts(
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
            source_draft = CaseFileService(session).get_draft(actor_id, project_id)
            second = workflow.create_generation_task(
                actor_id,
                project_id,
                brief_version_id=brief["current_version_id"],
                expected_draft_id=source_draft["draft_id"],
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

        adopted_a = _adopt_candidate(
            engine,
            actor_id,
            project_id,
            second_task_id,
        )
        draft_a_id = int(adopted_a["draft_id"])
        with factory() as session:
            workflow = WorkflowService(session)
            brief = workflow.get_brief(actor_id, project_id)
            current_draft = CaseFileService(session).get_draft(actor_id, project_id)
            third = workflow.create_generation_task(
                actor_id,
                project_id,
                brief_version_id=brief["current_version_id"],
                expected_draft_id=current_draft["draft_id"],
                expected_draft_revision=2,
            )
        third_task_id = int(third["task_run_id"])
        assert worker.run_once() is True

        with factory() as session:
            unchanged = CaseFileService(session).get_draft(actor_id, project_id)
        assert unchanged["revision"] == 2
        second_resolution_id = unchanged["content"]["resolution_specs"][0]["id"]

        adopted_b = _adopt_candidate(
            engine,
            actor_id,
            project_id,
            third_task_id,
        )
        draft_b_id = int(adopted_b["draft_id"])
        with factory() as session:
            workflow = WorkflowService(session)
            current_b = CaseFileService(session).get_draft(actor_id, project_id)
            drafts = CaseFileService(session).list_drafts(actor_id, project_id)
            candidates = workflow.list_generation_candidates(actor_id, project_id)
            active_counts = dict(
                session.execute(
                    select(CaseFileObject.draft_id, func.count(CaseFileObject.id))
                    .where(
                        CaseFileObject.project_id == project_id,
                        CaseFileObject.deleted_at.is_(None),
                    )
                    .group_by(CaseFileObject.draft_id)
                ).all()
            )
            archived_count = session.scalar(
                select(func.count(CaseFileObject.id)).where(
                    CaseFileObject.project_id == project_id,
                    CaseFileObject.deleted_at.is_not(None),
                )
            )
            operations = list(
                session.execute(
                    select(DraftOperation.draft_id, DraftOperation.operation_type)
                    .where(DraftOperation.project_id == project_id)
                    .order_by(DraftOperation.draft_id)
                )
            )
        assert draft_b_id != draft_a_id
        assert current_b["draft_id"] == draft_b_id
        assert current_b["revision"] == 2
        assert current_b["content"]["resolution_specs"][0]["id"] != second_resolution_id
        assert [item["draft_id"] for item in drafts] == [draft_b_id, draft_a_id]
        assert drafts[0]["is_current"] is True
        assert all(item["has_content"] for item in drafts)
        assert active_counts[draft_a_id] > 0
        assert active_counts[draft_b_id] > 0
        assert archived_count == 0
        assert operations == [
            (draft_a_id, "agent_adopt_brief_candidate"),
            (draft_b_id, "agent_adopt_brief_candidate"),
        ]
        assert candidates[0]["task_run_id"] == third_task_id
        assert candidates[0]["is_current"] is True
        assert candidates[1]["task_run_id"] == second_task_id
        assert candidates[1]["is_adopted"] is True
        assert candidates[2]["task_run_id"] == first_task_id
        assert candidates[2]["can_adopt"] is False

        with factory() as session:
            restored_a = CaseFileService(session).activate_draft(
                actor_id,
                project_id,
                draft_a_id,
                expected_current_draft_id=draft_b_id,
            )
        assert restored_a["draft_id"] == draft_a_id
        assert restored_a["revision"] == 2
        assert restored_a["content"] == unchanged["content"]

        with factory() as session:
            switched_candidates = WorkflowService(session).list_generation_candidates(
                actor_id,
                project_id,
            )
        assert switched_candidates[0]["is_current"] is False
        assert switched_candidates[1]["is_current"] is True

        with pytest.raises(ApplicationError) as changed_source:
            _adopt_candidate(
                engine,
                actor_id,
                project_id,
                first_task_id,
                expected_current_draft_id=draft_a_id,
            )
        assert changed_source.value.code == "candidate_source_draft_changed"

        with engine.begin() as connection, pytest.raises(DBAPIError):
            connection.execute(
                update(TaskAttempt)
                .where(TaskAttempt.task_run_id == first_task_id)
                .values(candidate_jsonb={"tampered": True})
            )


def test_historical_worker_repairs_structural_output_with_actionable_feedback(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    provider = StructuralFailureProvider(failures_before_success=1)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        with patch(
            "casefile.application.workflow_service.prompt_version_for_task",
            return_value="brief-to-draft-v6",
        ):
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
        failed_event = next(event for event in events if event["event_type"] == "validation.failed")
        assert failed_event["payload"]["issues"][0]["message"] == "字段类型应为 'object'"
        assert any(event["event_type"] == "model.repair_started" for event in events)
        assert "author-secret-value" not in repr(events)


def test_historical_worker_exhausts_structural_repairs_without_persisting_candidate(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    provider = StructuralFailureProvider(failures_before_success=99)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        with patch(
            "casefile.application.workflow_service.prompt_version_for_task",
            return_value="brief-to-draft-v6",
        ):
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
            task_row = session.get(TaskRun, task_run_id)
            assert task_row is not None
            expected_calls = (
                int(task_row.budget_jsonb["structural_repair_attempts"]) + 1
            )

        assert task["status"] == "failed"
        assert task["error_code"] == "candidate_validation_failed"
        assert task["failure"] == {
            "code": "candidate_validation_failed",
            "message": "模型输出未通过 CaseFile 结构校验，已停止写入草稿。",
            "retryable": True,
            "issues": [
                {
                    "code": "schema_invalid",
                    "path": "/events/0/time",
                    "message": "字段类型应为 'object'",
                },
            ],
        }
        assert provider.calls == expected_calls
        assert attempt is not None
        assert attempt.candidate_jsonb is None
        assert len(attempt.validation_errors_jsonb) == expected_calls
        assert draft["content"] is None
        assert events[-1]["event_type"] == "task.failed"
        assert events[-1]["payload"]["failure"] == task["failure"]
        assert "author-secret-value" not in repr(task)
        assert "author-secret-value" not in repr(events)


def test_v7_worker_does_not_retry_a_whole_invalid_casefile(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    provider = StructuralFailureProvider(failures_before_success=1)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        with patch(
            "casefile.application.workflow_service.prompt_version_for_task",
            return_value="brief-to-draft-v7",
        ):
            project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="v7-no-whole-repair-worker"),
            provider_factory=lambda _task: provider,
        )

        assert worker.run_once() is True

        with factory() as session:
            task = WorkflowService(session).get_task(actor_id, project_id, task_run_id)
            attempt = session.scalar(
                select(TaskAttempt).where(TaskAttempt.task_run_id == task_run_id)
            )

    assert task["status"] == "failed"
    assert task["error_code"] == "candidate_validation_failed"
    assert provider.calls == 1
    assert attempt is not None
    assert len(attempt.validation_errors_jsonb) == 1


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
        assert (
            next(
                item for item in sources if item["source_record_id"] == original["source_record_id"]
            )["content_text"]
            == "原稿必须完整保留；这句话不能被候选覆盖。"
        )

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
                    "constraint_id": (f"constraint_task_{extract_task['task_run_id']}_{index:02d}"),
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

        # 重复冻结同一份草稿必须幂等返回同一版本，不能静默递增版本号。
        with factory() as session:
            workflow = WorkflowService(session)
            reconfirmed = workflow.confirm_brief(
                actor_id,
                project_id,
                expected_revision=saved_reviewed["draft_revision"],
            )
        assert reconfirmed["brief_version_id"] == confirmed["brief_version_id"]
        assert reconfirmed["version_no"] == confirmed["version_no"]


def test_strategy_options_reuse_frozen_brief_unless_refresh_is_explicit(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        with factory() as session:
            project = CaseFileService(session).create_project(
                actor_id,
                ProjectCreate(title="策略缓存验证", description=None, profile={}),
            )
        project_id = int(project["id"])
        with factory() as session:
            workflow = WorkflowService(session)
            workflow.save_provider_setting(
                actor_id,
                api_key="sk-test-strategy-secret",
                model_id="gpt-5.6-sol",
                model_is_custom=False,
            )
            source = workflow.create_source(
                actor_id,
                project_id,
                source_kind="human_original",
                content_text="三份记录共同指向不存在的时间。",
                parent_source_record_id=None,
            )
            saved = workflow.update_brief(
                actor_id,
                project_id,
                expected_revision=1,
                content=_brief(source["source_record_id"]),
            )
            confirmed = workflow.confirm_brief(
                actor_id,
                project_id,
                expected_revision=saved["draft_revision"],
            )
            first = workflow.create_strategy_options_task(
                actor_id,
                project_id,
                brief_version_id=confirmed["brief_version_id"],
            )
            reused = workflow.create_strategy_options_task(
                actor_id,
                project_id,
                brief_version_id=confirmed["brief_version_id"],
            )
            refreshed = workflow.create_strategy_options_task(
                actor_id,
                project_id,
                brief_version_id=confirmed["brief_version_id"],
                refresh=True,
            )

        assert reused["task_run_id"] == first["task_run_id"]
        assert refreshed["task_run_id"] != first["task_run_id"]

        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="strategy-options-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert worker.run_once() is True

        with factory() as session:
            completed = WorkflowService(session).get_task(
                actor_id,
                project_id,
                first["task_run_id"],
            )

    assert completed["status"] == "succeeded"
    assert len(completed["result"]["options"]) == 3
    assert completed["result"]["recommended_strategy"] == "reasoning_first"


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
                    expected_draft_id=first["current_draft_id"],
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
        adopted = _adopt_candidate(engine, actor_id, project_id, task_run_id)
        draft_id = int(adopted["draft_id"])

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
            assert content["locations"][0]["spatial_position"] == {
                "coordinate_system": "schematic",
                "x": 28,
                "y": 42,
            }
            stored_spatial_position = session.scalar(
                select(Location.geo_jsonb)
                .join(CaseFileObject, Location.object_registry_id == CaseFileObject.id)
                .where(CaseFileObject.object_id == location_id)
            )
            assert stored_spatial_position == content["locations"][0]["spatial_position"]

        with factory() as session:
            entity, revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                entity_id,
                expected_draft_id=draft_id,
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
                expected_draft_id=draft_id,
                expected_revision=3,
                changes={
                    "name": "Edited laboratory",
                    "description": "Edited location description",
                    "spatial_position": {
                        "coordinate_system": "wgs84",
                        "latitude": 30.2741,
                        "longitude": 120.1551,
                    },
                },
            )
            assert revision == 4
            assert location["name"] == "Edited laboratory"
            assert location["description"] == "Edited location description"
            assert location["spatial_position"] == {
                "coordinate_system": "wgs84",
                "latitude": 30.2741,
                "longitude": 120.1551,
            }
            assert (
                session.scalar(
                    select(Location.geo_jsonb)
                    .join(CaseFileObject, Location.object_registry_id == CaseFileObject.id)
                    .where(CaseFileObject.object_id == location_id)
                )
                == location["spatial_position"]
            )

        with factory() as session:
            event, revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                event_id,
                expected_draft_id=draft_id,
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
                expected_draft_id=draft_id,
                expected_revision=5,
                changes={
                    "title": "Edited core resolution",
                    "reasoning_question": "Which evidence establishes the restart cause?",
                },
            )
            assert revision == 6
            assert resolution["title"] == "Edited core resolution"
            assert (
                resolution["reasoning_question"] == "Which evidence establishes the restart cause?"
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
                expected_draft_id=draft_id,
                expected_revision=6,
                changes={"id": "ent_replaced"},
            )
        assert field_read_only.value.code == "field_read_only"

        with factory() as session, pytest.raises(ApplicationError) as conflict:
            V1EditingService(session).patch_object(
                actor_id,
                project_id,
                entity_id,
                expected_draft_id=draft_id,
                expected_revision=4,
                changes={"name": "Stale edit"},
            )
        assert conflict.value.code == "draft_revision_conflict"

        with factory() as session:
            before_location = next(
                item
                for item in CaseFileService(session).get_draft(actor_id, project_id)["content"][
                    "locations"
                ]
                if item["id"] == location_id
            )

        saved_position = {
            "coordinate_system": "schematic",
            "x": 55,
            "y": 61,
        }


        with factory() as session:
            saved_location, revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                location_id,
                expected_draft_id=draft_id,
                expected_revision=6,
                changes={"spatial_position": saved_position},
            )
            assert revision == 7
            assert saved_location["spatial_position"] == saved_position
            assert saved_location["revision"] == before_location["revision"] + 1
            assert {
                key: value
                for key, value in saved_location.items()
                if key not in {"spatial_position", "revision", "updated_at"}
            } == {
                key: value
                for key, value in before_location.items()
                if key not in {"spatial_position", "revision", "updated_at"}
            }

        with factory() as session:
            final = CaseFileService(session).get_draft(actor_id, project_id)
            position_operations = list(
                session.scalars(
                    select(DraftOperation).where(
                        DraftOperation.draft_id == draft_id,
                        DraftOperation.result_revision == 7,
                    )
                )
            )
            assert final["revision"] == 7
            assert len(position_operations) == 1
            assert position_operations[0].field_path == f"/locations/{location_id}"
            assert position_operations[0].old_value_jsonb == before_location
            assert position_operations[0].new_value_jsonb == saved_location


def test_resolution_conclusion_confirm_withdraw_invalidation_and_agent_audit(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="conclusion-editing-test-worker"),
            provider_factory=lambda _task: ConclusionFixtureProvider(),
        )
        assert worker.run_once() is True
        adopted = _adopt_candidate(engine, actor_id, project_id, task_run_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            content = CaseFileService(session).get_draft(actor_id, project_id)["content"]
            assert content is not None
            resolution = content["resolution_specs"][0]
            conclusion = resolution["conclusion"]
            resolution_id = resolution["id"]
            information_id = content["information_units"][0]["id"]
            assert conclusion["review_status"] == "proposed"
            assert conclusion["values"][0]["value"] == {
                "object_type": "claim",
                "object_id": "claim_backup_trigger",
            }

        with factory() as session:
            confirmed, revision = V1EditingService(session).confirm_conclusion(
                actor_id,
                project_id,
                resolution_id,
                expected_draft_id=draft_id,
                expected_revision=2,
            )
            assert revision == 3
            assert confirmed["conclusion"]["review_status"] == "confirmed"

        with factory() as session, pytest.raises(ApplicationError) as repeated_confirm:
            V1EditingService(session).confirm_conclusion(
                actor_id,
                project_id,
                resolution_id,
                expected_draft_id=draft_id,
                expected_revision=3,
            )
        assert repeated_confirm.value.code == "conclusion_transition_invalid"

        with factory() as session:
            invalidated, revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                resolution_id,
                expected_draft_id=draft_id,
                expected_revision=3,
                changes={
                    "required_slots": [
                        *confirmed["required_slots"],
                        {
                            "slot_id": "slot_explanation_note",
                            "value_type": "text",
                            "required": False,
                        },
                    ]
                },
            )
            assert revision == 4
            assert invalidated["conclusion"]["review_status"] == "proposed"

        with factory() as session:
            _confirmed, revision = V1EditingService(session).confirm_conclusion(
                actor_id,
                project_id,
                resolution_id,
                expected_draft_id=draft_id,
                expected_revision=4,
            )
            assert revision == 5

        with factory() as session:
            information = next(
                item
                for item in CaseFileService(session).get_draft(actor_id, project_id)["content"][
                    "information_units"
                ]
                if item["id"] == information_id
            )
            _updated, revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                information_id,
                expected_draft_id=draft_id,
                expected_revision=5,
                changes={"content": information["content"] + "（已复核原始日志。）"},
            )
            assert revision == 7

        with factory() as session:
            current = CaseFileService(session).get_draft(actor_id, project_id)
            current_resolution = current["content"]["resolution_specs"][0]
            assert current_resolution["conclusion"]["review_status"] == "proposed"
            _confirmed, revision = V1EditingService(session).confirm_conclusion(
                actor_id,
                project_id,
                resolution_id,
                expected_draft_id=draft_id,
                expected_revision=7,
            )
            assert revision == 8

        with factory() as session:
            withdrawn, revision = V1EditingService(session).withdraw_conclusion(
                actor_id,
                project_id,
                resolution_id,
                expected_draft_id=draft_id,
                expected_revision=8,
            )
            assert revision == 9
            assert withdrawn["conclusion"]["review_status"] == "proposed"

        with factory() as session, session.begin():
            owned = ProjectRepository(session).get_owned(actor_id, project_id, lock=True)
            assert owned is not None
            current_document = build_casefile_document(session, owned)
            current_resolution = current_document["resolution_specs"][0]
            proposed_by_agent = {
                **current_resolution["conclusion"],
                "review_status": "confirmed",
                "summary": "Agent 修订后的结论建议。",
            }
            revision, _group_no, applied = V1EditingService(session).apply_operation_batch(
                owned,
                operations=[
                    {
                        "operation_id": 901,
                        "object_id": resolution_id,
                        "field_path": "/conclusion",
                        "old_value": current_resolution["conclusion"],
                        "new_value": proposed_by_agent,
                    }
                ],
                actor_user_id=actor_id,
                operation_type="agent_patch_apply",
                patch_set_id=901,
            )
            assert revision == 10
            assert applied[0]["new_value"]["review_status"] == "proposed"

        with factory() as session:
            final = CaseFileService(session).get_draft(actor_id, project_id)
            assert final["content"]["resolution_specs"][0]["conclusion"] == {
                **proposed_by_agent,
                "review_status": "proposed",
            }
            batch_operation = session.scalar(
                select(DraftOperation).where(
                    DraftOperation.operation_type == "agent_patch_apply",
                    DraftOperation.result_revision == 10,
                )
            )
            assert batch_operation is not None
            assert batch_operation.new_value_jsonb["operations"][0]["value"][
                "review_status"
            ] == "proposed"
            audit_actions = list(
                session.scalars(
                    select(AuditEvent.action)
                    .where(
                        AuditEvent.project_id == project_id,
                        AuditEvent.action.like("resolution.conclusion_%"),
                    )
                    .order_by(AuditEvent.id)
                )
            )
            assert audit_actions == [
                "resolution.conclusion_confirmed",
                "resolution.conclusion_invalidated",
                "resolution.conclusion_confirmed",
                "resolution.conclusion_invalidated",
                "resolution.conclusion_confirmed",
                "resolution.conclusion_withdrawn",
            ]


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
        adopted = _adopt_candidate(engine, actor_id, project_id, task_run_id)
        draft_id = int(adopted["draft_id"])

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
                    expected_draft_id=draft_id,
                    expected_revision=revision,
                    changes=changes,
                )
            assert next_revision == revision + 1
            revision = next_revision

        entity_refs = [
            {"object_type": "entity", "object_id": entity["id"]} for entity in content["entities"]
        ]
        with factory() as session:
            event, next_revision = V1EditingService(session).patch_object(
                actor_id,
                project_id,
                content["events"][0]["id"],
                expected_draft_id=draft_id,
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
                expected_draft_id=draft_id,
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
                operation.result_revision - operation.base_revision for operation in edit_operations
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
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="请通读整个卷宗并给出可以审阅的修改建议。",
            )
            chat_task_id = int(queued["task"]["task_run_id"])
            frozen_input = session.scalar(
                select(TaskRun.input_jsonb).where(TaskRun.id == chat_task_id)
            )
            assert set(frozen_input) == {
                "casefile",
                "history",
                "message",
                "focus",
                "validation",
                "context_policy_version",
                "routing_hint",
                "router_version",
                "context_state",
            }
            assert frozen_input["history"] == []
            assert frozen_input["casefile"]["events"]
            assert frozen_input["focus"]["object_ids"] == []
            assert frozen_input["context_policy_version"] == "casefile-chat-context-v6"
            assert frozen_input["routing_hint"] == {
                "entrypoint": "free_text",
                "preset_id": None,
            }
            assert frozen_input["router_version"] == "casefile-chat-router-v2"

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="chat-suggestion-worker"),
            provider_factory=lambda _task: provider,
        )
        assert chat_worker.run_once() is True
        assert len(provider.requests) == 1
        assert provider.requests[0].message == "请通读整个卷宗并给出可以审阅的修改建议。"
        routed_request = provider.requests[0]
        assert routed_request.route is not None
        assert routed_request.route.route_source == "llm"
        assert routed_request.route.execution_profile["prompt_component"] == "edit"
        assert routed_request.prompt_version == "casefile-chat-v12"
        assert routed_request.toolset_version == "casefile-chat-tools-v4"
        assert routed_request.context_policy_version == "casefile-chat-context-v6"
        assert routed_request.task_understanding is not None
        assert routed_request.task_understanding.primary_intent == "edit_request"

        with factory() as session:
            intent_step = session.scalar(
                select(AgentStepRun).where(
                    AgentStepRun.task_run_id == chat_task_id,
                    AgentStepRun.component_id == "intent_router",
                )
            )
            assert intent_step is not None
            assert intent_step.status == "succeeded"
            assert intent_step.ir_schema_id == "chat-task-understanding-v1"
            intent_call = session.scalar(
                select(AgentModelCall).where(
                    AgentModelCall.agent_step_run_id == intent_step.id,
                    AgentModelCall.prompt_component_id == "intent_router",
                )
            )
            assert intent_call is not None
            assert intent_call.target_schema_id == "chat-task-understanding-v1"

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
            operation_ids = [operation["operation_id"] for operation in patch_set["operations"]]
            applied = workflow.apply_agent_patch_set(
                actor_id,
                project_id,
                patch_set["patch_set_id"],
                expected_draft_id=draft_id,
                expected_revision=2,
                operation_ids=operation_ids,
            )
            assert applied["draft_revision"] == 3
            assert applied["status"] == "applied"
            assert {issue["rule_id"] for issue in applied["validator_issues"]} == {"CF-W-CLAIM-001"}

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
                expected_draft_id=draft_id,
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


def test_agent_chat_preset_hint_freezes_routes_and_suppresses_suggestions(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    provider = ChatSuggestionProvider()
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        Worker(
            factory,
            config=WorkerConfig(worker_id="routing-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="对整个卷宗做一次体检，并说明时间线与推理的收束情况。",
                routing_hint={"entrypoint": "preset", "preset_id": "inspect"},
            )
            chat_task_id = int(queued["task"]["task_run_id"])
            frozen_input, input_hash = session.execute(
                select(TaskRun.input_jsonb, TaskRun.input_hash).where(
                    TaskRun.id == chat_task_id
                )
            ).one()

        assert set(frozen_input) == {
            "casefile",
            "history",
            "message",
            "focus",
            "validation",
            "context_policy_version",
            "routing_hint",
            "router_version",
            "context_state",
        }
        assert frozen_input["routing_hint"] == {
            "entrypoint": "preset",
            "preset_id": "inspect",
        }
        assert frozen_input["router_version"] == INTENT_ROUTER_VERSION
        assert input_hash == hashlib.sha256(rfc8785.dumps(frozen_input)).hexdigest()

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="routing-preset-chat-worker"),
            provider_factory=lambda _task: provider,
        )
        assert chat_worker.run_once() is True
        assert len(provider.requests) == 1
        routed_request = provider.requests[0]
        assert routed_request.task_understanding is not None
        assert routed_request.task_understanding.primary_intent == "analysis"
        assert routed_request.route is not None
        assert routed_request.route.route_source == "rule_preset"
        assert routed_request.route.routes[0]["profile"] == "analysis.healthcheck"
        assert routed_request.rewrite is not None
        assert routed_request.rewrite.rewrite_decision == "CONTEXTUALIZE"

        with factory() as session:
            workflow = WorkflowService(session)
            events = workflow.list_task_events(actor_id, project_id, chat_task_id)
            routing_event_types = [
                event["event_type"]
                for event in events
                if event["event_type"]
                in {
                    "intent.understood",
                    "route.decided",
                    "query.rewritten",
                    "route.suggestions_suppressed",
                    "route.outcome",
                }
            ]
            assert routing_event_types == [
                "intent.understood",
                "route.decided",
                "query.rewritten",
                "route.suggestions_suppressed",
                "route.outcome",
            ]
            suppressed = next(
                event
                for event in events
                if event["event_type"] == "route.suggestions_suppressed"
            )
            assert suppressed["payload"]["route_source"] == "rule_preset"
            assert suppressed["payload"]["suggestion_policy"] == "deny"
            assert suppressed["payload"]["suppressed_count"] == 2
            outcome = next(
                event for event in events if event["event_type"] == "route.outcome"
            )
            assert outcome["payload"]["succeeded"] is True
            assert outcome["payload"]["route_hash"] == routed_request.route.route_hash
            assert outcome["payload"]["tool_metrics"]["calls"] == 0

            task = workflow.get_task(actor_id, project_id, chat_task_id)
            assert task["result"]["routing"]["suppressed_count"] == 2
            assert task["result"]["routing"]["suggestion_policy"] == "deny"
            assert task["result"]["tool_metrics"]["calls"] == 0

            messages = workflow.list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )
            assistant = messages[-1]
            assert assistant["status"] == "completed"
            assert assistant["patch_set"] is None
            patch_sets = list(
                session.scalars(
                    select(AgentPatchSet).where(
                        AgentPatchSet.task_run_id == chat_task_id
                    )
                )
            )
            assert patch_sets == []

        with factory() as session:
            workflow = WorkflowService(session)
            feedback = workflow.submit_agent_routing_feedback(
                actor_id,
                project_id,
                thread["thread_id"],
                int(assistant["message_id"]),
                correct_intent="question",
            )
            assert feedback["acknowledged"] is True
            with pytest.raises(ApplicationError, match="已经提交过路由反馈"):
                workflow.submit_agent_routing_feedback(
                    actor_id,
                    project_id,
                    thread["thread_id"],
                    int(assistant["message_id"]),
                    note="重复反馈",
                )
            feedback_events = [
                event
                for event in workflow.list_task_events(
                    actor_id,
                    project_id,
                    chat_task_id,
                )
                if event["event_type"] == "router.feedback"
            ]
            assert len(feedback_events) == 1
            assert feedback_events[0]["payload"]["correct_intent"] == "question"
            assert feedback_events[0]["payload"]["original"]["query"] == (
                "对整个卷宗做一次体检，并说明时间线与推理的收束情况。"
            )
            assert feedback_events[0]["payload"]["original"]["route"]["intent"] == "analysis"

        with factory() as session:
            exported = export_feedback_fixtures(factory, project_id=project_id)
            assert exported["schema_version"] == "casefile-chat-feedback-export-v1"
            assert exported["fixture_count"] == 1
            exported_fixture = exported["fixtures"][0]
            exported_source = exported["sources"][0]
            assert exported_fixture["expected_primary_intent"] == "question"
            assert exported_fixture["expected_prompt_component"] == "chat"
            assert exported_fixture["message"] == (
                "对整个卷宗做一次体检，并说明时间线与推理的收束情况。"
            )
            assert exported_fixture["casefile"]["entities"]
            assert exported_source["observed_intent"] == "analysis"
            assert exported_source["project_id"] == project_id

        with factory() as session:
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            assert draft["revision"] == 2


def test_agent_chat_issue_route_allows_suggestions_and_records_route_outcome(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        Worker(
            factory,
            config=WorkerConfig(worker_id="issue-route-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="请处理当前焦点中的验证问题。",
            )
            chat_task_id = int(queued["task"]["task_run_id"])
            draft = CaseFileService(session).get_draft(actor_id, project_id)
        entity_id = draft["content"]["entities"][0]["id"]
        event_id = draft["content"]["events"][0]["id"]
        claim_id = draft["content"]["claims"][0]["id"]

        claim = Worker(
            factory,
            config=WorkerConfig(worker_id="issue-route-completion-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )._claim_next()
        assert claim is not None
        assert claim[0] == chat_task_id

        with factory() as session:
            route = routing_policy(
                ChatTaskUnderstanding(
                    primary_intent="explain_issue",
                    confidence=1.0,
                    reason_codes=("rule_ui:issue_action",),
                ),
                budget={},
                route_source="rule_ui",
            )
            completion = WorkflowService(session).complete_chat_task(
                chat_task_id,
                claim[1],
                answer="已解释失败原因，并给出可逐项审阅的修改建议。",
                referenced_object_ids=[entity_id, event_id],
                referenced_event_ids=[event_id],
                referenced_validation_issue_ids=[],
                suggestions=[
                    {
                        "object_id": entity_id,
                        "path": "/description",
                        "value": "负责追查午夜重启原因的研究员。",
                        "reason": "补充人物在卷宗中的职责。",
                    },
                    {
                        "object_id": claim_id,
                        "path": "/support_refs",
                        "value": [],
                        "reason": "暴露关键主张缺少支撑的语义警告。",
                    },
                ],
                usage={"requests": 1},
                route=agent_state_to_jsonable(route),
            )

        assert completion["message"]["status"] == "completed"
        patch_set = completion["message"]["patch_set"]
        assert patch_set is not None
        assert patch_set["status"] == "pending"
        assert len(patch_set["operations"]) == 2

        with factory() as session:
            workflow = WorkflowService(session)
            task = workflow.get_task(actor_id, project_id, chat_task_id)
            events = workflow.list_task_events(actor_id, project_id, chat_task_id)
            event_types = [event["event_type"] for event in events]
            assert "route.suggestions_suppressed" not in event_types
            assert event_types[-2:] == ["route.outcome", "task.succeeded"]
            assert task["result"]["routing"]["intent"] == "explain_issue"
            assert task["result"]["routing"]["suggestion_policy"] == "allow"
            assert task["result"]["routing"]["suppressed_count"] == 0


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
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title="并发编辑",
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
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
                expected_draft_id=draft_id,
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
                    expected_draft_id=draft_id,
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
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])
        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
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
                    expected_draft_id=draft_id,
                    expected_revision=2,
                    operation_ids=[operation_id],
                )

        with factory() as session:
            unchanged = CaseFileService(session).get_draft(actor_id, project_id)
            assert unchanged["revision"] == 2
            assert unchanged["content"]["events"][0]["time"]["end"] == "2042-06-01T20:03"
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
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

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
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title="核对关键对象",
            )
            sent = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
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
        assert set(frozen_input) == {
            "casefile",
            "history",
            "message",
            "focus",
            "validation",
            "context_policy_version",
            "routing_hint",
            "router_version",
            "context_state",
        }
        assert frozen_input["casefile"] == initial_draft["content"]
        assert frozen_input["history"] == []
        assert frozen_input["message"] == "请逐项建议调整研究员、实验室和重启事件。"
        assert frozen_input["context_policy_version"] == "casefile-chat-context-v6"
        assert frozen_input["routing_hint"] == {
            "entrypoint": "free_text",
            "preset_id": None,
        }
        assert frozen_input["router_version"] == "casefile-chat-router-v2"
        assert input_draft_revision == 2
        assert input_hash == hashlib.sha256(rfc8785.dumps(frozen_input)).hexdigest()

        with factory() as session:
            prompt_version, toolset_version = session.execute(
                select(TaskRun.prompt_version, TaskRun.toolset_version).where(
                    TaskRun.id == first_chat_task_id
                )
            ).one()
        assert prompt_version == "casefile-chat-v12"
        assert toolset_version == "casefile-chat-tools-v4"

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
                referenced_event_ids=[event_id],
                referenced_validation_issue_ids=[],
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
        assert first_message["referenced_event_ids"] == [event_id]
        assert first_message["suggested_view"] is None
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
                expected_draft_id=draft_id,
                expected_draft_revision=2,
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
                referenced_event_ids=[event_id],
                referenced_validation_issue_ids=[],
                suggested_view="timeline",
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
        assert second_completion["message"]["suggested_view"] == "timeline"

        first_patch_id = int(first_patch["patch_set_id"])
        selected_operation_ids = [
            int(operation["operation_id"]) for operation in first_patch["operations"][:2]
        ]
        with factory() as session:
            rejected = WorkflowService(session).apply_agent_patch_set(
                actor_id,
                project_id,
                int(second_patch["patch_set_id"]),
                expected_draft_id=draft_id,
                expected_revision=2,
                operation_ids=[],
            )
        assert rejected["status"] == "rejected"
        assert rejected["draft_revision"] == 2
        assert [operation["decision"] for operation in rejected["operations"]] == ["rejected"]
        with factory() as session:
            unchanged = CaseFileService(session).get_draft(actor_id, project_id)
        assert unchanged["revision"] == 2

        with factory() as session:
            applied = WorkflowService(session).apply_agent_patch_set(
                actor_id,
                project_id,
                first_patch_id,
                expected_draft_id=draft_id,
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
        assert (
            next(item for item in applied_draft["content"]["entities"] if item["id"] == entity_id)[
                "name"
            ]
            == "林首席研究员"
        )
        assert (
            next(
                item for item in applied_draft["content"]["locations"] if item["id"] == location_id
            )["name"]
            == "中央实验室"
        )
        assert (
            next(item for item in applied_draft["content"]["events"] if item["id"] == event_id)[
                "title"
            ]
            == event["title"]
        )
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
                expected_draft_id=draft_id,
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
        assert (
            next(item for item in undone_draft["content"]["entities"] if item["id"] == entity_id)[
                "name"
            ]
            == entity["name"]
        )
        assert (
            next(
                item for item in undone_draft["content"]["locations"] if item["id"] == location_id
            )["name"]
            == location["name"]
        )
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
                expected_draft_id=draft_id,
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
            if message["task"] is not None and message["task"]["task_run_id"] == second_chat_task_id
        )
        assert second_assistant["patch_set"]["status"] == "rejected"
        assert second_assistant["patch_set"]["is_stale"] is False


def test_agent_chat_reference_autofill_only_fills_empty_unique_slots(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_REFERENCE_AUTOFILL": "1",
        },
    ):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        Worker(
            factory,
            config=WorkerConfig(worker_id="reference-autofill-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            entity = draft["content"]["entities"][0]
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="请核对关键人物。",
            )
        chat_task_id = int(queued["task"]["task_run_id"])
        claim = Worker(
            factory,
            config=WorkerConfig(worker_id="reference-autofill-completion-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )._claim_next()
        assert claim is not None
        assert claim[0] == chat_task_id

        with factory() as session:
            completion = WorkflowService(session).complete_chat_task(
                chat_task_id,
                claim[1],
                answer=f"{entity['name']} 在本案中负责关键调查。",
                referenced_object_ids=[],
                referenced_event_ids=[],
                referenced_validation_issue_ids=[],
                suggestions=[],
                usage={"requests": 1},
            )

        assert completion["message"]["referenced_object_ids"] == [entity["id"]]

        with factory() as session:
            events = WorkflowService(session).list_task_events(
                actor_id,
                project_id,
                chat_task_id,
            )
        autofill_events = [
            event for event in events if event["event_type"] == "context.reference_autofilled"
        ]
        assert len(autofill_events) == 1
        assert autofill_events[0]["payload"] == {
            "object_ids": [entity["id"]],
            "event_ids": [],
        }


def test_agent_chat_unknown_reference_gets_one_controlled_repair_call(
    workflow_database: tuple[Engine, int, str],
) -> None:
    class RepairingChatProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.chat_calls = 0

        def chat(self, request):
            self.chat_calls += 1
            result = super().chat(request)
            if self.chat_calls == 1:
                return dataclasses_replace(
                    result,
                    candidate=result.candidate.model_copy(
                        update={"referenced_object_ids": ["src_fabricated"]}
                    ),
                )
            return result

    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    provider = RepairingChatProvider()
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        Worker(
            factory,
            config=WorkerConfig(worker_id="reference-repair-fixture-worker"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="请核对关键人物。",
            )
        chat_task_id = int(queued["task"]["task_run_id"])

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="reference-repair-chat-worker"),
            provider_factory=lambda _task: provider,
        )
        assert chat_worker.run_once() is True

        with factory() as session:
            task = session.get(TaskRun, chat_task_id)
            assert task is not None
            assert task.status == "succeeded", (
                task.status,
                task.error_code,
                task.error_details_jsonb,
            )

        with factory() as session:
            events = WorkflowService(session).list_task_events(
                actor_id,
                project_id,
                chat_task_id,
            )
        assert provider.chat_calls == 2
        assert [event["event_type"] for event in events].count(
            "model.reference_repair_started"
        ) == 1
        repair_event = next(
            event for event in events if event["event_type"] == "model.reference_repair_started"
        )
        assert repair_event["payload"]["unknown_object_ids"] == ["src_fabricated"]
        assert repair_event["payload"]["repair_no"] == 1


def test_project_archive_unarchive_roundtrip_and_timestamps(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, _master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        service = CaseFileService(session)
        created = service.create_project(
            actor_id,
            ProjectCreate(title="归档往返", description=None, profile=PROFILE),
        )
        project_id = int(created["id"])
        assert created["status"] == "active"
        assert created["archived_at"] is None
        assert created["created_at"] is not None
        assert created["updated_at"] is not None

        listed = service.list_projects(actor_id)
        assert [item["id"] for item in listed] == [project_id]
        assert listed[0]["created_at"] == created["created_at"]

        archived = service.archive_project(actor_id, project_id)
        assert archived["status"] == "archived"
        assert archived["archived_at"] is not None

        restored = service.unarchive_project(actor_id, project_id)
        assert restored["status"] == "active"
        assert restored["archived_at"] is None

        # 取消归档是幂等的：再次调用保持不变。
        restored_again = service.unarchive_project(actor_id, project_id)
        assert restored_again["status"] == "active"


def test_get_brief_exposes_current_version_no(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, _master_key = workflow_database
    project_id, _task_run_id = _prepare_task(engine, actor_id)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        brief = WorkflowService(session).get_brief(actor_id, project_id)
        assert brief["current_version_id"] is not None
        assert brief["current_version_no"] == 1
