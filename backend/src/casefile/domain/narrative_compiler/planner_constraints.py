"""Deterministic hard-constraint projection for Story Planner experiments."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)
from casefile.domain.narrative_compiler.planner_input import validate_planner_input_bundle
from casefile_contracts import PlannerModelViewV3, PlannerModelViewV4

PLANNER_CONSTRAINT_IR_SCHEMA_ID = "compiler.planner-constraints.v1"
PLANNER_CONSTRAINT_IR_VERSION = "compiler.planner-constraint-projection.v1"
PLANNER_CONSTRAINT_IR_V2_SCHEMA_ID = "compiler.planner-constraints.v2"
PLANNER_CONSTRAINT_IR_V2_VERSION = "compiler.planner-constraint-projection.v2"
PLANNER_MODEL_VIEW_SCHEMA_ID = "compiler.story-planner-model-view.v3"
PLANNER_MODEL_VIEW_PROJECTION_VERSION = "compiler.story-planner-model-view-projection.v3"
PLANNER_MODEL_VIEW_V4_SCHEMA_ID = "compiler.story-planner-model-view.v4"
PLANNER_MODEL_VIEW_V4_PROJECTION_VERSION = "compiler.story-planner-model-view-projection.v4"


def build_planner_constraint_ir(planner_input: dict[str, Any]) -> dict[str, Any]:
    """Compile frozen planner inputs into provider-independent hard constraints."""

    parsed = validate_planner_input_bundle(planner_input)
    exposure = parsed.get("exposure_plan")
    entries = [] if exposure is None else exposure["frozen_payload"].get("entries", [])
    ordered_entries = sorted(
        entries,
        key=lambda item: (item["sequence_no"], item["entry_key"]),
    )
    exposure_keys = [str(item["entry_key"]) for item in ordered_entries]

    temporal_values: list[tuple[datetime, dict[str, str]]] = []
    for envelope in parsed["narrative_ir"]["objects"]["events"]:
        raw = _comparable_time(envelope["value"].get("time") or {})
        if raw is not None:
            temporal_values.append((raw, envelope["object_ref"]))
    temporal_values.sort(key=lambda item: (item[0], _ref_key(item[1])))

    anchors: list[dict[str, Any]] = []
    previous_time: datetime | None = None
    rank = 0
    for comparable_time, event_ref in temporal_values:
        if previous_time is None or comparable_time != previous_time:
            rank += 1
            previous_time = comparable_time
        anchors.append(
            {
                "event_ref": event_ref,
                "rank": rank,
                "comparable_time": comparable_time.isoformat(),
            }
        )

    resolutions = sorted(
        (
            envelope["object_ref"]
            for envelope in parsed["narrative_ir"]["objects"]["resolution_specs"]
        ),
        key=_ref_key,
    )
    constraints = parsed["planning_constraints"]
    return {
        "schema_id": PLANNER_CONSTRAINT_IR_SCHEMA_ID,
        "projection_version": PLANNER_CONSTRAINT_IR_VERSION,
        "source": {
            "planner_input_hash": canonical_json_sha256(parsed),
        },
        "structure": {
            "target_chapters": constraints["target_chapters"],
            "target_scenes": constraints["target_scenes"],
            "allowed_presentation_modes": constraints["allowed_presentation_modes"],
        },
        "exposure": {
            "introduce_order": exposure_keys,
            "precedence_edges": [
                {"before_entry_key": before, "after_entry_key": after}
                for before, after in zip(exposure_keys, exposure_keys[1:], strict=False)
            ],
        },
        "temporal": {"anchors": anchors},
        "resolutions": {
            "terminal_exactly_once": resolutions,
            "allowed_terminal_actions": ["resolve", "intentionally_unresolved"],
        },
    }


def validate_planner_constraint_ir(
    constraint_ir: dict[str, Any],
    *,
    planner_input: dict[str, Any],
) -> dict[str, Any]:
    """Re-prove that a constraint projection exactly matches frozen inputs."""

    expected = build_planner_constraint_ir(planner_input)
    if constraint_ir != expected:
        raise CompilerContractError("compiler_planner_constraint_ir_mismatch")
    return expected


def build_planner_constraint_ir_v2(planner_input: dict[str, Any]) -> dict[str, Any]:
    """Compile v3 input, including only author-declared hard semantic obligations."""

    parsed = validate_planner_input_bundle(planner_input)
    if parsed["schema_id"] != "compiler.story-planner-input.v3":
        raise CompilerContractError("compiler_planner_constraint_ir_v2_requires_input_v3")
    value = build_planner_constraint_ir(parsed)
    return {
        **value,
        "schema_id": PLANNER_CONSTRAINT_IR_V2_SCHEMA_ID,
        "projection_version": PLANNER_CONSTRAINT_IR_V2_VERSION,
        "semantic_obligations": parsed["planner_view"]["hard_constraints"][
            "semantic_obligations"
        ],
    }


def validate_planner_constraint_ir_v2(
    constraint_ir: dict[str, Any],
    *,
    planner_input: dict[str, Any],
) -> dict[str, Any]:
    expected = build_planner_constraint_ir_v2(planner_input)
    if constraint_ir != expected:
        raise CompilerContractError("compiler_planner_constraint_ir_v2_mismatch")
    return expected


def planner_constraint_ir_fingerprint(constraint_ir: dict[str, Any]) -> dict[str, str]:
    """Return stable projection identity for isolated benchmark comparisons."""

    if (
        constraint_ir.get("schema_id") != PLANNER_CONSTRAINT_IR_SCHEMA_ID
        or constraint_ir.get("projection_version") != PLANNER_CONSTRAINT_IR_VERSION
    ):
        raise CompilerContractError("compiler_planner_constraint_ir_invalid")
    return {
        "schema_id": PLANNER_CONSTRAINT_IR_SCHEMA_ID,
        "projection_version": PLANNER_CONSTRAINT_IR_VERSION,
        "content_hash": canonical_json_sha256(constraint_ir),
    }


def planner_constraint_ir_v2_fingerprint(constraint_ir: dict[str, Any]) -> dict[str, str]:
    if (
        constraint_ir.get("schema_id") != PLANNER_CONSTRAINT_IR_V2_SCHEMA_ID
        or constraint_ir.get("projection_version") != PLANNER_CONSTRAINT_IR_V2_VERSION
    ):
        raise CompilerContractError("compiler_planner_constraint_ir_v2_invalid")
    return {
        "schema_id": PLANNER_CONSTRAINT_IR_V2_SCHEMA_ID,
        "projection_version": PLANNER_CONSTRAINT_IR_V2_VERSION,
        "content_hash": canonical_json_sha256(constraint_ir),
    }


def build_planner_model_view_v3(planner_input: dict[str, Any]) -> dict[str, Any]:
    """Project a compact provider view while retaining full input server-side."""

    parsed = validate_planner_input_bundle(planner_input)
    if parsed["schema_id"] != "compiler.story-planner-input.v2":
        raise CompilerContractError("compiler_planner_model_view_v3_requires_input_v2")
    constraint_ir = build_planner_constraint_ir(parsed)
    objects = parsed["narrative_ir"]["objects"]
    object_catalog = {
        collection: [
            {
                "object_ref": envelope["object_ref"],
                "value": _without_source_refs(envelope["value"]),
            }
            for envelope in sorted(values, key=lambda item: _ref_key(item["object_ref"]))
        ]
        for collection, values in objects.items()
    }
    view = {
        "schema_id": PLANNER_MODEL_VIEW_SCHEMA_ID,
        "source": {
            "projection_version": PLANNER_MODEL_VIEW_PROJECTION_VERSION,
            "planner_input_hash": canonical_json_sha256(parsed),
            "constraint_ir_hash": canonical_json_sha256(constraint_ir),
        },
        "case": parsed["narrative_ir"]["case"],
        "hard_constraints": {
            "structure": constraint_ir["structure"],
            "exposure": constraint_ir["exposure"],
            "temporal": constraint_ir["temporal"],
            "resolutions": constraint_ir["resolutions"],
        },
        "object_catalog": object_catalog,
        "planning_context": parsed["planner_view"]["planning_context"],
    }
    try:
        return PlannerModelViewV3.model_validate(view).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_planner_model_view_v3_invalid") from error


def validate_planner_model_view_v3(
    model_view: dict[str, Any],
    *,
    planner_input: dict[str, Any],
) -> dict[str, Any]:
    """Re-prove a provider view from its complete frozen planner input."""

    expected = build_planner_model_view_v3(planner_input)
    if model_view != expected:
        raise CompilerContractError("compiler_planner_model_view_v3_mismatch")
    return expected


def build_planner_model_view_v4(planner_input: dict[str, Any]) -> dict[str, Any]:
    """Project v3 input with typed hard and soft obligations kept separate."""

    parsed = validate_planner_input_bundle(planner_input)
    if parsed["schema_id"] != "compiler.story-planner-input.v3":
        raise CompilerContractError("compiler_planner_model_view_v4_requires_input_v3")
    constraint_ir = build_planner_constraint_ir_v2(parsed)
    objects = parsed["narrative_ir"]["objects"]
    object_catalog = {
        collection: [
            {
                "object_ref": envelope["object_ref"],
                "value": _without_source_refs(envelope["value"]),
            }
            for envelope in sorted(values, key=lambda item: _ref_key(item["object_ref"]))
        ]
        for collection, values in objects.items()
    }
    view = {
        "schema_id": PLANNER_MODEL_VIEW_V4_SCHEMA_ID,
        "source": {
            "projection_version": PLANNER_MODEL_VIEW_V4_PROJECTION_VERSION,
            "planner_input_hash": canonical_json_sha256(parsed),
            "constraint_ir_hash": canonical_json_sha256(constraint_ir),
        },
        "case": parsed["narrative_ir"]["case"],
        "hard_constraints": {
            "structure": constraint_ir["structure"],
            "exposure": constraint_ir["exposure"],
            "temporal": constraint_ir["temporal"],
            "resolutions": constraint_ir["resolutions"],
            "semantic_obligations": constraint_ir["semantic_obligations"],
        },
        "object_catalog": object_catalog,
        "planning_context": parsed["planner_view"]["planning_context"],
    }
    try:
        return PlannerModelViewV4.model_validate(view).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_planner_model_view_v4_invalid") from error


def validate_planner_model_view_v4(
    model_view: dict[str, Any],
    *,
    planner_input: dict[str, Any],
) -> dict[str, Any]:
    expected = build_planner_model_view_v4(planner_input)
    if model_view != expected:
        raise CompilerContractError("compiler_planner_model_view_v4_mismatch")
    return expected


def planner_model_view_v3_fingerprint(model_view: dict[str, Any]) -> dict[str, str]:
    """Freeze provider-visible content independently from the audit bundle."""

    try:
        parsed = PlannerModelViewV3.model_validate(model_view).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_planner_model_view_v3_invalid") from error
    return {
        "schema_id": PLANNER_MODEL_VIEW_SCHEMA_ID,
        "projection_version": PLANNER_MODEL_VIEW_PROJECTION_VERSION,
        "content_hash": canonical_json_sha256(parsed),
    }


def planner_model_view_v4_fingerprint(model_view: dict[str, Any]) -> dict[str, str]:
    try:
        parsed = PlannerModelViewV4.model_validate(model_view).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_planner_model_view_v4_invalid") from error
    return {
        "schema_id": PLANNER_MODEL_VIEW_V4_SCHEMA_ID,
        "projection_version": PLANNER_MODEL_VIEW_V4_PROJECTION_VERSION,
        "content_hash": canonical_json_sha256(parsed),
    }


def _comparable_time(time_value: dict[str, Any]) -> datetime | None:
    raw = (
        time_value.get("value")
        if time_value.get("kind") in {"exact", "approximate"}
        else time_value.get("start")
    )
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _ref_key(ref: dict[str, Any]) -> tuple[str, str]:
    return str(ref.get("object_type", "")), str(ref.get("object_id", ""))


def _without_source_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_source_refs(item)
            for key, item in value.items()
            if key != "source_refs"
        }
    if isinstance(value, list):
        return [_without_source_refs(item) for item in value]
    return value


__all__ = [
    "PLANNER_CONSTRAINT_IR_SCHEMA_ID",
    "PLANNER_CONSTRAINT_IR_VERSION",
    "PLANNER_CONSTRAINT_IR_V2_SCHEMA_ID",
    "PLANNER_CONSTRAINT_IR_V2_VERSION",
    "PLANNER_MODEL_VIEW_PROJECTION_VERSION",
    "PLANNER_MODEL_VIEW_SCHEMA_ID",
    "PLANNER_MODEL_VIEW_V4_PROJECTION_VERSION",
    "PLANNER_MODEL_VIEW_V4_SCHEMA_ID",
    "build_planner_constraint_ir_v2",
    "build_planner_model_view_v4",
    "build_planner_model_view_v3",
    "build_planner_constraint_ir",
    "planner_constraint_ir_fingerprint",
    "planner_constraint_ir_v2_fingerprint",
    "planner_model_view_v3_fingerprint",
    "planner_model_view_v4_fingerprint",
    "validate_planner_constraint_ir",
    "validate_planner_constraint_ir_v2",
    "validate_planner_model_view_v3",
    "validate_planner_model_view_v4",
]
