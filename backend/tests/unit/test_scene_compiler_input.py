"""N4.4 v2 audited input and provider projection tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    build_scene_compiler_input_v2,
    build_scene_compiler_model_view,
    canonical_json_sha256,
    canonicalize_novel_plan,
    validate_scene_compiler_input_v2,
)
from casefile_contracts import SceneCompilerInputBundleV2, SceneCompilerModelView

ROOT = Path(__file__).resolve().parents[3]
REFERENCE = ROOT / "fixtures/novel_plan_benchmark/v4/references/linear_mystery__basic.json"
PLANNER_INPUT = (
    ROOT / "fixtures/novel_plan_benchmark/v4/inputs/v3/linear_mystery__basic.json"
)


def _inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    planner_input = json.loads(PLANNER_INPUT.read_text(encoding="utf-8"))
    candidate = json.loads(REFERENCE.read_text(encoding="utf-8"))
    novel_plan = canonicalize_novel_plan(
        candidate,
        planner_input=planner_input,
        planner_version="test.scene-compiler-input.v1",
        component_fingerprint="a" * 64,
    )
    profile_payload = planner_input["profile"]
    profile = {
        "profile_key": "novel_default",
        "profile_schema_id": profile_payload["schema_id"],
        "profile_version": 1,
        "frozen_payload": profile_payload,
        "content_hash": canonical_json_sha256(profile_payload),
    }
    return novel_plan, planner_input, profile


def _bundle() -> dict[str, Any]:
    novel_plan, planner_input, profile = _inputs()
    return build_scene_compiler_input_v2(
        novel_plan=novel_plan,
        narrative_ir=planner_input["narrative_ir"],
        exposure=planner_input["exposure_plan"],
        profile=profile,
    )


def test_v2_input_is_deterministic_bound_and_cross_language_serializable() -> None:
    first = _bundle()
    second = _bundle()

    assert first == second
    assert first["schema_id"] == "compiler.scene-compiler-input.v2"
    assert first["source"]["input_hash"] == canonical_json_sha256(
        {key: first[key] for key in first if key not in {"schema_id", "source"}}
    )
    assert [item["kind"] for item in first["execution_constraints"][0]["obligations"]] == [
        "event",
        "exposure",
    ]
    serialized = SceneCompilerInputBundleV2.model_validate(first).model_dump_json()
    assert SceneCompilerInputBundleV2.model_validate_json(serialized).model_dump(
        mode="json"
    ) == first


def test_model_view_is_chapter_bounded_and_does_not_expose_exposure_notes() -> None:
    bundle = _bundle()
    view = build_scene_compiler_model_view(bundle)

    assert view["schema_id"] == "compiler.scene-compiler-model-view.v1"
    assert view["source"]["scene_compiler_input_hash"] == bundle["source"]["input_hash"]
    assert [batch["scene_ids"] for batch in view["batches"]] == [
        ["scene_1", "scene_2", "scene_3"]
    ]
    serialized = json.dumps(view, ensure_ascii=False)
    assert "后披露自动保护触发证据" not in serialized
    assert "source_fragment_hash" not in serialized
    SceneCompilerModelView.model_validate(view)


def test_model_view_splits_nine_scenes_without_crossing_chapters() -> None:
    bundle = _bundle()
    original = bundle["novel_plan"]["scenes"][0]
    scenes: list[dict[str, Any]] = []
    for ordinal in range(1, 10):
        scene = deepcopy(original)
        scene["scene_id"] = f"scene_batch_test_{ordinal}"
        scene["discourse_order"] = ordinal
        scene["chapter_id"] = "chapter_1" if ordinal <= 8 else "chapter_2"
        scene["prerequisite_scene_ids"] = []
        scenes.append(scene)
    bundle["novel_plan"]["chapters"] = [
        {"chapter_id": "chapter_1", "ordinal": 1, "act_ordinal": 1, "title": "一"},
        {"chapter_id": "chapter_2", "ordinal": 2, "act_ordinal": 2, "title": "二"},
    ]
    bundle["novel_plan"]["scenes"] = scenes
    bundle["novel_plan"]["indexes"] = {
        "chapter_scene_ids": {
            "chapter_1": [scene["scene_id"] for scene in scenes[:8]],
            "chapter_2": [scenes[8]["scene_id"]],
        },
        "scene_dependencies": {scene["scene_id"]: [] for scene in scenes},
    }
    bundle["novel_plan"]["source"]["narrative_ir_hash"] = canonical_json_sha256(
        bundle["narrative_ir"]
    )
    novel_plan, planner_input, profile = _inputs()
    novel_plan.update(bundle["novel_plan"])
    rebuilt = build_scene_compiler_input_v2(
        novel_plan=novel_plan,
        narrative_ir=planner_input["narrative_ir"],
        exposure=planner_input["exposure_plan"],
        profile=profile,
    )

    view = build_scene_compiler_model_view(rebuilt)

    assert [len(batch["scene_ids"]) for batch in view["batches"]] == [8, 1]
    assert [batch["chapter_id"] for batch in view["batches"]] == [
        "chapter_1",
        "chapter_2",
    ]


def test_v2_input_rejects_hash_and_binding_drift() -> None:
    bundle = _bundle()
    drifted = deepcopy(bundle)
    drifted["source"]["input_hash"] = "0" * 64
    with pytest.raises(CompilerContractError, match="compiler_scene_input_hash_mismatch"):
        validate_scene_compiler_input_v2(drifted)

    drifted = deepcopy(bundle)
    drifted["profile"]["frozen_payload"]["structure"]["target_scenes"] = 4
    drifted["source"]["profile_hash"] = canonical_json_sha256(drifted["profile"])
    payload = {key: drifted[key] for key in drifted if key not in {"schema_id", "source"}}
    drifted["source"]["input_hash"] = canonical_json_sha256(payload)
    with pytest.raises(
        CompilerContractError, match="compiler_scene_input_profile_content_hash_mismatch"
    ):
        validate_scene_compiler_input_v2(drifted)
