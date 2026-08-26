"""Deterministic Story Planner input construction and identity."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from casefile_contracts import (
    NovelProfile,
    PlannerInputBundle,
    PlannerInputBundleV2,
    PlannerInputBundleV3,
)
from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)

PLANNER_INPUT_SCHEMA_ID = "compiler.story-planner-input.v1"
PLANNER_INPUT_V2_SCHEMA_ID = "compiler.story-planner-input.v2"
PLANNER_INPUT_V3_SCHEMA_ID = "compiler.story-planner-input.v3"


def build_planner_input_bundle(
    *,
    narrative_ir: dict[str, Any],
    exposure: dict[str, Any] | None,
    profile: dict[str, Any],
    compile_mode: str,
) -> dict[str, Any]:
    """Build the complete provider-visible input from frozen artifacts only."""

    narrative_ir, exposure, profile_json, planning_constraints = _validated_inputs(
        narrative_ir=narrative_ir,
        exposure=exposure,
        profile=profile,
        compile_mode=compile_mode,
    )
    bundle = {
        "schema_id": PLANNER_INPUT_SCHEMA_ID,
        "narrative_ir": narrative_ir,
        "exposure_plan": exposure,
        "profile": profile_json,
        "planning_constraints": planning_constraints,
    }
    try:
        return PlannerInputBundle.model_validate(bundle).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_story_planner_input_invalid") from error


def build_planner_input_bundle_v2(
    *,
    narrative_ir: dict[str, Any],
    exposure: dict[str, Any] | None,
    profile: dict[str, Any],
    compile_mode: str,
) -> dict[str, Any]:
    """Build an additive v2 input with an auditable, source-derived PlannerView."""

    narrative_ir, exposure, profile_json, planning_constraints = _validated_inputs(
        narrative_ir=narrative_ir,
        exposure=exposure,
        profile=profile,
        compile_mode=compile_mode,
    )
    bundle = {
        "schema_id": PLANNER_INPUT_V2_SCHEMA_ID,
        "narrative_ir": narrative_ir,
        "exposure_plan": exposure,
        "profile": profile_json,
        "planning_constraints": planning_constraints,
        "planner_view": _project_planner_view(
            narrative_ir=narrative_ir,
            exposure=exposure,
            planning_constraints=planning_constraints,
        ),
    }
    try:
        return PlannerInputBundleV2.model_validate(bundle).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_story_planner_input_v2_invalid") from error


def build_planner_input_bundle_v3(
    *,
    narrative_ir: dict[str, Any],
    exposure: dict[str, Any] | None,
    profile: dict[str, Any],
    compile_mode: str,
) -> dict[str, Any]:
    """Build v3 with explicit hard/soft typed semantic obligations."""

    narrative_ir, exposure, profile_json, planning_constraints = _validated_inputs(
        narrative_ir=narrative_ir,
        exposure=exposure,
        profile=profile,
        compile_mode=compile_mode,
    )
    bundle = {
        "schema_id": PLANNER_INPUT_V3_SCHEMA_ID,
        "narrative_ir": narrative_ir,
        "exposure_plan": exposure,
        "profile": profile_json,
        "planning_constraints": planning_constraints,
        "planner_view": _project_planner_view(
            narrative_ir=narrative_ir,
            exposure=exposure,
            planning_constraints=planning_constraints,
            include_semantic_obligations=True,
        ),
    }
    try:
        return PlannerInputBundleV3.model_validate(bundle).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_story_planner_input_v3_invalid") from error


def validate_planner_input_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Validate either frozen input version and independently re-prove the v2 view."""

    schema_id = bundle.get("schema_id")
    try:
        if schema_id == PLANNER_INPUT_SCHEMA_ID:
            return PlannerInputBundle.model_validate(bundle).model_dump(mode="json")
        if schema_id == PLANNER_INPUT_V2_SCHEMA_ID:
            parsed = PlannerInputBundleV2.model_validate(bundle).model_dump(mode="json")
        elif schema_id == PLANNER_INPUT_V3_SCHEMA_ID:
            parsed = PlannerInputBundleV3.model_validate(bundle).model_dump(mode="json")
        else:
            raise CompilerContractError("compiler_story_planner_input_schema_invalid")
    except ValidationError as error:
        raise CompilerContractError("compiler_story_planner_input_invalid") from error
    expected = _project_planner_view(
        narrative_ir=parsed["narrative_ir"],
        exposure=parsed["exposure_plan"],
        planning_constraints=parsed["planning_constraints"],
        include_semantic_obligations=(schema_id == PLANNER_INPUT_V3_SCHEMA_ID),
    )
    if parsed["planner_view"] != expected:
        raise CompilerContractError("compiler_story_planner_view_mismatch")
    return parsed


def _validated_inputs(
    *,
    narrative_ir: dict[str, Any],
    exposure: dict[str, Any] | None,
    profile: dict[str, Any],
    compile_mode: str,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any], dict[str, Any]]:
    try:
        parsed_profile = NovelProfile.model_validate(profile)
    except ValidationError as error:
        raise CompilerContractError("compiler_novel_profile_invalid") from error
    profile_json = parsed_profile.model_dump(mode="json")
    if compile_mode == "canonical" and exposure is None:
        raise CompilerContractError("compiler_story_planner_exposure_required")
    if exposure is None and profile_json["exposure_policy"] != "planner_default":
        raise CompilerContractError("compiler_story_planner_exposure_policy_invalid")
    if exposure is not None and profile_json["exposure_policy"] != "bound_plan":
        raise CompilerContractError("compiler_story_planner_bound_exposure_policy_invalid")

    structure = profile_json["structure"]
    constraints = {
        "target_chapters": structure["target_chapters"],
        "target_scenes": structure["target_scenes"],
        "allowed_presentation_modes": profile_json["allowed_presentation_modes"],
    }
    return narrative_ir, exposure, profile_json, constraints


def _project_planner_view(
    *,
    narrative_ir: dict[str, Any],
    exposure: dict[str, Any] | None,
    planning_constraints: dict[str, Any],
    include_semantic_obligations: bool = False,
) -> dict[str, Any]:
    entries = [] if exposure is None else exposure["frozen_payload"].get("entries", [])
    exposure_obligations = [
        {
            "entry_key": item["entry_key"],
            "sequence_no": item["sequence_no"],
            "introduce_exactly_once": True,
            "subsequent_actions_require_introduction": True,
        }
        for item in sorted(entries, key=lambda value: (value["sequence_no"], value["entry_key"]))
    ]
    resolution_obligations = [
        {
            "resolution_ref": envelope["object_ref"],
            "terminal_exactly_once": True,
            "allowed_terminal_actions": ["resolve", "intentionally_unresolved"],
        }
        for envelope in sorted(
            narrative_ir["objects"]["resolution_specs"],
            key=lambda value: _ref_key(value["object_ref"]),
        )
    ]
    chronology_anchors: list[dict[str, Any]] = []
    causal_edges: list[dict[str, Any]] = []
    for envelope in sorted(
        narrative_ir["objects"]["events"], key=lambda value: _ref_key(value["object_ref"])
    ):
        event_ref = envelope["object_ref"]
        value = envelope["value"]
        time_value = value.get("time") or {}
        raw = (
            time_value.get("value")
            if time_value.get("kind") in {"exact", "approximate"}
            else time_value.get("start")
        )
        if isinstance(raw, str):
            try:
                comparable = datetime.fromisoformat(raw).isoformat()
            except ValueError:
                pass
            else:
                chronology_anchors.append(
                    {"event_ref": event_ref, "comparable_time": comparable}
                )
        for relation, field in (("cause", "cause_refs"), ("effect", "effect_refs")):
            for related_ref in sorted(value.get(field, []), key=_ref_key):
                causal_edges.append(
                    {"relation": relation, "event_ref": event_ref, "related_ref": related_ref}
                )
    knowledge_snapshots: list[dict[str, Any]] = []
    for envelope in sorted(
        narrative_ir["objects"]["entities"], key=lambda value: _ref_key(value["object_ref"])
    ):
        for state in envelope["value"].get("knowledge_states", []):
            if not isinstance(state.get("as_of_event_ref"), dict):
                continue
            knowledge_snapshots.append(
                {
                    "subject_ref": envelope["object_ref"],
                    "as_of_event_ref": state["as_of_event_ref"],
                    "knows_refs": sorted(state.get("knows_refs", []), key=_ref_key),
                    "believes_refs": sorted(state.get("believes_refs", []), key=_ref_key),
                    "false_belief_refs": sorted(
                        state.get("false_belief_refs", []), key=_ref_key
                    ),
                }
            )
    author_guidance = [
        {
            "entry_key": item["entry_key"],
            "title": item["title"],
            "note": item.get("note"),
            "is_hard_constraint": False,
        }
        for item in sorted(entries, key=lambda value: (value["sequence_no"], value["entry_key"]))
    ]
    hard_semantic: list[dict[str, Any]] = []
    soft_semantic: list[dict[str, Any]] = []
    if include_semantic_obligations:
        for obligation in _typed_semantic_obligations(entries):
            if obligation["level"] == "hard":
                hard_semantic.append(obligation)
            else:
                soft_semantic.append(obligation)
    hard_constraints = {
        "structure": planning_constraints,
        "exposure_obligations": exposure_obligations,
        "resolution_obligations": resolution_obligations,
        "chronology_anchors": sorted(
            chronology_anchors,
            key=lambda value: (value["comparable_time"], _ref_key(value["event_ref"])),
        ),
    }
    planning_context = {
        "causal_edges": sorted(
            causal_edges,
            key=lambda value: (
                value["relation"],
                _ref_key(value["event_ref"]),
                _ref_key(value["related_ref"]),
            ),
        ),
        "knowledge_snapshots": sorted(
            knowledge_snapshots,
            key=lambda value: (
                _ref_key(value["subject_ref"]),
                _ref_key(value["as_of_event_ref"]),
            ),
        ),
        "author_guidance": author_guidance,
    }
    if include_semantic_obligations:
        hard_constraints["semantic_obligations"] = hard_semantic
        planning_context["semantic_obligations"] = soft_semantic
    return {
        "hard_constraints": {
            **hard_constraints,
        },
        "planning_context": {
            **planning_context,
        },
    }


def _typed_semantic_obligations(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    obligations: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda value: (value["sequence_no"], value["entry_key"])):
        for item in entry.get("planning_obligations", []):
            obligations.append({**item, "entry_key": entry["entry_key"]})
    return sorted(
        obligations,
        key=lambda value: (value["obligation_key"], value["entry_key"]),
    )


def _ref_key(ref: dict[str, Any]) -> tuple[str, str]:
    return str(ref.get("object_type", "")), str(ref.get("object_id", ""))


def planner_input_fingerprint(bundle: dict[str, Any]) -> dict[str, Any]:
    parsed = validate_planner_input_bundle(bundle)
    exposure = parsed["exposure_plan"]
    return {
        "schema_id": PLANNER_INPUT_SCHEMA_ID,
        "planner_input_schema_id": parsed["schema_id"],
        "narrative_ir_hash": canonical_json_sha256(parsed["narrative_ir"]),
        "exposure_hash": None if exposure is None else canonical_json_sha256(exposure),
        "profile_hash": canonical_json_sha256(parsed["profile"]),
        "planning_constraints_hash": canonical_json_sha256(parsed["planning_constraints"]),
        "planner_view_hash": (
            canonical_json_sha256(parsed["planner_view"])
            if parsed["schema_id"] in {PLANNER_INPUT_V2_SCHEMA_ID, PLANNER_INPUT_V3_SCHEMA_ID}
            else None
        ),
    }


__all__ = [
    "PLANNER_INPUT_SCHEMA_ID",
    "PLANNER_INPUT_V2_SCHEMA_ID",
    "PLANNER_INPUT_V3_SCHEMA_ID",
    "build_planner_input_bundle",
    "build_planner_input_bundle_v2",
    "build_planner_input_bundle_v3",
    "planner_input_fingerprint",
    "validate_planner_input_bundle",
]
