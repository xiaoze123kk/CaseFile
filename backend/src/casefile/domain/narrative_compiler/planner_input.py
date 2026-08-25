"""Deterministic Story Planner input construction and identity."""

from __future__ import annotations

from typing import Any

from casefile_contracts import NovelProfile, PlannerInputBundle
from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)

PLANNER_INPUT_SCHEMA_ID = "compiler.story-planner-input.v1"


def build_planner_input_bundle(
    *,
    narrative_ir: dict[str, Any],
    exposure: dict[str, Any] | None,
    profile: dict[str, Any],
    compile_mode: str,
) -> dict[str, Any]:
    """Build the complete provider-visible input from frozen artifacts only."""

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
    bundle = {
        "schema_id": PLANNER_INPUT_SCHEMA_ID,
        "narrative_ir": narrative_ir,
        "exposure_plan": exposure,
        "profile": profile_json,
        "planning_constraints": {
            "target_chapters": structure["target_chapters"],
            "target_scenes": structure["target_scenes"],
            "allowed_presentation_modes": profile_json["allowed_presentation_modes"],
        },
    }
    try:
        return PlannerInputBundle.model_validate(bundle).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_story_planner_input_invalid") from error


def planner_input_fingerprint(bundle: dict[str, Any]) -> dict[str, Any]:
    parsed = PlannerInputBundle.model_validate(bundle).model_dump(mode="json")
    exposure = parsed["exposure_plan"]
    return {
        "schema_id": PLANNER_INPUT_SCHEMA_ID,
        "narrative_ir_hash": canonical_json_sha256(parsed["narrative_ir"]),
        "exposure_hash": None if exposure is None else canonical_json_sha256(exposure),
        "profile_hash": canonical_json_sha256(parsed["profile"]),
        "planning_constraints_hash": canonical_json_sha256(parsed["planning_constraints"]),
    }


__all__ = [
    "PLANNER_INPUT_SCHEMA_ID",
    "build_planner_input_bundle",
    "planner_input_fingerprint",
]
