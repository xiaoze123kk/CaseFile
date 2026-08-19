"""M1 DB Canned Outcome integration test for the CaseFile chat Agent.

Every T1 Task is sent through the real production path once per trial in a
fresh project, completed by CannedChatOutcomeProvider, and the persisted
AgentMessage/PatchSet/Draft outcome is graded with the deterministic Grader.
The shared runner lives in ``chat_outcome_canned_support.py`` so the Phase 2
context acceptance suite can exercise the identical path.
"""

from __future__ import annotations

import pytest
from casefile.benchmark.chat_outcome_eval import (
    ChatOutcomeTrialVerdict,
    build_outcome_tasks,
)
from chat_outcome_canned_support import run_canned_trial
from sqlalchemy import Engine

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


def test_m1_canned_denied_route_suppresses_suggestions(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    task = next(task for task in build_outcome_tasks() if task.task_id == "golden-entity-question")
    outcome = run_canned_trial(engine, actor_id, master_key, task)
    assert outcome.verdict.passed
    assert outcome.verdict.allow_suggestions is False
    assert outcome.verdict.unnecessary_suggestions is False
