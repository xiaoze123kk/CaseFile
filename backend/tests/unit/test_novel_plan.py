"""Deterministic PlannerInput and NovelPlan coverage."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    build_planner_input_bundle,
    canonical_json_sha256,
    canonicalize_novel_plan,
    planner_input_fingerprint,
    project_narrative_ir_json,
    story_planner_component_fingerprint,
    validate_novel_plan_candidate,
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
    with pytest.raises(CompilerContractError) as captured:
        validate_novel_plan_candidate(candidate, planner_input=bundle)
    assert captured.value.reason_code == "compiler_story_plan_temporal_order_invalid"

    candidate["scenes"][1]["presentation_mode"] = "flashback"
    validate_novel_plan_candidate(candidate, planner_input=bundle)


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
