"""Unit tests for the result-level chat outcome Grader and M0 calibration."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from casefile.agent_runtime.models import CaseFileChatCandidateV2
from casefile.agent_runtime.prompt import chat_executor_output_type
from casefile.benchmark.chat_outcome_eval import (
    _request_for_task,
    build_grader_mutations,
    build_outcome_tasks,
    grade_chat_outcome,
    grade_reference_solution,
    resolve_task_route,
    run_calibration,
    validate_task_contract,
)


def test_outcome_suite_has_thirty_four_unique_tasks() -> None:
    tasks = build_outcome_tasks()
    assert len(tasks) == 34
    assert len({task.task_id for task in tasks}) == 34


def test_outcome_suite_is_balanced() -> None:
    tasks = build_outcome_tasks()
    kinds = {task.kind for task in tasks}
    assert kinds == {"golden", "boundary", "adversarial"}
    assert sum(task.kind == "golden" for task in tasks) == 18
    assert sum(task.kind == "boundary" for task in tasks) == 8
    assert sum(task.kind == "adversarial" for task in tasks) == 8


def test_fourteen_golden_series_tasks_have_one_capability() -> None:
    goldens = [task for task in build_outcome_tasks() if task.task_id.startswith("golden-")]
    assert len(goldens) == 14
    assert all(task.capability for task in goldens)


def test_every_reference_solution_passes_grader() -> None:
    for task in build_outcome_tasks():
        verdict = grade_reference_solution(task)
        assert verdict.passed, (task.task_id, verdict.failures)


def test_calibration_accepts_references_and_catches_mutations() -> None:
    report = run_calibration()
    assert report.status == "passed"
    assert report.reference_failures == ()
    assert report.contract_failures == ()
    assert report.mutation_misses == ()
    assert len(report.mutation_verdicts) == 16


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


def test_live_request_binds_v12_and_audit_output_v2() -> None:
    task = next(
        task for task in build_outcome_tasks() if task.task_id == "golden-audit-restart-loop"
    )
    request = _request_for_task(task)
    assert request.prompt_version == "casefile-chat-v12"
    assert request.toolset_version == "casefile-chat-tools-v4"
    resolved = resolve_task_route(task)
    assert resolved.route is not None
    assert chat_executor_output_type(resolved) is CaseFileChatCandidateV2


def test_calibrated_edit_references_are_non_noop_and_production_safe() -> None:
    edit_tasks = [
        task for task in build_outcome_tasks() if task.reference_candidate.suggestions
    ]
    assert edit_tasks
    for task in edit_tasks:
        assert validate_task_contract(task) == ()


def test_contract_gate_rejects_a_noop_reference() -> None:
    task = next(
        task for task in build_outcome_tasks() if task.task_id == "golden-edit-description"
    )
    casefile = deepcopy(task.frozen_casefile)
    suggestion = task.reference_candidate.suggestions[0]
    casefile["entities"][0]["description"] = __import__("json").loads(
        suggestion.value_json
    )

    failures = validate_task_contract(replace(task, casefile=casefile))

    assert any("reference_patch_noop" in failure for failure in failures)


def test_recalibrated_fixture_contracts_are_explicit() -> None:
    tasks = {task.task_id: task for task in build_outcome_tasks()}
    inspect = tasks["golden-analysis-inspect"].expectations
    assert inspect.expected_object_ids == ()
    assert inspect.expected_event_ids == ("evt_restart",)
    assert inspect.expected_validation_issue_ids == ("validator:issue-1",)
    assert "boundary-duplicate-label-with-focus" in tasks
    assert "boundary-cross-collection-refs" not in tasks

    multi_field = tasks["golden-multi-field-edit"]
    assert all(
        item.value_json is not None
        for item in multi_field.expectations.required_suggestions
    )
    multi_object = tasks["golden-multi-object-edit"]
    assert all(
        item.value_json is not None
        for item in multi_object.expectations.required_suggestions
    )
