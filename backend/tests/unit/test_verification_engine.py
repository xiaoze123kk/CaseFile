"""Unit coverage for the pure VerificationEngine core."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from casefile.application.verification_engine import (
    PatchOperation,
    VerificationEngine,
)

FIXTURE = Path(__file__).parents[2] / ".." / "fixtures" / "casefiles" / "restart_loop.casefile.json"


def load_document() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fast_engine_reuses_deterministic_validator_and_emits_contract() -> None:
    document = load_document()
    result = VerificationEngine(profile="fast").verify(document)

    assert result.engine_version == "verification-engine-v1"
    assert result.structural_valid is True
    assert all(finding.kind == "deterministic" for finding in result.findings)
    assert all(finding.rule_code for finding in result.findings)


def test_balanced_engine_normalizes_legacy_llm_finding_and_keeps_evidence() -> None:
    result = VerificationEngine(profile="balanced").verify(
        load_document(),
        llm_findings=[
            {
                "finding_id": "F1",
                "kind": "contradiction",
                "severity": "S2",
                "title": "描述冲突",
                "statement": "两个对象描述互相冲突。",
                "evidence_object_ids": ["ent_researcher"],
                "evidence_event_ids": ["evt_restart_seven"],
                "confidence": 0.8,
            }
        ],
    )

    finding = next(item for item in result.findings if item.kind == "llm")
    assert finding.severity == "error"
    assert finding.confidence == 0.8
    assert {ref.ref_key for ref in finding.refs} == {
        "ent_researcher",
        "evt_restart_seven",
    }


def test_ordered_batch_uses_intermediate_state_and_reports_delta() -> None:
    document = load_document()
    original = deepcopy(document)
    engine = VerificationEngine(
        profile="fast",
        editable_fields_by_type={"entity": ("name", "description")},
    )
    simulation = engine.simulate_patch_operation_batch(
        document,
        [
            PatchOperation(
                operation_id="op_one",
                object_id="ent_researcher",
                field_path="/name",
                old_value="林研究员",
                new_value="林调查员",
                object_type="entity",
                expected_object_revision=1,
            ),
            PatchOperation(
                operation_id="op_two",
                object_id="ent_researcher",
                field_path="/name",
                old_value="林调查员",
                new_value="林首席调查员",
                object_type="entity",
                expected_object_revision=1,
            ),
        ],
        object_revisions={"ent_researcher": 1},
    )

    assert simulation.valid is True
    assert simulation.can_apply is True
    assert [delta.old_value for delta in simulation.deltas] == ["林研究员", "林调查员"]
    assert simulation.document["entities"][0]["name"] == "林首席调查员"
    assert document == original


def test_batch_rejects_old_value_conflict_without_mutating_document() -> None:
    document = load_document()
    simulation = VerificationEngine(
        editable_fields_by_type={"entity": ("name",)},
    ).simulate_patch_operation_batch(
        document,
        [
            PatchOperation(
                operation_id="op_one",
                object_id="ent_researcher",
                field_path="/name",
                old_value="错误旧值",
                new_value="新名字",
                object_type="entity",
            )
        ],
    )

    assert simulation.valid is False
    assert simulation.can_apply is False
    assert simulation.reason_code == "old_value_conflict"
    assert document["entities"][0]["name"] == "林研究员"


def test_batch_blocks_new_deterministic_issue_and_structure_lock_overlap() -> None:
    document = load_document()
    document["structure_locks"] = [
        {
            "id": "lock_resolution_tags",
            "title": "锁定根因答案",
            "lock_type": "hard",
            "object_ref": {
                "object_type": "resolution_spec",
                "object_id": "res_root_cause",
            },
            "field_paths": ["/tags"],
            "reason": "作者已确认标签",
            "tags": ["author_lock"],
            "source_refs": [],
            "confidence": 1.0,
            "confirmation_status": "user_confirmed",
            "created_by": {"actor_type": "user", "actor_id": "user_local_owner"},
            "updated_at": "2042-06-01T12:00:00Z",
            "revision": 1,
        }
    ]
    simulation = VerificationEngine(
        editable_fields_by_type={"resolution_spec": ("tags",)},
    ).simulate_patch_operation_batch(
        document,
        [
            PatchOperation(
                operation_id="op_one",
                object_id="res_root_cause",
                field_path="/tags",
                new_value=["revised"],
                object_type="resolution_spec",
            )
        ],
    )

    assert simulation.valid is True
    assert simulation.can_apply is False
    assert simulation.reason_code == "structure_lock_conflict"
    assert simulation.structure_lock_conflicts == ("lock_resolution_tags",)


def test_batch_does_not_claim_to_resolve_pure_llm_finding() -> None:
    simulation = VerificationEngine(profile="fast").simulate_patch_operation_batch(
        load_document(),
        [
            PatchOperation(
                operation_id="op_description",
                object_id="ent_researcher",
                field_path="/description",
                old_value=None,
                new_value="负责调查午夜回航并复核日志。",
                object_type="entity",
            )
        ],
        target_finding_keys=("llm:prior-review",),
    )

    assert simulation.can_apply is True
    assert simulation.pending_recheck_finding_keys == ("llm:prior-review",)
    assert simulation.severity_delta == {
        severity: 0 for severity in simulation.severity_delta
    }


@pytest.mark.parametrize("severity", ["unknown", "S4"])
def test_llm_finding_rejects_unknown_severity(severity: str) -> None:
    with pytest.raises(ValueError, match="finding_severity_invalid"):
        VerificationEngine(profile="balanced").normalize_llm_findings(
            [
                {
                    "kind": "contradiction",
                    "severity": severity,
                    "title": "标题",
                    "statement": "说明",
                }
            ]
        )
