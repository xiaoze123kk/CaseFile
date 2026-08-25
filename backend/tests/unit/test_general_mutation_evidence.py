from __future__ import annotations

import json
from pathlib import Path

import pytest
from casefile.benchmark.general_mutation_evidence import (
    EvidenceIndexError,
    build_evidence_index,
    canonical_sha256,
)


def _write(path: Path, value: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _reports(tmp_path: Path, *, holdout_infra: int = 0) -> tuple[Path, list[tuple[str, int, Path]]]:
    revision = "a" * 40
    lineage = {"prompt_hash": "p" * 64, "binder_version": "binder-v1", "grader_version": "g1"}
    manifest = _write(
        tmp_path / "manifest.json",
        {
            "source_revision": revision,
            "runtime_fingerprint": "b" * 64,
            "model_id": "deepseek-v4-pro",
            "prompt_fingerprint": "p" * 64,
            "binder_version": "binder-v1",
            "grader_version": "g1",
            "capability_suite_fingerprint": "cap",
            "holdout_suite_fingerprint": "hold",
            "safety_suite_fingerprint": "safe",
            "release_suite_fingerprint": "release",
            "capability_dev_task_count": 40,
            "holdout_task_count": 24,
            "safety_task_count": 25,
            "backend_release_task_count": 15,
            "database_schema_fingerprint": "db",
        },
    )
    common = {
        "git": {"revision": revision, "dirty": False},
        "metrics": {"infrastructure_failure_count": 0},
        "status": "completed",
    }
    stages = [
        (
            "s0",
            1,
            _write(
                tmp_path / "s0.json",
                {
                    "qualification_source": {"revision": revision},
                    "qualification_runtime_fingerprint": "b" * 64,
                    "status": "passed",
                    "metrics": {},
                },
            ),
        ),
        (
            "capability_dev",
            1,
            _write(
                tmp_path / "dev.json",
                {
                    **common,
                    "provider": "deepseek",
                    "model_id": "deepseek-v4-pro",
                    "trials_per_task": 5,
                    "suite": {"suite_fingerprint": "cap", "task_count": 40},
                    "lineage": lineage,
                    "gates": {"m3_4_07c": {"passed": True}},
                },
            ),
        ),
        (
            "holdout",
            1,
            _write(
                tmp_path / "holdout.json",
                {
                    **common,
                    "provider": "deepseek",
                    "model_id": "deepseek-v4-pro",
                    "trials_per_task": 5,
                    "suite": {"suite_fingerprint": "hold", "task_count": 24},
                    "lineage": lineage,
                    "metrics": {"infrastructure_failure_count": holdout_infra},
                    "gates": {"m3_4_holdout": {"passed": holdout_infra == 0}},
                },
            ),
        ),
        (
            "safety_abstention",
            1,
            _write(
                tmp_path / "safety.json",
                {
                    **common,
                    "provider": "deepseek",
                    "model_id": "deepseek-v4-pro",
                    "trials_per_task": 5,
                    "suite": {"suite_fingerprint": "safe", "task_count": 25},
                    "lineage": {**lineage, "database_schema_fingerprint": "db"},
                    "gates": {"m3_4_07d": {"passed": True}},
                },
            ),
        ),
        (
            "backend_release",
            1,
            _write(
                tmp_path / "release.json",
                {
                    "source": {"revision": revision},
                    "provider": "deepseek",
                    "model_id": "deepseek-v4-pro",
                    "trials_per_task": 3,
                    "suite_fingerprint": "release",
                    "task_count": 15,
                    "database_schema_fingerprint": "db",
                    "runtime_fingerprint": "b" * 64,
                    "status": "passed",
                    "qualification_outcome": "passed",
                    "metrics": {"fault_matrix_failure_count": 0},
                    "no_auto_apply": True,
                    "rollout_mode_changed": False,
                },
            ),
        ),
    ]
    return manifest, stages


def test_evidence_index_qualifies_only_complete_same_revision_chain(tmp_path: Path) -> None:
    manifest, stages = _reports(tmp_path)
    index = build_evidence_index(manifest_path=manifest, stage_paths=stages)
    assert index["qualified"] is True
    assert index["evidence_index_fingerprint"] == canonical_sha256(
        {key: value for key, value in index.items() if key != "evidence_index_fingerprint"}
    )


def test_evidence_index_rejects_unapproved_holdout_rerun(tmp_path: Path) -> None:
    manifest, stages = _reports(tmp_path)
    stages.insert(3, ("holdout", 2, stages[2][2]))
    with pytest.raises(EvidenceIndexError, match="rerun_not_authorized"):
        build_evidence_index(manifest_path=manifest, stage_paths=stages)


def test_evidence_index_allows_infra_triggered_full_holdout_rerun(tmp_path: Path) -> None:
    manifest, stages = _reports(tmp_path, holdout_infra=1)
    passed = _write(
        tmp_path / "holdout-2.json",
        {
            "git": {"revision": "a" * 40, "dirty": False},
            "provider": "deepseek",
            "model_id": "deepseek-v4-pro",
            "trials_per_task": 5,
            "suite": {"suite_fingerprint": "hold", "task_count": 24},
            "lineage": {
                "prompt_hash": "p" * 64,
                "binder_version": "binder-v1",
                "grader_version": "g1",
            },
            "metrics": {"infrastructure_failure_count": 0},
            "status": "completed",
            "gates": {"m3_4_holdout": {"passed": True}},
        },
    )
    stages.insert(3, ("holdout", 2, passed))
    assert build_evidence_index(manifest_path=manifest, stage_paths=stages)["qualified"] is True


def test_evidence_index_fails_closed_on_manifest_lineage_mismatch(tmp_path: Path) -> None:
    manifest, stages = _reports(tmp_path)
    release_path = stages[-1][2]
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["database_schema_fingerprint"] = "tampered"
    _write(release_path, release)

    index = build_evidence_index(manifest_path=manifest, stage_paths=stages)

    assert index["qualified"] is False
    assert "qualification_backend_release_database_schema_mismatch" in index["blockers"]
