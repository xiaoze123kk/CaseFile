from dataclasses import replace

import pytest
from casefile.benchmark.chat_goal_qualification import (
    GoalTrialEvidence,
    _fatal_infrastructure_failure,
    _report,
    _trial_progress_line,
)


def test_goal_qualification_progress_tracks_trial_start_and_completion() -> None:
    assert (
        _trial_progress_line(
            trial_index=7,
            total_trials=72,
            task_id="goal_read_timeline_audit",
            trial_no=1,
            state="started",
        )
        == "[7/72] goal_read_timeline_audit trial=1 started"
    )
    assert _trial_progress_line(
        trial_index=7,
        total_trials=72,
        task_id="goal_read_timeline_audit",
        trial_no=1,
        state="completed",
        passed=False,
        failures=("goal_not_observed",),
        infrastructure_failure="executor_exception:TimeoutError",
        elapsed_seconds=12.3456,
    ) == (
        "[7/72] goal_read_timeline_audit trial=1 completed status=failed "
        "elapsed_s=12.346 failures=goal_not_observed "
        "infrastructure=executor_exception:TimeoutError"
    )

    with pytest.raises(ValueError, match="goal_trial_progress_state_invalid"):
        _trial_progress_line(
            trial_index=7,
            total_trials=72,
            task_id="goal_read_timeline_audit",
            trial_no=1,
            state="unknown",
        )


def test_goal_qualification_requires_complete_frozen_72_trials() -> None:
    rows = []
    families = [
        "mutation_create",
        "mutation_update",
        "mutation_delete",
        "read_only",
    ]
    for task_index in range(24):
        family = families[task_index % len(families)]
        for trial_no in range(1, 4):
            rows.append(
                GoalTrialEvidence(
                    task_id=f"task_{task_index}",
                    family=family,
                    trial_no=trial_no,
                    expected_path="goal",
                    completed=True,
                    passed=True,
                    goal_observed=True,
                    completion_observed=True,
                    obligation_coverage=1.0,
                    patch_present=family.startswith("mutation_"),
                    no_auto_apply=True,
                    public_contract_valid=True,
                    internal_leak=False,
                    sensitive_leak=False,
                    unsafe_patch=False,
                    model_evidence_complete=True,
                    exact_model=True,
                    exact_prompt=True,
                    infrastructure_failure=None,
                    failures=(),
                )
            )
    report = _report(
        rows,
        source={"revision": "a" * 40, "clean": True},
        source_stable=True,
        suite_fingerprint="b" * 64,
        database_revision="head",
        prompt_fingerprint="c" * 64,
        runtime_fingerprint="d" * 64,
    )
    assert report["qualified"] is True
    rows[0] = replace(rows[0], no_auto_apply=False)
    failed = _report(
        rows,
        source={"revision": "a" * 40, "clean": True},
        source_stable=True,
        suite_fingerprint="b" * 64,
        database_revision="head",
        prompt_fingerprint="c" * 64,
        runtime_fingerprint="d" * 64,
    )
    assert failed["qualified"] is False
    assert failed["metrics"]["auto_apply"] == 1


def test_goal_qualification_excludes_infrastructure_from_capability_rate() -> None:
    row = GoalTrialEvidence(
        task_id="task_1",
        family="read_only",
        trial_no=1,
        expected_path="goal",
        completed=False,
        passed=False,
        goal_observed=False,
        completion_observed=False,
        obligation_coverage=0.0,
        patch_present=False,
        no_auto_apply=True,
        public_contract_valid=False,
        internal_leak=False,
        sensitive_leak=False,
        unsafe_patch=False,
        model_evidence_complete=False,
        exact_model=False,
        exact_prompt=False,
        infrastructure_failure="provider_transport:provider_4xx",
        failures=("trial_not_completed",),
    )
    report = _report(
        [row],
        source={"revision": "a" * 40, "clean": True},
        source_stable=True,
        suite_fingerprint="b" * 64,
        database_revision="head",
        prompt_fingerprint="c" * 64,
        runtime_fingerprint="d" * 64,
    )

    assert report["qualification_outcome"] == "inconclusive_infrastructure"
    assert report["metrics"]["task_pass_rate"] is None
    assert report["metrics"]["capability_trial_count"] == 0
    assert _fatal_infrastructure_failure(row.infrastructure_failure) is True
    assert _fatal_infrastructure_failure("provider_transport:provider_timeout") is False
