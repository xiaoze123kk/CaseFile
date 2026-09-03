"""N4.5 prose contracts and deterministic Scene checklist construction."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from casefile_contracts import (
    NarrativeIR,
    NovelProfileV2,
    ProseJudgeChecklist,
    ProseJudgeReport,
    ScenePlanIRV2,
    SceneRender,
    SceneRenderCandidate,
)
from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)

PROSE_CHECKLIST_SCHEMA_ID = "compiler.prose-judge-checklist.v1"
PROSE_CHECKLIST_POLICY_VERSION = "prose-checklist-policy-v1"

_POLICY_SPEC: dict[str, Any] = {
    "version": PROSE_CHECKLIST_POLICY_VERSION,
    "order": [
        "beat_realization_with_event_modality",
        "scene_outcome",
        "allowed_reveal_control",
        "forbidden_reveal_control",
        "pov_knowledge",
        "location_time",
        "beat_causality_ordering",
        "scene_causality_ordering",
        "major_hallucination",
    ],
    "check_id": "check_{scene_id}_{ordinal:03d}",
    "evidence_policy": {
        "required": "required_on_pass",
        "forbidden": "required_on_fail",
    },
    "templates": {
        "beat_realization": "正文必须实现 Beat {beat_id}：{directive}",
        "event_modality": (
            "正文必须以 ScenePlan 规定的模态实现事件 {event_ref}，"
            "不得改写其事实性、否定、条件或时间语义。"
        ),
        "scene_outcome": "正文必须形成 ScenePlan 规定的场景结果：{outcome}",
        "allowed_reveal": "正文必须按 {action} 实现允许的 Reveal {entry_key}，不得改变其信息范围。",
        "forbidden_reveal": "正文不得提前呈现禁止的 Reveal {entry_key}。",
        "pov_knowledge": "正文不得让 POV 或其他角色知道冻结入站状态未授权的事实。",
        "location_time": "正文必须保持地点 {location_ref} 与故事时间 {story_time_refs} 一致。",
        "beat_causality": "正文必须先实现前置 Beat {prerequisite_beat_id}，再实现 Beat {beat_id}。",
        "scene_causality": (
            "当前 Scene 必须承接前置 Scene {prerequisite_scene_id} 已接受的结果，"
            "不得颠倒场景因果顺序。"
        ),
        "major_hallucination": (
            "正文不得新增 ScenePlan、NarrativeIR 或冻结状态未授权的"
            "重要人物、事件、Reveal、结论或状态变化。"
        ),
    },
}
PROSE_CHECKLIST_POLICY_HASH = canonical_json_sha256(_POLICY_SPEC)


def validate_novel_profile_v2(profile: dict[str, Any]) -> NovelProfileV2:
    """Validate Profile v2, including constraints JSON Schema cannot express."""

    try:
        parsed = NovelProfileV2.model_validate(profile)
    except ValidationError as error:
        raise CompilerContractError("compiler_novel_profile_v2_invalid") from error
    value = parsed.model_dump(mode="json")
    prose = value["prose"]
    if prose["target_scene_chars"]["min"] > prose["target_scene_chars"]["max"]:
        raise CompilerContractError("compiler_novel_profile_v2_scene_chars_order_invalid")
    if prose["dialogue_ratio"]["min"] > prose["dialogue_ratio"]["max"]:
        raise CompilerContractError("compiler_novel_profile_v2_dialogue_ratio_order_invalid")
    normalized = [
        unicodedata.normalize("NFKC", item).strip().casefold()
        for item in prose["forbidden_style_patterns"]
    ]
    if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
        raise CompilerContractError("compiler_novel_profile_v2_style_pattern_duplicate")
    return parsed


def build_prose_judge_checklist(
    *,
    scene_plan: dict[str, Any],
    narrative_ir: dict[str, Any],
    profile: dict[str, Any],
    scene_id: str,
    previous_scene_render: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one complete, provider-independent Checklist from frozen inputs."""

    plan = _validate_model(
        ScenePlanIRV2, scene_plan, "compiler_prose_checklist_scene_plan_invalid"
    )
    narrative = _validate_model(
        NarrativeIR, narrative_ir, "compiler_prose_checklist_narrative_ir_invalid"
    )
    profile_model = validate_novel_profile_v2(profile)
    plan_json = plan.model_dump(mode="json")
    narrative_json = narrative.model_dump(mode="json")
    profile_json = profile_model.model_dump(mode="json")
    narrative_hash = canonical_json_sha256(narrative_json)
    plan_hash = canonical_json_sha256(plan_json)
    profile_hash = canonical_json_sha256(profile_json)
    if plan_json["source"]["narrative_ir_hash"] != narrative_hash:
        raise CompilerContractError("compiler_prose_checklist_narrative_ir_hash_mismatch")

    scenes = sorted(plan_json["scenes"], key=lambda item: item["discourse_order"])
    try:
        scene_index = next(
            index for index, item in enumerate(scenes) if item["scene_id"] == scene_id
        )
    except StopIteration as error:
        raise CompilerContractError("compiler_prose_checklist_scene_unknown") from error
    scene = scenes[scene_index]
    previous = _validate_previous_scene(
        previous_scene_render,
        scenes=scenes,
        scene_index=scene_index,
        profile=profile_json,
        scene_plan_hash=plan_hash,
        profile_hash=profile_hash,
    )
    states = _replay_scene_states(plan_json)
    before, after = states[scene_id]
    beats_by_id = {item["beat_id"]: item for item in plan_json["beats"]}
    beats = [beats_by_id[beat_id] for beat_id in scene["beat_ids"]]
    checks = _build_checks(scene, beats)
    context_without_catalog = _scene_context(
        scene=scene,
        beats=beats,
        before=before,
        after=after,
        previous=previous,
    )
    object_catalog = _build_object_catalog(
        narrative_json,
        seed_values=[scene, beats, before, after, checks],
    )
    checklist = {
        "schema_id": PROSE_CHECKLIST_SCHEMA_ID,
        "scene_id": scene_id,
        "scene_ordinal": scene["discourse_order"],
        "source": {
            "scene_plan_hash": plan_hash,
            "narrative_ir_hash": narrative_hash,
            "profile_hash": profile_hash,
            "previous_scene_render_hash": (
                None if previous is None else canonical_json_sha256(previous)
            ),
            "checklist_policy_version": PROSE_CHECKLIST_POLICY_VERSION,
            "checklist_policy_hash": PROSE_CHECKLIST_POLICY_HASH,
        },
        "scene_context": {**context_without_catalog, "object_catalog": object_catalog},
        "checks": checks,
    }
    try:
        return ProseJudgeChecklist.model_validate(checklist).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_prose_checklist_invalid") from error


def validate_prose_judge_checklist(
    checklist: dict[str, Any],
    *,
    scene_plan: dict[str, Any],
    narrative_ir: dict[str, Any],
    profile: dict[str, Any],
    previous_scene_render: dict[str, Any] | None = None,
) -> ProseJudgeChecklist:
    """Rebuild the Checklist and require byte-independent canonical equality."""

    try:
        parsed = ProseJudgeChecklist.model_validate(checklist)
    except ValidationError as error:
        raise CompilerContractError("compiler_prose_checklist_invalid") from error
    parsed_json = parsed.model_dump(mode="json")
    expected = build_prose_judge_checklist(
        scene_plan=scene_plan,
        narrative_ir=narrative_ir,
        profile=profile,
        scene_id=parsed_json["scene_id"],
        previous_scene_render=previous_scene_render,
    )
    if parsed_json != expected:
        raise CompilerContractError("compiler_prose_checklist_mismatch")
    return parsed


def prose_checklist_fingerprint(checklist: dict[str, Any]) -> dict[str, Any]:
    """Return the identity needed to persist or reuse one successful Checklist."""

    try:
        parsed = ProseJudgeChecklist.model_validate(checklist).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_prose_checklist_invalid") from error
    return {
        "schema_id": PROSE_CHECKLIST_SCHEMA_ID,
        "checklist_hash": canonical_json_sha256(parsed),
        **parsed["source"],
    }


def validate_scene_render(
    render: dict[str, Any],
    *,
    checklist: dict[str, Any],
    profile: dict[str, Any],
) -> SceneRender:
    """Validate server-owned render identity, length, stage and block invariants."""

    try:
        parsed = SceneRender.model_validate(render)
        checklist_json = ProseJudgeChecklist.model_validate(checklist).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_render_invalid") from error
    profile_json = validate_novel_profile_v2(profile).model_dump(mode="json")
    value = parsed.model_dump(mode="json")
    if (value["scene_id"], value["scene_ordinal"]) != (
        checklist_json["scene_id"],
        checklist_json["scene_ordinal"],
    ):
        raise CompilerContractError("compiler_scene_render_scene_mismatch")
    source = value["source"]
    if source != {
        "checklist_hash": canonical_json_sha256(checklist_json),
        "profile_hash": checklist_json["source"]["profile_hash"],
        "scene_plan_hash": checklist_json["source"]["scene_plan_hash"],
        "previous_scene_render_hash": checklist_json["source"][
            "previous_scene_render_hash"
        ],
        "component_input_hash": source["component_input_hash"],
    }:
        raise CompilerContractError("compiler_scene_render_source_mismatch")
    for ordinal, block in enumerate(value["blocks"], start=1):
        if block["ordinal"] != ordinal or block["block_id"] != (
            f"block_{value['scene_id']}_{ordinal:03d}"
        ):
            raise CompilerContractError("compiler_scene_render_block_identity_invalid")
    character_count = sum(len(block["text"]) for block in value["blocks"])
    if value["character_count"] != character_count:
        raise CompilerContractError("compiler_scene_render_character_count_mismatch")
    stage = value["stage"]
    length_range = profile_json["prose"]["target_scene_chars"]
    if stage in {"writer", "rewrite_1", "rewrite_2"} and not (
        length_range["min"] <= character_count <= length_range["max"]
    ):
        raise CompilerContractError("compiler_scene_render_length_out_of_bounds")
    expected_round = {"writer": 0, "rewrite_1": 1, "rewrite_2": 2}.get(stage)
    if expected_round is not None and value["round"] != expected_round:
        raise CompilerContractError("compiler_scene_render_round_invalid")
    if stage == "writer" and value["previous_render_hash"] is not None:
        raise CompilerContractError("compiler_scene_render_previous_hash_invalid")
    if stage != "writer" and value["previous_render_hash"] is None:
        raise CompilerContractError("compiler_scene_render_previous_hash_invalid")
    if (stage == "accepted") != (value["selection_reason"] is not None):
        raise CompilerContractError("compiler_scene_render_selection_reason_invalid")
    return parsed


def normalize_scene_render_candidate(
    candidate: dict[str, Any],
    *,
    checklist: dict[str, Any],
    profile: dict[str, Any],
    component_input_hash: str,
) -> SceneRender:
    """Normalize one model-owned Writer candidate into a server-owned Render."""

    return _normalize_scene_render_candidate(
        candidate,
        checklist=checklist,
        profile=profile,
        component_input_hash=component_input_hash,
        stage="writer",
        round_index=0,
        previous_render_hash=None,
    )


def normalize_scene_rewrite_candidate(
    candidate: dict[str, Any],
    *,
    checklist: dict[str, Any],
    profile: dict[str, Any],
    current_render: dict[str, Any],
    rewrite_round: int,
    component_input_hash: str,
) -> SceneRender:
    """Normalize one full-Scene Rewrite candidate with direct render lineage."""

    if rewrite_round not in {1, 2}:
        raise CompilerContractError("compiler_scene_rewrite_round_invalid")
    try:
        checklist_json = ProseJudgeChecklist.model_validate(checklist).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_render_candidate_invalid") from error
    current = validate_scene_render(
        current_render, checklist=checklist_json, profile=profile
    ).model_dump(mode="json")
    expected_stage = "writer" if rewrite_round == 1 else "rewrite_1"
    if current["stage"] != expected_stage or current["round"] != rewrite_round - 1:
        raise CompilerContractError("compiler_scene_rewrite_source_stage_invalid")
    return _normalize_scene_render_candidate(
        candidate,
        checklist=checklist_json,
        profile=profile,
        component_input_hash=component_input_hash,
        stage=f"rewrite_{rewrite_round}",
        round_index=rewrite_round,
        previous_render_hash=canonical_json_sha256(current),
    )


def normalize_scene_polish_candidate(
    candidate: dict[str, Any],
    *,
    checklist: dict[str, Any],
    profile: dict[str, Any],
    current_render: dict[str, Any],
    component_input_hash: str,
) -> SceneRender:
    """Normalize a full-Scene polish with direct semantic-source lineage."""

    try:
        checklist_json = ProseJudgeChecklist.model_validate(checklist).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_render_candidate_invalid") from error
    current = validate_scene_render(
        current_render, checklist=checklist_json, profile=profile
    ).model_dump(mode="json")
    if (
        current["stage"] not in {"writer", "rewrite_1", "rewrite_2"}
        or current["selection_reason"] is not None
    ):
        raise CompilerContractError("compiler_scene_polish_source_stage_invalid")
    return _normalize_scene_render_candidate(
        candidate,
        checklist=checklist_json,
        profile=profile,
        component_input_hash=component_input_hash,
        stage="polished",
        round_index=current["round"],
        previous_render_hash=canonical_json_sha256(current),
    )


def finalize_scene_render(
    selected_render: dict[str, Any],
    *,
    original_render: dict[str, Any],
    checklist: dict[str, Any],
    profile: dict[str, Any],
    component_input_hash: str,
    selection_reason: str,
) -> SceneRender:
    """Create the server-owned accepted copy after semantic/quality selection."""

    try:
        checklist_json = ProseJudgeChecklist.model_validate(checklist).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_render_candidate_invalid") from error
    original = validate_scene_render(
        original_render, checklist=checklist_json, profile=profile
    ).model_dump(mode="json")
    selected = validate_scene_render(
        selected_render, checklist=checklist_json, profile=profile
    ).model_dump(mode="json")
    valid_reasons = {
        "polished_accepted",
        "polish_semantic_rollback",
        "quality_rollback",
        "quality_unstable",
    }
    if (
        original["stage"] not in {"writer", "rewrite_1", "rewrite_2"}
        or selection_reason not in valid_reasons
    ):
        raise CompilerContractError("compiler_scene_finalize_selection_invalid")
    original_hash = canonical_json_sha256(original)
    if selection_reason == "polished_accepted":
        if (
            selected["stage"] != "polished"
            or selected["round"] != original["round"]
            or selected["previous_render_hash"] != original_hash
        ):
            raise CompilerContractError("compiler_scene_finalize_selection_invalid")
    elif canonical_json_sha256(selected) != original_hash:
        raise CompilerContractError("compiler_scene_finalize_selection_invalid")
    candidate = {
        "schema_id": "compiler.scene-render-candidate.v1",
        "blocks": [{"text": block["text"]} for block in selected["blocks"]],
    }
    return _normalize_scene_render_candidate(
        candidate,
        checklist=checklist_json,
        profile=profile,
        component_input_hash=component_input_hash,
        stage="accepted",
        round_index=original["round"],
        previous_render_hash=canonical_json_sha256(selected),
        selection_reason=selection_reason,
    )


def _normalize_scene_render_candidate(
    candidate: dict[str, Any],
    *,
    checklist: dict[str, Any],
    profile: dict[str, Any],
    component_input_hash: str,
    stage: str,
    round_index: int,
    previous_render_hash: str | None,
    selection_reason: str | None = None,
) -> SceneRender:

    if not isinstance(candidate, dict) or set(candidate) != {"schema_id", "blocks"}:
        raise CompilerContractError("compiler_scene_render_candidate_invalid")
    if candidate.get("schema_id") != "compiler.scene-render-candidate.v1":
        raise CompilerContractError("compiler_scene_render_candidate_invalid")
    raw_blocks = candidate.get("blocks")
    if not isinstance(raw_blocks, list):
        raise CompilerContractError("compiler_scene_render_candidate_invalid")
    filtered_blocks: list[dict[str, str]] = []
    for block in raw_blocks:
        if not isinstance(block, dict) or set(block) != {"text"}:
            raise CompilerContractError("compiler_scene_render_candidate_invalid")
        text = block.get("text")
        if not isinstance(text, str):
            raise CompilerContractError("compiler_scene_render_candidate_invalid")
        if text.strip():
            filtered_blocks.append({"text": text})
    try:
        normalized_candidate = SceneRenderCandidate.model_validate(
            {"schema_id": candidate["schema_id"], "blocks": filtered_blocks}
        ).model_dump(mode="json")
        checklist_json = ProseJudgeChecklist.model_validate(checklist).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_render_candidate_invalid") from error
    if not isinstance(component_input_hash, str) or len(component_input_hash) != 64:
        raise CompilerContractError("compiler_scene_render_component_input_hash_invalid")
    try:
        int(component_input_hash, 16)
    except ValueError as error:
        raise CompilerContractError("compiler_scene_render_component_input_hash_invalid") from error
    render = {
        "schema_id": "compiler.scene-render.v1",
        "scene_id": checklist_json["scene_id"],
        "scene_ordinal": checklist_json["scene_ordinal"],
        "source": {
            "checklist_hash": canonical_json_sha256(checklist_json),
            "profile_hash": checklist_json["source"]["profile_hash"],
            "scene_plan_hash": checklist_json["source"]["scene_plan_hash"],
            "previous_scene_render_hash": checklist_json["source"][
                "previous_scene_render_hash"
            ],
            "component_input_hash": component_input_hash,
        },
        "stage": stage,
        "round": round_index,
        "previous_render_hash": previous_render_hash,
        "blocks": [
            {
                "block_id": f"block_{checklist_json['scene_id']}_{ordinal:03d}",
                "ordinal": ordinal,
                "text": block["text"],
            }
            for ordinal, block in enumerate(normalized_candidate["blocks"], start=1)
        ],
        "character_count": sum(len(block["text"]) for block in normalized_candidate["blocks"]),
        "selection_reason": selection_reason,
    }
    return validate_scene_render(render, checklist=checklist_json, profile=profile)


def validate_prose_judge_report(
    report: dict[str, Any],
    *,
    checklist: dict[str, Any],
    render: dict[str, Any],
    profile: dict[str, Any],
    disputed_check_ids: list[str] | None = None,
) -> ProseJudgeReport:
    """Bind Judge evidence and exact check coverage to one frozen render."""

    try:
        parsed = ProseJudgeReport.model_validate(report)
        checklist_json = ProseJudgeChecklist.model_validate(checklist).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_prose_judge_report_invalid") from error
    render_json = validate_scene_render(
        render, checklist=checklist_json, profile=profile
    ).model_dump(mode="json")
    value = parsed.model_dump(mode="json")
    if value["scene_id"] != checklist_json["scene_id"]:
        raise CompilerContractError("compiler_prose_judge_report_scene_mismatch")
    if value["checklist_hash"] != canonical_json_sha256(checklist_json):
        raise CompilerContractError("compiler_prose_judge_report_checklist_hash_mismatch")
    if value["render_hash"] != canonical_json_sha256(render_json):
        raise CompilerContractError("compiler_prose_judge_report_render_hash_mismatch")
    all_check_ids = [item["check_id"] for item in checklist_json["checks"]]
    expected_ids = disputed_check_ids if value["role"] == "arbiter" else all_check_ids
    if value["role"] == "arbiter" and not expected_ids:
        raise CompilerContractError("compiler_prose_arbiter_scope_invalid")
    if expected_ids is None or [item["check_id"] for item in value["assessments"]] != expected_ids:
        raise CompilerContractError("compiler_prose_judge_report_coverage_mismatch")
    checks_by_id = {item["check_id"]: item for item in checklist_json["checks"]}
    blocks = {item["block_id"]: item["text"] for item in render_json["blocks"]}
    for assessment in value["assessments"]:
        check = checks_by_id.get(assessment["check_id"])
        if check is None:
            raise CompilerContractError("compiler_prose_judge_report_check_unknown")
        for evidence in assessment["evidence"]:
            text = blocks.get(evidence["block_id"])
            start, end = evidence["start_char"], evidence["end_char"]
            if (
                text is None
                or start >= end
                or end > len(text)
                or text[start:end] != evidence["text"]
            ):
                raise CompilerContractError("compiler_prose_judge_evidence_invalid")
        evidence_required = (
            check["polarity"] == "required" and assessment["verdict"] == "pass"
        ) or (check["polarity"] == "forbidden" and assessment["verdict"] == "fail")
        if evidence_required and not assessment["evidence"]:
            raise CompilerContractError("compiler_prose_judge_evidence_required")
    return parsed


def _validate_model(model: Any, value: dict[str, Any], error_code: str) -> Any:
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise CompilerContractError(error_code) from error


def _validate_previous_scene(
    value: dict[str, Any] | None,
    *,
    scenes: list[dict[str, Any]],
    scene_index: int,
    profile: dict[str, Any],
    scene_plan_hash: str,
    profile_hash: str,
) -> dict[str, Any] | None:
    if scene_index == 0:
        if value is not None:
            raise CompilerContractError("compiler_prose_checklist_first_scene_previous_invalid")
        return None
    if value is None:
        raise CompilerContractError("compiler_prose_checklist_previous_scene_required")
    try:
        parsed = SceneRender.model_validate(value).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_prose_checklist_previous_scene_invalid") from error
    expected = scenes[scene_index - 1]
    if parsed["stage"] != "accepted" or (
        parsed["scene_id"], parsed["scene_ordinal"]
    ) != (expected["scene_id"], expected["discourse_order"]):
        raise CompilerContractError("compiler_prose_checklist_previous_scene_mismatch")
    if parsed["selection_reason"] is None:
        raise CompilerContractError("compiler_prose_checklist_previous_scene_invalid")
    if parsed["previous_render_hash"] is None:
        raise CompilerContractError("compiler_prose_checklist_previous_scene_invalid")
    source = parsed["source"]
    if source["scene_plan_hash"] != scene_plan_hash or source["profile_hash"] != profile_hash:
        raise CompilerContractError("compiler_prose_checklist_previous_scene_source_mismatch")
    if (parsed["scene_ordinal"] == 1) != (source["previous_scene_render_hash"] is None):
        raise CompilerContractError("compiler_prose_checklist_previous_scene_source_mismatch")
    for ordinal, block in enumerate(parsed["blocks"], start=1):
        if block["ordinal"] != ordinal or block["block_id"] != (
            f"block_{parsed['scene_id']}_{ordinal:03d}"
        ):
            raise CompilerContractError("compiler_prose_checklist_previous_scene_invalid")
    count = sum(len(block["text"]) for block in parsed["blocks"])
    length_range = profile["prose"]["target_scene_chars"]
    if parsed["character_count"] != count or not (
        length_range["min"] <= count <= length_range["max"]
    ):
        raise CompilerContractError("compiler_prose_checklist_previous_scene_invalid")
    return parsed


def _scene_context(
    *,
    scene: dict[str, Any],
    beats: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "objective": scene["objective"],
        "dramatic_goal": scene["dramatic_goal"],
        "conflict": scene["conflict"],
        "outcome": scene["outcome"],
        "pov_ref": scene["pov_ref"],
        "participant_refs": _sorted_refs(scene["participant_refs"]),
        "location_ref": scene["location_ref"],
        "story_time_refs": _sorted_refs(scene["story_time_refs"]),
        "state_before": before,
        "expected_state_after": after,
        "prerequisite_scene_ids": sorted(scene["prerequisite_scene_ids"]),
        "prerequisite_beat_ids": sorted(
            {item for beat in beats for item in beat["prerequisite_beat_ids"]}
        ),
        "beats": beats,
        "event_refs": _sorted_refs(
            ref for beat in beats for ref in beat["event_refs"]
        ),
        "exposure_actions": _stable_unique(
            action for beat in beats for action in beat["exposure_actions"]
        ),
        "resolution_actions": _stable_unique(
            action for beat in beats for action in beat["resolution_actions"]
        ),
        "setup_keys": sorted({key for beat in beats for key in beat["setup_keys"]}),
        "payoff_keys": sorted({key for beat in beats for key in beat["payoff_keys"]}),
        "obligation_keys": sorted(
            {key for beat in beats for key in beat["obligation_keys"]}
        ),
        "previous_scene_render": previous,
    }


def _build_checks(scene: dict[str, Any], beats: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def add(
        *,
        kind: str,
        polarity: str,
        expectation: str,
        beat_ids: Iterable[str] = (),
        basis_refs: Iterable[dict[str, Any]] = (),
        event_refs: Iterable[dict[str, Any]] = (),
        exposure_entry_keys: Iterable[str] = (),
        state_refs: Iterable[str] = (),
    ) -> None:
        ordinal = len(checks) + 1
        checks.append(
            {
                "check_id": f"check_{scene['scene_id']}_{ordinal:03d}",
                "ordinal": ordinal,
                "kind": kind,
                "polarity": polarity,
                "expectation": expectation,
                "beat_ids": list(beat_ids),
                "basis_refs": _sorted_refs(basis_refs),
                "event_refs": _sorted_refs(event_refs),
                "exposure_entry_keys": sorted(set(exposure_entry_keys)),
                "state_refs": sorted(set(state_refs)),
                "evidence_policy": _POLICY_SPEC["evidence_policy"][polarity],
            }
        )

    beat_by_id = {item["beat_id"]: item for item in beats}
    for beat in beats:
        add(
            kind="beat_realization",
            polarity="required",
            expectation=_template(
                "beat_realization", beat_id=beat["beat_id"], directive=beat["directive"]
            ),
            beat_ids=[beat["beat_id"]],
            basis_refs=beat["basis_refs"],
            event_refs=beat["event_refs"],
            exposure_entry_keys=(
                item["entry_key"] for item in beat["exposure_actions"]
            ),
        )
        for event_ref in _sorted_refs(beat["event_refs"]):
            add(
                kind="event_modality",
                polarity="required",
                expectation=_template("event_modality", event_ref=_ref_key(event_ref)),
                beat_ids=[beat["beat_id"]],
                basis_refs=[event_ref, *beat["basis_refs"]],
                event_refs=[event_ref],
            )
    add(
        kind="scene_outcome",
        polarity="required",
        expectation=_template("scene_outcome", outcome=scene["outcome"]),
        beat_ids=[item["beat_id"] for item in beats],
        basis_refs=(ref for item in beats for ref in item["basis_refs"]),
        event_refs=(ref for item in beats for ref in item["event_refs"]),
        state_refs=["/expected_state_after"],
    )
    for action in scene["allowed_reveals"]:
        related = [
            beat
            for beat in beats
            if any(item["entry_key"] == action["entry_key"] for item in beat["exposure_actions"])
        ]
        add(
            kind="reveal_control",
            polarity="required",
            expectation=_template(
                "allowed_reveal", action=action["action"], entry_key=action["entry_key"]
            ),
            beat_ids=[item["beat_id"] for item in related],
            basis_refs=(ref for item in related for ref in item["basis_refs"]),
            exposure_entry_keys=[action["entry_key"]],
            state_refs=[
                "/state_before/audience_exposure",
                "/expected_state_after/audience_exposure",
            ],
        )
    for entry_key in sorted(scene["forbidden_reveal_entry_keys"]):
        add(
            kind="reveal_control",
            polarity="forbidden",
            expectation=_template("forbidden_reveal", entry_key=entry_key),
            exposure_entry_keys=[entry_key],
            state_refs=["/state_before/audience_exposure"],
        )
    add(
        kind="pov_knowledge",
        polarity="forbidden",
        expectation=_template("pov_knowledge"),
        basis_refs=[] if scene["pov_ref"] is None else [scene["pov_ref"]],
        state_refs=[
            "/state_before/character_knowledge",
            "/expected_state_after/character_knowledge",
        ],
    )
    if scene["location_ref"] is not None or scene["story_time_refs"]:
        add(
            kind="location_time",
            polarity="required",
            expectation=_template(
                "location_time",
                location_ref=(
                    "none" if scene["location_ref"] is None else _ref_key(scene["location_ref"])
                ),
                story_time_refs=", ".join(_ref_key(ref) for ref in scene["story_time_refs"]),
            ),
            basis_refs=[
                *([] if scene["location_ref"] is None else [scene["location_ref"]]),
                *scene["story_time_refs"],
            ],
            event_refs=scene["story_time_refs"],
            state_refs=["/state_before/locations", "/expected_state_after/locations"],
        )
    for beat in beats:
        for prerequisite_id in sorted(beat["prerequisite_beat_ids"]):
            prerequisite = beat_by_id.get(prerequisite_id)
            basis = beat["basis_refs"] if prerequisite is None else [
                *prerequisite["basis_refs"], *beat["basis_refs"]
            ]
            add(
                kind="causality_ordering",
                polarity="required",
                expectation=_template(
                    "beat_causality",
                    prerequisite_beat_id=prerequisite_id,
                    beat_id=beat["beat_id"],
                ),
                beat_ids=[prerequisite_id, beat["beat_id"]],
                basis_refs=basis,
            )
    for prerequisite_scene_id in sorted(scene["prerequisite_scene_ids"]):
        add(
            kind="causality_ordering",
            polarity="required",
            expectation=_template(
                "scene_causality", prerequisite_scene_id=prerequisite_scene_id
            ),
            state_refs=["/state_before"],
        )
    add(
        kind="major_hallucination",
        polarity="forbidden",
        expectation=_template("major_hallucination"),
        beat_ids=[item["beat_id"] for item in beats],
        basis_refs=(ref for item in beats for ref in item["basis_refs"]),
        event_refs=(ref for item in beats for ref in item["event_refs"]),
        state_refs=["/state_before", "/expected_state_after"],
    )
    return checks


def _replay_scene_states(
    plan: dict[str, Any],
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    state = deepcopy(plan["initial_state"])
    beats_by_id = {item["beat_id"]: item for item in plan["beats"]}
    result: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for scene in sorted(plan["scenes"], key=lambda item: item["discourse_order"]):
        before = _canonical_state(state)
        if canonical_json_sha256(before) != scene["state_before_hash"]:
            raise CompilerContractError("compiler_prose_checklist_state_before_mismatch")
        for beat_id in scene["beat_ids"]:
            beat = beats_by_id[beat_id]
            for transition in beat["state_delta"]["knowledge_transitions"]:
                _apply_knowledge(state, transition)
            state["locations"].extend(deepcopy(beat["state_delta"]["location_assertions"]))
            for setup_key in beat["setup_keys"]:
                state["open_setups"].append(
                    {
                        "setup_key": setup_key,
                        "setup_beat_id": beat_id,
                        "basis_refs": deepcopy(beat["basis_refs"]),
                    }
                )
            if beat["payoff_keys"]:
                state["open_setups"] = [
                    item
                    for item in state["open_setups"]
                    if item["setup_key"] not in set(beat["payoff_keys"])
                ]
        state["audience_exposure"] = deepcopy(scene["audience_state_after"])
        after = _canonical_state(state)
        if canonical_json_sha256(after) != scene["state_after_hash"]:
            raise CompilerContractError("compiler_prose_checklist_state_after_mismatch")
        result[scene["scene_id"]] = (before, after)
    if _canonical_state(state) != plan["final_state"]:
        raise CompilerContractError("compiler_prose_checklist_final_state_mismatch")
    return result


def _apply_knowledge(state: dict[str, Any], transition: dict[str, Any]) -> None:
    subject_key = _ref_key(transition["subject_ref"])
    item = next(
        (
            value
            for value in state["character_knowledge"]
            if _ref_key(value["subject_ref"]) == subject_key
        ),
        None,
    )
    if item is None:
        item = {
            "subject_ref": deepcopy(transition["subject_ref"]),
            "knows_refs": [],
            "believes_refs": [],
            "false_belief_refs": [],
        }
        state["character_knowledge"].append(item)
    ref = transition["object_ref"]
    key = _ref_key(ref)

    def without(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [value for value in values if _ref_key(value) != key]

    operation = transition["operation"]
    if operation in {"learn", "correct"}:
        item["knows_refs"] = _sorted_refs([*item["knows_refs"], ref])
        item["believes_refs"] = without(item["believes_refs"])
        item["false_belief_refs"] = without(item["false_belief_refs"])
    elif operation == "believe":
        if key not in {_ref_key(value) for value in item["knows_refs"]}:
            item["believes_refs"] = _sorted_refs([*item["believes_refs"], ref])
            item["false_belief_refs"] = without(item["false_belief_refs"])
    else:
        item["false_belief_refs"] = _sorted_refs([*item["false_belief_refs"], ref])
        item["believes_refs"] = without(item["believes_refs"])


def _canonical_state(state: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(state)
    value["audience_exposure"] = sorted(
        value["audience_exposure"], key=lambda item: item["entry_key"]
    )
    value["character_knowledge"] = sorted(
        value["character_knowledge"], key=lambda item: _ref_key(item["subject_ref"])
    )
    for item in value["character_knowledge"]:
        item["knows_refs"] = _sorted_refs(item["knows_refs"])
        item["believes_refs"] = _sorted_refs(item["believes_refs"])
        item["false_belief_refs"] = _sorted_refs(item["false_belief_refs"])
    value["locations"] = sorted(
        value["locations"],
        key=lambda item: (
            _ref_key(item["subject_ref"]),
            tuple(_ref_key(ref) for ref in item["story_time_refs"]),
            _ref_key(item["location_ref"]),
        ),
    )
    value["open_setups"] = sorted(value["open_setups"], key=lambda item: item["setup_key"])
    return value


def _build_object_catalog(
    narrative: dict[str, Any], *, seed_values: Iterable[Any]
) -> list[dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for envelopes in narrative["objects"].values():
        for envelope in envelopes:
            catalog[_ref_key(envelope["object_ref"])] = envelope
    required = {_ref_key(ref) for value in seed_values for ref in _walk_refs(value)}
    result = []
    for key in sorted(required):
        envelope = catalog.get(key)
        if envelope is None:
            raise CompilerContractError("compiler_prose_checklist_object_ref_unresolved")
        result.append(
            {
                "object_ref": deepcopy(envelope["object_ref"]),
                "source_ref": deepcopy(envelope["source_ref"]),
                "label": _object_label(envelope["value"], envelope["object_ref"]),
                "value": deepcopy(envelope["value"]),
            }
        )
    return result


def _walk_refs(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if set(value) == {"object_type", "object_id"}:
            yield value
            return
        for nested in value.values():
            yield from _walk_refs(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_refs(nested)


def _object_label(value: Any, ref: dict[str, Any]) -> str:
    if isinstance(value, dict):
        for key in ("title", "name", "statement", "reasoning_question", "content", "id"):
            label = value.get(key)
            if isinstance(label, str) and label.strip():
                return label[:2000]
    return str(ref["object_id"])


def _sorted_refs(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for value in values:
        unique[_ref_key(value)] = deepcopy(value)
    return [unique[key] for key in sorted(unique)]


def _stable_unique(values: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for value in values:
        unique[canonical_json_sha256(value)] = deepcopy(value)
    return [unique[key] for key in sorted(unique)]


def _ref_key(ref: dict[str, Any]) -> str:
    return f"{ref['object_type']}:{ref['object_id']}"


def _template(name: str, **values: str) -> str:
    template: object = _POLICY_SPEC["templates"][name]
    if not isinstance(template, str):
        raise CompilerContractError("compiler_prose_checklist_policy_invalid")
    return template.format(**values)


__all__ = [
    "PROSE_CHECKLIST_POLICY_HASH",
    "PROSE_CHECKLIST_POLICY_VERSION",
    "PROSE_CHECKLIST_SCHEMA_ID",
    "build_prose_judge_checklist",
    "prose_checklist_fingerprint",
    "validate_novel_profile_v2",
    "validate_prose_judge_checklist",
    "validate_prose_judge_report",
    "validate_scene_render",
    "normalize_scene_render_candidate",
    "normalize_scene_rewrite_candidate",
]
