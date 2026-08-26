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
    canonical_json_sha256,
    compile_scene_plan_json,
    inspect_scene_plan,
    scene_plan_component_fingerprint,
    validate_scene_plan,
)
from casefile_contracts import ScenePlanIR

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = (
    ROOT / "fixtures" / "novel_plan_benchmark" / "v4" / "references" / "linear_mystery__basic.json"
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
