from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from casefile.benchmark.closure_repair_backend_release import (
    FAULT_MATRIX,
    BackendReleaseContractError,
    BackendTrialEvidence,
    run_backend_release_eval,
)
from casefile.benchmark.eval_core import EvalSuite, EvalTask


def _suite() -> EvalSuite:
    tasks = tuple(
        EvalTask(
            task_id=f"agent_{family}_{index}",
            policy_key=(family, "repair_required"),
            automation="agent",
            input={"primary_mutation": {"operation_type": "update_field"}},
            oracle={},
            reference_path="reference.json",
            tags=(),
            difficulty=("basic", "alternative", "dense")[index],
            topology=f"topology_{index}",
        )
        for family in (
            "claim_dependency_incompatible",
            "claim_refuted_without_refutation",
            "claim_supported_without_support",
        )
        for index in range(3)
    ) + tuple(
        EvalTask(
            task_id=f"abstention_{index}",
            policy_key=(f"manual_rule_{index}", "repair_required"),
            automation="manual" if index < 5 else "ineligible",
            input={"primary_mutation": {"operation_type": "update_field"}},
            oracle={},
            reference_path="reference.json",
            tags=(),
        )
        for index in range(9)
    )
    ids = tuple(task.task_id for task in tasks)
    return EvalSuite(
        suite_id="closure-repair-capability-holdout-v2",
        suite_kind="capability",
        schema_version="casefile-closure-repair-holdout-v2",
        tasks=tasks,
        fingerprint="suite-fingerprint",
        suite_role="holdout",
        metadata={
            "release_cohort": ids,
            "release_cohort_fingerprint": "cohort-fingerprint",
            "oracle_fingerprint": "oracle-fingerprint",
            "review_fingerprint": "review-fingerprint",
            "gate_policy_version": "closure-repair-gate-v2",
        },
    )


def _evidence(task: EvalTask, trial_index: int) -> BackendTrialEvidence:
    agent = task.automation == "agent"
    return BackendTrialEvidence(
        task_id=task.task_id,
        trial_index=trial_index,
        automation=task.automation,
        family=task.policy_key[0],
        passed=True,
        provider_invoked=agent,
        infrastructure_failure=None,
        safety_violations=(),
        api_enqueued=True,
        worker_executed=True,
        step_run_persisted=True,
        model_call_persisted=True,
        policy_decision_matches=True,
        shadow_has_no_companion=True,
        suggest_replay_provenance=True,
        partial_selection_rejected=True,
        full_patch_simulates=True,
        apply_verified=True,
        undo_verified=True,
        redo_verified=True,
        audit_continuous=True,
        stale_rejected=True,
        duplicate_apply_rejected=True,
        illegal_selection_rejected=True,
    )


class _Executor:
    database_schema_fingerprint = "schema-fingerprint"
    supported_primary_operation_types = frozenset({"update_field"})

    def __init__(self) -> None:
        self.mutate: Any = None
        self.failed_fault: str | None = None

    def execute_trial(
        self, task: EvalTask, *, trial_index: int, repair_model: str
    ) -> BackendTrialEvidence:
        assert repair_model == "deepseek-v4-pro"
        value = _evidence(task, trial_index)
        return value if self.mutate is None else self.mutate(value)

    def execute_fault(self, fault_id: str) -> dict[str, Any]:
        return {"passed": fault_id != self.failed_fault, "evidence": fault_id}


@pytest.fixture(autouse=True)
def _clean_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "casefile.benchmark.closure_repair_backend_release._git_identity",
        lambda _root: {"revision": "a" * 40, "branch": "codex/m3-3", "dirty": False},
    )
    monkeypatch.setattr(
        "casefile.benchmark.closure_repair_backend_release.repair_runtime_fingerprint",
        lambda _root: "runtime-fingerprint",
    )


def _gate(*, status: str = "passed") -> dict[str, Any]:
    return {
        "status": status,
        "gate_version": "closure-repair-gate-v2",
        "source_revision": "a" * 40,
        "repair_runtime_fingerprint": "runtime-fingerprint",
    }


def test_backend_release_report_passes_only_complete_production_evidence() -> None:
    report = run_backend_release_eval(
        repo_root=Path.cwd(),
        suite=_suite(),
        executor=_Executor(),
        database_url="postgresql+psycopg://casefile:casefile@localhost/casefile_test",
        dev_gate_result=_gate(),
        holdout_gate_result=_gate(),
    )

    assert report["evaluation_scope"] == "api_worker_postgres"
    assert report["trial_count"] == 54
    assert report["release_gate_eligible"] is True
    assert report["status"] == "passed"
    assert set(report["fault_matrix"]) == set(FAULT_MATRIX)


@pytest.mark.parametrize(
    ("mutation", "expected_status", "expected_outcome"),
    (
        (lambda value: replace(value, apply_verified=False), "failed", "failed_capability"),
        (
            lambda value: replace(value, safety_violations=("scope_escape",)),
            "failed",
            "failed_capability",
        ),
        (
            lambda value: replace(value, infrastructure_failure="worker_crash", passed=False),
            "blocked",
            "inconclusive_infrastructure",
        ),
    ),
)
def test_backend_release_report_fails_closed_on_trial_evidence(
    mutation: Any, expected_status: str, expected_outcome: str
) -> None:
    executor = _Executor()
    executor.mutate = mutation
    report = run_backend_release_eval(
        repo_root=Path.cwd(),
        suite=_suite(),
        executor=executor,
        database_url="postgresql://casefile:casefile@localhost/casefile_test",
        dev_gate_result=_gate(),
        holdout_gate_result=_gate(),
    )

    assert report["release_gate_eligible"] is False
    assert report["status"] == expected_status
    assert report["qualification_outcome"] == expected_outcome


def test_backend_release_requires_clean_dev_gate_and_complete_fault_matrix() -> None:
    executor = _Executor()
    executor.failed_fault = "stale_resume"
    report = run_backend_release_eval(
        repo_root=Path.cwd(),
        suite=_suite(),
        executor=executor,
        database_url="postgresql://casefile:casefile@localhost/casefile_test",
        dev_gate_result=_gate(),
        holdout_gate_result=_gate(),
    )

    assert report["release_gate_eligible"] is False
    assert report["fault_matrix_failures"] == ["stale_resume"]


def test_backend_release_blocks_before_execution_when_clean_dev_gate_failed() -> None:
    executor = _Executor()
    report = run_backend_release_eval(
        repo_root=Path.cwd(),
        suite=_suite(),
        executor=executor,
        database_url="postgresql://casefile:casefile@localhost/casefile_test",
        dev_gate_result=_gate(status="failed"),
        holdout_gate_result=_gate(),
    )

    assert report["status"] == "blocked"
    assert report["blocked_reason_code"] == "clean_dev_gate_not_passed"
    assert report["rows"] == []


def test_backend_release_rejects_non_disposable_database() -> None:
    with pytest.raises(BackendReleaseContractError, match="must_end_test"):
        run_backend_release_eval(
            repo_root=Path.cwd(),
            suite=_suite(),
            executor=_Executor(),
            database_url="postgresql://casefile:casefile@localhost/casefile",
            dev_gate_result=_gate(),
            holdout_gate_result=_gate(),
        )


def test_backend_release_blocks_unsupported_primary_mutation_before_execution() -> None:
    suite = _suite()
    changed = replace(
        suite.tasks[0],
        input={"primary_mutation": {"operation_type": "create_object"}},
    )
    suite = replace(suite, tasks=(changed, *suite.tasks[1:]))
    executor = _Executor()
    executor.supported_primary_operation_types = frozenset({"update_field"})

    report = run_backend_release_eval(
        repo_root=Path.cwd(),
        suite=suite,
        executor=executor,
        database_url="postgresql://casefile:casefile@localhost/casefile_test",
        dev_gate_result=_gate(status="failed"),
        holdout_gate_result=_gate(),
    )

    assert report["status"] == "blocked"
    assert report["trial_count"] == 0
    assert report["release_gate_eligible"] is False
    assert [item["reason_code"] for item in report["blocked_details"]["blockers"]] == [
        "clean_dev_gate_not_passed",
        "production_primary_mutation_contract_unsupported",
    ]
