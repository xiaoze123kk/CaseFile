"""Authoritative N4.4 ScenePlanIR v2 State Engine tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.agent_runtime.scene_compiler import execute_scene_semantic_fill
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    build_scene_compiler_input_v2,
    build_scene_compiler_model_view,
    canonical_json_sha256,
    canonicalize_novel_plan,
    compile_scene_plan_v2,
    inspect_scene_plan_v2,
    validate_scene_plan_v2,
)
from casefile_contracts import ScenePlanIRV2

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = ROOT / "fixtures/novel_plan_benchmark/v4/references/linear_mystery__basic.json"
PLANNER_INPUT = (
    ROOT / "fixtures/novel_plan_benchmark/v4/inputs/v3/linear_mystery__basic.json"
)


def _input_and_fills() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    planner_input = json.loads(PLANNER_INPUT.read_text(encoding="utf-8"))
    candidate = json.loads(REFERENCE.read_text(encoding="utf-8"))
    novel_plan = canonicalize_novel_plan(
        candidate,
        planner_input=planner_input,
        planner_version="test.scene-state-engine.v1",
        component_fingerprint="b" * 64,
    )
    profile_payload = planner_input["profile"]
    bundle = build_scene_compiler_input_v2(
        novel_plan=novel_plan,
        narrative_ir=planner_input["narrative_ir"],
        exposure=planner_input["exposure_plan"],
        profile={
            "profile_key": "novel_default",
            "profile_schema_id": profile_payload["schema_id"],
            "profile_version": 1,
            "frozen_payload": profile_payload,
            "content_hash": canonical_json_sha256(profile_payload),
        },
    )
    execution = execute_scene_semantic_fill(
        FakeProvider(),
        task_run_id=1,
        model_view=build_scene_compiler_model_view(bundle),
        component_hash="c" * 64,
        model_id="fake",
        api_key="unused",
    )
    return bundle, list(execution.proposals)


def test_scene_plan_v2_is_deterministic_typed_and_replayable() -> None:
    bundle, fills = _input_and_fills()
    first = compile_scene_plan_v2(scene_compiler_input=bundle, semantic_fills=fills)
    second = compile_scene_plan_v2(
        scene_compiler_input=deepcopy(bundle), semantic_fills=deepcopy(fills)
    )

    assert first == second
    assert first["schema_id"] == "compiler.scene-plan.v2"
    assert first["compiler_version"] == "compiler.scene-execution.v2"
    assert first["scenes"][0]["state_before_hash"] == canonical_json_sha256(
        first["initial_state"]
    )
    assert first["scenes"][0]["audience_state_before"] == []
    assert first["scenes"][0]["audience_state_after"][0]["status"] == "introduced"
    assert first["metrics"]["beat_count"] == len(first["beats"])
    serialized = ScenePlanIRV2.model_validate(first).model_dump_json()
    assert ScenePlanIRV2.model_validate_json(serialized).model_dump(mode="json") == first
    assert validate_scene_plan_v2(
        first, scene_compiler_input=bundle, semantic_fills=fills
    ).schema_id == "compiler.scene-plan.v2"


def test_state_engine_applies_knowledge_location_and_cross_scene_setup_payoff() -> None:
    bundle, fills = _input_and_fills()
    first_scene = fills[0]["scenes"][0]
    second_scene = fills[0]["scenes"][1]
    first_beat = first_scene["beats"][0]
    second_beat = second_scene["beats"][0]
    participant = bundle["novel_plan"]["scenes"][0]["participant_refs"][0]
    event_ref = bundle["novel_plan"]["scenes"][0]["event_refs"][0]
    location_ref = bundle["novel_plan"]["scenes"][0]["location_ref"]
    assert location_ref is not None
    first_beat["knowledge_transitions"] = [
        {
            "operation": "learn",
            "subject_ref": participant,
            "object_ref": event_ref,
            "basis_refs": [event_ref],
        }
    ]
    first_beat["location_assertions"] = [
        {
            "subject_ref": participant,
            "location_ref": location_ref,
            "story_time_refs": [event_ref],
            "basis_refs": [event_ref],
        }
    ]
    first_beat["setup_keys"] = ["setup_manual_trace"]
    second_beat["payoff_keys"] = ["setup_manual_trace"]

    compiled = compile_scene_plan_v2(scene_compiler_input=bundle, semantic_fills=fills)

    subject_state = next(
        item
        for item in compiled["final_state"]["character_knowledge"]
        if item["subject_ref"] == participant
    )
    assert event_ref in subject_state["knows_refs"]
    assert compiled["final_state"]["open_setups"] == []
    assert compiled["indexes"]["setup_beat_ids"]["setup_manual_trace"].startswith("beat_")
    assert compiled["indexes"]["payoff_beat_ids"]["setup_manual_trace"]
    assert any(edge["relation"] == "beat_pays_off_setup" for edge in compiled["edges"])
    assert compiled["metrics"]["knowledge_transition_count"] == 1
    assert compiled["metrics"]["location_assertion_count"] == 1


def test_state_engine_rejects_unpaid_setup_and_known_fact_false_belief() -> None:
    bundle, fills = _input_and_fills()
    fills[0]["scenes"][0]["beats"][0]["setup_keys"] = ["setup_unpaid"]
    with pytest.raises(CompilerContractError, match="compiler_scene_setup_unpaid"):
        compile_scene_plan_v2(scene_compiler_input=bundle, semantic_fills=fills)

    bundle, fills = _input_and_fills()
    participant = bundle["novel_plan"]["scenes"][0]["participant_refs"][0]
    event_ref = bundle["novel_plan"]["scenes"][0]["event_refs"][0]
    beat = fills[0]["scenes"][0]["beats"][0]
    beat["knowledge_transitions"] = [
        {
            "operation": "learn",
            "subject_ref": participant,
            "object_ref": event_ref,
            "basis_refs": [event_ref],
        },
        {
            "operation": "misbelieve",
            "subject_ref": participant,
            "object_ref": event_ref,
            "basis_refs": [event_ref],
        },
    ]
    with pytest.raises(
        CompilerContractError, match="compiler_scene_known_fact_cannot_be_false_belief"
    ):
        compile_scene_plan_v2(scene_compiler_input=bundle, semantic_fills=fills)


def test_v2_replay_linter_rejects_state_and_edge_tampering() -> None:
    bundle, fills = _input_and_fills()
    compiled = compile_scene_plan_v2(scene_compiler_input=bundle, semantic_fills=fills)
    tampered = deepcopy(compiled)
    tampered["scenes"][0]["state_after_hash"] = "0" * 64
    tampered["edges"].pop()

    report = inspect_scene_plan_v2(
        tampered, scene_compiler_input=bundle, semantic_fills=fills
    )

    assert not report.succeeded
    assert {item.code for item in report.violations} >= {
        "compiler_scene_plan_v2_scenes_mismatch",
        "compiler_scene_plan_v2_edges_mismatch",
    }
