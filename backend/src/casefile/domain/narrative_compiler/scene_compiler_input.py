"""Deterministic audited input and provider-facing projection for N4.4."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, cast

from casefile_contracts import (
    CompilerProfileBinding,
    ExposureBinding,
    NarrativeIR,
    NovelPlanIR,
    SceneCompilerInputBundleV2,
    SceneCompilerModelView,
)
from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)

SCENE_COMPILER_INPUT_V2_SCHEMA_ID = "compiler.scene-compiler-input.v2"
SCENE_COMPILER_MODEL_VIEW_SCHEMA_ID = "compiler.scene-compiler-model-view.v1"
SCENE_COMPILER_MODEL_VIEW_PROJECTION_VERSION = (
    "compiler.scene-compiler-model-view-projection.v2"
)
SCENE_COMPILER_BATCH_SIZE = 8


def build_scene_compiler_input_v2(
    *,
    novel_plan: NovelPlanIR | dict[str, Any],
    narrative_ir: NarrativeIR | dict[str, Any],
    exposure: ExposureBinding | dict[str, Any] | None,
    profile: CompilerProfileBinding | dict[str, Any],
) -> dict[str, Any]:
    """Bind all authoritative N4.4 inputs and derive hard execution obligations."""

    plan = _model_json(NovelPlanIR, novel_plan, "compiler_scene_input_novel_plan_invalid")
    narrative = _model_json(
        NarrativeIR, narrative_ir, "compiler_scene_input_narrative_ir_invalid"
    )
    exposure_json = (
        None
        if exposure is None
        else _model_json(
            ExposureBinding, exposure, "compiler_scene_input_exposure_invalid"
        )
    )
    profile_json = _model_json(
        CompilerProfileBinding, profile, "compiler_scene_input_profile_invalid"
    )
    _validate_bindings(plan, narrative, exposure_json, profile_json)
    constraints = _execution_constraints(plan)
    state_seed = _state_seed(narrative)
    payload = {
        "novel_plan": plan,
        "narrative_ir": narrative,
        "exposure_plan": exposure_json,
        "profile": profile_json,
        "execution_constraints": constraints,
        "state_seed": state_seed,
    }
    bundle = {
        "schema_id": SCENE_COMPILER_INPUT_V2_SCHEMA_ID,
        "source": {
            "novel_plan_hash": canonical_json_sha256(plan),
            "narrative_ir_hash": canonical_json_sha256(narrative),
            "exposure_hash": (
                None if exposure_json is None else canonical_json_sha256(exposure_json)
            ),
            "profile_hash": canonical_json_sha256(profile_json),
            "input_hash": canonical_json_sha256(payload),
        },
        **payload,
    }
    return validate_scene_compiler_input_v2(bundle).model_dump(mode="json")


def validate_scene_compiler_input_v2(
    bundle: SceneCompilerInputBundleV2 | dict[str, Any],
) -> SceneCompilerInputBundleV2:
    try:
        parsed = (
            bundle
            if isinstance(bundle, SceneCompilerInputBundleV2)
            else SceneCompilerInputBundleV2.model_validate(bundle)
        )
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_input_contract_invalid") from error
    value = parsed.model_dump(mode="json")
    source = value["source"]
    exposure = value["exposure_plan"]
    expected = {
        "novel_plan_hash": canonical_json_sha256(value["novel_plan"]),
        "narrative_ir_hash": canonical_json_sha256(value["narrative_ir"]),
        "exposure_hash": None if exposure is None else canonical_json_sha256(exposure),
        "profile_hash": canonical_json_sha256(value["profile"]),
    }
    for field, expected_hash in expected.items():
        if source[field] != expected_hash:
            raise CompilerContractError(f"compiler_scene_input_{field}_mismatch")
    payload = {key: value[key] for key in value if key not in {"schema_id", "source"}}
    if source["input_hash"] != canonical_json_sha256(payload):
        raise CompilerContractError("compiler_scene_input_hash_mismatch")
    _validate_bindings(
        value["novel_plan"], value["narrative_ir"], exposure, value["profile"]
    )
    if value["execution_constraints"] != _execution_constraints(value["novel_plan"]):
        raise CompilerContractError("compiler_scene_input_constraints_mismatch")
    if value["state_seed"] != _state_seed(value["narrative_ir"]):
        raise CompilerContractError("compiler_scene_input_state_seed_mismatch")
    return parsed


def build_scene_compiler_model_view(
    bundle: SceneCompilerInputBundleV2 | dict[str, Any],
) -> dict[str, Any]:
    """Project chapter-local batches without leaking the complete audit payload."""

    value = validate_scene_compiler_input_v2(bundle).model_dump(mode="json")
    constraints_by_chapter: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for constraint in value["execution_constraints"]:
        constraints_by_chapter[constraint["chapter_id"]].append(constraint)
    chapter_order = {
        chapter["chapter_id"]: chapter["ordinal"] for chapter in value["novel_plan"]["chapters"]
    }
    batches: list[dict[str, Any]] = []
    batch_ordinal = 0
    for chapter_id in sorted(constraints_by_chapter, key=chapter_order.__getitem__):
        scenes = sorted(
            constraints_by_chapter[chapter_id], key=lambda item: item["discourse_order"]
        )
        for chapter_batch_ordinal, offset in enumerate(
            range(0, len(scenes), SCENE_COMPILER_BATCH_SIZE), start=1
        ):
            batch_ordinal += 1
            batch_scenes = scenes[offset : offset + SCENE_COMPILER_BATCH_SIZE]
            selected_refs = _constraint_refs(batch_scenes)
            batch_state_seed = _filter_state_seed(value["state_seed"], selected_refs)
            visible_refs = selected_refs | _object_refs(batch_state_seed)
            object_catalog = _object_catalog(value["narrative_ir"], visible_refs)
            catalog_refs = {_ref_key(item["object_ref"]) for item in object_catalog}
            if catalog_refs != visible_refs:
                raise CompilerContractError(
                    "compiler_scene_model_view_reference_closure_invalid"
                )
            batch = {
                "batch_id": f"scene_batch_{chapter_id}_{chapter_batch_ordinal:03d}",
                "ordinal": batch_ordinal,
                "chapter_id": chapter_id,
                "scene_ids": [scene["scene_id"] for scene in batch_scenes],
                "scenes": batch_scenes,
                "object_catalog": object_catalog,
                "state_seed": batch_state_seed,
            }
            batches.append(batch)
    model_view = {
        "schema_id": SCENE_COMPILER_MODEL_VIEW_SCHEMA_ID,
        "source": {
            "projection_version": SCENE_COMPILER_MODEL_VIEW_PROJECTION_VERSION,
            "scene_compiler_input_hash": value["source"]["input_hash"],
        },
        "batches": batches,
    }
    try:
        return SceneCompilerModelView.model_validate(model_view).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_model_view_contract_invalid") from error


def scene_compiler_model_view_fingerprint(
    model_view: SceneCompilerModelView | dict[str, Any],
) -> dict[str, str]:
    try:
        value = (
            model_view.model_dump(mode="json")
            if isinstance(model_view, SceneCompilerModelView)
            else SceneCompilerModelView.model_validate(model_view).model_dump(mode="json")
        )
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_model_view_contract_invalid") from error
    return {
        "schema_id": value["schema_id"],
        "projection_version": value["source"]["projection_version"],
        "scene_compiler_input_hash": value["source"]["scene_compiler_input_hash"],
        "model_view_hash": canonical_json_sha256(value),
    }


def _validate_bindings(
    plan: dict[str, Any],
    narrative: dict[str, Any],
    exposure: dict[str, Any] | None,
    profile: dict[str, Any],
) -> None:
    narrative_hash = canonical_json_sha256(narrative)
    if plan["source"]["narrative_ir_hash"] != narrative_hash:
        raise CompilerContractError("compiler_scene_input_narrative_ir_binding_mismatch")
    if profile["content_hash"] != canonical_json_sha256(profile["frozen_payload"]):
        raise CompilerContractError("compiler_scene_input_profile_content_hash_mismatch")
    if plan["source"]["profile_hash"] != canonical_json_sha256(profile["frozen_payload"]):
        raise CompilerContractError("compiler_scene_input_profile_binding_mismatch")
    if exposure is None:
        if plan["source"]["exposure_hash"] is not None:
            raise CompilerContractError("compiler_scene_input_exposure_binding_mismatch")
    else:
        if exposure["content_hash"] != canonical_json_sha256(exposure["frozen_payload"]):
            raise CompilerContractError("compiler_scene_input_exposure_content_hash_mismatch")
        if plan["source"]["exposure_hash"] != canonical_json_sha256(exposure):
            raise CompilerContractError("compiler_scene_input_exposure_binding_mismatch")


def _execution_constraints(plan: dict[str, Any]) -> list[dict[str, Any]]:
    scenes = sorted(plan["scenes"], key=lambda item: item["discourse_order"])
    introduction_order = {
        placement["entry_key"]: scene["discourse_order"]
        for scene in scenes
        for placement in scene["exposure"]
        if placement["action"] == "introduce"
    }
    constraints: list[dict[str, Any]] = []
    for scene in scenes:
        obligations: list[dict[str, Any]] = []
        for ordinal, event_ref in enumerate(scene["event_refs"], start=1):
            obligations.append(
                _obligation(scene, "event", ordinal, [event_ref], event_ref=event_ref)
            )
        for ordinal, exposure in enumerate(scene["exposure"], start=1):
            obligations.append(
                _obligation(
                    scene, "exposure", ordinal, scene["basis_refs"], exposure=exposure
                )
            )
        for ordinal, resolution in enumerate(scene["resolutions"], start=1):
            obligations.append(
                _obligation(
                    scene,
                    "resolution",
                    ordinal,
                    [resolution["resolution_ref"]],
                    resolution=resolution,
                )
            )
        if not obligations:
            obligations.append(
                _obligation(scene, "transition", 1, scene["basis_refs"])
            )
        constraints.append(
            {
                "scene_id": scene["scene_id"],
                "chapter_id": scene["chapter_id"],
                "discourse_order": scene["discourse_order"],
                "purpose": scene["purpose"],
                "presentation_mode": scene["presentation_mode"],
                "pov_ref": scene["pov_ref"],
                "participant_refs": scene["participant_refs"],
                "location_ref": scene["location_ref"],
                "story_time_refs": scene["story_time_refs"],
                "basis_refs": scene["basis_refs"],
                "prerequisite_scene_ids": scene["prerequisite_scene_ids"],
                "obligations": obligations,
                "forbidden_reveal_entry_keys": sorted(
                    key
                    for key, order in introduction_order.items()
                    if order > scene["discourse_order"]
                ),
            }
        )
    return constraints


def _obligation(
    scene: dict[str, Any],
    kind: str,
    ordinal: int,
    basis_refs: list[dict[str, str]],
    *,
    event_ref: dict[str, str] | None = None,
    exposure: dict[str, str] | None = None,
    resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "obligation_key": f"obligation_{scene['scene_id']}_{kind}_{ordinal:03d}",
        "kind": kind,
        "basis_refs": sorted(basis_refs, key=_ref_key),
        "event_ref": event_ref,
        "exposure": exposure,
        "resolution": resolution,
    }


def _state_seed(narrative: dict[str, Any]) -> dict[str, Any]:
    knowledge: list[dict[str, Any]] = []
    for envelope in narrative["objects"]["entities"]:
        subject_ref = envelope["object_ref"]
        for state in envelope["value"]["knowledge_states"]:
            knowledge.append({"subject_ref": subject_ref, **state})
    events = [
        {
            "event_ref": envelope["object_ref"],
            "participant_refs": envelope["value"]["participant_refs"],
            "observer_refs": envelope["value"]["observed_by_refs"],
            "location_ref": envelope["value"]["location_ref"],
            "cause_refs": envelope["value"]["cause_refs"],
            "effect_refs": envelope["value"]["effect_refs"],
        }
        for envelope in narrative["objects"]["events"]
    ]
    return {
        "character_knowledge": sorted(
            knowledge,
            key=lambda item: (
                _ref_key(item["subject_ref"]),
                "" if item["as_of_event_ref"] is None else _ref_key(item["as_of_event_ref"]),
            ),
        ),
        "events": sorted(events, key=lambda item: _ref_key(item["event_ref"])),
    }


def _constraint_refs(scenes: list[dict[str, Any]]) -> set[str]:
    refs: list[dict[str, str]] = []
    for scene in scenes:
        refs.extend(scene["participant_refs"])
        refs.extend(scene["story_time_refs"])
        refs.extend(scene["basis_refs"])
        if scene["pov_ref"] is not None:
            refs.append(scene["pov_ref"])
        if scene["location_ref"] is not None:
            refs.append(scene["location_ref"])
        for obligation in scene["obligations"]:
            refs.extend(obligation["basis_refs"])
            if obligation["event_ref"] is not None:
                refs.append(obligation["event_ref"])
            if obligation["resolution"] is not None:
                refs.append(obligation["resolution"]["resolution_ref"])
    return {_ref_key(ref) for ref in refs}


def _object_catalog(
    narrative: dict[str, Any], selected_refs: set[str]
) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for envelopes in narrative["objects"].values():
        for envelope in envelopes:
            if _ref_key(envelope["object_ref"]) not in selected_refs:
                continue
            value = envelope["value"]
            label = next(
                (
                    str(value[key])
                    for key in ("title", "name", "summary", "content")
                    if isinstance(value.get(key), str) and value[key].strip()
                ),
                envelope["object_ref"]["object_id"],
            )
            facts: list[str] = []
            for key in ("traits", "goals", "capabilities", "access_rules"):
                for item in value.get(key, []):
                    if isinstance(item, str) and item.strip():
                        facts.append(f"{key}:{item}")
            catalog.append(
                {
                    "object_ref": envelope["object_ref"],
                    "label": label[:500],
                    "facts": sorted(set(facts)),
                }
            )
    return sorted(catalog, key=lambda item: _ref_key(item["object_ref"]))


def _filter_state_seed(
    state_seed: dict[str, Any], selected_refs: set[str]
) -> dict[str, Any]:
    return {
        "character_knowledge": [
            item
            for item in state_seed["character_knowledge"]
            if _ref_key(item["subject_ref"]) in selected_refs
        ],
        "events": [
            item
            for item in state_seed["events"]
            if _ref_key(item["event_ref"]) in selected_refs
        ],
    }


def _object_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        object_type = value.get("object_type")
        object_id = value.get("object_id")
        if isinstance(object_type, str) and isinstance(object_id, str):
            refs.add(f"{object_type}:{object_id}")
        for nested in value.values():
            refs.update(_object_refs(nested))
    elif isinstance(value, list):
        for nested in value:
            refs.update(_object_refs(nested))
    return refs


def _model_json(model: Any, value: Any, error_code: str) -> dict[str, Any]:
    try:
        parsed = value if isinstance(value, model) else model.model_validate(value)
    except ValidationError as error:
        raise CompilerContractError(error_code) from error
    return cast(dict[str, Any], parsed.model_dump(mode="json"))


def _ref_key(ref: dict[str, str]) -> str:
    return f"{ref['object_type']}:{ref['object_id']}"


__all__ = [
    "SCENE_COMPILER_BATCH_SIZE",
    "SCENE_COMPILER_INPUT_V2_SCHEMA_ID",
    "SCENE_COMPILER_MODEL_VIEW_PROJECTION_VERSION",
    "SCENE_COMPILER_MODEL_VIEW_SCHEMA_ID",
    "build_scene_compiler_input_v2",
    "build_scene_compiler_model_view",
    "scene_compiler_model_view_fingerprint",
    "validate_scene_compiler_input_v2",
]
