from dataclasses import replace

from casefile.benchmark.chat_goal_qualification import GoalTrialEvidence, _report


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
