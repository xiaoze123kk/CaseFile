"""Generate the frozen Novel Plan v2 capability matrix.

Run from ``backend`` with its project environment so the generated PlannerInput
artifacts use the same deterministic projector and validator as production.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from casefile.domain.narrative_compiler import (
    build_planner_input_bundle,
    canonical_json_sha256,
    project_narrative_ir_json,
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "fixtures" / "novel_plan_benchmark" / "v2"
CAPABILITIES = (
    "linear_mystery",
    "nonlinear_reveal",
    "multiple_suspects",
    "false_belief",
    "competing_hypotheses",
    "resolution_closure",
    "flashback_chronology",
    "complex_mixed",
)
VARIANTS = {
    "basic": {"chapters": 1, "scenes": 3, "participants": 2},
    "decoy": {"chapters": 2, "scenes": 4, "participants": 3},
    "dense": {"chapters": 2, "scenes": 5, "participants": 3},
}


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _rich_casefile() -> dict[str, Any]:
    document = json.loads(
        (ROOT / "fixtures" / "casefiles" / "m3_reasoning_closure.casefile.json").read_text(
            encoding="utf-8"
        )
    )
    researcher = document["entities"][0]
    knowledge = researcher["knowledge_states"][0]
    knowledge["false_belief_refs"] = [{"object_type": "claim", "object_id": "claim_manual_trigger"}]

    observer = copy.deepcopy(researcher)
    observer["id"] = "ent_safety_observer"
    observer["name"] = "安全观察员"
    observer["aliases"] = []
    observer["knowledge_states"] = []
    document["entities"].append(observer)

    earlier_event = copy.deepcopy(document["events"][0])
    earlier_event["id"] = "evt_restart_six"
    earlier_event["title"] = "系统第六次重启"
    earlier_event["time"] = {
        "kind": "range",
        "start": "2042-06-01T19:00",
        "end": "2042-06-01T19:03",
        "precision": "minute",
    }
    earlier_event["cause_refs"] = [{"object_type": "claim", "object_id": "claim_manual_trigger"}]
    earlier_event["observed_by_refs"] = [
        {"object_type": "entity", "object_id": "ent_safety_observer"}
    ]
    document["events"].insert(0, earlier_event)
    return document


def _profile(capability: str, variant: str) -> dict[str, Any]:
    shape = VARIANTS[variant]
    nonlinear = capability in {
        "nonlinear_reveal",
        "flashback_chronology",
        "complex_mixed",
    }
    return {
        "schema_id": "compiler.novel-profile.v1",
        "structure": {
            "strategy": "three_act",
            "target_chapters": shape["chapters"],
            "target_scenes": shape["scenes"],
        },
        "allowed_presentation_modes": ["linear", "flashback"] if nonlinear else ["linear"],
        "exposure_policy": "bound_plan",
    }


def _exposure(capability: str, variant: str) -> dict[str, Any]:
    intent = {
        "linear_mystery": "保持线性调查，并按钩子、揭示、收束推进。",
        "nonlinear_reveal": "用倒叙重排披露，但保持依赖链清晰。",
        "multiple_suspects": "让多个相关参与者进入调查与排除过程。",
        "false_belief": "先建立人工触发误信，再用证据重新解释。",
        "competing_hypotheses": "并置人工触发与自动保护两条竞争假说。",
        "resolution_closure": "在最终场景完整回答两项 Resolution。",
        "flashback_chronology": "先呈现第七次重启，再倒叙第六次重启。",
        "complex_mixed": "混合倒叙、多参与者、竞争假说、误信反转与收束。",
    }[capability]
    frozen_payload = {
        "entries": [
            {
                "entry_key": "exposure_manual_trace",
                "sequence_no": 1,
                "title": "人工重启痕迹",
                "note": f"{intent} 变体：{variant}。先建立人工触发假说。",
                "refs": [{"object_type": "information_unit", "object_id": "info_manual_trace"}],
            },
            {
                "entry_key": "exposure_restart_log",
                "sequence_no": 2,
                "title": "备份系统日志",
                "note": f"{intent} 变体：{variant}。后披露自动保护触发证据。",
                "refs": [{"object_type": "information_unit", "object_id": "info_restart_log"}],
            },
        ]
    }
    return {
        "draft_id": 1,
        "plan_revision_id": 2,
        "revision_no": 2,
        "frozen_payload": frozen_payload,
        "content_hash": canonical_json_sha256(frozen_payload),
    }


def _ref(object_type: str, object_id: str) -> dict[str, str]:
    return {"object_type": object_type, "object_id": object_id}


def _reference(capability: str, variant: str) -> dict[str, Any]:
    shape = VARIANTS[variant]
    chapter_ids = [f"chapter_{index}" for index in range(1, shape["chapters"] + 1)]
    chapters = [
        {
            "chapter_id": chapter_id,
            "ordinal": index,
            "act_ordinal": min(index, 3),
            "title": f"第{index}章",
        }
        for index, chapter_id in enumerate(chapter_ids, start=1)
    ]
    participant_ids = ["ent_researcher", "ent_backup_system"]
    if shape["participants"] == 3:
        participant_ids.append("ent_safety_observer")
    participants = [_ref("entity", object_id) for object_id in participant_ids]
    scene_count = shape["scenes"]
    scenes: list[dict[str, Any]] = []
    for index in range(1, scene_count + 1):
        is_final = index == scene_count
        chapter_index = min((index - 1) * shape["chapters"] // scene_count, shape["chapters"] - 1)
        flashback = (
            capability
            in {
                "nonlinear_reveal",
                "flashback_chronology",
                "complex_mixed",
            }
            and index == 2
        )
        event_id = "evt_restart_six" if flashback else "evt_restart_seven"
        basis_refs = [_ref("event", event_id)]
        if index == 1:
            basis_refs.extend(
                [
                    _ref("information_unit", "info_manual_trace"),
                    _ref("claim", "claim_manual_trigger"),
                ]
            )
        elif index == 2:
            basis_refs.extend(
                [
                    _ref("information_unit", "info_restart_log"),
                    _ref("claim", "claim_backup_trigger"),
                ]
            )
        else:
            basis_refs.extend(
                [
                    _ref("hypothesis", "hyp_manual_restart"),
                    _ref("hypothesis", "hyp_automatic_restart"),
                ]
            )
        if is_final:
            basis_refs.extend(
                [
                    _ref("resolution_spec", "res_root_cause"),
                    _ref("resolution_spec", "res_shutdown_rule"),
                ]
            )
        exposure: list[dict[str, str]] = []
        if index == 1:
            exposure.append({"entry_key": "exposure_manual_trace", "action": "introduce"})
        if index == 2:
            exposure.append({"entry_key": "exposure_restart_log", "action": "introduce"})
        if capability in {"false_belief", "complex_mixed"} and is_final:
            exposure.append({"entry_key": "exposure_manual_trace", "action": "reinterpret"})
        scenes.append(
            {
                "scene_id": f"scene_{index}",
                "chapter_id": chapter_ids[chapter_index],
                "discourse_order": index,
                "purpose": (
                    "resolution"
                    if is_final
                    else "hook"
                    if index == 1
                    else "reveal"
                    if index == scene_count - 1
                    else "investigation"
                ),
                "intent": f"推进 {capability} 的 {variant} 场景 {index}。",
                "presentation_mode": "flashback" if flashback else "linear",
                "pov_ref": _ref("entity", "ent_researcher"),
                "participant_refs": participants,
                "location_ref": _ref("location", "loc_lab"),
                "event_refs": [_ref("event", event_id)],
                "story_time_refs": [_ref("event", event_id)],
                "basis_refs": basis_refs,
                "exposure": exposure,
                "resolutions": (
                    [
                        {
                            "resolution_ref": _ref("resolution_spec", "res_root_cause"),
                            "action": "resolve",
                        },
                        {
                            "resolution_ref": _ref("resolution_spec", "res_shutdown_rule"),
                            "action": "resolve",
                        },
                    ]
                    if is_final
                    else []
                ),
                "prerequisite_scene_ids": [] if index == 1 else [f"scene_{index - 1}"],
            }
        )
    return {
        "schema_id": "compiler.novel-plan-candidate.v1",
        "chapters": chapters,
        "scenes": scenes,
    }


def _outcome_invariants(capability: str, variant: str) -> list[dict[str, Any]]:
    shape = VARIANTS[variant]
    if capability == "linear_mystery":
        return [
            {"kind": "all_presentation_modes", "allowed": ["linear"]},
            {"kind": "purpose_order", "values": ["hook", "reveal", "resolution"]},
        ]
    if capability == "nonlinear_reveal":
        return [
            {"kind": "presentation_mode_present", "value": "flashback"},
            {"kind": "dependency_chain_min_length", "value": shape["scenes"]},
        ]
    if capability == "multiple_suspects":
        return [
            {"kind": "min_distinct_participant_refs", "value": shape["participants"]},
            {"kind": "basis_refs_include_all", "refs": [_ref("claim", "claim_manual_trigger")]},
        ]
    if capability == "false_belief":
        return [
            {
                "kind": "exposure_action_present",
                "entry_key": "exposure_manual_trace",
                "action": "reinterpret",
            },
            {"kind": "basis_refs_include_all", "refs": [_ref("claim", "claim_manual_trigger")]},
        ]
    if capability == "competing_hypotheses":
        return [
            {
                "kind": "basis_refs_include_all",
                "refs": [
                    _ref("hypothesis", "hyp_manual_restart"),
                    _ref("hypothesis", "hyp_automatic_restart"),
                ],
            },
            {"kind": "purpose_present", "value": "reveal"},
        ]
    if capability == "resolution_closure":
        return [
            {
                "kind": "resolution_actions",
                "refs": [
                    _ref("resolution_spec", "res_root_cause"),
                    _ref("resolution_spec", "res_shutdown_rule"),
                ],
                "allowed": ["resolve"],
            },
            {"kind": "resolution_in_final_scene"},
        ]
    if capability == "flashback_chronology":
        return [
            {
                "kind": "flashback_after_event",
                "earlier_event_ref": _ref("event", "evt_restart_six"),
                "later_event_ref": _ref("event", "evt_restart_seven"),
            },
            {"kind": "presentation_mode_present", "value": "flashback"},
        ]
    return [
        {"kind": "presentation_mode_present", "value": "flashback"},
        {"kind": "min_distinct_participant_refs", "value": shape["participants"]},
        {
            "kind": "basis_refs_include_all",
            "refs": [
                _ref("hypothesis", "hyp_manual_restart"),
                _ref("hypothesis", "hyp_automatic_restart"),
            ],
        },
        {
            "kind": "exposure_action_present",
            "entry_key": "exposure_manual_trace",
            "action": "reinterpret",
        },
        {"kind": "resolution_in_final_scene"},
    ]


def main() -> None:
    narrative_ir = project_narrative_ir_json(_rich_casefile())
    tasks: list[dict[str, Any]] = []
    for capability in CAPABILITIES:
        for variant in VARIANTS:
            task_id = f"{capability}__{variant}"
            planner_input = build_planner_input_bundle(
                narrative_ir=narrative_ir,
                exposure=_exposure(capability, variant),
                profile=_profile(capability, variant),
                compile_mode="canonical",
            )
            reference = _reference(capability, variant)
            input_path = f"inputs/{task_id}.json"
            reference_path = f"references/{task_id}.json"
            _write(OUTPUT / input_path, planner_input)
            _write(OUTPUT / reference_path, reference)
            tasks.append(
                {
                    "task_id": task_id,
                    "primary_capability": capability,
                    "variant": variant,
                    "planner_input": input_path,
                    "planner_input_hash": canonical_json_sha256(planner_input),
                    "reference": reference_path,
                    "outcome_invariants": _outcome_invariants(capability, variant),
                }
            )
    _write(
        OUTPUT / "suite.json",
        {
            "schema_id": "benchmark.novel-plan-suite.v2",
            "suite_id": "novel-plan-capability-v2",
            "tasks": tasks,
        },
    )


if __name__ == "__main__":
    main()
