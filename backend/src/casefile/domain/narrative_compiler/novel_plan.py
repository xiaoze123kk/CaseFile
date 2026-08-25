"""Pure NovelPlan semantic validation, canonicalization, and identity."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from graphlib import CycleError, TopologicalSorter
from typing import Any

from casefile_contracts import NovelPlanCandidate, NovelPlanIR
from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)

NOVEL_PLAN_CANDIDATE_SCHEMA_ID = "compiler.novel-plan-candidate.v1"
NOVEL_PLAN_SCHEMA_ID = "compiler.novel-plan.v1"
STORY_PLANNER_COMPONENT_VERSION = "compiler.story-planner.v1"

_COLLECTION_TYPE = {
    "resolution_specs": "resolution_spec",
    "entities": "entity",
    "relationships": "relationship",
    "locations": "location",
    "events": "event",
    "information_units": "information_unit",
    "claims": "claim",
    "hypotheses": "hypothesis",
    "reasoning_paths": "reasoning_path",
    "constraints": "constraint",
    "structure_locks": "structure_lock",
}
_RUNTIME_KEYS = {
    "compile_run_id",
    "task_run_id",
    "agent_step_run_id",
    "user_id",
    "database_id",
}


def validate_novel_plan_candidate(
    candidate: dict[str, Any],
    *,
    planner_input: dict[str, Any],
) -> NovelPlanCandidate:
    """Validate model-owned arrangement without repairing semantic violations."""

    try:
        parsed = NovelPlanCandidate.model_validate(candidate)
    except ValidationError as error:
        raise CompilerContractError("compiler_story_planner_output_invalid") from error
    value = parsed.model_dump(mode="json")
    _validate_no_runtime_identity(value)

    chapters = value["chapters"]
    scenes = value["scenes"]
    _unique_contiguous(chapters, "chapter_id", "ordinal", "compiler_story_plan_chapter_invalid")
    _unique_contiguous(scenes, "scene_id", "discourse_order", "compiler_story_plan_scene_invalid")
    chapter_ids = {item["chapter_id"] for item in chapters}
    if any(scene["chapter_id"] not in chapter_ids for scene in scenes):
        raise CompilerContractError("compiler_story_plan_chapter_reference_invalid")

    scene_ids = {item["scene_id"] for item in scenes}
    graph: dict[str, set[str]] = {}
    for scene in scenes:
        dependencies = set(scene["prerequisite_scene_ids"])
        if scene["scene_id"] in dependencies or not dependencies <= scene_ids:
            raise CompilerContractError("compiler_story_plan_dependency_invalid")
        graph[scene["scene_id"]] = dependencies
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as error:
        raise CompilerContractError("compiler_story_plan_dependency_cycle") from error

    narrative_ir = planner_input["narrative_ir"]
    catalog, source_refs = _narrative_catalog(narrative_ir)
    for scene in scenes:
        _validate_scene_refs(scene, catalog)
        if any(_ref_key(ref) not in source_refs for ref in scene["basis_refs"]):
            raise CompilerContractError("compiler_story_plan_provenance_invalid")
        for ref in scene["story_time_refs"]:
            if ref["object_type"] != "event":
                raise CompilerContractError("compiler_story_plan_temporal_invalid")

    profile = planner_input["profile"]
    constraints = planner_input["planning_constraints"]
    if (
        len(chapters) != constraints["target_chapters"]
        or len(scenes) != constraints["target_scenes"]
    ):
        raise CompilerContractError("compiler_story_plan_profile_target_invalid")
    allowed_modes = set(profile["allowed_presentation_modes"])
    if any(scene["presentation_mode"] not in allowed_modes for scene in scenes):
        raise CompilerContractError("compiler_story_plan_presentation_mode_invalid")

    _validate_exposure(scenes, planner_input.get("exposure_plan"))
    _validate_resolutions(scenes, catalog)
    _validate_story_time_order(scenes, narrative_ir)
    return parsed


def canonicalize_novel_plan(
    candidate: dict[str, Any],
    *,
    planner_input: dict[str, Any],
    planner_version: str,
    component_fingerprint: str,
) -> dict[str, Any]:
    parsed = validate_novel_plan_candidate(candidate, planner_input=planner_input)
    value = parsed.model_dump(mode="json")
    _, source_refs = _narrative_catalog(planner_input["narrative_ir"])
    chapters = sorted(value["chapters"], key=lambda item: item["ordinal"])
    scenes: list[dict[str, Any]] = []
    chapter_index: dict[str, list[str]] = defaultdict(list)
    dependency_index: dict[str, list[str]] = {}
    for scene in sorted(value["scenes"], key=lambda item: item["discourse_order"]):
        basis = sorted({_ref_key(ref) for ref in scene["basis_refs"]})
        source = [source_refs[key] for key in basis]
        canonical_scene = {**scene, "source_refs": source}
        scenes.append(canonical_scene)
        chapter_index[scene["chapter_id"]].append(scene["scene_id"])
        dependency_index[scene["scene_id"]] = sorted(scene["prerequisite_scene_ids"])

    fingerprint = planner_input_fingerprint_json(planner_input)
    output = {
        "schema_id": NOVEL_PLAN_SCHEMA_ID,
        "planner_version": planner_version,
        "source": {
            "planner_input_hash": canonical_json_sha256(planner_input),
            "narrative_ir_hash": fingerprint["narrative_ir_hash"],
            "profile_hash": fingerprint["profile_hash"],
            "exposure_hash": fingerprint["exposure_hash"],
            "component_fingerprint": component_fingerprint,
        },
        "chapters": chapters,
        "scenes": scenes,
        "indexes": {
            "chapter_scene_ids": dict(sorted(chapter_index.items())),
            "scene_dependencies": dict(sorted(dependency_index.items())),
        },
    }
    try:
        return NovelPlanIR.model_validate(output).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_story_plan_canonicalization_failed") from error


def story_planner_component_fingerprint(
    *,
    planner_input: dict[str, Any],
    prompt_version: str,
    prompt_sha256: str,
    provider: str,
    model_id: str,
    provider_config_version: int,
) -> dict[str, Any]:
    input_fp = planner_input_fingerprint_json(planner_input)
    return {
        "component_version": STORY_PLANNER_COMPONENT_VERSION,
        "candidate_schema_id": NOVEL_PLAN_CANDIDATE_SCHEMA_ID,
        "novel_plan_schema_id": NOVEL_PLAN_SCHEMA_ID,
        **input_fp,
        "prompt_version": prompt_version,
        "prompt_sha256": prompt_sha256,
        "provider": provider,
        "model_id": model_id,
        "provider_config_version": provider_config_version,
    }


def planner_input_fingerprint_json(planner_input: dict[str, Any]) -> dict[str, Any]:
    exposure = planner_input.get("exposure_plan")
    return {
        "narrative_ir_hash": canonical_json_sha256(planner_input["narrative_ir"]),
        "profile_hash": canonical_json_sha256(planner_input["profile"]),
        "exposure_hash": None if exposure is None else canonical_json_sha256(exposure),
    }


def _narrative_catalog(
    narrative_ir: dict[str, Any],
) -> tuple[set[tuple[str, str]], dict[tuple[str, str], dict[str, Any]]]:
    catalog: set[tuple[str, str]] = set()
    source_refs: dict[tuple[str, str], dict[str, Any]] = {}
    root_ref = narrative_ir["source"]["casefile_ref"]
    root_key = _ref_key(root_ref)
    catalog.add(root_key)
    source_refs[root_key] = narrative_ir["source"]["root_source_refs"][0]
    for collection, object_type in _COLLECTION_TYPE.items():
        for envelope in narrative_ir["objects"][collection]:
            key = _ref_key(envelope["object_ref"])
            if key[0] != object_type:
                raise CompilerContractError("compiler_story_plan_narrative_catalog_invalid")
            catalog.add(key)
            source_refs[key] = envelope["source_ref"]
    return catalog, source_refs


def _validate_scene_refs(scene: dict[str, Any], catalog: set[tuple[str, str]]) -> None:
    refs = (
        list(scene["participant_refs"])
        + list(scene["event_refs"])
        + list(scene["story_time_refs"])
        + list(scene["basis_refs"])
    )
    if scene["pov_ref"] is not None:
        refs.append(scene["pov_ref"])
    if scene["location_ref"] is not None:
        refs.append(scene["location_ref"])
    refs.extend(item["resolution_ref"] for item in scene["resolutions"])
    if any(_ref_key(ref) not in catalog for ref in refs):
        raise CompilerContractError("compiler_story_plan_reference_invalid")
    if scene["pov_ref"] is not None and scene["pov_ref"]["object_type"] != "entity":
        raise CompilerContractError("compiler_story_plan_reference_type_invalid")
    if scene["location_ref"] is not None and scene["location_ref"]["object_type"] != "location":
        raise CompilerContractError("compiler_story_plan_reference_type_invalid")
    if any(ref["object_type"] != "event" for ref in scene["event_refs"]):
        raise CompilerContractError("compiler_story_plan_reference_type_invalid")
    if any(
        item["resolution_ref"]["object_type"] != "resolution_spec" for item in scene["resolutions"]
    ):
        raise CompilerContractError("compiler_story_plan_reference_type_invalid")


def _validate_exposure(scenes: list[dict[str, Any]], exposure: dict[str, Any] | None) -> None:
    if exposure is None:
        return
    entries = exposure["frozen_payload"].get("entries", [])
    expected = [item["entry_key"] for item in entries]
    introduced: list[str] = []
    seen: set[str] = set()
    expected_set = set(expected)
    for scene in scenes:
        for placement in scene["exposure"]:
            key = placement["entry_key"]
            if key not in expected_set:
                raise CompilerContractError("compiler_story_plan_exposure_reference_invalid")
            if placement["action"] == "introduce":
                if key in seen:
                    raise CompilerContractError("compiler_story_plan_exposure_duplicate")
                seen.add(key)
                introduced.append(key)
            elif key not in seen:
                raise CompilerContractError("compiler_story_plan_exposure_before_introduction")
    if introduced != expected:
        raise CompilerContractError("compiler_story_plan_exposure_violation")


def _validate_resolutions(scenes: list[dict[str, Any]], catalog: set[tuple[str, str]]) -> None:
    required = {key for key in catalog if key[0] == "resolution_spec"}
    terminal: dict[tuple[str, str], str] = {}
    for scene in scenes:
        for placement in scene["resolutions"]:
            action = placement["action"]
            key = _ref_key(placement["resolution_ref"])
            if action in {"resolve", "intentionally_unresolved"}:
                if key in terminal:
                    raise CompilerContractError("compiler_story_plan_resolution_duplicate")
                terminal[key] = action
    if set(terminal) != required:
        raise CompilerContractError("compiler_story_plan_resolution_uncovered")


def _validate_story_time_order(scenes: list[dict[str, Any]], narrative_ir: dict[str, Any]) -> None:
    event_times: dict[str, datetime] = {}
    for envelope in narrative_ir["objects"]["events"]:
        time = envelope["value"].get("time") or {}
        raw = (
            time.get("value") if time.get("kind") in {"exact", "approximate"} else time.get("start")
        )
        if isinstance(raw, str):
            try:
                event_times[envelope["object_ref"]["object_id"]] = datetime.fromisoformat(raw)
            except ValueError:
                continue

    previous: datetime | None = None
    for scene in sorted(scenes, key=lambda item: item["discourse_order"]):
        anchors = [
            event_times.get(ref["object_id"])
            for ref in scene["story_time_refs"]
            if ref["object_type"] == "event"
        ]
        known = [value for value in anchors if value is not None]
        if not known:
            continue
        current = min(known)
        mode = scene["presentation_mode"]
        if previous is not None:
            if mode == "linear" and current < previous:
                raise CompilerContractError("compiler_story_plan_temporal_order_invalid")
            if mode == "flashback" and current > previous:
                raise CompilerContractError("compiler_story_plan_flashback_invalid")
            if mode == "flashforward" and current < previous:
                raise CompilerContractError("compiler_story_plan_flashforward_invalid")
        previous = current


def _unique_contiguous(
    values: list[dict[str, Any]], id_key: str, ordinal_key: str, error_code: str
) -> None:
    identities = [item[id_key] for item in values]
    ordinals = [item[ordinal_key] for item in values]
    if len(set(identities)) != len(identities) or sorted(ordinals) != list(
        range(1, len(values) + 1)
    ):
        raise CompilerContractError(error_code)


def _ref_key(ref: dict[str, Any]) -> tuple[str, str]:
    return str(ref["object_type"]), str(ref["object_id"])


def _validate_no_runtime_identity(value: Any) -> None:
    if isinstance(value, dict):
        if _RUNTIME_KEYS.intersection(value):
            raise CompilerContractError("compiler_story_plan_runtime_identity_forbidden")
        for item in value.values():
            _validate_no_runtime_identity(item)
    elif isinstance(value, list):
        for item in value:
            _validate_no_runtime_identity(item)


__all__ = [
    "NOVEL_PLAN_CANDIDATE_SCHEMA_ID",
    "NOVEL_PLAN_SCHEMA_ID",
    "STORY_PLANNER_COMPONENT_VERSION",
    "canonicalize_novel_plan",
    "story_planner_component_fingerprint",
    "validate_novel_plan_candidate",
]
