"""N4.4 deterministic ScenePlan execution compiler tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    NarrativeExecutionGraph,
    build_baseline_scene_plan_candidate,
    build_scene_compiler_input,
    canonical_json_sha256,
    canonicalize_novel_plan,
    canonicalize_scene_plan_candidate,
    compile_scene_plan_json,
    inspect_scene_plan,
    inspect_scene_plan_candidate,
    scene_plan_component_fingerprint,
    scene_plan_semantic_signature,
    validate_scene_compiler_input,
    validate_scene_plan,
    validate_scene_plan_candidate,
)
from casefile_contracts import ScenePlanIR

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = (
    ROOT / "fixtures" / "novel_plan_benchmark" / "v4" / "references" / "linear_mystery__basic.json"
)
PLANNER_INPUT = (
    ROOT
    / "fixtures"
    / "novel_plan_benchmark"
    / "v4"
    / "inputs"
    / "v3"
    / "linear_mystery__basic.json"
)


def _novel_plan() -> dict[str, Any]:
    candidate = json.loads(REFERENCE.read_text(encoding="utf-8"))
    scenes = []
    chapter_scene_ids: dict[str, list[str]] = {
        chapter["chapter_id"]: [] for chapter in candidate["chapters"]
    }
    scene_dependencies: dict[str, list[str]] = {}
    for scene in candidate["scenes"]:
        source_refs = [
            {
                "object_ref": ref,
                "field_path": "",
                "source_fragment_hash": canonical_json_sha256(ref),
            }
            for ref in scene["basis_refs"]
        ]
        scenes.append({**scene, "source_refs": source_refs})
        chapter_scene_ids[scene["chapter_id"]].append(scene["scene_id"])
        scene_dependencies[scene["scene_id"]] = scene["prerequisite_scene_ids"]
    return {
        "schema_id": "compiler.novel-plan.v1",
        "planner_version": "test-planner.v1",
        "source": {
            "planner_input_hash": "1" * 64,
            "narrative_ir_hash": "2" * 64,
            "profile_hash": "3" * 64,
            "exposure_hash": "4" * 64,
            "component_fingerprint": "5" * 64,
        },
        "chapters": candidate["chapters"],
        "scenes": scenes,
        "indexes": {
            "chapter_scene_ids": chapter_scene_ids,
            "scene_dependencies": scene_dependencies,
        },
    }


def _scene_compiler_case() -> tuple[dict[str, Any], dict[str, Any]]:
    planner_input = json.loads(PLANNER_INPUT.read_text(encoding="utf-8"))
    novel_candidate = json.loads(REFERENCE.read_text(encoding="utf-8"))
    novel_plan = canonicalize_novel_plan(
        novel_candidate,
        planner_input=planner_input,
        planner_version="test.scene-plan-reference.v1",
        component_fingerprint="a" * 64,
    )
    bundle = build_scene_compiler_input(
        novel_plan=novel_plan, narrative_ir=planner_input["narrative_ir"]
    )
    return bundle, build_baseline_scene_plan_candidate(novel_plan)


def test_scene_plan_is_deterministic_serializable_and_provenance_complete() -> None:
    novel_plan = _novel_plan()

    first = compile_scene_plan_json(novel_plan)
    second = compile_scene_plan_json(deepcopy(novel_plan))

    assert first == second
    assert first["schema_id"] == "compiler.scene-plan.v1"
    assert first["source"]["novel_plan_hash"] == canonical_json_sha256(novel_plan)
    assert first["source"]["component_fingerprint"] == canonical_json_sha256(
        scene_plan_component_fingerprint(novel_plan)
    )
    assert first["metrics"] == {
        "chapter_count": 1,
        "scene_count": 3,
        "beat_count": 7,
        "exposure_count": 2,
        "resolution_action_count": 2,
    }
    assert [beat["beat_id"] for beat in first["beats"][:2]] == [
        "beat_scene_1_001",
        "beat_scene_1_002",
    ]
    assert all(beat["source_refs"] for beat in first["beats"])
    serialized = ScenePlanIR.model_validate(first).model_dump_json()
    assert ScenePlanIR.model_validate_json(serialized).model_dump(mode="json") == first
    assert "networkx" not in serialized.lower()


def test_scene_plan_tracks_reader_exposure_and_future_reveal_boundaries() -> None:
    compiled = compile_scene_plan_json(_novel_plan())
    first, second, third = compiled["scenes"]

    assert first["audience_state_before"] == []
    assert first["allowed_reveals"] == [
        {"entry_key": "exposure_manual_trace", "action": "introduce"}
    ]
    assert first["forbidden_reveal_entry_keys"] == ["exposure_restart_log"]
    assert first["audience_state_after"][0]["status"] == "introduced"
    assert [item["entry_key"] for item in second["audience_state_before"]] == [
        "exposure_manual_trace"
    ]
    assert second["forbidden_reveal_entry_keys"] == []
    assert len(third["audience_state_after"]) == 2


def test_execution_graph_exposes_domain_queries_without_leaking_networkx() -> None:
    compiled = compile_scene_plan_json(_novel_plan())
    graph = NarrativeExecutionGraph(compiled)

    assert graph.dependency_cycles() == ()
    assert "scene_3" in graph.affected_nodes("scene_1")
    assert "beat_scene_3_003" in graph.descendants("chapter_1")
    assert "chapter_1" in graph.ancestors("beat_scene_1_001")
    assert graph.descendants("missing") == ()


def test_linter_rejects_tampered_scene_execution_output() -> None:
    novel_plan = _novel_plan()
    compiled = compile_scene_plan_json(novel_plan)
    compiled["beats"][0]["directive"] = "tampered"

    report = inspect_scene_plan(compiled, novel_plan=novel_plan)

    assert not report.succeeded
    assert report.violations[0].code == "compiler_scene_plan_beat_coverage_invalid"
    with pytest.raises(
        CompilerContractError, match="compiler_scene_plan_beat_coverage_invalid"
    ):
        validate_scene_plan(compiled, novel_plan=novel_plan)


def test_model_candidate_is_bound_validated_and_canonicalized_without_prose() -> None:
    bundle, candidate = _scene_compiler_case()

    validated = validate_scene_plan_candidate(
        candidate, scene_compiler_input=bundle
    ).model_dump(mode="json")
    canonical = canonicalize_scene_plan_candidate(
        validated, scene_compiler_input=bundle
    )
    rewritten = deepcopy(validated)
    rewritten["scenes"][0]["beats"][0]["directive"] = "同义改写执行指令。"
    rewritten_canonical = canonicalize_scene_plan_candidate(
        rewritten, scene_compiler_input=bundle
    )

    assert bundle["schema_id"] == "compiler.scene-compiler-input.v1"
    assert validated["schema_id"] == "compiler.scene-plan-candidate.v1"
    assert canonical["source"]["scene_compiler_input_hash"] == bundle["source"]["input_hash"]
    assert canonical["source"]["candidate_hash"] == canonical_json_sha256(validated)
    assert canonical["beats"][0]["participant_refs"]
    assert canonical["beats"][0]["source_refs"]
    assert scene_plan_semantic_signature(canonical) == scene_plan_semantic_signature(
        rewritten_canonical
    )


def test_model_candidate_rejects_out_of_scope_grounding_and_provenance() -> None:
    bundle, candidate = _scene_compiler_case()
    candidate["scenes"][0]["beats"][0]["participant_refs"].append(
        {"object_type": "entity", "object_id": "ent_safety_observer"}
    )
    candidate["scenes"][0]["beats"][0]["basis_refs"].append(
        {"object_type": "claim", "object_id": "claim_backup_trigger"}
    )

    report = inspect_scene_plan_candidate(candidate, scene_compiler_input=bundle)

    assert not report.succeeded
    assert {item.code for item in report.violations} >= {
        "compiler_scene_plan_candidate_participant_invalid",
        "compiler_scene_plan_candidate_provenance_invalid",
    }


def test_scene_compiler_input_rejects_hash_drift_and_dependency_cycle() -> None:
    bundle, _candidate = _scene_compiler_case()
    drifted = deepcopy(bundle)
    drifted["source"]["input_hash"] = "0" * 64
    with pytest.raises(CompilerContractError, match="compiler_scene_plan_input_hash_mismatch"):
        validate_scene_compiler_input(drifted)

    cyclic = deepcopy(bundle)
    scenes = cyclic["novel_plan"]["scenes"]
    first_id = scenes[0]["scene_id"]
    last_id = scenes[-1]["scene_id"]
    scenes[0]["prerequisite_scene_ids"] = [last_id]
    cyclic["novel_plan"]["indexes"]["scene_dependencies"][first_id] = [last_id]
    cyclic["source"]["novel_plan_hash"] = canonical_json_sha256(cyclic["novel_plan"])
    cyclic["source"]["input_hash"] = canonical_json_sha256(
        {"novel_plan": cyclic["novel_plan"], "narrative_ir": cyclic["narrative_ir"]}
    )
    with pytest.raises(CompilerContractError, match="compiler_scene_plan_input_dependency_cycle"):
        validate_scene_compiler_input(cyclic)
