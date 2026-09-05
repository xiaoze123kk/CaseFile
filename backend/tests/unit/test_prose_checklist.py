"""N4.5 Profile v2 and deterministic prose Checklist coverage."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from casefile.domain.narrative_compiler import (
    PROSE_CHECKLIST_POLICY_HASH,
    CompilerContractError,
    build_planner_input_bundle,
    build_planner_input_bundle_v2,
    build_planner_input_bundle_v3,
    build_prose_judge_checklist,
    canonical_json_sha256,
    planner_input_fingerprint,
    prose_checklist_fingerprint,
    validate_novel_profile_v2,
    validate_prose_judge_checklist,
    validate_prose_judge_report,
    validate_scene_render,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures/compiler/prose_rendering/v1"
SCENE_PLAN = (
    ROOT
    / "fixtures/scene_plan_benchmark/v2/runtime_references/dependency_transfer__basic.json"
)
SCENE_INPUT = (
    ROOT / "fixtures/scene_plan_benchmark/v1/inputs/dependency_transfer__basic.json"
)
PLANNER_INPUT = (
    ROOT / "fixtures/novel_plan_benchmark/v4/inputs/v3/linear_mystery__basic.json"
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source = _load(SCENE_INPUT)
    return _load(SCENE_PLAN), source["narrative_ir"], _load(FIXTURES / "profile_v2.json")


def test_checklist_is_deterministic_complete_and_does_not_mutate_inputs() -> None:
    plan, narrative, profile = _inputs()
    frozen = deepcopy((plan, narrative, profile))

    first = build_prose_judge_checklist(
        scene_plan=plan,
        narrative_ir=narrative,
        profile=profile,
        scene_id="scene_1",
    )
    second = build_prose_judge_checklist(
        scene_plan=deepcopy(plan),
        narrative_ir=deepcopy(narrative),
        profile=deepcopy(profile),
        scene_id="scene_1",
    )

    assert first == second
    assert (plan, narrative, profile) == frozen
    assert [item["ordinal"] for item in first["checks"]] == list(
        range(1, len(first["checks"]) + 1)
    )
    assert [item["check_id"] for item in first["checks"]] == [
        f"check_scene_1_{ordinal:03d}"
        for ordinal in range(1, len(first["checks"]) + 1)
    ]
    assert [item["kind"] for item in first["checks"]] == [
        "beat_realization",
        "event_modality",
        "beat_realization",
        "scene_outcome",
        "reveal_control",
        "reveal_control",
        "pov_knowledge",
        "location_time",
        "causality_ordering",
        "major_hallucination",
    ]
    assert all(
        item["evidence_policy"]
        == ("required_on_pass" if item["polarity"] == "required" else "required_on_fail")
        for item in first["checks"]
    )
    assert first["source"]["checklist_policy_hash"] == PROSE_CHECKLIST_POLICY_HASH
    assert first["scene_context"]["object_catalog"]
    assert all(
        set(item) == {"object_ref", "source_ref", "label", "value"}
        for item in first["scene_context"]["object_catalog"]
    )
    assert validate_prose_judge_checklist(
        first,
        scene_plan=plan,
        narrative_ir=narrative,
        profile=profile,
    ).schema_id == "compiler.prose-judge-checklist.v1"
    assert prose_checklist_fingerprint(first)["checklist_hash"] == canonical_json_sha256(
        first
    )


def test_checklist_rejects_input_and_policy_drift() -> None:
    plan, narrative, profile = _inputs()
    checklist = build_prose_judge_checklist(
        scene_plan=plan,
        narrative_ir=narrative,
        profile=profile,
        scene_id="scene_1",
    )
    tampered = deepcopy(checklist)
    tampered["checks"][0]["ordinal"] = 2
    with pytest.raises(CompilerContractError, match="compiler_prose_checklist_mismatch"):
        validate_prose_judge_checklist(
            tampered,
            scene_plan=plan,
            narrative_ir=narrative,
            profile=profile,
        )

    policy_drift = deepcopy(checklist)
    policy_drift["source"]["checklist_policy_hash"] = "0" * 64
    with pytest.raises(CompilerContractError, match="compiler_prose_checklist_mismatch"):
        validate_prose_judge_checklist(
            policy_drift,
            scene_plan=plan,
            narrative_ir=narrative,
            profile=profile,
        )

    narrative_drift = deepcopy(narrative)
    narrative_drift["case"]["title"] += "漂移"
    with pytest.raises(
        CompilerContractError, match="compiler_prose_checklist_narrative_ir_hash_mismatch"
    ):
        build_prose_judge_checklist(
            scene_plan=plan,
            narrative_ir=narrative_drift,
            profile=profile,
            scene_id="scene_1",
        )


def test_scene_chain_requires_the_immediately_previous_accepted_render() -> None:
    plan, narrative, profile = _inputs()
    accepted = _load(FIXTURES / "scene_render_accepted.json")

    second = build_prose_judge_checklist(
        scene_plan=plan,
        narrative_ir=narrative,
        profile=profile,
        scene_id="scene_2",
        previous_scene_render=accepted,
    )
    assert second == _load(FIXTURES / "checklist_scene_2.json")
    assert second["source"]["previous_scene_render_hash"] == canonical_json_sha256(
        accepted
    )
    assert second["scene_context"]["previous_scene_render"] == accepted

    with pytest.raises(CompilerContractError, match="previous_scene_required"):
        build_prose_judge_checklist(
            scene_plan=plan,
            narrative_ir=narrative,
            profile=profile,
            scene_id="scene_2",
        )
    with pytest.raises(CompilerContractError, match="first_scene_previous_invalid"):
        build_prose_judge_checklist(
            scene_plan=plan,
            narrative_ir=narrative,
            profile=profile,
            scene_id="scene_1",
            previous_scene_render=accepted,
        )
    wrong_stage = deepcopy(accepted)
    wrong_stage["stage"] = "polished"
    wrong_stage["selection_reason"] = None
    with pytest.raises(CompilerContractError, match="previous_scene_mismatch"):
        build_prose_judge_checklist(
            scene_plan=plan,
            narrative_ir=narrative,
            profile=profile,
            scene_id="scene_2",
            previous_scene_render=wrong_stage,
        )


def test_profile_v2_enforces_cross_field_and_normalized_uniqueness() -> None:
    profile = _load(FIXTURES / "profile_v2.json")
    assert validate_novel_profile_v2(profile).schema_id == "compiler.novel-profile.v2"

    reversed_chars = deepcopy(profile)
    reversed_chars["prose"]["target_scene_chars"] = {"min": 1100, "max": 1000}
    with pytest.raises(CompilerContractError, match="scene_chars_order_invalid"):
        validate_novel_profile_v2(reversed_chars)
    reversed_ratio = deepcopy(profile)
    reversed_ratio["prose"]["dialogue_ratio"] = {"min": 0.8, "max": 0.2}
    with pytest.raises(CompilerContractError, match="dialogue_ratio_order_invalid"):
        validate_novel_profile_v2(reversed_ratio)
    duplicate = deepcopy(profile)
    duplicate["prose"]["forbidden_style_patterns"].append(" 总结式解释凶手动机 ")
    with pytest.raises(CompilerContractError, match="style_pattern_duplicate"):
        validate_novel_profile_v2(duplicate)


def test_render_and_judge_report_bind_exact_unicode_evidence() -> None:
    profile = _load(FIXTURES / "profile_v2.json")
    checklist = _load(FIXTURES / "checklist_scene_1.json")
    render = _load(FIXTURES / "scene_render_writer.json")
    report = _load(FIXTURES / "judge_required_pass.json")

    assert validate_scene_render(
        render, checklist=checklist, profile=profile
    ).stage.value == "writer"
    assert validate_prose_judge_report(
        report,
        checklist=checklist,
        render=render,
        profile=profile,
    ).role.value == "fidelity"

    bad_count = deepcopy(render)
    bad_count["character_count"] += 1
    with pytest.raises(CompilerContractError, match="character_count_mismatch"):
        validate_scene_render(bad_count, checklist=checklist, profile=profile)
    bad_round = deepcopy(render)
    bad_round["round"] = 1
    with pytest.raises(CompilerContractError, match="round_invalid"):
        validate_scene_render(bad_round, checklist=checklist, profile=profile)
    bad_evidence = deepcopy(report)
    bad_evidence["assessments"][0]["evidence"][0]["text"] = "改写引文"
    with pytest.raises(CompilerContractError, match="evidence_invalid"):
        validate_prose_judge_report(
            bad_evidence,
            checklist=checklist,
            render=render,
            profile=profile,
        )
    missing = deepcopy(report)
    missing["assessments"].pop()
    with pytest.raises(CompilerContractError, match="coverage_mismatch"):
        validate_prose_judge_report(
            missing,
            checklist=checklist,
            render=render,
            profile=profile,
        )


@pytest.mark.parametrize(
    "builder",
    [build_planner_input_bundle, build_planner_input_bundle_v2, build_planner_input_bundle_v3],
)
def test_planner_input_versions_accept_profile_v2_without_prose_affecting_constraints(
    builder: Any,
) -> None:
    original = _load(PLANNER_INPUT)
    profile_v1 = original["profile"]
    profile_v2 = {
        **profile_v1,
        "schema_id": "compiler.novel-profile.v2",
        "prose": deepcopy(_load(FIXTURES / "profile_v2.json")["prose"]),
    }
    common = {
        "narrative_ir": original["narrative_ir"],
        "exposure": original["exposure_plan"],
        "compile_mode": "preview",
    }
    v1_bundle = builder(profile=profile_v1, **common)
    v2_bundle = builder(profile=profile_v2, **common)

    assert v1_bundle["planning_constraints"] == v2_bundle["planning_constraints"]
    assert v2_bundle["profile"] == profile_v2
    assert planner_input_fingerprint(v1_bundle)["profile_hash"] != (
        planner_input_fingerprint(v2_bundle)["profile_hash"]
    )
    changed = deepcopy(profile_v2)
    changed["prose"]["style_brief"] += " 保持短句。"
    changed_bundle = builder(profile=changed, **common)
    assert changed_bundle["planning_constraints"] == v2_bundle["planning_constraints"]
    assert planner_input_fingerprint(changed_bundle)["profile_hash"] != (
        planner_input_fingerprint(v2_bundle)["profile_hash"]
    )
