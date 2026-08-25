"""Deterministic PlannerInput and NovelPlan coverage."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from casefile.domain.narrative_compiler import (
    CompilerContractError,
    build_planner_constraint_ir,
    build_planner_input_bundle,
    build_planner_input_bundle_v2,
    build_planner_model_view_v3,
    canonical_json_sha256,
    canonicalize_novel_plan,
    inspect_novel_plan_candidate,
    planner_constraint_ir_fingerprint,
    planner_input_fingerprint,
    planner_model_view_v3_fingerprint,
    project_narrative_ir_json,
    repair_novel_plan_candidate,
    story_planner_component_fingerprint,
    validate_novel_plan_candidate,
    validate_planner_constraint_ir,
    validate_planner_input_bundle,
    validate_planner_model_view_v3,
)

ROOT = Path(__file__).resolve().parents[3]


def _planner_input(*, exposure: bool = True, mode: str = "preview") -> dict[str, Any]:
    document = json.loads(
        (ROOT / "fixtures" / "casefiles" / "restart_loop.casefile.json").read_text(encoding="utf-8")
    )
    profile = {
        "schema_id": "compiler.novel-profile.v1",
        "structure": {"strategy": "three_act", "target_chapters": 1, "target_scenes": 2},
        "allowed_presentation_modes": ["linear", "flashback"],
        "exposure_policy": "bound_plan" if exposure else "planner_default",
    }
    exposure_binding = (
        {
            "draft_id": 1,
            "plan_revision_id": 1,
            "revision_no": 1,
            "frozen_payload": {
                "entries": [
                    {
                        "entry_key": "exposure_restart_log",
                        "sequence_no": 1,
                        "title": "重启日志",
                        "note": None,
                        "refs": [
                            {"object_type": "information_unit", "object_id": "info_restart_log"}
                        ],
                    }
                ]
            },
            "content_hash": "a" * 64,
        }
        if exposure
        else None
    )
    return build_planner_input_bundle(
        narrative_ir=project_narrative_ir_json(document),
        exposure=exposure_binding,
        profile=profile,
        compile_mode=mode,
    )


def _candidate() -> dict[str, Any]:
    return {
        "schema_id": "compiler.novel-plan-candidate.v1",
        "chapters": [
            {"chapter_id": "chapter_opening", "ordinal": 1, "act_ordinal": 1, "title": "循环"}
        ],
        "scenes": [
            {
                "scene_id": "scene_discovery",
                "chapter_id": "chapter_opening",
                "discourse_order": 1,
                "purpose": "hook",
                "intent": "研究者发现第七次重启留下的日志。",
                "presentation_mode": "linear",
                "pov_ref": {"object_type": "entity", "object_id": "ent_researcher"},
                "participant_refs": [{"object_type": "entity", "object_id": "ent_researcher"}],
                "location_ref": {"object_type": "location", "object_id": "loc_lab"},
                "event_refs": [{"object_type": "event", "object_id": "evt_restart_seven"}],
                "story_time_refs": [{"object_type": "event", "object_id": "evt_restart_seven"}],
                "basis_refs": [
                    {"object_type": "event", "object_id": "evt_restart_seven"},
                    {"object_type": "information_unit", "object_id": "info_restart_log"},
                ],
                "exposure": [{"entry_key": "exposure_restart_log", "action": "introduce"}],
                "resolutions": [],
                "prerequisite_scene_ids": [],
            },
            {
                "scene_id": "scene_resolution",
                "chapter_id": "chapter_opening",
                "discourse_order": 2,
                "purpose": "resolution",
                "intent": "研究者确认根因并保留关机条件。",
                "presentation_mode": "flashback",
                "pov_ref": {"object_type": "entity", "object_id": "ent_researcher"},
                "participant_refs": [{"object_type": "entity", "object_id": "ent_researcher"}],
                "location_ref": {"object_type": "location", "object_id": "loc_lab"},
                "event_refs": [{"object_type": "event", "object_id": "evt_restart_seven"}],
                "story_time_refs": [{"object_type": "event", "object_id": "evt_restart_seven"}],
                "basis_refs": [
                    {"object_type": "resolution_spec", "object_id": "res_root_cause"},
                    {"object_type": "resolution_spec", "object_id": "res_shutdown_rule"},
                ],
                "exposure": [],
                "resolutions": [
                    {
                        "resolution_ref": {
                            "object_type": "resolution_spec",
                            "object_id": "res_root_cause",
                        },
                        "action": "resolve",
                    },
                    {
                        "resolution_ref": {
                            "object_type": "resolution_spec",
                            "object_id": "res_shutdown_rule",
                        },
                        "action": "intentionally_unresolved",
                    },
                ],
                "prerequisite_scene_ids": ["scene_discovery"],
            },
        ],
    }


def test_planner_input_and_fingerprint_are_deterministic() -> None:
    first = _planner_input()
    second = copy.deepcopy(first)
    assert first == second
    assert planner_input_fingerprint(first) == planner_input_fingerprint(second)
    assert canonical_json_sha256(first) == canonical_json_sha256(second)


def test_planner_input_v2_projects_and_reproves_authoritative_constraints() -> None:
    v1 = _planner_input()
    v2 = build_planner_input_bundle_v2(
        narrative_ir=v1["narrative_ir"],
        exposure=v1["exposure_plan"],
        profile=v1["profile"],
        compile_mode="preview",
    )
    assert v2["schema_id"] == "compiler.story-planner-input.v2"
    view = v2["planner_view"]
    assert view["hard_constraints"]["structure"] == v1["planning_constraints"]
    assert len(view["hard_constraints"]["resolution_obligations"]) == 2
    assert view["planning_context"]["knowledge_snapshots"]
    assert view["planning_context"]["author_guidance"][0]["is_hard_constraint"] is False
    assert planner_input_fingerprint(v2)["planner_view_hash"]

    v1_component = story_planner_component_fingerprint(
        planner_input=v1,
        prompt_version="story-planner-v3",
        prompt_sha256="b" * 64,
        provider="deepseek",
        model_id="deepseek-v4-pro",
        provider_config_version=1,
    )
    v2_component = story_planner_component_fingerprint(
        planner_input=v2,
        prompt_version="story-planner-v3",
        prompt_sha256="b" * 64,
        provider="deepseek",
        model_id="deepseek-v4-pro",
        provider_config_version=1,
    )
    assert v1_component["planner_input_schema_id"] == "compiler.story-planner-input.v1"
    assert v2_component["planner_input_schema_id"] == "compiler.story-planner-input.v2"
    assert v2_component["candidate_repair_version"] == "compiler.story-plan-mode-repair.v1"
    assert canonical_json_sha256(v1_component) != canonical_json_sha256(v2_component)

    tampered = copy.deepcopy(v2)
    tampered["planner_view"]["hard_constraints"]["structure"]["target_scenes"] = 99
    with pytest.raises(CompilerContractError) as captured:
        validate_planner_input_bundle(tampered)
    assert captured.value.reason_code == "compiler_story_planner_view_mismatch"


def test_planner_input_v2_preserves_unknown_time_without_inventing_anchor() -> None:
    v1 = _planner_input()
    v1["narrative_ir"]["objects"]["events"][0]["value"]["time"] = {
        "kind": "unknown",
    }
    v2 = build_planner_input_bundle_v2(
        narrative_ir=v1["narrative_ir"],
        exposure=v1["exposure_plan"],
        profile=v1["profile"],
        compile_mode="preview",
    )
    assert v2["planner_view"]["hard_constraints"]["chronology_anchors"] == []


def test_planner_constraint_ir_compiles_precedence_ranks_and_stable_identity() -> None:
    bundle = _planner_input()
    earlier = copy.deepcopy(bundle["narrative_ir"]["objects"]["events"][0])
    earlier["object_ref"]["object_id"] = "evt_restart_six"
    earlier["value"]["id"] = "evt_restart_six"
    earlier["value"]["time"]["start"] = "2042-05-31T20:00"
    earlier["value"]["time"]["end"] = "2042-05-31T20:03"
    bundle["narrative_ir"]["objects"]["events"].append(earlier)
    second_entry = copy.deepcopy(bundle["exposure_plan"]["frozen_payload"]["entries"][0])
    second_entry["entry_key"] = "exposure_second"
    second_entry["sequence_no"] = 2
    bundle["exposure_plan"]["frozen_payload"]["entries"].append(second_entry)

    constraint_ir = build_planner_constraint_ir(bundle)

    assert constraint_ir["exposure"] == {
        "introduce_order": ["exposure_restart_log", "exposure_second"],
        "precedence_edges": [
            {
                "before_entry_key": "exposure_restart_log",
                "after_entry_key": "exposure_second",
            }
        ],
    }
    assert [item["rank"] for item in constraint_ir["temporal"]["anchors"]] == [1, 2]
    assert (
        validate_planner_constraint_ir(
            copy.deepcopy(constraint_ir), planner_input=copy.deepcopy(bundle)
        )
        == constraint_ir
    )
    assert planner_constraint_ir_fingerprint(constraint_ir)["content_hash"] == (
        canonical_json_sha256(constraint_ir)
    )

    tampered = copy.deepcopy(constraint_ir)
    tampered["temporal"]["anchors"][0]["rank"] = 99
    with pytest.raises(CompilerContractError) as captured:
        validate_planner_constraint_ir(tampered, planner_input=bundle)
    assert captured.value.reason_code == "compiler_planner_constraint_ir_mismatch"


def test_planner_model_view_v3_is_compact_reprovable_and_source_ref_free() -> None:
    v1 = _planner_input()
    bundle = build_planner_input_bundle_v2(
        narrative_ir=v1["narrative_ir"],
        exposure=v1["exposure_plan"],
        profile=v1["profile"],
        compile_mode="preview",
    )

    model_view = build_planner_model_view_v3(bundle)

    assert model_view["schema_id"] == "compiler.story-planner-model-view.v3"
    assert "narrative_ir" not in model_view
    assert "exposure_plan" not in model_view
    assert "source_ref" not in json.dumps(model_view, ensure_ascii=False)
    assert len(json.dumps(model_view, ensure_ascii=False)) < len(
        json.dumps(bundle, ensure_ascii=False)
    )
    assert (
        validate_planner_model_view_v3(
            copy.deepcopy(model_view), planner_input=copy.deepcopy(bundle)
        )
        == model_view
    )
    assert planner_model_view_v3_fingerprint(model_view)["content_hash"] == (
        canonical_json_sha256(model_view)
    )

    tampered = copy.deepcopy(model_view)
    tampered["hard_constraints"]["temporal"]["anchors"] = []
    with pytest.raises(CompilerContractError) as captured:
        validate_planner_model_view_v3(tampered, planner_input=bundle)
    assert captured.value.reason_code == "compiler_planner_model_view_v3_mismatch"


def test_preview_default_exposure_is_allowed_but_canonical_requires_binding() -> None:
    assert _planner_input(exposure=False)["exposure_plan"] is None
    with pytest.raises(CompilerContractError) as captured:
        _planner_input(exposure=False, mode="canonical")
    assert captured.value.reason_code == "compiler_story_planner_exposure_required"


def test_candidate_canonicalization_is_stable_and_derives_indexes_and_sources() -> None:
    planner_input = _planner_input()
    candidate = _candidate()
    validate_novel_plan_candidate(candidate, planner_input=planner_input)
    component = story_planner_component_fingerprint(
        planner_input=planner_input,
        prompt_version="story-planner-v1",
        prompt_sha256="b" * 64,
        provider="fake",
        model_id="fake-story-planner",
        provider_config_version=1,
    )
    component_hash = canonical_json_sha256(component)
    first = canonicalize_novel_plan(
        candidate,
        planner_input=planner_input,
        planner_version="compiler.story-planner.v1",
        component_fingerprint=component_hash,
    )
    second = canonicalize_novel_plan(
        copy.deepcopy(candidate),
        planner_input=copy.deepcopy(planner_input),
        planner_version="compiler.story-planner.v1",
        component_fingerprint=component_hash,
    )
    assert first == second
    assert first["indexes"]["chapter_scene_ids"] == {
        "chapter_opening": ["scene_discovery", "scene_resolution"]
    }
    assert first["scenes"][0]["source_refs"]


def test_story_time_and_discourse_order_require_explicit_flashback() -> None:
    bundle = _planner_input()
    earlier = copy.deepcopy(bundle["narrative_ir"]["objects"]["events"][0])
    earlier["object_ref"]["object_id"] = "evt_restart_six"
    earlier["value"]["id"] = "evt_restart_six"
    earlier["value"]["time"]["start"] = "2042-05-31T20:00"
    earlier["value"]["time"]["end"] = "2042-05-31T20:03"
    bundle["narrative_ir"]["objects"]["events"].append(earlier)
    candidate = _candidate()
    earlier_ref = {"object_type": "event", "object_id": "evt_restart_six"}
    candidate["scenes"][1]["event_refs"] = [earlier_ref]
    candidate["scenes"][1]["story_time_refs"] = [earlier_ref]

    candidate["scenes"][1]["presentation_mode"] = "linear"
    report = inspect_novel_plan_candidate(candidate, planner_input=bundle)
    assert report.valid is False
    assert report.violations[0].code == "compiler_story_plan_temporal_order_invalid"
    assert report.violations[0].details == {
        "scene_id": "scene_resolution",
        "previous_scene_id": "scene_discovery",
        "previous_time": "2042-06-01T20:00:00",
        "current_time": "2042-05-31T20:00:00",
        "presentation_mode": "linear",
    }
    with pytest.raises(CompilerContractError) as captured:
        validate_novel_plan_candidate(candidate, planner_input=bundle)
    assert captured.value.reason_code == "compiler_story_plan_temporal_order_invalid"

    candidate["scenes"][1]["presentation_mode"] = "flashback"
    validate_novel_plan_candidate(candidate, planner_input=bundle)


def test_temporal_mode_repair_relocates_flashback_without_changing_other_fields() -> None:
    bundle = _planner_input()
    later = copy.deepcopy(bundle["narrative_ir"]["objects"]["events"][0])
    later["object_ref"]["object_id"] = "evt_restart_eight"
    later["value"]["id"] = "evt_restart_eight"
    later["value"]["time"]["start"] = "2042-06-01T21:00"
    later["value"]["time"]["end"] = "2042-06-01T21:03"
    bundle["narrative_ir"]["objects"]["events"].append(later)
    candidate = _candidate()
    later_ref = {"object_type": "event", "object_id": "evt_restart_eight"}
    candidate["scenes"][1]["event_refs"] = [later_ref]
    candidate["scenes"][1]["story_time_refs"] = [later_ref]
    original = copy.deepcopy(candidate)

    repaired = repair_novel_plan_candidate(candidate, planner_input=bundle)

    assert repaired.applied is True
    assert repaired.after.valid is True
    assert repaired.changes == (
        {
            "scene_id": "scene_discovery",
            "field": "presentation_mode",
            "before": "linear",
            "after": "flashback",
        },
        {
            "scene_id": "scene_resolution",
            "field": "presentation_mode",
            "before": "flashback",
            "after": "linear",
        },
    )
    assert sum(
        scene["presentation_mode"] == "flashback" for scene in repaired.candidate["scenes"]
    ) == 1
    for before, after in zip(original["scenes"], repaired.candidate["scenes"], strict=True):
        assert {key: value for key, value in before.items() if key != "presentation_mode"} == {
            key: value for key, value in after.items() if key != "presentation_mode"
        }
    validate_novel_plan_candidate(repaired.candidate, planner_input=bundle)


def test_temporal_mode_repair_adds_flashback_only_when_profile_allows_it() -> None:
    bundle = _planner_input()
    earlier = copy.deepcopy(bundle["narrative_ir"]["objects"]["events"][0])
    earlier["object_ref"]["object_id"] = "evt_restart_six"
    earlier["value"]["id"] = "evt_restart_six"
    earlier["value"]["time"]["start"] = "2042-05-31T20:00"
    earlier["value"]["time"]["end"] = "2042-05-31T20:03"
    bundle["narrative_ir"]["objects"]["events"].append(earlier)
    candidate = _candidate()
    earlier_ref = {"object_type": "event", "object_id": "evt_restart_six"}
    candidate["scenes"][1]["event_refs"] = [earlier_ref]
    candidate["scenes"][1]["story_time_refs"] = [earlier_ref]
    candidate["scenes"][1]["presentation_mode"] = "linear"

    repaired = repair_novel_plan_candidate(candidate, planner_input=bundle)
    assert repaired.applied is True
    assert repaired.candidate["scenes"][1]["presentation_mode"] == "flashback"

    linear_only = copy.deepcopy(bundle)
    linear_only["profile"]["allowed_presentation_modes"] = ["linear"]
    linear_only["planning_constraints"]["allowed_presentation_modes"] = ["linear"]
    rejected = repair_novel_plan_candidate(candidate, planner_input=linear_only)
    assert rejected.applied is False
    assert rejected.candidate == candidate


def test_temporal_mode_repair_does_not_touch_non_temporal_failure() -> None:
    bundle = _planner_input()
    candidate = _candidate()
    candidate["scenes"][0]["exposure"] = []

    repaired = repair_novel_plan_candidate(candidate, planner_input=bundle)

    assert repaired.applied is False
    assert repaired.candidate == candidate
    assert repaired.before.violations[0].code == "compiler_story_plan_exposure_violation"


def test_validation_report_explains_exposure_order_without_repairing_candidate() -> None:
    bundle = _planner_input()
    second_entry = copy.deepcopy(bundle["exposure_plan"]["frozen_payload"]["entries"][0])
    second_entry["entry_key"] = "exposure_second"
    second_entry["sequence_no"] = 2
    bundle["exposure_plan"]["frozen_payload"]["entries"].append(second_entry)
    candidate = _candidate()
    candidate["scenes"][0]["exposure"] = [{"entry_key": "exposure_second", "action": "introduce"}]
    candidate["scenes"][1]["exposure"] = [
        {"entry_key": "exposure_restart_log", "action": "introduce"}
    ]
    original = copy.deepcopy(candidate)

    report = inspect_novel_plan_candidate(candidate, planner_input=bundle)

    assert report.valid is False
    assert report.violations[0].code == "compiler_story_plan_exposure_violation"
    assert report.violations[0].details == {
        "expected_introduce_order": ["exposure_restart_log", "exposure_second"],
        "actual_introduce_order": ["exposure_second", "exposure_restart_log"],
        "first_mismatch_index": 0,
    }
    assert candidate == original


@pytest.mark.parametrize(
    ("mutate", "reason_code"),
    [
        (
            lambda value: value["scenes"][1].update(discourse_order=1),
            "compiler_story_plan_scene_invalid",
        ),
        (
            lambda value: value["scenes"][1].update(chapter_id="chapter_missing"),
            "compiler_story_plan_chapter_reference_invalid",
        ),
        (
            lambda value: value["scenes"][0].update(prerequisite_scene_ids=["scene_resolution"]),
            "compiler_story_plan_dependency_cycle",
        ),
        (
            lambda value: value["scenes"][0]["event_refs"][0].update(object_id="evt_missing"),
            "compiler_story_plan_reference_invalid",
        ),
        (
            lambda value: value["scenes"][0]["basis_refs"][0].update(object_id="evt_missing"),
            "compiler_story_plan_reference_invalid",
        ),
        (
            lambda value: value["scenes"][0]["story_time_refs"][0].update(
                object_type="entity", object_id="ent_researcher"
            ),
            "compiler_story_plan_temporal_invalid",
        ),
        (
            lambda value: value["scenes"][0]["exposure"][0].update(entry_key="exposure_unknown"),
            "compiler_story_plan_exposure_reference_invalid",
        ),
        (
            lambda value: value["scenes"][0].update(exposure=[]),
            "compiler_story_plan_exposure_violation",
        ),
        (
            lambda value: value["scenes"][1].update(resolutions=[]),
            "compiler_story_plan_resolution_uncovered",
        ),
        (
            lambda value: value["scenes"][0].update(presentation_mode="flashforward"),
            "compiler_story_plan_presentation_mode_invalid",
        ),
    ],
)
def test_semantic_validator_rejects_invalid_plans(mutate: Any, reason_code: str) -> None:
    candidate = _candidate()
    mutate(candidate)
    with pytest.raises(CompilerContractError) as captured:
        validate_novel_plan_candidate(candidate, planner_input=_planner_input())
    assert captured.value.reason_code == reason_code
