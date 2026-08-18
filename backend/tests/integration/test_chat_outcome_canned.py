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
    assert len(verdicts) == 30
    assert all(verdict.draft_unchanged for verdict in verdicts)


def test_m1_canned_denied_route_suppresses_suggestions(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    task = next(task for task in build_outcome_tasks() if task.task_id == "golden-entity-question")
    outcome = run_canned_trial(engine, actor_id, master_key, task)
    assert outcome.verdict.passed
    assert outcome.verdict.allow_suggestions is False
    assert outcome.verdict.unnecessary_suggestions is False
