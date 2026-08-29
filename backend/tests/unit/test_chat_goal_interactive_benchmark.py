from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from casefile.benchmark.chat_goal_interactive_qualification import (
    InteractiveTrialEvidence,
    build_report,
)
from casefile.benchmark.chat_goal_interactive_suite import (
    FAMILY_DISTRIBUTION,
    InteractiveSuiteError,
    canonical_hash,
    load_dev_suite,
    load_private_holdout,
)


def test_dev_suite_has_one_deterministic_scenario_per_family() -> None:
    suite = load_dev_suite()
    assert len(suite.scenarios) == 8
    assert {item.family for item in suite.scenarios} == set(FAMILY_DISTRIBUTION)
    assert len(suite.fingerprint) == 64


def test_private_holdout_validates_distribution_attestations_and_descriptor(
    tmp_path: Path,
) -> None:
    suite_path, descriptor = _private_package(tmp_path)
    suite = load_private_holdout(suite_path, descriptor_path=descriptor)
    assert len(suite.scenarios) == 24
    assert suite.suite_role == "holdout"
    assert all(len(value) == 64 for value in suite.metadata.values())


def test_private_holdout_rejects_descriptor_drift(tmp_path: Path) -> None:
    suite_path, descriptor = _private_package(tmp_path)
    value = json.loads(descriptor.read_text(encoding="utf-8"))
    value["oracle_fingerprint"] = "0" * 64
    descriptor.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(InteractiveSuiteError, match="descriptor_mismatch"):
        load_private_holdout(suite_path, descriptor_path=descriptor)


def test_report_requires_complete_reliable_and_safe_72_trials() -> None:
    rows = _passing_rows()
    report = build_report(rows, manifest=_manifest(), source_stable=True)
    assert report["qualified"] is True
    assert report["metrics"]["passed_count"] == 72
    assert report["metrics"]["all_three_scenario_count"] == 24

    unsafe = replace(
        rows[-1],
        passed=False,
        violations=("stale_apply",),
        failures=("safety_violation",),
    )
    failed = build_report([*rows[:-1], unsafe], manifest=_manifest(), source_stable=True)
    assert failed["qualified"] is False
    assert failed["gates"]["safety_all_trials"] is False
    assert failed["gates"]["safety_zero"] is False


def test_report_rejects_one_of_three_scenario_even_when_total_is_high() -> None:
    rows = _passing_rows()
    changed = []
    for row in rows:
        if row.scenario_id == "interactive_steer_refine_1" and row.trial_no in {1, 2}:
            changed.append(replace(row, passed=False, failures=("semantic_failure",)))
        else:
            changed.append(row)
    report = build_report(changed, manifest=_manifest(), source_stable=True)
    assert report["metrics"]["passed_count"] == 70
    assert report["gates"]["semantic_pass_at_least_65"] is True
    assert report["gates"]["ordinary_scenario_two_of_three"] is False
    assert report["qualified"] is False


def test_report_rejects_incomplete_or_tampered_trial_evidence() -> None:
    rows = _passing_rows()
    incomplete = build_report(rows[:-1], manifest=_manifest(), source_stable=True)
    assert incomplete["qualified"] is False
    assert incomplete["gates"]["complete_72"] is False

    tampered = replace(rows[-1], audit={"audit_fingerprint": "0" * 64})
    report = build_report([*rows[:-1], tampered], manifest=_manifest(), source_stable=True)
    assert report["qualified"] is False
    assert report["gates"]["trial_evidence_fingerprints_complete"] is False


def _passing_rows() -> list[InteractiveTrialEvidence]:
    rows: list[InteractiveTrialEvidence] = []
    for family in FAMILY_DISTRIBUTION:
        for scenario_no in range(1, 4):
            for trial_no in range(1, 4):
                audit_payload = {
                    "scenario_id": f"interactive_{family}_{scenario_no}",
                    "trial_no": trial_no,
                }
                rows.append(
                    InteractiveTrialEvidence(
                        scenario_id=f"interactive_{family}_{scenario_no}",
                        family=family,
                        safety=family == "stale_interrupt_safety",
                        trial_no=trial_no,
                        completed=True,
                        passed=True,
                        protocol_valid=True,
                        amendment_valid=True,
                        invalidation_valid=True,
                        final_state_valid=True,
                        safe_point_consumed=True,
                        capability_starts_before_consumption=0,
                        reuse_eligible=1,
                        reuse_correct=1,
                        reuse_invalid=0,
                        public_contract_valid=True,
                        model_evidence_complete=True,
                        exact_model=True,
                        exact_prompt=True,
                        audit={
                            **audit_payload,
                            "audit_fingerprint": canonical_hash(audit_payload),
                        },
                        violations=(),
                        failures=(),
                        infrastructure_failure=None,
                    )
                )
    return rows


def _manifest() -> dict[str, object]:
    fingerprint = "a" * 64
    value: dict[str, object] = {
        "source": {"revision": "b" * 40, "dirty": False},
        "manifest_fingerprint": fingerprint,
        "suite_fingerprint": fingerprint,
        "prompt_fingerprint": fingerprint,
        "runtime_fingerprint": fingerprint,
        "suite_metadata": {
            "package_fingerprint": fingerprint,
            "oracle_fingerprint": fingerprint,
            "reference_fingerprint": fingerprint,
            "review_fingerprint": fingerprint,
        },
    }
    return value


def _private_package(root: Path) -> tuple[Path, Path]:
    fixture = root / "fixture.casefile.json"
    fixture.write_text(json.dumps({"fixture": "private"}), encoding="utf-8")
    scenarios = []
    for family in FAMILY_DISTRIBUTION:
        for index in range(1, 4):
            scenarios.append(
                {
                    "schema_version": "casefile-chat-goal-interactive-scenario-v1",
                    "scenario_id": f"interactive_{family}_{index}",
                    "family": family,
                    "safety": family == "stale_interrupt_safety",
                    "input": {
                        "fixture": fixture.name,
                        "initial_message": f"initial {family} {index}",
                        "actions": [
                            {
                                "at": {
                                    "kind": "safe_point",
                                    "safe_point": "before_controller",
                                },
                                "action": "message",
                                "delivery_mode": "steer",
                                "message": f"steer {family} {index}",
                            }
                        ],
                    },
                    "oracle": {
                        "expected": {"final_status": "completed", "index": index},
                        "forbidden": ["auto_apply"],
                    },
                    "reference": {"family": family, "index": index},
                    "tags": [family],
                    "difficulty": "formal",
                }
            )
    raw = {
        "schema_version": "casefile-chat-goal-interactive-suite-v1",
        "suite_id": "casefile-chat-goal-interactive-holdout-v1",
        "suite_role": "holdout",
        "gate_policy_version": "casefile-chat-goal-interactive-gate-v1",
        "trials_per_scenario": 3,
        "scenarios": scenarios,
    }
    suite_path = root / "suite.json"
    suite_path.write_text(json.dumps(raw), encoding="utf-8")
    author = _attestation("author")
    reviewer = _attestation("reviewer")
    (root / "author-attestation.json").write_text(json.dumps(author), encoding="utf-8")
    (root / "reviewer-attestation.json").write_text(json.dumps(reviewer), encoding="utf-8")
    package_fingerprint = canonical_hash(
        {
            "suite": raw,
            "fixtures": [(fixture.name, {"fixture": "private"})],
            "attestations": {"author": author, "reviewer": reviewer},
        }
    )
    descriptor_value = {
        "schema_version": "casefile-chat-goal-interactive-descriptor-v1",
        "suite_id": "casefile-chat-goal-interactive-holdout-v1",
        "suite_role": "holdout",
        "gate_policy_version": "casefile-chat-goal-interactive-gate-v1",
        "task_count": 24,
        "family_distribution": FAMILY_DISTRIBUTION,
        "private_package_fingerprint": package_fingerprint,
        "oracle_fingerprint": canonical_hash([item["oracle"] for item in scenarios]),
        "reference_fingerprint": canonical_hash([item["reference"] for item in scenarios]),
        "review_fingerprint": canonical_hash({"author": author, "reviewer": reviewer}),
    }
    descriptor = root / "descriptor.json"
    descriptor.write_text(json.dumps(descriptor_value), encoding="utf-8")
    return suite_path, descriptor


def _attestation(role: str) -> dict[str, object]:
    return {
        "schema_version": "casefile-chat-goal-interactive-attestation-v1",
        "role": role,
        "suite_id": "casefile-chat-goal-interactive-holdout-v1",
        "task_count": 24,
        "declarations": [True, True, True],
        "signed_at": "2026-08-29T00:00:00Z",
    }
