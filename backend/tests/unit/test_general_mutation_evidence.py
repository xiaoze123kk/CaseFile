from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
import rfc8785
from casefile.benchmark.general_mutation_evidence import (
    EvidenceIndexError,
    build_evidence_index,
    canonical_sha256,
)


def _write(path: Path, value: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _rows(
    prefix: str,
    task_count: int,
    trials: int,
    *,
    infrastructure_failures: int = 0,
    capability_failures: int = 0,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for task_index in range(1, task_count + 1):
        for trial_index in range(1, trials + 1):
            classification = "success"
            passed = True
            if infrastructure_failures > 0:
                classification = "infrastructure_failure"
                infrastructure_failures -= 1
                passed = False
            elif capability_failures > 0:
                classification = "capability_failure"
                capability_failures -= 1
                passed = False
            rows.append(
                {
                    "task_id": f"{prefix}-{task_index:02d}",
                    "trial_index": trial_index,
                    "classification": classification,
                    "passed": passed,
                    "model_call_count": 1,
                    "exact_model_observed": True,
                }
            )
    return rows


def _lineage(*, grader: str = "capability-grader-v1") -> dict[str, str]:
    return {
        "prompt_version": "prompt-v1",
        "prompt_hash": "p" * 64,
        "plan_contract_version": "plan-v1",
        "capability_policy_version": "policy-v1",
        "binder_version": "binder-v1",
        "transport_version": "transport-v1",
        "grader_version": grader,
    }


def _reports(tmp_path: Path, *, holdout_infra: int = 0) -> tuple[Path, list[tuple[str, int, Path]]]:
    revision = "a" * 40
    manifest = _write(
        tmp_path / "manifest.json",
        {
            "source_revision": revision,
            "source_clean": True,
            "runtime_fingerprint": "b" * 64,
            "provider": "deepseek",
            "model_id": "deepseek-v4-pro",
            "prompt_version": "prompt-v1",
            "prompt_fingerprint": "p" * 64,
            "plan_contract_version": "plan-v1",
            "capability_policy_version": "policy-v1",
            "binder_version": "binder-v1",
            "transport_version": "transport-v1",
            "capability_grader_version": "capability-grader-v1",
            "safety_grader_version": "safety-grader-v1",
            "capability_suite_fingerprint": "cap",
            "holdout_suite_fingerprint": "hold",
            "safety_suite_fingerprint": "safe",
            "release_suite_fingerprint": "release",
            "capability_dev_task_count": 40,
            "holdout_task_count": 24,
            "safety_task_count": 25,
            "backend_release_task_count": 15,
            "database_schema_fingerprint": "db",
            "holdout_reference_fingerprint": "hold-reference",
            "holdout_oracle_fingerprint": "hold-oracle",
            "holdout_review_fingerprint": "hold-review",
            "gate_policy_fingerprint": "gate-policy",
            "holdout_descriptor_fingerprint": "holdout-descriptor",
            "trials_per_task": 5,
            "formal_trial_count": 490,
            "rollout": {
                "general_mutation_mode": "suggest",
                "create_enabled": True,
                "delete_enabled": True,
                "no_auto_apply": True,
                "persistent_environment_mutation": False,
            },
        },
    )
    common = {
        "git": {"revision": revision, "dirty": False},
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
                    "lineage": _lineage(),
                    "metrics": {"trial_count": 200, "infrastructure_failure_count": 0},
                    "gates": {"m3_4_07c": {"passed": True}},
                    "rows": _rows("cap", 40, 5),
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
                    "lineage": {
                        **_lineage(),
                        "reference_fingerprint": "hold-reference",
                    },
                    "metrics": {
                        "trial_count": 120,
                        "infrastructure_failure_count": holdout_infra,
                    },
                    "status": ("inconclusive_infrastructure" if holdout_infra else "completed"),
                    "gates": {"m3_4_holdout": {"passed": holdout_infra == 0}},
                    "rows": _rows("hold", 24, 5, infrastructure_failures=holdout_infra),
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
                    "lineage": {
                        **_lineage(grader="safety-grader-v1"),
                        "database_schema_fingerprint": "db",
                    },
                    "metrics": {"trial_count": 125, "infrastructure_failure_count": 0},
                    "gates": {"m3_4_07d": {"passed": True}},
                    "rows": _rows("safe", 25, 5),
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
                    "trial_count": 45,
                    "suite_fingerprint": "release",
                    "task_count": 15,
                    "database_schema_fingerprint": "db",
                    "runtime_fingerprint": "b" * 64,
                    "status": "passed",
                    "qualification_outcome": "passed",
                    "metrics": {
                        "infrastructure_failure_count": 0,
                        "fault_matrix_failure_count": 0,
                    },
                    "rows": _rows("release", 15, 3),
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
    assert index["artifacts"][0]["path"] == "s0.json"
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
            "lineage": {**_lineage(), "reference_fingerprint": "hold-reference"},
            "metrics": {"trial_count": 120, "infrastructure_failure_count": 0},
            "status": "completed",
            "gates": {"m3_4_holdout": {"passed": True}},
            "rows": _rows("hold", 24, 5),
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


def test_evidence_index_fails_closed_on_manifest_rollout_mismatch(tmp_path: Path) -> None:
    manifest_path, stages = _reports(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rollout"]["delete_enabled"] = False
    _write(manifest_path, manifest)

    index = build_evidence_index(manifest_path=manifest_path, stage_paths=stages)

    assert index["qualified"] is False
    assert "qualification_manifest_rollout_invalid" in index["blockers"]


def test_canonical_sha256_uses_rfc8785() -> None:
    value = {"number": 333333333.33333329, "unicode": "编排"}
    assert canonical_sha256(value) == sha256(rfc8785.dumps(value)).hexdigest()


def test_evidence_index_rejects_duplicate_non_holdout_attempt(tmp_path: Path) -> None:
    manifest, stages = _reports(tmp_path)
    stages.insert(2, ("capability_dev", 2, stages[1][2]))
    with pytest.raises(EvidenceIndexError, match="capability_dev_attempt_count_invalid"):
        build_evidence_index(manifest_path=manifest, stage_paths=stages)


def test_evidence_index_rejects_out_of_order_stage_chain(tmp_path: Path) -> None:
    manifest, stages = _reports(tmp_path)
    stages[1], stages[2] = stages[2], stages[1]
    with pytest.raises(EvidenceIndexError, match="stage_order_invalid"):
        build_evidence_index(manifest_path=manifest, stage_paths=stages)


def test_evidence_index_rejects_mixed_failure_holdout_rerun(tmp_path: Path) -> None:
    manifest, stages = _reports(tmp_path, holdout_infra=1)
    first_path = stages[2][2]
    first = json.loads(first_path.read_text(encoding="utf-8"))
    first["rows"][1]["classification"] = "capability_failure"
    first["rows"][1]["passed"] = False
    _write(first_path, first)
    stages.insert(3, ("holdout", 2, first_path))
    with pytest.raises(EvidenceIndexError, match="rerun_not_authorized"):
        build_evidence_index(manifest_path=manifest, stage_paths=stages)


def test_evidence_index_fails_closed_on_incomplete_trial_matrix(tmp_path: Path) -> None:
    manifest, stages = _reports(tmp_path)
    capability_path = stages[1][2]
    capability = json.loads(capability_path.read_text(encoding="utf-8"))
    capability["rows"].pop()
    _write(capability_path, capability)

    index = build_evidence_index(manifest_path=manifest, stage_paths=stages)

    assert index["qualified"] is False
    assert "qualification_capability_dev_trial_count_mismatch" in index["blockers"]
    assert "qualification_capability_dev_trial_matrix_incomplete" in index["blockers"]


def test_evidence_index_detects_rfc8785_report_tamper(tmp_path: Path) -> None:
    manifest, stages = _reports(tmp_path)
    release_path = stages[-1][2]
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["report_fingerprint"] = canonical_sha256(release)
    _write(release_path, release)
    release["status"] = "tampered"
    _write(release_path, release)

    with pytest.raises(EvidenceIndexError, match="report_fingerprint_invalid"):
        build_evidence_index(manifest_path=manifest, stage_paths=stages)
