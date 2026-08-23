from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from casefile.benchmark.closure_repair_evidence import (
    EvidenceIndexError,
    build_evidence_index,
)


def _write_report(
    path: Path,
    *,
    stage: str,
    revision: str = "a" * 40,
    runtime: str = "b" * 64,
    infra: int = 0,
    passed: bool = True,
) -> Path:
    report: dict[str, Any] = {
        "source": {"revision": revision},
        "repair_runtime_fingerprint": runtime,
        "status": "passed" if stage == "backend" and passed else "completed",
        "qualification_outcome": "passed" if passed else "failed_capability",
        "trial_count": 54 if stage == "backend" else 5,
        "metrics": {"infrastructure_failure_count": infra},
    }
    report["report_fingerprint"] = sha256(
        json.dumps(
            report,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_evidence_index_qualifies_same_revision_complete_reports(tmp_path: Path) -> None:
    index = build_evidence_index(
        clean_dev_report=_write_report(tmp_path / "clean.json", stage="clean"),
        holdout_reports=[_write_report(tmp_path / "holdout.json", stage="holdout")],
        backend_release_report=_write_report(tmp_path / "backend.json", stage="backend"),
    )

    assert index["qualified"] is True
    assert len(index["attempts"]) == 3
    assert index["legacy_m3_3_07_evidence_overwritten"] is False


def test_evidence_index_rejects_cross_runtime_and_tampering(tmp_path: Path) -> None:
    clean = _write_report(tmp_path / "clean.json", stage="clean")
    holdout = _write_report(
        tmp_path / "holdout.json",
        stage="holdout",
        runtime="c" * 64,
    )
    backend = _write_report(tmp_path / "backend.json", stage="backend")
    with pytest.raises(EvidenceIndexError, match="runtime_fingerprint_mismatch"):
        build_evidence_index(
            clean_dev_report=clean,
            holdout_reports=[holdout],
            backend_release_report=backend,
        )

    holdout = _write_report(tmp_path / "holdout.json", stage="holdout")
    holdout.write_text(holdout.read_text(encoding="utf-8") + " ", encoding="utf-8")
    value = json.loads(holdout.read_text(encoding="utf-8"))
    value["status"] = "blocked"
    holdout.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(EvidenceIndexError, match="report_fingerprint_invalid"):
        build_evidence_index(
            clean_dev_report=clean,
            holdout_reports=[holdout],
            backend_release_report=backend,
        )


def test_evidence_index_allows_only_infra_triggered_full_holdout_rerun(
    tmp_path: Path,
) -> None:
    clean = _write_report(tmp_path / "clean.json", stage="clean")
    first = _write_report(tmp_path / "holdout-1.json", stage="holdout")
    second = _write_report(tmp_path / "holdout-2.json", stage="holdout")
    backend = _write_report(tmp_path / "backend.json", stage="backend")

    with pytest.raises(EvidenceIndexError, match="rerun_not_authorized"):
        build_evidence_index(
            clean_dev_report=clean,
            holdout_reports=[first, second],
            backend_release_report=backend,
        )

    first = _write_report(tmp_path / "holdout-1.json", stage="holdout", infra=1)
    index = build_evidence_index(
        clean_dev_report=clean,
        holdout_reports=[first, second],
        backend_release_report=backend,
    )
    assert index["qualified"] is True
