"""Unit tests for the result-level chat outcome Grader and M0 calibration."""

from __future__ import annotations

from casefile.benchmark.chat_outcome_eval import (
    build_grader_mutations,
    build_outcome_tasks,
    grade_chat_outcome,
    grade_reference_solution,
    run_calibration,
)


def test_outcome_suite_has_thirty_unique_tasks() -> None:
    tasks = build_outcome_tasks()
    assert len(tasks) == 30
    assert len({task.task_id for task in tasks}) == 30


def test_outcome_suite_is_balanced() -> None:
    tasks = build_outcome_tasks()
    kinds = {task.kind for task in tasks}
    assert kinds == {"golden", "boundary", "adversarial"}
    assert sum(task.kind == "golden" for task in tasks) == 14
    assert sum(task.kind == "boundary" for task in tasks) == 8
    assert sum(task.kind == "adversarial" for task in tasks) == 8


def test_every_reference_solution_passes_grader() -> None:
    for task in build_outcome_tasks():
        verdict = grade_reference_solution(task)
        assert verdict.passed, (task.task_id, verdict.failures)


def test_calibration_accepts_references_and_catches_mutations() -> None:
    report = run_calibration()
    assert report.status == "passed"
    assert report.reference_failures == ()
    assert report.mutation_misses == ()
    assert len(report.mutation_verdicts) == 12


def test_mutations_fail_their_expected_gate() -> None:
    tasks = {task.task_id: task for task in build_outcome_tasks()}
    for mutation in build_grader_mutations():
        task = tasks[mutation.task_id]
        verdict = grade_reference_solution(task)
        graded = grade_chat_outcome(
            task,
            mutation.mutated_candidate,
            allow_suggestions=verdict.allow_suggestions,
            actual_intent=verdict.actual_intent,
            route_source=verdict.route_source,
        )
        assert not graded.passed
        assert mutation.expected_failure in graded.failures


def test_golden_edit_suggestion_is_legal() -> None:
    task = next(task for task in build_outcome_tasks() if task.task_id == "golden-edit-description")
    verdict = grade_reference_solution(task)
    assert verdict.suggestion_legality == 1.0
    assert verdict.missing_edit_suggestion is False
    assert verdict.allow_suggestions is True


def test_question_route_denies_suggestions() -> None:
    task = next(task for task in build_outcome_tasks() if task.task_id == "golden-entity-question")
    verdict = grade_reference_solution(task)
    assert verdict.actual_intent == "question"
    assert verdict.allow_suggestions is False
    assert verdict.unnecessary_suggestions is False
