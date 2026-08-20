"""M1 DB Canned Outcome integration test for the CaseFile chat Agent.

Every T1 Task is sent through the real production path once per trial in a
fresh project, completed by CannedChatOutcomeProvider, and the persisted
AgentMessage/PatchSet/Draft outcome is graded with the deterministic Grader.
The shared runner lives in ``chat_outcome_canned_support.py`` so the Phase 2
context acceptance suite can exercise the identical path.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from application_services_test_support import (
    RichFixtureProvider,
    _adopt_candidate,
    _prepare_task,
)
from chat_outcome_canned_support import run_canned_trial
from sqlalchemy import Engine, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from casefile.application.services import CaseFileService
from casefile.application.workflow_service import WorkflowService
from casefile.benchmark.chat_outcome_canned import CannedChatOutcomeProvider
from casefile.benchmark.chat_outcome_eval import (
    ChatOutcomeTrialVerdict,
    build_outcome_tasks,
)
from casefile.data_postgres.models import (
    AgentPatchOperation,
    TaskEvent,
    TaskRun,
    VerificationFinding,
    VerificationFindingPatchOperation,
    VerificationFindingRef,
    VerificationRun,
)
from casefile.worker.runtime import Worker, WorkerConfig

pytestmark = pytest.mark.postgres


def test_m1_canned_outcome_suite_passes_through_production_path(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    tasks = build_outcome_tasks()
    verdicts: list[ChatOutcomeTrialVerdict] = []
    for task in tasks:
        outcome = run_canned_trial(engine, actor_id, master_key, task)
        verdicts.append(outcome.verdict)
        assert outcome.verdict.passed, (task.task_id, outcome.verdict.failures)
    assert len(verdicts) == 35
    assert all(verdict.draft_unchanged for verdict in verdicts)


def test_m1_canned_logic_audit_preset_yields_pending_patch_set(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    task = next(
        task for task in build_outcome_tasks() if task.task_id == "golden-logic-audit"
    )
    outcome = run_canned_trial(engine, actor_id, master_key, task)

    assert outcome.verdict.passed, outcome.verdict.failures
    assert outcome.verdict.allow_suggestions is True
    assert outcome.draft_unchanged is True
    routing = outcome.result_jsonb["routing"]
    assert routing["intent"] == "logic_audit"
    assert routing["route_source"] == "rule_preset"
    assert len(outcome.result_jsonb["audit_findings"]) == 1
    assert outcome.result_jsonb["audit_findings"][0]["finding_id"] == "F1"
    assert outcome.result_jsonb["audit_findings"][0]["kind"] == "contradiction"
    assert outcome.provider_request is not None
    execution_profile = outcome.provider_request.route.execution_profile
    assert execution_profile["profile"] == "logic_audit.full_review"
    assert outcome.patch_set is not None
    assert outcome.patch_set["status"] == "pending"
    assert [operation["field_path"] for operation in outcome.patch_set["operations"]] == [
        "/description"
    ]
    first_entity_id = outcome.frozen_input["casefile"]["entities"][0]["id"]
    assert [operation["object_id"] for operation in outcome.patch_set["operations"]] == [
        first_entity_id
    ]

    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        task = session.get(TaskRun, outcome.chat_task_id)
        assert task is not None
        run = session.scalar(
            select(VerificationRun).where(
                VerificationRun.project_id == task.project_id,
                VerificationRun.source_task_run_id == task.id,
            )
        )
        assert run is not None
        assert outcome.result_jsonb["verification_run_id"] == run.id
        findings = list(
            session.scalars(
                select(VerificationFinding).where(
                    VerificationFinding.verification_run_id == run.id
                )
            )
        )
        assert len(findings) == 1
        assert findings[0].kind == "llm"
        assert findings[0].severity == "error"
        refs = list(
            session.scalars(
                select(VerificationFindingRef).where(
                    VerificationFindingRef.finding_id == findings[0].id
                )
            )
        )
        assert any(ref.ref_key == first_entity_id and ref.role == "evidence" for ref in refs)
        operation = session.scalar(
            select(AgentPatchOperation).where(
                AgentPatchOperation.patch_set_id == outcome.patch_set["patch_set_id"]
            )
        )
        assert operation is not None
        link = session.scalar(
            select(VerificationFindingPatchOperation).where(
                VerificationFindingPatchOperation.finding_id == findings[0].id,
                VerificationFindingPatchOperation.patch_operation_id == operation.id,
            )
        )
        assert link is not None and link.relation_kind == "fixes"

    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "DELETE FROM verification_runs WHERE id = %s",
                (run.id,),
            )


def test_manual_rerun_persists_verification_events_and_result(
    workflow_database: tuple[Engine, int, str],
) -> None:
    """A manual rerun is a normal chat TaskRun with domain-result lineage."""

    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        generation_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="verification-manual-generation"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        _adopt_candidate(engine, actor_id, project_id, generation_task_id)

        with factory() as session:
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            queued = WorkflowService(session).rerun_verification(
                actor_id,
                project_id,
                expected_draft_id=int(draft["draft_id"]),
                expected_draft_revision=int(draft["revision"]),
                provider="openai",
            )
        task_run_id = int(queued["task"]["task_run_id"])

        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="verification-manual-chat"),
            provider_factory=lambda _task: CannedChatOutcomeProvider(),
        )
        assert worker.run_once() is True

    with factory() as session:
        task = session.get(TaskRun, task_run_id)
        assert task is not None
        assert task.status == "succeeded"
        assert task.input_jsonb["verification_trigger"] == "manual"
        assert task.input_jsonb["routing_hint"] == {
            "entrypoint": "preset",
            "preset_id": "audit",
        }
        verification_events = list(
            session.scalars(
                select(TaskEvent)
                .where(
                    TaskEvent.task_run_id == task_run_id,
                    TaskEvent.event_type.like("verification.%"),
                )
                .order_by(TaskEvent.sequence_no)
            )
        )
        assert [event.event_type for event in verification_events] == [
            "verification.started",
            "verification.finding",
            "verification.completed",
        ]
        assert verification_events[1].payload_jsonb["current"] == 1
        assert verification_events[1].payload_jsonb["total"] == 1
        assert verification_events[2].payload_jsonb["trigger"] == "manual"
        assert verification_events[2].payload_jsonb["profile"] == "balanced"

        run = session.scalar(
            select(VerificationRun).where(
                VerificationRun.project_id == project_id,
                VerificationRun.source_task_run_id == task_run_id,
            )
        )
        assert run is not None
        assert run.trigger == "manual"
        assert run.profile == "balanced"
        assert task.result_jsonb["verification_run_id"] == run.id
        findings = list(
            session.scalars(
                select(VerificationFinding)
                .where(VerificationFinding.verification_run_id == run.id)
                .order_by(VerificationFinding.id)
            )
        )
        assert len(findings) == 1
        assert findings[0].kind == "llm"
        assert findings[0].status == "open"


def test_m1_canned_denied_route_suppresses_suggestions(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    task = next(task for task in build_outcome_tasks() if task.task_id == "golden-entity-question")
    outcome = run_canned_trial(engine, actor_id, master_key, task)
    assert outcome.verdict.passed
    assert outcome.verdict.allow_suggestions is False
    assert outcome.verdict.unnecessary_suggestions is False
