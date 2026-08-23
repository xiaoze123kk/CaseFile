"""Fail-closed Backend Release Eval contract for Closure Repair."""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.engine import make_url

from casefile.agent_runtime import (
    CLOSURE_REPAIR_AGENT_VERSION,
    CLOSURE_REPAIR_PROMPT_VERSION,
    CLOSURE_REPAIR_SCHEMA_ID,
)
from casefile.benchmark.closure_repair_gate import evaluate_backend_shadow_gate
from casefile.benchmark.eval_core import EvalSuite, EvalTask
from casefile.domain.logical_mutation import ACTIVE_APPLY_POLICY
from casefile.domain.logical_mutation.repair import REPAIR_CONTEXT_V2, REPAIR_POLICY_V1

BACKEND_RELEASE_VERSION = "closure-repair-backend-release-v1"
BACKEND_RELEASE_REPORT_VERSION = "casefile-closure-repair-backend-release-report-v1"
PRIMARY_PROVIDER = "deterministic-fixture"
REPAIR_PROVIDER = "deepseek"
REPAIR_MODEL = "deepseek-v4-pro"
TRIALS_PER_TASK = 3

FAULT_MATRIX = (
    "lease_timeout_recovery",
    "worker_interruption",
    "duplicate_finalize",
    "failure_before_persistence",
    "failure_after_persistence",
    "stale_resume",
    "sse_task_projection",
    "apply_idempotency",
    "undo_idempotency",
    "redo_idempotency",
    "revision_conflict",
)


class BackendReleaseContractError(ValueError):
    """Stable release-eval setup or evidence contract error."""


@dataclass(frozen=True, slots=True)
class BackendTrialEvidence:
    task_id: str
    trial_index: int
    automation: str
    family: str
    passed: bool
    provider_invoked: bool
    infrastructure_failure: str | None
    safety_violations: tuple[str, ...]
    api_enqueued: bool
    worker_executed: bool
    step_run_persisted: bool
    model_call_persisted: bool
    policy_decision_matches: bool
    shadow_has_no_companion: bool
    suggest_replay_provenance: bool
    partial_selection_rejected: bool
    full_patch_simulates: bool
    apply_verified: bool
    undo_verified: bool
    redo_verified: bool
    audit_continuous: bool
    stale_rejected: bool
    duplicate_apply_rejected: bool
    illegal_selection_rejected: bool


class BackendReleaseExecutor(Protocol):
    """Production-path adapter; implementations own API/Worker/PostgreSQL orchestration."""

    database_schema_fingerprint: str
    supported_primary_operation_types: frozenset[str]

    def execute_trial(
        self, task: EvalTask, *, trial_index: int, repair_model: str
    ) -> BackendTrialEvidence: ...

    def execute_fault(self, fault_id: str) -> Mapping[str, Any]: ...


def run_backend_release_eval(
    *,
    repo_root: Path,
    suite: EvalSuite,
    executor: BackendReleaseExecutor,
    database_url: str,
    dev_gate_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Run the frozen 18-task cohort and deterministic production failure matrix."""

    _require_test_database(database_url)
    cohort = _release_cohort(suite)
    unsupported = _unsupported_primary_operations(
        cohort, supported=executor.supported_primary_operation_types
    )
    blockers: list[dict[str, Any]] = []
    if dev_gate_result.get("status") != "passed":
        blockers.append({"reason_code": "clean_dev_gate_not_passed"})
    if unsupported:
        blockers.append(
            {
                "reason_code": "production_primary_mutation_contract_unsupported",
                "unsupported_primary_mutations": [dict(item) for item in unsupported],
            }
        )
    if blockers:
        return _blocked_release_report(
            repo_root,
            suite,
            database_schema_fingerprint=executor.database_schema_fingerprint,
            dev_gate_result=dev_gate_result,
            blockers=blockers,
        )
    rows = [
        executor.execute_trial(task, trial_index=trial, repair_model=REPAIR_MODEL)
        for task in cohort
        for trial in range(1, TRIALS_PER_TASK + 1)
    ]
    faults = {fault_id: dict(executor.execute_fault(fault_id)) for fault_id in FAULT_MATRIX}
    return _release_report(
        repo_root,
        suite,
        rows,
        faults,
        database_schema_fingerprint=executor.database_schema_fingerprint,
        dev_gate_result=dev_gate_result,
    )


def _unsupported_primary_operations(
    cohort: Sequence[EvalTask], *, supported: frozenset[str]
) -> tuple[dict[str, str], ...]:
    unsupported: list[dict[str, str]] = []
    for task in cohort:
        primary = task.input.get("primary_mutation")
        operation_type = primary.get("operation_type") if isinstance(primary, Mapping) else None
        if not isinstance(operation_type, str):
            raise BackendReleaseContractError("backend_release_primary_mutation_invalid")
        if operation_type not in supported:
            unsupported.append({"task_id": task.task_id, "operation_type": operation_type})
    return tuple(unsupported)


def _blocked_release_report(
    repo_root: Path,
    suite: EvalSuite,
    *,
    database_schema_fingerprint: str,
    dev_gate_result: Mapping[str, Any],
    blockers: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    source = _git_identity(repo_root)
    report: dict[str, Any] = {
        **_report_provenance(
            source=source,
            suite=suite,
            database_schema_fingerprint=database_schema_fingerprint,
        ),
        "task_count": 18,
        "trials_per_task": TRIALS_PER_TASK,
        "trial_count": 0,
        "metrics": None,
        "cohort_gate": None,
        "clean_dev_gate": dict(dev_gate_result),
        "fault_matrix": {},
        "fault_matrix_failures": [],
        "rows": [],
        "release_gate_eligible": False,
        "status": "blocked",
        "blocked_reason_code": str(blockers[0]["reason_code"]),
        "blocked_details": {
            "blockers": [dict(item) for item in blockers],
            "required_action": "Resolve every frozen precondition before running 54 trials.",
        },
        "rollout_mode_changed": False,
        "frontend_suggest_enabled": False,
    }
    report["report_fingerprint"] = sha256(_canonical_bytes(report)).hexdigest()
    return report


def _require_test_database(database_url: str) -> None:
    try:
        database = make_url(database_url).database
    except Exception as error:
        raise BackendReleaseContractError("backend_release_database_url_invalid") from error
    if not database or not database.endswith("_test"):
        raise BackendReleaseContractError("backend_release_database_must_end_test")


def _release_cohort(suite: EvalSuite) -> tuple[EvalTask, ...]:
    raw = suite.metadata.get("release_cohort")
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes) or len(raw) != 18:
        raise BackendReleaseContractError("backend_release_cohort_invalid")
    by_id = {task.task_id: task for task in suite.tasks}
    try:
        cohort = tuple(by_id[str(task_id)] for task_id in raw)
    except KeyError as error:
        raise BackendReleaseContractError("backend_release_cohort_unknown") from error
    automation = Counter(task.automation for task in cohort)
    family = Counter(task.policy_key[0] for task in cohort if task.automation == "agent")
    if automation["agent"] != 9 or automation["manual"] + automation["ineligible"] != 9:
        raise BackendReleaseContractError("backend_release_cohort_classes_invalid")
    if set(family.values()) != {3} or len(family) != 3:
        raise BackendReleaseContractError("backend_release_cohort_families_invalid")
    return cohort


def _release_report(
    repo_root: Path,
    suite: EvalSuite,
    rows: Sequence[BackendTrialEvidence],
    faults: Mapping[str, Mapping[str, Any]],
    *,
    database_schema_fingerprint: str,
    dev_gate_result: Mapping[str, Any],
) -> dict[str, Any]:
    source = _git_identity(repo_root)
    lifecycle_fields = (
        "api_enqueued",
        "worker_executed",
        "step_run_persisted",
        "policy_decision_matches",
        "shadow_has_no_companion",
        "stale_rejected",
        "duplicate_apply_rejected",
        "illegal_selection_rejected",
    )
    agent_fields = (
        "model_call_persisted",
        "suggest_replay_provenance",
        "partial_selection_rejected",
        "full_patch_simulates",
        "apply_verified",
        "undo_verified",
        "redo_verified",
        "audit_continuous",
    )
    complete = len(rows) == 54 and all(
        1 <= row.trial_index <= TRIALS_PER_TASK for row in rows
    )
    lifecycle_failures = {
        field: sum(not getattr(row, field) for row in rows) for field in lifecycle_fields
    }
    agent_rows = [row for row in rows if row.automation == "agent"]
    lifecycle_failures.update(
        {field: sum(not getattr(row, field) for row in agent_rows) for field in agent_fields}
    )
    fault_failures = sorted(
        fault_id for fault_id in FAULT_MATRIX if faults.get(fault_id, {}).get("passed") is not True
    )
    capability_report = _capability_projection(rows, source=source)
    cohort_gate = evaluate_backend_shadow_gate(capability_report)
    dev_gate_passed = dev_gate_result.get("status") == "passed"
    all_passed = bool(
        complete
        and not source["dirty"]
        and all(row.passed for row in rows)
        and not any(lifecycle_failures.values())
        and not fault_failures
        and cohort_gate["status"] == "passed"
        and dev_gate_passed
    )
    report = {
        "schema_version": BACKEND_RELEASE_REPORT_VERSION,
        "harness_version": BACKEND_RELEASE_VERSION,
        "evaluation_scope": "api_worker_postgres",
        "release_scope": "backend_shadow_suggest_core",
        "source": source,
        "suite_id": suite.suite_id,
        "suite_fingerprint": suite.fingerprint,
        "oracle_fingerprint": suite.metadata.get("oracle_fingerprint"),
        "review_fingerprint": suite.metadata.get("review_fingerprint"),
        "release_cohort_fingerprint": suite.metadata.get("release_cohort_fingerprint"),
        "gate_policy_version": suite.metadata.get("gate_policy_version"),
        "prompt_version": CLOSURE_REPAIR_PROMPT_VERSION,
        "agent_version": CLOSURE_REPAIR_AGENT_VERSION,
        "output_schema_id": CLOSURE_REPAIR_SCHEMA_ID,
        "context_version": REPAIR_CONTEXT_V2,
        "closure_policy_version": ACTIVE_APPLY_POLICY,
        "repair_policy_version": REPAIR_POLICY_V1,
        "primary_provider": PRIMARY_PROVIDER,
        "repair_provider": REPAIR_PROVIDER,
        "repair_model_id": REPAIR_MODEL,
        "database_schema_fingerprint": database_schema_fingerprint,
        "task_count": 18,
        "trials_per_task": TRIALS_PER_TASK,
        "trial_count": len(rows),
        "metrics": {
            "passed_trial_count": sum(row.passed for row in rows),
            "unsafe_trial_count": sum(bool(row.safety_violations) for row in rows),
            "provider_mistakenly_invoked_count": sum(
                row.provider_invoked for row in rows if row.automation != "agent"
            ),
            "infrastructure_failure_count": sum(
                row.infrastructure_failure is not None for row in rows
            ),
            "lifecycle_failure_counts": lifecycle_failures,
            "fault_matrix_failure_count": len(fault_failures),
        },
        "cohort_gate": cohort_gate,
        "clean_dev_gate": dict(dev_gate_result),
        "fault_matrix": dict(faults),
        "fault_matrix_failures": fault_failures,
        "rows": [asdict(row) for row in rows],
        "release_gate_eligible": all_passed,
        "status": "passed" if all_passed else "failed",
        "rollout_mode_changed": False,
        "frontend_suggest_enabled": False,
    }
    report["report_fingerprint"] = sha256(_canonical_bytes(report)).hexdigest()
    return report


def _report_provenance(
    *, source: Mapping[str, Any], suite: EvalSuite, database_schema_fingerprint: str
) -> dict[str, Any]:
    return {
        "schema_version": BACKEND_RELEASE_REPORT_VERSION,
        "harness_version": BACKEND_RELEASE_VERSION,
        "evaluation_scope": "api_worker_postgres",
        "release_scope": "backend_shadow_suggest_core",
        "source": dict(source),
        "suite_id": suite.suite_id,
        "suite_fingerprint": suite.fingerprint,
        "oracle_fingerprint": suite.metadata.get("oracle_fingerprint"),
        "review_fingerprint": suite.metadata.get("review_fingerprint"),
        "release_cohort_fingerprint": suite.metadata.get("release_cohort_fingerprint"),
        "gate_policy_version": suite.metadata.get("gate_policy_version"),
        "prompt_version": CLOSURE_REPAIR_PROMPT_VERSION,
        "agent_version": CLOSURE_REPAIR_AGENT_VERSION,
        "output_schema_id": CLOSURE_REPAIR_SCHEMA_ID,
        "context_version": REPAIR_CONTEXT_V2,
        "closure_policy_version": ACTIVE_APPLY_POLICY,
        "repair_policy_version": REPAIR_POLICY_V1,
        "primary_provider": PRIMARY_PROVIDER,
        "repair_provider": REPAIR_PROVIDER,
        "repair_model_id": REPAIR_MODEL,
        "database_schema_fingerprint": database_schema_fingerprint,
    }


def _capability_projection(
    rows: Sequence[BackendTrialEvidence], *, source: Mapping[str, Any]
) -> dict[str, Any]:
    projected = [
        {
            "task_id": row.task_id,
            "trial_index": row.trial_index,
            "passed": row.passed,
            "transcript": {
                "input_summary": {
                    "automation": row.automation,
                    "policy_key": [row.family, "repair_required"],
                }
            },
        }
        for row in rows
    ]
    agent = [row for row in rows if row.automation == "agent"]
    return {
        "status": "completed",
        "source": dict(source),
        "provider": REPAIR_PROVIDER,
        "model_id": REPAIR_MODEL,
        "trials_per_task": TRIALS_PER_TASK,
        "task_count": 18,
        "trial_count": len(rows),
        "comparison_fingerprint": "release-cohort",
        "metrics": {
            "capability": {
                "task_macro_pass_at_1": (
                    round(sum(row.passed for row in agent) / len(agent), 6) if agent else 0.0
                )
            },
            "abstention": {
                "correct_abstention_rate": _rate(
                    sum(row.passed for row in rows if row.automation != "agent"),
                    sum(row.automation != "agent" for row in rows),
                ),
                "provider_mistakenly_invoked_count": sum(
                    row.provider_invoked for row in rows if row.automation != "agent"
                ),
            },
            "safety": {
                "unsafe_trial_count": sum(bool(row.safety_violations) for row in rows),
                "violation_counts": dict(
                    Counter(value for row in rows for value in row.safety_violations)
                ),
            },
            "efficiency": {"protocol_repair_count": 0},
            "infrastructure_failure_count": sum(
                row.infrastructure_failure is not None for row in rows
            ),
        },
        "rows": projected,
    }


def write_backend_release_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _git_identity(repo_root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False
        ).stdout.strip()

    return {
        "revision": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(run("status", "--porcelain")),
    }


__all__ = [
    "BACKEND_RELEASE_REPORT_VERSION",
    "BACKEND_RELEASE_VERSION",
    "FAULT_MATRIX",
    "BackendReleaseContractError",
    "BackendReleaseExecutor",
    "BackendTrialEvidence",
    "run_backend_release_eval",
    "write_backend_release_report",
]
