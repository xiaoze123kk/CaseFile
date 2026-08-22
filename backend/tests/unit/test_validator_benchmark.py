"""Release-gate coverage for the deterministic Validator benchmark."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from casefile.benchmark.validator_eval import apply_mutations, run_validator_benchmark

REPO_ROOT = Path(__file__).resolve().parents[3]


def _row(report: dict[str, Any], layer: str, case_id: str) -> dict[str, Any]:
    return next(
        row
        for row in report["layers"][layer]["rows"]
        if row["case_id"] == case_id
    )


def test_validator_release_suite_passes_all_deterministic_gates() -> None:
    report = run_validator_benchmark(repo_root=REPO_ROOT)

    assert report["status"] == "passed"
    assert report["layers"]["V0"]["metrics"]["required_rule_recall"] == 1.0
    assert report["layers"]["V0"]["metrics"]["clean_false_positive_rate"] == 0.0
    assert report["layers"]["V0"]["metrics"]["finding_identity_stability"] == 1.0
    assert report["layers"]["V1"]["metrics"]["batch_unsafe_false_accept_rate"] == 0.0
    assert report["layers"]["V1"]["metrics"]["batch_safe_false_reject_rate"] == 0.0
    assert report["layers"]["V1"]["metrics"]["chat_gate_unsafe_false_accept_rate"] == 0.0
    assert report["layers"]["V1"]["metrics"]["chat_gate_safe_false_reject_rate"] == 0.0
    assert report["layers"]["V1"]["metrics"]["input_immutability_rate"] == 1.0
    assert report["layers"]["V2"]["metrics"]["repair_target_precision"] == 1.0
    assert report["layers"]["V2"]["metrics"]["repair_target_recall"] == 1.0


def test_v0_contains_positive_negative_and_boundary_examples() -> None:
    report = run_validator_benchmark(repo_root=REPO_ROOT, layer="v0")

    assert _row(report, "V0", "v0-knowledge-before-source-detected")["passed"] is True
    assert _row(report, "V0", "v0-knowledge-same-time-boundary-clean")["passed"] is True
    assert _row(report, "V0", "v0-temporal-exclusivity-detected")["passed"] is True
    assert _row(report, "V0", "v0-temporal-nonoverlap-boundary-clean")["passed"] is True
    assert _row(report, "V0", "v0-dangling-location-is-structural-invalid")["passed"] is True


def test_v1_covers_patch_failure_reason_matrix_and_safe_resolution() -> None:
    report = run_validator_benchmark(repo_root=REPO_ROOT, layer="v1")

    expected_reasons = {
        "v1-operation-limit-exceeded": "operation_limit_exceeded",
        "v1-base-document-invalid": "base_document_invalid",
        "v1-operation-id-missing": "operation_id_missing",
        "v1-object-id-missing": "object_id_missing",
        "v1-field-path-invalid": "field_path_invalid",
        "v1-operation-type-not-supported": "operation_type_not_supported",
        "v1-object-revision-conflict": "object_revision_conflict",
        "v1-object-not-found": "object_not_found",
        "v1-object-type-conflict": "object_type_conflict",
        "v1-field-not-editable": "field_not_editable",
        "v1-path-not-found": "path_not_found",
        "v1-old-value-conflict": "old_value_conflict",
        "v1-post-document-invalid": "post_document_invalid",
        "v1-target-finding-not-resolved": "finding_not_resolved",
        "v1-structure-lock-conflict": "structure_lock_conflict",
        "v1-deterministic-severity-regression": "deterministic_severity_regression",
    }
    for case_id, reason_code in expected_reasons.items():
        row = _row(report, "V1", case_id)
        assert row["passed"] is True
        assert row["actual"]["reason_code"] == reason_code

    resolved = _row(report, "V1", "v1-resolves-target-finding")
    assert resolved["passed"] is True
    assert resolved["actual"]["can_apply"] is True
    assert resolved["actual"]["fixed_rule_codes"] == ["temporal_exclusivity_violation"]


def test_v1_chat_safe_patch_gate_covers_runtime_failure_modes() -> None:
    report = run_validator_benchmark(repo_root=REPO_ROOT, layer="v1")

    expected = {
        "v1-chat-gate-rejects-missing-object": "object_not_found",
        "v1-chat-gate-rejects-uneditable-field": "field_not_editable",
        "v1-chat-gate-rejects-missing-path": "path_not_found",
        "v1-chat-gate-rejects-markdown-value": "value_json_wrapped_in_markdown",
        "v1-chat-gate-rejects-semantic-regression": "simulation_failed",
        "v1-chat-gate-rejects-malformed-proposal-shape": "proposal_shape_invalid",
    }
    for case_id, reason_code in expected.items():
        row = _row(report, "V1", case_id)
        assert row["passed"] is True
        assert row["actual"]["failure_reason_codes"] == [reason_code]

    normalized = _row(report, "V1", "v1-chat-gate-normalizes-plain-string")
    assert normalized["actual"]["candidate_value_json"] == ['"林调查员"']
    duplicate = _row(report, "V1", "v1-chat-gate-discards-duplicate-target")
    assert duplicate["actual"]["discard_reason_codes"] == ["duplicate_patch_target"]


def test_v2_add_wins_when_same_target_is_rejected_and_required() -> None:
    report = run_validator_benchmark(repo_root=REPO_ROOT, layer="v2")
    row = _row(report, "V2", "v2-add-wins-over-remove-for-same-target")

    assert row["passed"] is True
    assert row["actual"]["add"] == ["ent_researcher:/description"]
    assert row["actual"]["remove"] == []


def test_fixture_mutations_do_not_modify_source_document() -> None:
    source = {"items": [{"value": 1}]}
    mutated = apply_mutations(
        source,
        (
            {"op": "replace", "path": "/items/0/value", "value": 2},
            {"op": "add", "path": "/items/-", "value": {"value": 3}},
        ),
    )

    assert source == {"items": [{"value": 1}]}
    assert mutated == {"items": [{"value": 2}, {"value": 3}]}
