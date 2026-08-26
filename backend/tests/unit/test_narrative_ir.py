"""Pure deterministic NarrativeIR projection coverage."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    canonical_json_sha256,
    narrative_ir_component_fingerprint,
    project_narrative_ir_json,
    validate_narrative_ir,
)
from casefile_contracts import NarrativeIR

ROOT = Path(__file__).resolve().parents[3]


def _document(name: str = "restart_loop.casefile.json") -> dict:
    return json.loads((ROOT / "fixtures" / "casefiles" / name).read_text(encoding="utf-8"))


def test_projection_is_stable_and_preserves_object_order() -> None:
    document = _document()
    first = project_narrative_ir_json(document)
    second = project_narrative_ir_json(copy.deepcopy(document))

    assert first == second
    assert canonical_json_sha256(first) == canonical_json_sha256(second)
    assert [item["value"]["id"] for item in first["objects"]["events"]] == [
        item["id"] for item in document["events"]
    ]
    reversed_document = copy.deepcopy(document)
    second_event = copy.deepcopy(reversed_document["events"][0])
    second_event["id"] = "evt_restart_eight"
    second_event["title"] = "第八次重启"
    reversed_document["events"].append(second_event)
    reversed_document["events"].reverse()
    assert canonical_json_sha256(
        project_narrative_ir_json(reversed_document)
    ) != canonical_json_sha256(first)


def test_projection_captures_nested_context_without_numeric_source_paths() -> None:
    document = _document()
    document["entities"][0]["knowledge_states"][0]["false_belief_refs"] = [
        document["entities"][0]["knowledge_states"][0]["believes_refs"][0]
    ]
    ir = project_narrative_ir_json(document)
    edges = ir["indexes"]["reference_edges"]

    assert any(
        edge["relation"] == "entity.false_belief"
        and edge["context"]["container_path"] == "/knowledge_states"
        and edge["context"]["container_ordinal"] >= 1
        for edge in edges
    )
    assert any(
        edge["relation"] == "reasoning.input"
        and edge["context"]["container_key"]
        for edge in edges
    )
    for source_ref in ir["source"]["root_source_refs"]:
        assert not any(segment.isdecimal() for segment in source_ref["field_path"].split("/"))
    for envelope in (
        envelope
        for collection in ir["objects"].values()
        for envelope in collection
    ):
        assert envelope["source_ref"]["field_path"] == ""
    for edge in edges:
        assert not any(
            segment.isdecimal() for segment in edge["source_ref"]["field_path"].split("/")
        )


def test_relative_time_remains_source_data_and_creates_anchor_edge() -> None:
    document = _document()
    relative = document["events"][0]
    relative["time"] = {
        "kind": "relative",
        "anchor_event_ref": {"object_type": "event", "object_id": relative["id"]},
        "relation": "after",
        "offset_minutes": 20,
    }
    ir = project_narrative_ir_json(document)
    projected = next(
        item["value"] for item in ir["objects"]["events"] if item["value"]["id"] == relative["id"]
    )

    assert projected["time"] == relative["time"]
    assert any(
        edge["relation"] == "event.relative_anchor"
        and edge["from_ref"]["object_id"] == relative["id"]
        and edge["to_ref"] == relative["time"]["anchor_event_ref"]
        for edge in ir["indexes"]["reference_edges"]
    )


def test_validator_rejects_missing_edge_and_component_boundary_is_source_only() -> None:
    document = _document()
    ir = project_narrative_ir_json(document)
    ir["indexes"]["reference_edges"].pop()

    with pytest.raises(CompilerContractError, match="semantic_mismatch"):
        validate_narrative_ir(NarrativeIR.model_validate(ir), source_document=document)

    assert narrative_ir_component_fingerprint(document) == {
        "projection_version": "compiler.narrative-ir-projection.v1",
        "source_schema_id": "casefile.v2",
        "target_schema_id": "compiler.narrative-ir.v1",
        "source_content_hash": canonical_json_sha256(document),
    }


def test_golden_hashes_freeze_projection_contract() -> None:
    golden = json.loads(
        (
            ROOT
            / "fixtures"
            / "compiler"
            / "narrative_ir"
            / "v1"
            / "expected_hashes.json"
        ).read_text(encoding="utf-8")
    )
    for name, expected in golden["cases"].items():
        document = _document(name)
        ir = project_narrative_ir_json(document)
        assert canonical_json_sha256(ir) == expected["narrative_ir_hash"]
        assert canonical_json_sha256(
            narrative_ir_component_fingerprint(document)
        ) == expected["component_fingerprint_hash"]
        assert len(ir["indexes"]["reference_edges"]) == expected["reference_edge_count"]
