"""N4.4 ScenePlan benchmark contract tests; no live Provider is used."""

from __future__ import annotations

from casefile.benchmark.scene_plan_eval import run_suite, validate_suite


def test_scene_plan_suite_is_audited_24_task_matrix() -> None:
    validated = validate_suite()

    assert len(validated["suite"]["tasks"]) == 24
    assert len(validated["alternatives"]) == 8
    assert len(validated["mutations"]) >= 10
    assert all(
        invariant["input_evidence_paths"]
        for task in validated["suite"]["tasks"]
        for invariant in task["outcome_invariants"]
    )
    assert validated["suite"]["qualification"] == {
        "status": "uncalibrated",
        "qualified": False,
        "reason": "live_baseline_not_run",
    }


def test_scene_plan_reference_and_alternative_outcomes_pass() -> None:
    capability = run_suite(suite_kind="capability")
    regression = run_suite(suite_kind="regression")

    assert capability["status"] == "passed"
    assert capability["metrics"]["trial_count"] == 24
    assert regression["status"] == "passed"
    assert regression["metrics"]["trial_count"] == 8
    assert capability["qualification"]["qualified"] is False


def test_scene_plan_safety_mutations_are_rejected_by_expected_reason() -> None:
    report = run_suite(suite_kind="safety")

    assert report["status"] == "passed"
    assert report["metrics"]["trial_count"] >= 10
    assert all(not trial["candidate_accepted"] for trial in report["trials"])
    assert all(trial["expected_reason_code"] for trial in report["trials"])
    assert report["metrics"]["failure_taxonomy"]["Infrastructure"] == 0


def test_scene_plan_report_fingerprint_is_deterministic() -> None:
    first = run_suite(suite_kind="regression")
    second = run_suite(suite_kind="regression")

    assert first["fingerprint"] == second["fingerprint"]
    assert first["metrics"] == second["metrics"]
