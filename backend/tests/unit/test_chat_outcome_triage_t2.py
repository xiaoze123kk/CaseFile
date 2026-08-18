"""Unit tests for outcome Eval triage and the T2 difficulty pool."""

from __future__ import annotations

from casefile.benchmark.chat_outcome_t2 import (
    build_t2_tasks,
    verify_t2_reference_solutions,
)
from casefile.benchmark.chat_outcome_triage import triage_chat_outcome_report


def test_t2_pool_has_five_healthy_reference_solutions() -> None:
    tasks = build_t2_tasks()
    assert len(tasks) == 5
    assert all(task.tier == "T2" for task in tasks)
    assert verify_t2_reference_solutions() == []


def test_triage_groups_provider_and_safety_failures() -> None:
    report = {
        "rows": [
            {
                "task_id": "golden-entity-question",
                "passed": False,
                "danger_miss": False,
                "route_source": "llm",
                "failures": ["provider_error"],
            },
            {
                "task_id": "adversarial-danger-delete",
                "passed": False,
                "danger_miss": True,
                "route_source": "llm",
                "failures": ["unnecessary_suggestion"],
            },
            {
                "task_id": "golden-edit-description",
                "passed": False,
                "danger_miss": False,
                "route_source": "llm",
                "failures": ["reference_precision"],
            },
            {
                "task_id": "golden-edit-description",
                "passed": True,
                "danger_miss": False,
                "route_source": "llm",
                "failures": [],
            },
        ]
    }
    triage = triage_chat_outcome_report(report)
    assert triage.failing_tasks == (
        "adversarial-danger-delete",
        "golden-edit-description",
        "golden-entity-question",
    )
    assert triage.failure_signature_counts["provider_error"] == 1
    assert triage.failure_signature_counts["reference_precision"] == 1
    assert "agent_error" in triage.suggested_categories["adversarial-danger-delete"]
    assert "agent_or_grader_error" in triage.suggested_categories["golden-edit-description"]
    assert len(triage.action_lines) >= 4
