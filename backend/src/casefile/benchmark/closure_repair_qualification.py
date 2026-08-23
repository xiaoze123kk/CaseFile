"""Formal same-commit orchestration for Closure Repair Gate v2 qualification."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from casefile.benchmark.closure_repair_backend_release import run_backend_release_eval
from casefile.benchmark.closure_repair_capability import (
    DEFAULT_CAPABILITY_RELATIVE,
    GRADER_VERSION,
    load_capability_suite,
    run_capability_benchmark,
)
from casefile.benchmark.closure_repair_evidence import (
    build_evidence_index,
    write_evidence_index,
)
from casefile.benchmark.closure_repair_gate import evaluate_closure_repair_gate_v2
from casefile.benchmark.closure_repair_holdout import (
    HOLDOUT_SCHEMA_VERSION_V2,
    load_holdout_suite,
)
from casefile.benchmark.closure_repair_lineage import repair_runtime_fingerprint

MODEL_ID = "deepseek-v4-pro"
TRIALS_PER_TASK = 5
QUALIFICATION_VERSION = "closure-repair-qualification-v2"
_GATE_POLICY = Path(__file__).with_name("policies") / "closure-repair-gate-v2.json"


class QualificationError(ValueError):
    """Stable fail-closed formal qualification error."""


def qualification_preflight(
    *, repo_root: Path, holdout_suite_path: Path, database_url: str
) -> dict[str, Any]:
    root = repo_root.resolve()
    source = _git_identity(root)
    if source["dirty"]:
        raise QualificationError("qualification_git_must_be_clean")
    database = _database_name(database_url)
    if not database.endswith("_test"):
        raise QualificationError("qualification_database_must_end_test")
    capability = load_capability_suite(root)
    holdout = load_holdout_suite(holdout_suite_path.resolve())
    if holdout.schema_version != HOLDOUT_SCHEMA_VERSION_V2:
        raise QualificationError("qualification_holdout_v2_required")
    grader_module_file = __import__(
        "casefile.benchmark.closure_repair_capability", fromlist=["__file__"]
    ).__file__
    if not isinstance(grader_module_file, str):
        raise QualificationError("qualification_grader_path_unavailable")
    grader_path = Path(grader_module_file).resolve()
    return {
        "schema_version": QUALIFICATION_VERSION,
        "source_revision": source["revision"],
        "source_branch": source["branch"],
        "source_clean": True,
        "model_id": MODEL_ID,
        "database_name": database,
        "trials_per_task": TRIALS_PER_TASK,
        "clean_dev_task_count": len(capability.tasks),
        "clean_dev_trial_count": len(capability.tasks) * TRIALS_PER_TASK,
        "holdout_task_count": len(holdout.tasks),
        "holdout_trial_count": len(holdout.tasks) * TRIALS_PER_TASK,
        "backend_trial_count": 54,
        "capability_suite_fingerprint": capability.fingerprint,
        "holdout_suite_fingerprint": holdout.fingerprint,
        "holdout_oracle_fingerprint": holdout.metadata["oracle_fingerprint"],
        "holdout_review_fingerprint": holdout.metadata["review_fingerprint"],
        "holdout_release_cohort_fingerprint": holdout.metadata["release_cohort_fingerprint"],
        "gate_policy_fingerprint": sha256(_GATE_POLICY.read_bytes()).hexdigest(),
        "grader_version": GRADER_VERSION,
        "grader_fingerprint": _versioned_file_fingerprint(grader_path, GRADER_VERSION),
        "repair_runtime_fingerprint": repair_runtime_fingerprint(root),
    }


def run_formal_qualification(
    *,
    repo_root: Path,
    holdout_suite_path: Path,
    output_dir: Path,
    database_url: str,
    api_key: str,
) -> dict[str, Any]:
    from casefile.benchmark.closure_repair_backend_executor import (
        PostgresBackendReleaseExecutor,
    )

    if not api_key.strip():
        raise QualificationError("qualification_credential_missing")
    root = repo_root.resolve()
    output = output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise QualificationError("qualification_output_directory_not_empty")
    frozen = qualification_preflight(
        repo_root=root,
        holdout_suite_path=holdout_suite_path,
        database_url=database_url,
    )
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "qualification-manifest.json", frozen)

    clean_report_path = output / "clean-dev" / "report.json"
    clean_report = run_capability_benchmark(
        repo_root=root,
        model_id=MODEL_ID,
        api_key=api_key,
        trials=TRIALS_PER_TASK,
        suite_path=root / DEFAULT_CAPABILITY_RELATIVE,
        artifact_dir=clean_report_path.parent / "trials",
    ).as_dict()
    _assert_frozen_report(clean_report, frozen, stage="clean_dev")
    _write_json(clean_report_path, clean_report)
    clean_gate = evaluate_closure_repair_gate_v2(clean_report)
    _write_json(clean_report_path.parent / "gate.json", clean_gate)
    if not clean_gate["passed"]:
        raise QualificationError("qualification_clean_dev_gate_failed")

    holdout_suite = load_holdout_suite(holdout_suite_path.resolve())
    holdout_paths: list[Path] = []
    holdout_gate: dict[str, Any] | None = None
    for attempt in (1, 2):
        attempt_dir = output / "holdout" / f"attempt-{attempt:02d}"
        report_path = attempt_dir / "report.json"
        report = run_capability_benchmark(
            repo_root=root,
            model_id=MODEL_ID,
            api_key=api_key,
            trials=TRIALS_PER_TASK,
            suite_path=holdout_suite_path.resolve(),
            artifact_dir=attempt_dir / "trials",
        ).as_dict()
        _assert_frozen_report(report, frozen, stage="holdout")
        _write_json(report_path, report)
        holdout_paths.append(report_path)
        holdout_gate = evaluate_closure_repair_gate_v2(report)
        _write_json(attempt_dir / "gate.json", holdout_gate)
        if holdout_gate["passed"]:
            break
        if attempt == 1 and _infrastructure_failure_count(report) > 0:
            continue
        raise QualificationError("qualification_holdout_gate_failed")
    if holdout_gate is None or not holdout_gate["passed"]:
        raise QualificationError("qualification_holdout_gate_failed")

    executor = PostgresBackendReleaseExecutor(
        database_url=database_url,
        repair_api_key=api_key,
    )
    try:
        backend_report = run_backend_release_eval(
            repo_root=root,
            suite=holdout_suite,
            executor=executor,
            database_url=database_url,
            dev_gate_result=clean_gate,
            holdout_gate_result=holdout_gate,
        )
    finally:
        executor.close()
    backend_path = output / "backend-release" / "report.json"
    _write_json(backend_path, backend_report)
    if (
        backend_report.get("status") != "passed"
        or backend_report.get("qualification_outcome") != "passed"
    ):
        raise QualificationError("qualification_backend_release_failed")

    evidence = build_evidence_index(
        clean_dev_report=clean_report_path,
        holdout_reports=holdout_paths,
        backend_release_report=backend_path,
    )
    evidence_path = output / "evidence-index-v2.json"
    write_evidence_index(evidence_path, evidence)
    if not evidence["qualified"]:
        raise QualificationError("qualification_evidence_not_qualified")
    return evidence


def _assert_frozen_report(report: dict[str, Any], frozen: dict[str, Any], *, stage: str) -> None:
    expected_suite = frozen[
        "capability_suite_fingerprint" if stage == "clean_dev" else "holdout_suite_fingerprint"
    ]
    expected = {
        "model_id": frozen["model_id"],
        "trials_per_task": frozen["trials_per_task"],
        "suite_fingerprint": expected_suite,
        "grader_fingerprint": frozen["grader_fingerprint"],
        "repair_runtime_fingerprint": frozen["repair_runtime_fingerprint"],
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise QualificationError(f"qualification_{stage}_{key}_mismatch")
    source = report.get("source")
    if not isinstance(source, dict) or source.get("revision") != frozen["source_revision"]:
        raise QualificationError(f"qualification_{stage}_source_revision_mismatch")
    if source.get("dirty") is not False:
        raise QualificationError(f"qualification_{stage}_source_dirty")


def _git_identity(repo_root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise QualificationError("qualification_git_identity_unavailable")
        return result.stdout.strip()

    return {
        "revision": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(run("status", "--porcelain")),
    }


def _database_name(database_url: str) -> str:
    try:
        database = make_url(database_url).database
    except Exception as error:
        raise QualificationError("qualification_database_url_invalid") from error
    if not database:
        raise QualificationError("qualification_database_url_invalid")
    return database


def _versioned_file_fingerprint(path: Path, version: str) -> str:
    digest = sha256(version.encode("utf-8"))
    digest.update(path.name.encode("utf-8"))
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _infrastructure_failure_count(report: dict[str, Any]) -> int:
    metrics = report.get("metrics")
    value = metrics.get("infrastructure_failure_count") if isinstance(metrics, dict) else None
    return value if isinstance(value, int) else 0


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run M3.3 Closure Repair qualification v2")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--holdout-suite", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    try:
        database_url = args.database_url or os.environ.get("CASEFILE_TEST_DATABASE_URL", "")
        if not database_url:
            raise QualificationError("qualification_database_url_required")
        if args.preflight:
            result = qualification_preflight(
                repo_root=args.repo_root,
                holdout_suite_path=args.holdout_suite,
                database_url=database_url,
            )
        else:
            if args.output_dir is None:
                raise QualificationError("qualification_output_directory_required")
            api_key = os.environ.get("CASEFILE_DEEPSEEK_API_KEY", "")
            result = run_formal_qualification(
                repo_root=args.repo_root,
                holdout_suite_path=args.holdout_suite,
                output_dir=args.output_dir,
                database_url=database_url,
                api_key=api_key,
            )
    except QualificationError as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


__all__ = [
    "MODEL_ID",
    "QUALIFICATION_VERSION",
    "QualificationError",
    "qualification_preflight",
    "run_formal_qualification",
]
