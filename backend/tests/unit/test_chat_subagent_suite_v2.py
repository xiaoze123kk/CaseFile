"""Grader controls for scope, belief/fact distinction and large independent evidence tasks."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from casefile.benchmark.chat_outcome_suite import _request_for_task
from casefile.benchmark.chat_subagent_suite_v2 import (
    answer_classifications,
    build_suite_tasks,
    calibration_report,
    freeze_suite,
    grade_trial,
    load_suite,
)
from casefile.contracts import validate_casefile


def test_references_pass_and_wrong_answers_fail_before_live_run() -> None:
    report = calibration_report()
    assert report["passed"], report
    assert report["reference_count"] == 24
    assert report["mutation_count"] >= 48


def test_frozen_payload_is_reproducible_from_reviewed_specification(tmp_path: Any) -> None:
    target = tmp_path / "suite.json"
    freeze_suite(target)
    assert json.loads(target.read_text("utf-8")) == load_suite()
    with pytest.raises(FileExistsError):
        freeze_suite(target)


def test_large_cases_are_schema_valid_distinct_and_preannotated() -> None:
    tasks = build_suite_tasks()
    hashes = set()
    for task in tasks[8:]:
        validate_casefile(task.frozen_casefile)
        hashes.add(
            hashlib.sha256(json.dumps(task.frozen_casefile, sort_keys=True).encode()).hexdigest()
        )
        assert sum(len(v) for v in task.frozen_casefile.values() if isinstance(v, list)) >= 50
        assert task.validation_issues == ()  # no unrelated inherited time-reversal issue
    assert len(hashes) == 16
    annotations = load_suite()["annotations"]
    assert sum(row["parallel_eligible"] for row in annotations) == 10
    assert all(
        row["independent_evidence_units"] >= 3 for row in annotations if row["parallel_eligible"]
    )


def test_questions_and_edit_authority_are_aligned() -> None:
    for task in build_suite_tasks()[8:]:
        if task.task_id == "audit-07":
            assert "仅给" in task.message and "/title" in task.message
            assert task.expectations.requires_suggestion
        else:
            assert "不提出任何修改建议" in task.message
            assert not task.expectations.required_suggestions
            assert task.expectations.suggestion_count_range == (0, 0)


def test_annotations_and_answers_are_not_in_model_request() -> None:
    for task in build_suite_tasks()[8:]:
        request = _request_for_task(task)
        assert "annotations" not in request.casefile
        assert "expected" not in request.message
        assert "rationale" not in request.casefile
        assert request.message != task.reference_candidate.answer


@pytest.mark.parametrize("case_id", ["audit-02", "audit-04", "audit-05", "audit-08"])
def test_compatible_states_do_not_require_a_conflict(case_id: str) -> None:
    task = next(task for task in build_suite_tasks() if task.task_id == case_id)
    assert task.expectations.audit_finding_count_range == (0, 0)
    assert grade_trial(task, task.reference_candidate, allow_suggestions=True).passed


def test_conflicting_summary_labels_cannot_pass_by_containing_right_keyword() -> None:
    task = next(task for task in build_suite_tasks() if task.task_id == "audit-02")
    candidate = task.reference_candidate.model_copy(
        update={
            "answer": task.reference_candidate.answer + "\n【A】事实冲突",
        }
    )
    assert not grade_trial(task, candidate, allow_suggestions=True).passed
    assert answer_classifications("**【A】**：认知差异。理由") == {"A": {"认知差异"}}


def test_parallel_runner_selects_v2_tasks_and_grader(monkeypatch: Any, tmp_path: Any) -> None:
    from decimal import Decimal

    from casefile.benchmark import chat_subagent_live_eval as live

    seen: dict[str, Any] = {}

    def fake_eval(_factory: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return type("Report", (), {"as_dict": lambda _self: {"rows": []}})()

    monkeypatch.setattr(live, "run_live_chat_outcome_eval", fake_eval)
    prices = live.load_prices(
        live.Path("src/casefile/benchmark/prices/deepseek-flash-20260916.json")
    )
    live.run(
        tmp_path,
        api_key="unused",
        prices=prices,
        budget_cny=Decimal("1"),
        max_input_tokens_per_trial=100,
        max_output_tokens_per_trial=100,
        arms=("subagents_v4",),
        trials=3,
        suite_version="v2",
    )
    assert len(seen["tasks"]) == 24
    assert seen["trial_grader"] is grade_trial
    assert seen["retain_candidate"] is True
