"""Integration tests for the audit feedback export SELECT-only loop.

The exporter must read the same persisted PatchSet lifecycle rows that the
human apply/reject/undo endpoints write, without creating tables or rows.
"""

from __future__ import annotations

import pytest
from chat_outcome_canned_support import run_canned_trial
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from casefile.application.workflow_service import WorkflowService
from casefile.benchmark.audit_feedback_export import (
    AUDIT_FEEDBACK_EXPORT_SCHEMA,
    export_audit_feedback_fixtures,
)
from casefile.benchmark.chat_outcome_eval import (
    build_outcome_tasks,
    build_outcome_tasks_from_audit_feedback,
    grade_reference_solution,
)
from casefile.data_postgres.models import AgentPatchSet
from casefile.data_postgres.session import create_session_factory

pytestmark = pytest.mark.postgres


def test_rejected_audit_patch_replays_as_zero_gate_fixture(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    task = next(
        task for task in build_outcome_tasks() if task.task_id == "golden-logic-audit"
    )
    outcome = run_canned_trial(engine, actor_id, master_key, task)
    assert outcome.patch_set is not None and outcome.patch_set["status"] == "pending"
    patch_set_id = int(outcome.patch_set["patch_set_id"])

    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        patch = session.get(AgentPatchSet, patch_set_id)
        assert patch is not None
        project_id = int(patch.project_id)
        draft_id = int(patch.draft_id)
        base_revision = int(patch.base_draft_revision)
    with factory() as session:
        rejected = WorkflowService(session).apply_agent_patch_set(
            actor_id,
            project_id,
            patch_set_id,
            expected_draft_id=draft_id,
            expected_revision=base_revision,
            operation_ids=[],
        )
    assert rejected["status"] == "rejected"

    export = export_audit_feedback_fixtures(
        create_session_factory(engine),
        project_id=project_id,
    )
    assert export["schema_version"] == AUDIT_FEEDBACK_EXPORT_SCHEMA
    assert export["fixture_count"] >= 1
    rejected_fixture = next(
        fixture
        for fixture in export["fixtures"]
        if fixture["decision"] == "rejected"
    )

    (feedback_task,) = build_outcome_tasks_from_audit_feedback([rejected_fixture])
    assert feedback_task.expectations.audit_finding_count_range == (0, 0)
    assert feedback_task.expectations.suggestion_count_range == (0, 0)
    assert grade_reference_solution(feedback_task).passed
