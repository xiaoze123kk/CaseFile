"""Canonical evidence index for M3.4-07f qualification."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

EVIDENCE_INDEX_VERSION = "casefile-general-mutation-evidence-index-v1"
RELEASE_SCOPE = "backend_general_mutation_suggest_create_update_delete"


class EvidenceIndexError(ValueError):
    """Stable fail-closed evidence error."""


def canonical_sha256(value: Any) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def build_evidence_index(
    *,
    manifest_path: Path,
    stage_paths: Sequence[tuple[str, int, Path]],
    blocked_reason_code: str | None = None,
) -> dict[str, Any]:
    manifest = _read_object(manifest_path)
    entries: list[dict[str, Any]] = []
    reports: dict[str, list[dict[str, Any]]] = {}
    for stage, attempt, path in stage_paths:
        report = _read_object(path)
        reports.setdefault(stage, []).append(report)
        entries.append(
            {
                "stage": stage,
                "attempt": attempt,
                "path": str(path.resolve()),
                "canonical_sha256": canonical_sha256(report),
                "status": report.get("status"),
                "qualification_outcome": _stage_outcome(stage, report),
                "infrastructure_failure_count": _infrastructure_count(report),
            }
        )
    _validate_holdout_rerun(reports.get("holdout", []))
    integrity_blockers = _manifest_integrity_blockers(manifest, reports)
    expected_stages = {"s0", "capability_dev", "holdout", "safety_abstention", "backend_release"}
    complete = expected_stages.issubset(reports)
    same_revision = all(
        _source_revision(stage, report) == manifest.get("source_revision")
        for stage, attempts in reports.items()
        for report in attempts
    )
    passed = bool(
        blocked_reason_code is None
        and complete
        and same_revision
        and not integrity_blockers
        and _last_passed(reports, "s0")
        and _last_passed(reports, "capability_dev")
        and _last_passed(reports, "holdout")
        and _last_passed(reports, "safety_abstention")
        and _last_passed(reports, "backend_release")
        and reports["backend_release"][-1].get("no_auto_apply") is True
        and reports["backend_release"][-1].get("rollout_mode_changed") is False
        and reports["backend_release"][-1].get("metrics", {}).get("fault_matrix_failure_count") == 0
    )
    blockers = []
    if blocked_reason_code:
        blockers.append(blocked_reason_code)
    if not complete:
        blockers.extend(
            f"qualification_stage_missing:{stage}"
            for stage in sorted(expected_stages - set(reports))
        )
    if not same_revision:
        blockers.append("qualification_source_revision_mismatch")
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
        "qualification_outcome": "passed" if passed else "failed",
        "qualified": passed,
        "release_scope": RELEASE_SCOPE,
        "blockers": sorted(set(blockers)),
        "rollout_mode_changed": False,
        "frontend_changed": False,
    }
    index["evidence_index_fingerprint"] = canonical_sha256(index)
    return index


def write_evidence_index(path: Path, index: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_holdout_rerun(attempts: Sequence[Mapping[str, Any]]) -> None:
    if len(attempts) > 2:
        raise EvidenceIndexError("evidence_holdout_attempt_count_invalid")
    if len(attempts) == 2 and _infrastructure_count(attempts[0]) == 0:
        raise EvidenceIndexError("evidence_holdout_rerun_not_authorized")


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
    expected_prompt = manifest.get("prompt_fingerprint")
    expected_binder = manifest.get("binder_version")
    expected_grader = manifest.get("grader_version")
    stage_contracts = {
        "capability_dev": ("capability_suite_fingerprint", "capability_dev_task_count", 5),
        "holdout": ("holdout_suite_fingerprint", "holdout_task_count", 5),
        "safety_abstention": ("safety_suite_fingerprint", "safety_task_count", 5),
        "backend_release": ("release_suite_fingerprint", "backend_release_task_count", 3),
    }
    for stage, (suite_key, count_key, trials) in stage_contracts.items():
        for report in reports.get(stage, []):
            if expected_model is not None and report.get("model_id") != expected_model:
                blockers.append(f"qualification_{stage}_model_mismatch")
            if report.get("provider") != "deepseek":
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
            lineage = report.get("lineage")
            if stage in {"capability_dev", "holdout", "safety_abstention"}:
                if not isinstance(lineage, Mapping):
                    blockers.append(f"qualification_{stage}_lineage_missing")
                else:
                    if (
                        expected_prompt is not None
                        and lineage.get("prompt_hash") != expected_prompt
                    ):
                        blockers.append(f"qualification_{stage}_prompt_fingerprint_mismatch")
                    if (
                        expected_binder is not None
                        and lineage.get("binder_version") != expected_binder
                    ):
                        blockers.append(f"qualification_{stage}_binder_version_mismatch")
                    if (
                        expected_grader is not None
                        and lineage.get("grader_version") != expected_grader
                    ):
                        blockers.append(f"qualification_{stage}_grader_version_mismatch")
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
        if isinstance(rows, list) and rows and not all(
            isinstance(row, Mapping)
            and (
                row.get("model_call_count") == 0
                or row.get("exact_model_observed") is True
            )
            for row in rows
        ):
            blockers.append("qualification_backend_release_model_lineage_mismatch")
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
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


__all__ = [
    "EVIDENCE_INDEX_VERSION",
    "EvidenceIndexError",
    "build_evidence_index",
    "canonical_sha256",
    "write_evidence_index",
]
