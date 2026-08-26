"""Canonical evidence index for M3.4-07f qualification."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

import rfc8785

EVIDENCE_INDEX_VERSION = "casefile-general-mutation-evidence-index-v1"
RELEASE_SCOPE = "backend_general_mutation_suggest_create_update_delete"
EXPECTED_STAGES = {
    "s0",
    "capability_dev",
    "holdout",
    "safety_abstention",
    "backend_release",
}
STAGE_CONTRACTS = {
    "capability_dev": ("capability_suite_fingerprint", "capability_dev_task_count", 5),
    "holdout": ("holdout_suite_fingerprint", "holdout_task_count", 5),
    "safety_abstention": ("safety_suite_fingerprint", "safety_task_count", 5),
    "backend_release": ("release_suite_fingerprint", "backend_release_task_count", 3),
}


class EvidenceIndexError(ValueError):
    """Stable fail-closed evidence error."""


def canonical_sha256(value: Any) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def build_evidence_index(
    *,
    manifest_path: Path,
    stage_paths: Sequence[tuple[str, int, Path]],
    blocked_reason_code: str | None = None,
    blocked_reason_codes: Sequence[str] = (),
    diagnostic_paths: Sequence[Path] = (),
) -> dict[str, Any]:
    manifest = _read_object(manifest_path)
    evidence_root = manifest_path.resolve().parent
    entries: list[dict[str, Any]] = []
    reports: dict[str, list[dict[str, Any]]] = {}
    attempt_numbers: dict[str, list[int]] = {}
    diagnostics: list[dict[str, Any]] = []
    for stage, attempt, path in stage_paths:
        report = _read_object(path)
        reports.setdefault(stage, []).append(report)
        attempt_numbers.setdefault(stage, []).append(attempt)
        entries.append(
            {
                "stage": stage,
                "attempt": attempt,
                "path": _relative_artifact_path(path, evidence_root),
                "canonical_sha256": canonical_sha256(report),
                "status": report.get("status"),
                "qualification_outcome": _stage_outcome(stage, report),
                "infrastructure_failure_count": _infrastructure_count(report),
            }
        )
    for path in diagnostic_paths:
        diagnostic = _read_object(path)
        diagnostics.append(
            {
                "path": _relative_artifact_path(path, evidence_root),
                "canonical_sha256": canonical_sha256(diagnostic),
                "reason_code": diagnostic.get("reason_code"),
                "error_type": diagnostic.get("error_type"),
            }
        )
    _validate_attempts(reports, attempt_numbers)
    _validate_stage_order(stage_paths)
    integrity_blockers = [
        *_manifest_contract_blockers(manifest),
        *_manifest_integrity_blockers(manifest, reports),
    ]
    complete = EXPECTED_STAGES.issubset(reports)
    same_revision = all(
        _source_revision(stage, report) == manifest.get("source_revision")
        for stage, attempts in reports.items()
        for report in attempts
    )
    release_report = reports.get("backend_release", [{}])[-1]
    no_auto_apply = release_report.get("no_auto_apply")
    rollout_mode_changed = release_report.get("rollout_mode_changed")
    release_metrics = release_report.get("metrics")
    fault_matrix_failure_count = (
        release_metrics.get("fault_matrix_failure_count")
        if isinstance(release_metrics, Mapping)
        else None
    )
    supplied_blockers = [*blocked_reason_codes]
    if blocked_reason_code:
        supplied_blockers.append(blocked_reason_code)
    passed = bool(
        not supplied_blockers
        and complete
        and same_revision
        and not integrity_blockers
        and _last_passed(reports, "s0")
        and _last_passed(reports, "capability_dev")
        and _last_passed(reports, "holdout")
        and _last_passed(reports, "safety_abstention")
        and _last_passed(reports, "backend_release")
        and no_auto_apply is True
        and rollout_mode_changed is False
        and fault_matrix_failure_count == 0
    )
    blockers = list(supplied_blockers)
    if not complete:
        blockers.extend(
            f"qualification_stage_missing:{stage}"
            for stage in sorted(EXPECTED_STAGES - set(reports))
        )
    if not same_revision:
        blockers.append("qualification_source_revision_mismatch")
    if complete and no_auto_apply is not True:
        blockers.append("qualification_backend_release_auto_apply_not_disproven")
    if complete and rollout_mode_changed is not False:
        blockers.append("qualification_backend_release_rollout_change_not_disproven")
    if complete and fault_matrix_failure_count != 0:
        blockers.append("qualification_backend_release_fault_matrix_failed")
    blockers.extend(integrity_blockers)
    for stage, attempts in reports.items():
        if not _stage_passed(stage, attempts[-1]):
            blockers.append(f"qualification_{stage}_gate_failed")
    index: dict[str, Any] = {
        "schema_version": EVIDENCE_INDEX_VERSION,
        "source_revision": manifest.get("source_revision"),
        "manifest_canonical_sha256": canonical_sha256(manifest),
        "runtime_fingerprint": manifest.get("runtime_fingerprint"),
        "artifacts": entries,
        "diagnostics": diagnostics,
        "qualification_outcome": "passed" if passed else "failed",
        "qualified": passed,
        "release_scope": RELEASE_SCOPE,
        "blockers": sorted(set(blockers)),
        "rollout_mode_changed": rollout_mode_changed,
        "frontend_changed": False,
    }
    index["evidence_index_fingerprint"] = canonical_sha256(index)
    return index


def write_evidence_index(path: Path, index: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_attempts(
    reports: Mapping[str, Sequence[Mapping[str, Any]]],
    attempt_numbers: Mapping[str, Sequence[int]],
) -> None:
    unknown = set(reports) - EXPECTED_STAGES
    if unknown:
        raise EvidenceIndexError(f"evidence_stage_unknown:{sorted(unknown)[0]}")
    for stage, attempts in reports.items():
        if stage == "holdout":
            if len(attempts) > 2:
                raise EvidenceIndexError("evidence_holdout_attempt_count_invalid")
            if list(attempt_numbers.get(stage, ())) not in ([1], [1, 2]):
                raise EvidenceIndexError("evidence_holdout_attempt_sequence_invalid")
            if len(attempts) == 2 and not holdout_rerun_authorized(attempts[0]):
                raise EvidenceIndexError("evidence_holdout_rerun_not_authorized")
        else:
            if len(attempts) != 1:
                raise EvidenceIndexError(f"evidence_{stage}_attempt_count_invalid")
            if list(attempt_numbers.get(stage, ())) != [1]:
                raise EvidenceIndexError(f"evidence_{stage}_attempt_sequence_invalid")


def _validate_stage_order(stage_paths: Sequence[tuple[str, int, Path]]) -> None:
    actual = [(stage, attempt) for stage, attempt, _path in stage_paths]
    allowed_sequences = (
        [
            ("s0", 1),
            ("capability_dev", 1),
            ("holdout", 1),
            ("safety_abstention", 1),
            ("backend_release", 1),
        ],
        [
            ("s0", 1),
            ("capability_dev", 1),
            ("holdout", 1),
            ("holdout", 2),
            ("safety_abstention", 1),
            ("backend_release", 1),
        ],
    )
    if not any(actual == expected[: len(actual)] for expected in allowed_sequences):
        raise EvidenceIndexError("evidence_stage_order_invalid")


def holdout_rerun_authorized(report: Mapping[str, Any]) -> bool:
    rows = report.get("rows")
    gates = report.get("gates")
    holdout_gate = gates.get("m3_4_holdout") if isinstance(gates, Mapping) else None
    gate_passed = holdout_gate.get("passed") if isinstance(holdout_gate, Mapping) else None
    if (
        _infrastructure_count(report) <= 0
        or report.get("status") != "inconclusive_infrastructure"
        or gate_passed is not False
        or not isinstance(rows, list)
        or not rows
    ):
        return False
    return all(
        isinstance(row, Mapping)
        and (row.get("classification") == "infrastructure_failure" or row.get("passed") is True)
        for row in rows
    )


def _last_passed(reports: Mapping[str, Sequence[Mapping[str, Any]]], stage: str) -> bool:
    values = reports.get(stage)
    return bool(values and _stage_passed(stage, values[-1]))


def _stage_passed(stage: str, report: Mapping[str, Any]) -> bool:
    if stage == "s0":
        return report.get("status") == "passed"
    if stage == "capability_dev":
        return bool(report.get("gates", {}).get("m3_4_07c", {}).get("passed"))
    if stage == "holdout":
        return bool(report.get("gates", {}).get("m3_4_holdout", {}).get("passed"))
    if stage == "safety_abstention":
        return bool(report.get("gates", {}).get("m3_4_07d", {}).get("passed"))
    if stage == "backend_release":
        return report.get("qualification_outcome") == "passed"
    raise EvidenceIndexError(f"evidence_stage_unknown:{stage}")


def _stage_outcome(stage: str, report: Mapping[str, Any]) -> str:
    return "passed" if _stage_passed(stage, report) else str(report.get("status", "failed"))


def _source_revision(stage: str, report: Mapping[str, Any]) -> str | None:
    source = report.get("source") if stage == "backend_release" else report.get("git")
    if stage == "s0":
        source = report.get("qualification_source")
    return source.get("revision") if isinstance(source, Mapping) else None


def _infrastructure_count(report: Mapping[str, Any]) -> int:
    metrics = report.get("metrics")
    value = metrics.get("infrastructure_failure_count") if isinstance(metrics, Mapping) else 0
    return int(value) if isinstance(value, int) else 0


def _manifest_integrity_blockers(
    manifest: Mapping[str, Any],
    reports: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[str]:
    blockers: list[str] = []
    expected_model = manifest.get("model_id")
    expected_provider = manifest.get("provider")
    expected_prompt_version = manifest.get("prompt_version")
    expected_prompt = manifest.get("prompt_fingerprint")
    expected_plan_contract = manifest.get("plan_contract_version")
    expected_policy = manifest.get("capability_policy_version")
    expected_binder = manifest.get("binder_version")
    expected_transport = manifest.get("transport_version")
    for stage, (suite_key, count_key, trials) in STAGE_CONTRACTS.items():
        for report in reports.get(stage, []):
            if expected_model is not None and report.get("model_id") != expected_model:
                blockers.append(f"qualification_{stage}_model_mismatch")
            if expected_provider is not None and report.get("provider") != expected_provider:
                blockers.append(f"qualification_{stage}_provider_mismatch")
            suite = report.get("suite") if stage != "backend_release" else report
            if not isinstance(suite, Mapping):
                blockers.append(f"qualification_{stage}_suite_missing")
                continue
            suite_mismatch = suite.get("suite_fingerprint") != manifest.get(suite_key)
            if manifest.get(suite_key) is not None and suite_mismatch:
                blockers.append(f"qualification_{stage}_suite_fingerprint_mismatch")
            if manifest.get(count_key) is not None and suite.get("task_count") != manifest.get(
                count_key
            ):
                blockers.append(f"qualification_{stage}_task_count_mismatch")
            if report.get("trials_per_task") != trials:
                blockers.append(f"qualification_{stage}_trial_scale_mismatch")
            blockers.extend(
                _trial_integrity_blockers(
                    stage,
                    report,
                    expected_task_count=manifest.get(count_key),
                    expected_trials=trials,
                )
            )
            lineage = report.get("lineage")
            if stage in {"capability_dev", "holdout", "safety_abstention"}:
                if not isinstance(lineage, Mapping):
                    blockers.append(f"qualification_{stage}_lineage_missing")
                else:
                    lineage_contract = {
                        "prompt_version": (expected_prompt_version, "prompt_version"),
                        "prompt_hash": (expected_prompt, "prompt_fingerprint"),
                        "plan_contract_version": (
                            expected_plan_contract,
                            "plan_contract_version",
                        ),
                        "capability_policy_version": (
                            expected_policy,
                            "capability_policy_version",
                        ),
                        "binder_version": (expected_binder, "binder_version"),
                        "transport_version": (expected_transport, "transport_version"),
                        "grader_version": (
                            manifest.get("safety_grader_version")
                            if stage == "safety_abstention"
                            else manifest.get("capability_grader_version"),
                            "grader_version",
                        ),
                    }
                    for key, (expected, blocker_suffix) in lineage_contract.items():
                        if expected is not None and lineage.get(key) != expected:
                            blockers.append(f"qualification_{stage}_{blocker_suffix}_mismatch")
                    if stage == "holdout" and lineage.get("reference_fingerprint") != manifest.get(
                        "holdout_reference_fingerprint"
                    ):
                        blockers.append("qualification_holdout_reference_fingerprint_mismatch")
    for stage in ("capability_dev", "holdout", "safety_abstention", "backend_release"):
        attempts = reports.get(stage, [])
        if attempts and _infrastructure_count(attempts[-1]) != 0:
            blockers.append(f"qualification_{stage}_infrastructure_failure")
    for stage in ("safety_abstention", "backend_release"):
        for report in reports.get(stage, []):
            schema_fingerprint = (
                report.get("database_schema_fingerprint")
                if stage == "backend_release"
                else report.get("lineage", {}).get("database_schema_fingerprint")
            )
            expected_schema = manifest.get("database_schema_fingerprint")
            if expected_schema is not None and schema_fingerprint != expected_schema:
                blockers.append(f"qualification_{stage}_database_schema_mismatch")
    runtime = manifest.get("runtime_fingerprint")
    for report in reports.get("s0", []):
        if runtime is not None and report.get("qualification_runtime_fingerprint") != runtime:
            blockers.append("qualification_s0_runtime_fingerprint_mismatch")
    for report in reports.get("backend_release", []):
        if runtime is not None and report.get("runtime_fingerprint") != runtime:
            blockers.append("qualification_backend_release_runtime_fingerprint_mismatch")
        rows = report.get("rows")
        if (
            isinstance(rows, list)
            and rows
            and not all(
                isinstance(row, Mapping)
                and (row.get("model_call_count") == 0 or row.get("exact_model_observed") is True)
                for row in rows
            )
        ):
            blockers.append("qualification_backend_release_model_lineage_mismatch")
    return blockers


def _manifest_contract_blockers(manifest: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if manifest.get("source_clean") is not True:
        blockers.append("qualification_manifest_source_clean_invalid")
    if manifest.get("trials_per_task") != 5:
        blockers.append("qualification_manifest_trial_scale_invalid")
    count_contract = {
        "capability_dev_task_count": 5,
        "holdout_task_count": 5,
        "safety_task_count": 5,
        "backend_release_task_count": 3,
    }
    counts = [manifest.get(key) for key in count_contract]
    if not all(type(value) is int and value > 0 for value in counts):
        blockers.append("qualification_manifest_task_counts_invalid")
    else:
        expected_total = sum(
            int(manifest[key]) * trials for key, trials in count_contract.items()
        )
        if manifest.get("formal_trial_count") != expected_total:
            blockers.append("qualification_manifest_formal_trial_count_mismatch")
    expected_rollout = {
        "general_mutation_mode": "suggest",
        "create_enabled": True,
        "delete_enabled": True,
        "no_auto_apply": True,
        "persistent_environment_mutation": False,
    }
    if manifest.get("rollout") != expected_rollout:
        blockers.append("qualification_manifest_rollout_invalid")
    required_fingerprints = (
        "runtime_fingerprint",
        "database_schema_fingerprint",
        "capability_suite_fingerprint",
        "holdout_suite_fingerprint",
        "holdout_oracle_fingerprint",
        "holdout_reference_fingerprint",
        "holdout_review_fingerprint",
        "safety_suite_fingerprint",
        "release_suite_fingerprint",
        "gate_policy_fingerprint",
        "holdout_descriptor_fingerprint",
        "prompt_fingerprint",
    )
    if any(
        not isinstance(manifest.get(key), str) or not manifest.get(key)
        for key in required_fingerprints
    ):
        blockers.append("qualification_manifest_fingerprints_incomplete")
    return blockers


def _trial_integrity_blockers(
    stage: str,
    report: Mapping[str, Any],
    *,
    expected_task_count: Any,
    expected_trials: int,
) -> list[str]:
    if not isinstance(expected_task_count, int):
        return [f"qualification_{stage}_task_count_manifest_invalid"]
    expected_count = expected_task_count * expected_trials
    rows = report.get("rows")
    if not isinstance(rows, list):
        return [f"qualification_{stage}_rows_missing"]
    blockers: list[str] = []
    if len(rows) != expected_count:
        blockers.append(f"qualification_{stage}_trial_count_mismatch")
    metrics = report.get("metrics")
    declared_count = (
        report.get("trial_count")
        if stage == "backend_release"
        else metrics.get("trial_count")
        if isinstance(metrics, Mapping)
        else None
    )
    if declared_count != expected_count:
        blockers.append(f"qualification_{stage}_declared_trial_count_mismatch")
    pairs: list[tuple[str, int]] = []
    for row in rows:
        if (
            not isinstance(row, Mapping)
            or not isinstance(row.get("task_id"), str)
            or not row.get("task_id")
            or type(row.get("trial_index")) is not int
        ):
            blockers.append(f"qualification_{stage}_trial_identity_invalid")
            continue
        pairs.append((str(row["task_id"]), int(row["trial_index"])))
    if len(pairs) != len(set(pairs)):
        blockers.append(f"qualification_{stage}_trial_identity_duplicate")
    task_trials: dict[str, set[int]] = {}
    for task_id, trial_index in pairs:
        task_trials.setdefault(task_id, set()).add(trial_index)
    expected_indices = set(range(1, expected_trials + 1))
    if len(task_trials) != expected_task_count or any(
        indices != expected_indices for indices in task_trials.values()
    ):
        blockers.append(f"qualification_{stage}_trial_matrix_incomplete")
    return blockers


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceIndexError(f"evidence_json_invalid:{path}") from error
    if not isinstance(value, dict):
        raise EvidenceIndexError("evidence_json_object_required")
    fingerprint = value.get("report_fingerprint")
    if isinstance(fingerprint, str):
        payload = {key: item for key, item in value.items() if key != "report_fingerprint"}
        if canonical_sha256(payload) != fingerprint:
            raise EvidenceIndexError("evidence_report_fingerprint_invalid")
    return value


def _canonical_bytes(value: Any) -> bytes:
    return rfc8785.dumps(value)


def _relative_artifact_path(path: Path, evidence_root: Path) -> str:
    try:
        return path.resolve().relative_to(evidence_root).as_posix()
    except ValueError as error:
        raise EvidenceIndexError("evidence_artifact_outside_output_directory") from error


__all__ = [
    "EVIDENCE_INDEX_VERSION",
    "EvidenceIndexError",
    "build_evidence_index",
    "canonical_sha256",
    "holdout_rerun_authorized",
    "write_evidence_index",
]
