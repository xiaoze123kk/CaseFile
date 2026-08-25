"""M3.4-07e fail-closed Backend Release contract and report assembly."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SUITE = Path("fixtures/general_mutation_benchmark/release/v1/suite.json")
SUITE_VERSION = "casefile-general-mutation-backend-release-suite-v1"
REPORT_VERSION = "casefile-general-mutation-backend-release-report-v2"
HARNESS_VERSION = "general-mutation-backend-release-v2"
MODEL_ID = "deepseek-v4-pro"
TRIALS_PER_TASK = 3

FAULT_MATRIX = (
    "lease_timeout_recovery",
    "worker_interruption",
    "duplicate_finalize",
    "failure_before_persistence",
    "failure_after_persistence",
    "stale_resume",
    "sse_task_projection",
    "stale_patch_apply",
    "wrong_draft_apply",
    "confirmed_impact_hash_missing",
    "confirmed_impact_hash_tampered",
    "impact_changed_after_preview",
    "apply_idempotency",
    "undo_idempotency",
    "redo_idempotency",
    "revision_conflict",
    "concurrent_apply",
    "operation_selection_tamper",
    "protected_field_tamper",
    "provider_timeout",
)

_FAMILY_COUNTS = {
    "existing_update": 3,
    "create": 4,
    "delete": 3,
    "closure_sensitive": 3,
    "abstention_neighbor": 2,
}


class BackendReleaseContractError(ValueError):
    """Stable 07e setup or evidence-contract error."""


@dataclass(frozen=True, slots=True)
class ReleaseTask:
    task_id: str
    family: str
    expectation: Literal["apply", "abstain"]
    fixture: str
    message: str
    oracle: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ReleaseSuite:
    suite_id: str
    schema_version: str
    tasks: tuple[ReleaseTask, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class BackendTrialEvidence:
    task_id: str
    family: str
    expectation: str
    trial_index: int
    passed: bool
    classification: str
    infrastructure_failure: str | None
    safety_violations: tuple[str, ...]
    api_thread_created: bool
    api_message_enqueued: bool
    worker_claimed: bool
    task_succeeded: bool
    route_lineage_continuous: bool
    step_run_persisted: bool
    model_call_persisted: bool
    exact_model_observed: bool | None
    pending_before_approval: bool | None
    no_auto_apply: bool
    operations_persisted: bool | None
    proof_persisted: bool | None
    simulation_can_apply: bool | None
    delete_hash_gate_passed: bool | None
    apply_verified: bool | None
    final_state_oracle_passed: bool | None
    post_apply_verification_passed: bool | None
    undo_verified: bool | None
    redo_verified: bool | None
    revision_continuous: bool | None
    operation_sequence_continuous: bool | None
    audit_continuous: bool | None
    ownership_isolated: bool | None
    patch_set_count: int = 0
    draft_revision_before: int = 0
    draft_revision_after: int = 0
    model_call_count: int = 0
    route_source: str | None = None
    primary_intent: str | None = None
    failure_stage: str | None = None
    reason_code: str | None = None
    undo_http_status: int | None = None
    undo_reason_code: str | None = None
    undo_semantic_delta: Mapping[str, Any] | None = None
    redo_http_status: int | None = None
    redo_reason_code: str | None = None
    redo_semantic_delta: Mapping[str, Any] | None = None


class BackendReleaseExecutor(Protocol):
    database_schema_fingerprint: str

    def execute_trial(
        self, task: ReleaseTask, *, trial_index: int, model_id: str
    ) -> BackendTrialEvidence: ...

    def execute_fault(self, fault_id: str) -> Mapping[str, Any]: ...


def load_release_suite(repo_root: Path = ROOT, suite_path: Path | None = None) -> ReleaseSuite:
    path = (suite_path or repo_root / DEFAULT_SUITE).resolve()
    raw = _read_object(path)
    if set(raw) != {"schema_version", "suite_id", "tasks"}:
        raise BackendReleaseContractError("backend_release_suite_keys_invalid")
    if raw["schema_version"] != SUITE_VERSION:
        raise BackendReleaseContractError("backend_release_suite_version_invalid")
    rows = raw["tasks"]
    if not isinstance(rows, list) or len(rows) != 15:
        raise BackendReleaseContractError("backend_release_task_count_invalid")
    tasks = tuple(_load_task(path.parent, cast(Mapping[str, Any], row)) for row in rows)
    ids = [task.task_id for task in tasks]
    if len(ids) != len(set(ids)):
        raise BackendReleaseContractError("backend_release_task_id_duplicate")
    if Counter(task.family for task in tasks) != Counter(_FAMILY_COUNTS):
        raise BackendReleaseContractError("backend_release_family_distribution_invalid")
    if sum(task.expectation == "abstain" for task in tasks) != 2:
        raise BackendReleaseContractError("backend_release_abstention_count_invalid")
    return ReleaseSuite(
        suite_id=str(raw["suite_id"]),
        schema_version=SUITE_VERSION,
        tasks=tasks,
        fingerprint=_canonical_hash(raw),
    )


def run_backend_release(
    *,
    repo_root: Path,
    database_url: str,
    executor: BackendReleaseExecutor,
    suite_path: Path | None = None,
    trials: int = TRIALS_PER_TASK,
) -> dict[str, Any]:
    _require_test_database(database_url)
    source = _git_identity(repo_root)
    suite = load_release_suite(repo_root, suite_path)
    if trials != TRIALS_PER_TASK:
        raise BackendReleaseContractError("backend_release_trials_must_equal_three")
    if source["dirty"]:
        return _blocked_report(source, suite, executor.database_schema_fingerprint)
    rows = [
        executor.execute_trial(task, trial_index=trial, model_id=MODEL_ID)
        for task in suite.tasks
        for trial in range(1, trials + 1)
    ]
    faults = {fault_id: dict(executor.execute_fault(fault_id)) for fault_id in FAULT_MATRIX}
    return build_backend_release_report(
        source=source,
        suite=suite,
        rows=rows,
        faults=faults,
        database_schema_fingerprint=executor.database_schema_fingerprint,
        runtime_fingerprint=_runtime_fingerprint(repo_root),
    )


def build_backend_release_report(
    *,
    source: Mapping[str, Any],
    suite: ReleaseSuite,
    rows: Sequence[BackendTrialEvidence],
    faults: Mapping[str, Mapping[str, Any]],
    database_schema_fingerprint: str,
    runtime_fingerprint: str | None = None,
) -> dict[str, Any]:
    complete = len(rows) == 45 and {(row.task_id, row.trial_index) for row in rows} == {
        (task.task_id, trial) for task in suite.tasks for trial in range(1, 4)
    }
    missing_faults = sorted(set(FAULT_MATRIX) - set(faults))
    extra_faults = sorted(set(faults) - set(FAULT_MATRIX))
    if missing_faults or extra_faults:
        raise BackendReleaseContractError("backend_release_fault_matrix_keys_invalid")
    fault_failures = [
        fault_id for fault_id in FAULT_MATRIX if faults[fault_id].get("passed") is not True
    ]
    classes = Counter(_effective_classification(row) for row in rows)
    infrastructure_count = classes["infrastructure_failure"]
    safety_count = classes["safety_failure"]
    lifecycle_count = classes["lifecycle_failure"]
    capability_count = classes["capability_failure"]
    if infrastructure_count:
        outcome = "inconclusive_infrastructure"
    elif safety_count:
        outcome = "failed_safety"
    elif lifecycle_count or fault_failures:
        outcome = "failed_lifecycle"
    elif capability_count or not complete:
        outcome = "failed_capability"
    else:
        outcome = "passed"
    report: dict[str, Any] = {
        "schema_version": REPORT_VERSION,
        "harness_version": HARNESS_VERSION,
        "source": dict(source),
        "suite_id": suite.suite_id,
        "suite_fingerprint": suite.fingerprint,
        "provider": "deepseek",
        "model_id": MODEL_ID,
        "database_schema_fingerprint": database_schema_fingerprint,
        "runtime_fingerprint": runtime_fingerprint,
        "task_count": 15,
        "trials_per_task": 3,
        "trial_count": len(rows),
        "metrics": {
            "passed_trial_count": sum(row.passed for row in rows),
            "capability_failure_count": capability_count,
            "safety_failure_count": safety_count,
            "lifecycle_failure_count": lifecycle_count,
            "infrastructure_failure_count": infrastructure_count,
            "fault_matrix_failure_count": len(fault_failures),
            "classification_counts": dict(sorted(classes.items())),
        },
        "fault_matrix": dict(faults),
        "fault_matrix_failures": fault_failures,
        "rows": [asdict(row) for row in rows],
        "status": "passed" if outcome == "passed" else "failed",
        "qualification_outcome": outcome,
        "release_scope": "backend_general_mutation_suggest_create_update_delete",
        "no_auto_apply": all(row.no_auto_apply for row in rows),
        "rollout_mode_changed": False,
        "frontend_changed": False,
    }
    report["report_fingerprint"] = _canonical_hash(report)
    return report


def write_backend_release_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _effective_classification(row: BackendTrialEvidence) -> str:
    """Return one mutually exclusive terminal class, failing closed on stale rows."""

    if row.infrastructure_failure is not None or row.classification == "infrastructure_failure":
        return "infrastructure_failure"
    if row.safety_violations or row.classification == "safety_failure":
        return "safety_failure"
    if row.classification == "lifecycle_failure" or row.failure_stage in {
        "apply",
        "undo",
        "redo",
        "database",
        "worker",
        "lease",
    }:
        return "lifecycle_failure"
    if row.expectation == "apply" and not all(
        (
            row.api_thread_created,
            row.api_message_enqueued,
            row.worker_claimed,
            row.task_succeeded,
            row.route_lineage_continuous,
            row.step_run_persisted,
        )
    ):
        return "lifecycle_failure"
    if not row.passed:
        return "capability_failure"
    return row.classification


def main() -> None:
    parser = argparse.ArgumentParser(description="Run M3.4-07e Backend Release")
    parser.add_argument("--database-url")
    parser.add_argument("--api-key")
    parser.add_argument("--model", default=MODEL_ID)
    parser.add_argument("--trials", type=int, default=TRIALS_PER_TASK)
    parser.add_argument("--suite-path", type=Path)
    parser.add_argument("--report-path", type=Path, required=True)
    parser.add_argument("--gate-07e", action="store_true")
    args = parser.parse_args()
    if args.model != MODEL_ID:
        raise SystemExit("backend_release_model_invalid")
    database_url = args.database_url or os.environ.get("CASEFILE_TEST_DATABASE_URL", "")
    api_key = args.api_key or os.environ.get("CASEFILE_DEEPSEEK_API_KEY", "")
    if not database_url:
        raise SystemExit("backend_release_database_url_required")
    if not api_key:
        raise SystemExit("backend_release_credential_required")
    from casefile.benchmark.general_mutation_backend_executor import (
        PostgresBackendReleaseExecutor,
    )

    executor = PostgresBackendReleaseExecutor(database_url=database_url, api_key=api_key)
    try:
        report = run_backend_release(
            repo_root=ROOT,
            database_url=database_url,
            executor=executor,
            suite_path=args.suite_path,
            trials=args.trials,
        )
    finally:
        executor.close()
    write_backend_release_report(args.report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.gate_07e and report["qualification_outcome"] != "passed":
        raise SystemExit(1)


def _load_task(root: Path, row: Mapping[str, Any]) -> ReleaseTask:
    required = {"task_id", "family", "expectation", "source", "source_path"}
    if not required.issubset(row) or set(row) - (required | {"source_task_id"}):
        raise BackendReleaseContractError("backend_release_task_keys_invalid")
    source_path = (root / str(row["source_path"])).resolve()
    try:
        source_path.relative_to(ROOT)
    except ValueError as error:
        raise BackendReleaseContractError("backend_release_source_path_escape") from error
    source = str(row["source"])
    if source == "capability":
        raw = _read_object(source_path)
        fixture = str(cast(Mapping[str, Any], raw["input"])["fixture"])
        message = str(cast(Mapping[str, Any], raw["input"])["message"])
        oracle = cast(Mapping[str, Any], raw["oracle"])
    elif source == "safety":
        suite = _read_object(source_path)
        source_id = str(row.get("source_task_id", ""))
        candidates = [
            item
            for item in cast(Sequence[Mapping[str, Any]], suite["tasks"])
            if item.get("task_id") == source_id
        ]
        if len(candidates) != 1:
            raise BackendReleaseContractError("backend_release_safety_task_missing")
        safety_task = candidates[0]
        fixture = str(suite["fixture"])
        message = str(safety_task["message"])
        oracle = {
            "acceptable_statuses": ["safe_block"],
            "required_state": [],
            "forbidden_changes": [""],
        }
    else:
        raise BackendReleaseContractError("backend_release_source_invalid")
    expectation = str(row["expectation"])
    if expectation not in {"apply", "abstain"}:
        raise BackendReleaseContractError("backend_release_expectation_invalid")
    return ReleaseTask(
        task_id=str(row["task_id"]),
        family=str(row["family"]),
        expectation=cast(Literal["apply", "abstain"], expectation),
        fixture=fixture,
        message=message,
        oracle=oracle,
    )


def _blocked_report(
    source: Mapping[str, Any], suite: ReleaseSuite, database_schema_fingerprint: str
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": REPORT_VERSION,
        "harness_version": HARNESS_VERSION,
        "source": dict(source),
        "suite_id": suite.suite_id,
        "suite_fingerprint": suite.fingerprint,
        "model_id": MODEL_ID,
        "database_schema_fingerprint": database_schema_fingerprint,
        "task_count": 15,
        "trials_per_task": 3,
        "trial_count": 0,
        "metrics": None,
        "fault_matrix": {},
        "fault_matrix_failures": list(FAULT_MATRIX),
        "rows": [],
        "status": "blocked",
        "qualification_outcome": "blocked_preflight",
        "blocked_reason_code": "backend_release_git_must_be_clean",
        "release_scope": "backend_general_mutation_suggest_create_update_delete",
        "no_auto_apply": True,
        "rollout_mode_changed": False,
        "frontend_changed": False,
    }
    report["report_fingerprint"] = _canonical_hash(report)
    return report


def _require_test_database(database_url: str) -> None:
    try:
        name = make_url(database_url).database
    except Exception as error:
        raise BackendReleaseContractError("backend_release_database_url_invalid") from error
    if not name or not name.endswith("_test"):
        raise BackendReleaseContractError("backend_release_database_must_end_test")


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BackendReleaseContractError(f"backend_release_json_invalid:{path}") from error
    if not isinstance(value, dict):
        raise BackendReleaseContractError("backend_release_json_object_required")
    return value


def _git_identity(repo_root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise BackendReleaseContractError("backend_release_git_identity_unavailable")
        return result.stdout.strip()

    return {
        "revision": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(run("status", "--porcelain")),
    }


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _runtime_fingerprint(repo_root: Path) -> str:
    from casefile.benchmark.general_mutation_lineage import (
        general_mutation_runtime_fingerprint,
    )

    return general_mutation_runtime_fingerprint(repo_root)


__all__ = [
    "BackendReleaseContractError",
    "BackendReleaseExecutor",
    "BackendTrialEvidence",
    "DEFAULT_SUITE",
    "FAULT_MATRIX",
    "ReleaseSuite",
    "ReleaseTask",
    "build_backend_release_report",
    "load_release_suite",
    "run_backend_release",
    "write_backend_release_report",
]
