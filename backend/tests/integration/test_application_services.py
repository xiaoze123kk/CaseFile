"""PostgreSQL integration tests for the Brief-to-Draft application workflow."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from application_services_test_support import (
    PROFILE,
    ConclusionFixtureProvider,
    EmptyKnowledgeStateProvider,
    RichFixtureProvider,
    StructuralFailureProvider,
    _adopt_candidate,
    _brief,
    _prepare_task,
)
from casefile.agent_runtime import FakeProvider
from casefile.application.casefile_v1 import build_casefile_document
from casefile.application.commands import ProjectCreate
from casefile.application.errors import ApplicationError
from casefile.application.services import CaseFileService
from casefile.application.snapshot import casefile_content_hash
from casefile.application.v1_editing import V1EditingService
from casefile.application.workbench_read_model import WorkbenchReadModel
from casefile.application.workflow_service import WorkflowService
from casefile.contracts import validate_casefile
from casefile.data_postgres.models import (
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
from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres


def test_provider_credential_deletion_blocks_active_tasks_and_erases_material(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        with patch(
            "casefile.application.workflow.content.prompt_version_for_task",
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


def test_author_answer_suggestion_accepts_incomplete_brief_context(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        project = CaseFileService(session).create_project(
            actor_id,
            ProjectCreate(title="未完成建案", description=None, profile=PROFILE),
        )
    project_id = int(project["id"])

    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        with factory() as session:
            workflow = WorkflowService(session)
            workflow.save_provider_setting(
                actor_id,
                provider="openai",
                api_key="sk-test-author-answer-suggestion",
                model_id="gpt-5.6-sol",
                model_is_custom=False,
            )
            task = workflow.create_anchor_extract_task(
                actor_id,
                project_id,
                expected_brief_revision=1,
                provider="openai",
                mode="suggest_author_answer",
                content={},
            )

        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="author-answer-suggestion-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert worker.run_once() is True

        with factory() as session:
            completed = WorkflowService(session).get_task(
                actor_id,
                project_id,
                task["task_run_id"],
            )

    assert completed["status"] == "succeeded"
    assert completed["result"]["suggested_author_answer"]


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
            "casefile.application.workflow.content.prompt_version_for_task",
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
            "casefile.application.workflow.content.prompt_version_for_task",
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
            "casefile.application.workflow.content.prompt_version_for_task",
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
            "casefile.application.workflow.content.prompt_version_for_task",
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
            expected_calls = int(task_row.budget_jsonb["structural_repair_attempts"]) + 1

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
            "casefile.application.workflow.content.prompt_version_for_task",
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
            assert position_operations[0].field_path == ""
            history_before = position_operations[0].old_value_jsonb
            history_after = position_operations[0].new_value_jsonb
            assert history_before["document"]["locations"][0] == before_location
            assert history_after["document"]["locations"][0] == saved_location
            assert history_after["mutation_set"]["operations"][0]["field_path"] == (
                "/spatial_position"
            )


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
            assert revision == 6

        with factory() as session:
            current = CaseFileService(session).get_draft(actor_id, project_id)
            current_resolution = current["content"]["resolution_specs"][0]
            assert current_resolution["conclusion"]["review_status"] == "proposed"
            _confirmed, revision = V1EditingService(session).confirm_conclusion(
                actor_id,
                project_id,
                resolution_id,
                expected_draft_id=draft_id,
                expected_revision=6,
            )
            assert revision == 7

        with factory() as session:
            withdrawn, revision = V1EditingService(session).withdraw_conclusion(
                actor_id,
                project_id,
                resolution_id,
                expected_draft_id=draft_id,
                expected_revision=7,
            )
            assert revision == 8
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
            assert revision == 9
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
                    DraftOperation.result_revision == 9,
                )
            )
            assert batch_operation is not None
            assert (
                batch_operation.new_value_jsonb["mutation_set"]["operations"][0]["new_value"][
                    "review_status"
                ]
                == "proposed"
            )
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
            assert event["participant_refs"] == sorted(
                entity_refs,
                key=lambda ref: (ref["object_type"], ref["object_id"]),
            )
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
                        DraftOperation.operation_type == "logical_mutation_apply",
                    )
                )
            )
            assert len(edit_operations) == len(edits) + 1
            assert {
                operation.result_revision - operation.base_revision for operation in edit_operations
            } == {1}
