"""Unit tests for the M3-C4 audit adoption feedback loop."""

from __future__ import annotations

import json

import pytest

from casefile.benchmark.audit_feedback_export import (
    AUDIT_FEEDBACK_EXPORT_SCHEMA,
    audit_feedback_fixture_from_payload,
    load_audit_feedback_fixtures,
)
from casefile.benchmark.chat_outcome_eval import (
    build_outcome_tasks,
    build_outcome_tasks_from_audit_feedback,
    grade_reference_solution,
)


def _restart_task():
    return next(
        task for task in build_outcome_tasks() if task.task_id == "golden-audit-restart-loop"
    )


def _feedback_payload(decision: str, *, with_findings: bool = True) -> dict:
    task = _restart_task()
    findings = (
        [
            {
                "finding_id": "F1",
                "kind": "contradiction",
                "severity": "S2",
                "title": "重启原因描述矛盾",
                "statement": "研究员描述与备用系统自动触发主张冲突。",
                "needs_manual_review": False,
                "evidence_object_ids": ["ent_researcher", "ent_backup_system"],
                "evidence_event_ids": [],
                "evidence_validation_issue_ids": [],
            }
        ]
        if with_findings
        else []
    )
    operations = (
        [
            {
                "operation_id": "op_1",
                "target_object_id": "ent_researcher",
                "field_path": "/description",
                "new_value": "查明第七次重启由备用系统依据安全规则自动触发。",
                "reason": "修正矛盾描述。",
                "decision": "pending",
            }
        ]
        if decision == "applied"
        else []
    )
    return audit_feedback_fixture_from_payload(
        fixture_id=f"unit-{decision}",
        decision=decision,
        task_run_id=1,
        patch_set_id=2,
        input_jsonb={
            "casefile": task.frozen_casefile,
            "message": task.message,
            "hint": task.hint,
            "focus": task.focus,
            "validation": {"issues": list(task.frozen_validation_issues)},
        },
        result_jsonb={
            "answer": "审计反馈回流基准回复。",
            "referenced_object_ids": ["ent_researcher", "ent_backup_system"],
            "referenced_event_ids": [],
            "referenced_validation_issue_ids": [],
            "audit_findings": findings,
        },
        patch_operations=operations,
        source={"project_id": 9},
    )


def test_applied_feedback_becomes_golden_audit_task() -> None:
    fixture = _feedback_payload("applied")
    assert fixture is not None
    assert fixture["schema_version"] == AUDIT_FEEDBACK_EXPORT_SCHEMA
    assert fixture["decision"] == "applied"
    assert fixture["patch_operations"][0]["new_value"]

    (task,) = build_outcome_tasks_from_audit_feedback([fixture])
    assert task.kind == "feedback"
    assert task.expectations.required_audit_finding_kinds == ("contradiction",)
    assert task.expectations.required_audit_evidence_object_ids == (
        "ent_researcher",
        "ent_backup_system",
    )
    assert task.expectations.required_suggestion_paths == (
        ("ent_researcher", "description"),
    )
    assert task.expectations.simulate_suggestions is True

    verdict = grade_reference_solution(task)
    assert verdict.passed, verdict.failures
    assert verdict.audit_finding_count == 1
    assert verdict.simulate_legality == 1.0
    assert verdict.allow_suggestions is True


def test_rejected_and_undone_feedback_become_zero_gates() -> None:
    for decision in ("rejected", "undone"):
        fixture = _feedback_payload(decision, with_findings=False)
        assert fixture is not None

        (task,) = build_outcome_tasks_from_audit_feedback([fixture])
        assert task.kind == "feedback"
        assert "[审计反馈·" in task.message
        assert task.expectations.audit_finding_count_range == (0, 0)
        assert task.expectations.suggestion_count_range == (0, 0)
        assert task.expectations.no_unnecessary_suggestions is True

        verdict = grade_reference_solution(task)
        assert verdict.passed, verdict.failures
        assert verdict.audit_finding_count == 0
        assert verdict.unnecessary_suggestions is False


def test_feedback_fixture_round_trip_via_json(tmp_path) -> None:
    fixture = _feedback_payload("applied")
    assert fixture is not None
    payload = {
        "schema_version": AUDIT_FEEDBACK_EXPORT_SCHEMA,
        "fixture_count": 1,
        "skipped_count": 0,
        "fixtures": [fixture],
        "sources": [],
    }
    path = tmp_path / "audit-feedback.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    loaded = load_audit_feedback_fixtures(path)
    assert len(loaded) == 1
    assert loaded[0]["decision"] == "applied"
    (task,) = build_outcome_tasks_from_audit_feedback(loaded)
    assert grade_reference_solution(task).passed


def test_malformed_feedback_fixture_is_loud() -> None:
    with pytest.raises(ValueError, match="unsupported decision"):
        build_outcome_tasks_from_audit_feedback([{"fixture_id": "bad", "decision": "stale"}])
    with pytest.raises(ValueError, match="casefile"):
        build_outcome_tasks_from_audit_feedback(
            [{"fixture_id": "bad", "decision": "applied", "message": "复查"}]
        )
    with pytest.raises(ValueError, match="audit_findings"):
        build_outcome_tasks_from_audit_feedback(
            [
                {
                    "fixture_id": "bad",
                    "decision": "applied",
                    "message": "复查",
                    "casefile": _restart_task().frozen_casefile,
                    "audit_findings": [],
                    "patch_operations": [],
                }
            ]
        )


def test_unusable_payload_returns_none_instead_of_raising() -> None:
    assert (
        audit_feedback_fixture_from_payload(
            fixture_id="bad",
            decision="pending",
            task_run_id=1,
            patch_set_id=2,
            input_jsonb=None,
            result_jsonb=None,
            patch_operations=None,
        )
        is None
    )
    assert (
        audit_feedback_fixture_from_payload(
            fixture_id="bad",
            decision="rejected",
            task_run_id=1,
            patch_set_id=2,
            input_jsonb={"casefile": None},
            result_jsonb={},
            patch_operations=[],
        )
        is None
    )
