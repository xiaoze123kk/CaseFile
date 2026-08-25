from __future__ import annotations

from dataclasses import replace

import pytest

from casefile.benchmark.general_mutation_backend_release import (
    FAULT_MATRIX,
    BackendReleaseContractError,
    BackendTrialEvidence,
    build_backend_release_report,
    load_release_suite,
)


def _row(task_id: str, family: str, trial: int) -> BackendTrialEvidence:
    abstention = family == "abstention_neighbor"
    return BackendTrialEvidence(
        task_id=task_id,
        family=family,
        expectation="abstain" if abstention else "apply",
        trial_index=trial,
        passed=True,
        classification="safe_block" if abstention else "success",
        infrastructure_failure=None,
        safety_violations=(),
        api_thread_created=True,
        api_message_enqueued=True,
        worker_claimed=True,
        task_succeeded=True,
        route_lineage_continuous=True,
        step_run_persisted=True,
        model_call_persisted=not abstention,
        exact_model_observed=None if abstention else True,
        pending_before_approval=None if abstention else True,
        no_auto_apply=True,
        operations_persisted=None if abstention else True,
        proof_persisted=None if abstention else True,
        simulation_can_apply=None if abstention else True,
        delete_hash_gate_passed=None if abstention else True,
        apply_verified=None if abstention else True,
        final_state_oracle_passed=None if abstention else True,
        post_apply_verification_passed=None if abstention else True,
        undo_verified=None if abstention else True,
        redo_verified=None if abstention else True,
        revision_continuous=None if abstention else True,
        operation_sequence_continuous=None if abstention else True,
        audit_continuous=None if abstention else True,
        ownership_isolated=None if abstention else True,
        patch_set_count=0 if abstention else 1,
        draft_revision_before=2,
        draft_revision_after=2 if abstention else 5,
        model_call_count=0 if abstention else 1,
    )


def test_release_suite_freezes_15_task_distribution() -> None:
    suite = load_release_suite()
    assert len(suite.tasks) == 15
    assert sum(task.expectation == "abstain" for task in suite.tasks) == 2
    assert {task.family for task in suite.tasks} == {
        "existing_update",
        "create",
        "delete",
        "closure_sensitive",
        "abstention_neighbor",
    }


def test_release_report_requires_all_45_rows_and_20_faults() -> None:
    suite = load_release_suite()
    rows = [_row(task.task_id, task.family, trial) for task in suite.tasks for trial in range(1, 4)]
    report = build_backend_release_report(
        source={"revision": "a" * 40, "branch": "codex/test", "dirty": False},
        suite=suite,
        rows=rows,
        faults={fault_id: {"passed": True} for fault_id in FAULT_MATRIX},
        database_schema_fingerprint="b" * 64,
    )
    assert report["qualification_outcome"] == "passed"
    assert report["trial_count"] == 45
    assert report["metrics"]["fault_matrix_failure_count"] == 0
    assert report["no_auto_apply"] is True
    abstention_rows = [row for row in report["rows"] if row["expectation"] == "abstain"]
    assert all(row["patch_set_count"] == row["model_call_count"] == 0 for row in abstention_rows)
    assert all(row["apply_verified"] is None for row in abstention_rows)


@pytest.mark.parametrize(
    ("mutation", "outcome"),
    [
        ({"infrastructure_failure": "timeout", "passed": False}, "inconclusive_infrastructure"),
        ({"safety_violations": ("escape",), "passed": False}, "failed_safety"),
        ({"route_lineage_continuous": False, "passed": False}, "failed_lifecycle"),
        ({"final_state_oracle_passed": False, "passed": False}, "failed_capability"),
    ],
)
def test_release_outcome_precedence(mutation: dict[str, object], outcome: str) -> None:
    suite = load_release_suite()
    rows = [_row(task.task_id, task.family, trial) for task in suite.tasks for trial in range(1, 4)]
    rows[0] = replace(rows[0], **mutation)
    report = build_backend_release_report(
        source={"revision": "a" * 40, "branch": "codex/test", "dirty": False},
        suite=suite,
        rows=rows,
        faults={fault_id: {"passed": True} for fault_id in FAULT_MATRIX},
        database_schema_fingerprint="b" * 64,
    )
    assert report["qualification_outcome"] == outcome
    assert sum(
        report["metrics"][key]
        for key in (
            "capability_failure_count",
            "safety_failure_count",
            "lifecycle_failure_count",
            "infrastructure_failure_count",
        )
    ) == 1


def test_release_report_rejects_fault_key_drift() -> None:
    suite = load_release_suite()
    with pytest.raises(BackendReleaseContractError, match="fault_matrix_keys_invalid"):
        build_backend_release_report(
            source={"revision": "a" * 40, "branch": "codex/test", "dirty": False},
            suite=suite,
            rows=[],
            faults={},
            database_schema_fingerprint="b" * 64,
        )
