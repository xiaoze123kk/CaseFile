"""Evidence Index v2 assembly for same-revision Closure Repair qualification."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

EVIDENCE_INDEX_VERSION = "casefile-closure-repair-evidence-index-v2"


class EvidenceIndexError(ValueError):
    """Stable fail-closed lineage or report-integrity error."""


def build_evidence_index(
    *,
    clean_dev_report: Path,
    holdout_reports: Sequence[Path],
    backend_release_report: Path,
) -> dict[str, Any]:
    if not holdout_reports or len(holdout_reports) > 2:
        raise EvidenceIndexError("evidence_holdout_attempt_count_invalid")
    loaded = [
        ("clean_dev", 1, clean_dev_report, _read_report(clean_dev_report)),
        *(
            ("holdout", index, path, _read_report(path))
            for index, path in enumerate(holdout_reports, start=1)
        ),
        (
            "backend_release",
            1,
            backend_release_report,
            _read_report(backend_release_report),
        ),
    ]
    for _stage, _attempt, _path, report in loaded:
        _validate_report_fingerprint(report)
    revisions = {_source_revision(report) for *_prefix, report in loaded}
    runtimes = {_runtime_fingerprint(report) for *_prefix, report in loaded}
    if len(revisions) != 1:
        raise EvidenceIndexError("evidence_source_revision_mismatch")
    if len(runtimes) != 1:
        raise EvidenceIndexError("evidence_runtime_fingerprint_mismatch")
    if len(holdout_reports) == 2:
        first = loaded[1][3]
        if _infra_count(first) == 0:
            raise EvidenceIndexError("evidence_holdout_rerun_not_authorized")
    clean = loaded[0][3]
    holdout_passed = any(
        report.get("qualification_outcome") == "passed"
        and report.get("status") == "completed"
        and _infra_count(report) == 0
        for report in (item[3] for item in loaded if item[0] == "holdout")
    )
    backend = loaded[-1][3]
    qualified = bool(
        clean.get("qualification_outcome") == "passed"
        and clean.get("status") == "completed"
        and _infra_count(clean) == 0
        and holdout_passed
        and backend.get("qualification_outcome") == "passed"
        and backend.get("status") == "passed"
        and backend.get("trial_count") == 54
    )
    index: dict[str, Any] = {
        "schema_version": EVIDENCE_INDEX_VERSION,
        "source_revision": next(iter(revisions)),
        "repair_runtime_fingerprint": next(iter(runtimes)),
        "attempts": [
            {
                "stage": stage,
                "attempt": attempt,
                "path": str(path.resolve()),
                "sha256": sha256(path.read_bytes()).hexdigest(),
                "report_fingerprint": report["report_fingerprint"],
                "status": report.get("status"),
                "qualification_outcome": report.get("qualification_outcome"),
                "infrastructure_failure_count": _infra_count(report),
            }
            for stage, attempt, path, report in loaded
        ],
        "qualification_outcome": "passed" if qualified else "failed",
        "qualified": qualified,
        "legacy_m3_3_07_evidence_overwritten": False,
    }
    index["evidence_index_fingerprint"] = sha256(_canonical_bytes(index)).hexdigest()
    return index


def write_evidence_index(path: Path, index: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceIndexError(f"evidence_report_invalid:{path}") from error
    if not isinstance(value, dict):
        raise EvidenceIndexError("evidence_report_object_required")
    return value


def _validate_report_fingerprint(report: Mapping[str, Any]) -> None:
    expected = report.get("report_fingerprint")
    payload = {key: value for key, value in report.items() if key != "report_fingerprint"}
    if not isinstance(expected, str) or sha256(_canonical_bytes(payload)).hexdigest() != expected:
        raise EvidenceIndexError("evidence_report_fingerprint_invalid")


def _source_revision(report: Mapping[str, Any]) -> str:
    source = report.get("source")
    value = source.get("revision") if isinstance(source, dict) else None
    if not isinstance(value, str) or not value:
        raise EvidenceIndexError("evidence_source_revision_missing")
    return value


def _runtime_fingerprint(report: Mapping[str, Any]) -> str:
    value = report.get("repair_runtime_fingerprint")
    if not isinstance(value, str) or len(value) != 64:
        raise EvidenceIndexError("evidence_runtime_fingerprint_missing")
    return value


def _infra_count(report: Mapping[str, Any]) -> int:
    metrics = report.get("metrics")
    value = metrics.get("infrastructure_failure_count") if isinstance(metrics, dict) else 0
    return value if isinstance(value, int) else 0


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


__all__ = [
    "EVIDENCE_INDEX_VERSION",
    "EvidenceIndexError",
    "build_evidence_index",
    "write_evidence_index",
]
