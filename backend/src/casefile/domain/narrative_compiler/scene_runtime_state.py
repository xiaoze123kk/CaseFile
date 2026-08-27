"""Typed cross-batch state for bounded N4.4 Scene Semantic Fill."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from casefile_contracts import SceneCompilerInboundState, SceneCompilerModelView
from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)
from casefile.domain.narrative_compiler.scene_fill import validate_scene_semantic_fill

SCENE_COMPILER_INBOUND_STATE_SCHEMA_ID = "compiler.scene-compiler-inbound-state.v1"


@dataclass(slots=True)
class SceneCompilerRuntimeState:
    """Authoritative minimal state carried between Provider-facing batches."""

    object_refs: dict[str, dict[str, str]]
    observer_keys: set[str]
    knowledge: dict[str, dict[str, set[str]]]
    open_setups: dict[str, dict[str, str]]
    used_setup_keys: set[str]


def build_scene_compiler_runtime_state(
    model_view: SceneCompilerModelView | dict[str, Any],
) -> SceneCompilerRuntimeState:
    """Build the deterministic initial runtime state from the audited ModelView."""

    try:
        value = (
            model_view.model_dump(mode="json")
            if isinstance(model_view, SceneCompilerModelView)
            else SceneCompilerModelView.model_validate(model_view).model_dump(mode="json")
        )
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_model_view_contract_invalid") from error
    object_refs = {
        _ref_key(item["object_ref"]): item["object_ref"]
        for batch in value["batches"]
        for item in batch["object_catalog"]
    }
    observer_keys = {
        _ref_key(ref)
        for batch in value["batches"]
        for event in batch["state_seed"]["events"]
        for ref in event["observer_refs"]
    }
    knowledge: dict[str, dict[str, set[str]]] = {}
    for batch in value["batches"]:
        for item in batch["state_seed"]["character_knowledge"]:
            subject_key = _ref_key(item["subject_ref"])
            current = knowledge.setdefault(subject_key, _empty_knowledge())
            current["knows"].update(_ref_key(ref) for ref in item["knows_refs"])
            current["believes"].update(_ref_key(ref) for ref in item["believes_refs"])
            current["false_beliefs"].update(_ref_key(ref) for ref in item["false_belief_refs"])
    for current in knowledge.values():
        current["believes"] -= current["knows"]
        current["false_beliefs"] -= current["knows"]
    return SceneCompilerRuntimeState(
        object_refs=object_refs,
        observer_keys=observer_keys,
        knowledge=knowledge,
        open_setups={},
        used_setup_keys=set(),
    )


def project_scene_compiler_inbound_state(
    state: SceneCompilerRuntimeState,
    *,
    batch_view: dict[str, Any],
) -> dict[str, Any]:
    """Project only batch-visible dynamic state plus exact operation constraints."""

    visible_keys = {_ref_key(item["object_ref"]) for item in batch_view["object_catalog"]}
    knowledge: list[dict[str, Any]] = []
    constraints: list[dict[str, Any]] = []
    for subject_key, current in sorted(state.knowledge.items()):
        if subject_key not in visible_keys:
            continue
        knowledge.append(
            {
                "subject_ref": state.object_refs[subject_key],
                "knows_refs": _visible_refs(state, current["knows"], visible_keys),
                "believes_refs": _visible_refs(state, current["believes"], visible_keys),
                "false_belief_refs": _visible_refs(state, current["false_beliefs"], visible_keys),
            }
        )
        constraints.extend(
            {
                "subject_ref": state.object_refs[subject_key],
                "object_ref": state.object_refs[object_key],
                "allowed_operations": ["learn", "believe", "correct"],
            }
            for object_key in sorted(current["knows"] & visible_keys)
        )
    payload = {
        "schema_id": SCENE_COMPILER_INBOUND_STATE_SCHEMA_ID,
        "state_hash": scene_compiler_runtime_state_hash(state),
        "character_knowledge": knowledge,
        "knowledge_operation_constraints": constraints,
        "open_setups": [state.open_setups[key] for key in sorted(state.open_setups)],
        "used_setup_keys": sorted(state.used_setup_keys),
    }
    try:
        return SceneCompilerInboundState.model_validate(payload).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_inbound_state_contract_invalid") from error


def advance_scene_compiler_runtime_state(
    state: SceneCompilerRuntimeState,
    *,
    batch_view: dict[str, Any],
    semantic_fill: dict[str, Any],
) -> SceneCompilerRuntimeState:
    """Apply one validated batch using the same knowledge/setup invariants as replay."""

    fill = validate_scene_semantic_fill(semantic_fill, batch_view=batch_view)
    next_state = _copy_state(state)
    scenes = {item["scene_id"]: item for item in batch_view["scenes"]}
    for scene_fill in fill["scenes"]:
        scene = scenes[scene_fill["scene_id"]]
        allowed_subjects = {
            _ref_key(ref) for ref in scene["participant_refs"]
        } | next_state.observer_keys
        for beat_ordinal, beat in enumerate(scene_fill["beats"], start=1):
            beat_id = f"beat_{scene_fill['scene_id']}_{beat_ordinal:03d}"
            for transition in beat["knowledge_transitions"]:
                _apply_knowledge_transition(
                    next_state,
                    transition,
                    allowed_subjects=allowed_subjects,
                )
            for setup_key in beat["setup_keys"]:
                if setup_key in next_state.used_setup_keys:
                    raise CompilerContractError("compiler_scene_setup_duplicate")
                next_state.used_setup_keys.add(setup_key)
                next_state.open_setups[setup_key] = {
                    "setup_key": setup_key,
                    "setup_beat_id": beat_id,
                }
            for payoff_key in beat["payoff_keys"]:
                setup = next_state.open_setups.get(payoff_key)
                if setup is None or setup["setup_beat_id"] == beat_id:
                    raise CompilerContractError("compiler_scene_payoff_without_prior_setup")
                del next_state.open_setups[payoff_key]
    return next_state


def scene_compiler_runtime_state_hash(state: SceneCompilerRuntimeState) -> str:
    return canonical_json_sha256(
        {
            "character_knowledge": _all_knowledge(state),
            "open_setups": [state.open_setups[key] for key in sorted(state.open_setups)],
            "used_setup_keys": sorted(state.used_setup_keys),
        }
    )


def allowed_scene_knowledge_operations(current: dict[str, set[str]], object_key: str) -> set[str]:
    if object_key in current["knows"]:
        return {"learn", "believe", "correct"}
    return {"learn", "believe", "misbelieve", "correct"}


def _apply_knowledge_transition(
    state: SceneCompilerRuntimeState,
    transition: dict[str, Any],
    *,
    allowed_subjects: set[str],
) -> None:
    subject_key = _ref_key(transition["subject_ref"])
    if subject_key not in allowed_subjects:
        raise CompilerContractError("compiler_scene_knowledge_subject_invalid")
    object_key = _ref_key(transition["object_ref"])
    if object_key not in state.object_refs:
        raise CompilerContractError("compiler_scene_knowledge_object_invalid")
    current = state.knowledge.setdefault(subject_key, _empty_knowledge())
    operation = str(transition["operation"])
    if operation not in allowed_scene_knowledge_operations(current, object_key):
        raise CompilerContractError("compiler_scene_known_fact_cannot_be_false_belief")
    if operation in {"learn", "correct"}:
        current["knows"].add(object_key)
        current["believes"].discard(object_key)
        current["false_beliefs"].discard(object_key)
    elif operation == "believe":
        if object_key not in current["knows"]:
            current["believes"].add(object_key)
            current["false_beliefs"].discard(object_key)
    else:
        current["false_beliefs"].add(object_key)
        current["believes"].discard(object_key)


def _all_knowledge(state: SceneCompilerRuntimeState) -> list[dict[str, Any]]:
    return [
        {
            "subject_ref": state.object_refs[subject_key],
            "knows_refs": _refs(state, current["knows"]),
            "believes_refs": _refs(state, current["believes"]),
            "false_belief_refs": _refs(state, current["false_beliefs"]),
        }
        for subject_key, current in sorted(state.knowledge.items())
    ]


def _visible_refs(
    state: SceneCompilerRuntimeState, keys: set[str], visible_keys: set[str]
) -> list[dict[str, str]]:
    return _refs(state, keys & visible_keys)


def _refs(state: SceneCompilerRuntimeState, keys: set[str]) -> list[dict[str, str]]:
    return [state.object_refs[key] for key in sorted(keys)]


def _copy_state(state: SceneCompilerRuntimeState) -> SceneCompilerRuntimeState:
    return SceneCompilerRuntimeState(
        object_refs=dict(state.object_refs),
        observer_keys=set(state.observer_keys),
        knowledge={
            key: {name: set(values) for name, values in current.items()}
            for key, current in state.knowledge.items()
        },
        open_setups={key: dict(value) for key, value in state.open_setups.items()},
        used_setup_keys=set(state.used_setup_keys),
    )


def _empty_knowledge() -> dict[str, set[str]]:
    return {"knows": set(), "believes": set(), "false_beliefs": set()}


def _ref_key(ref: dict[str, str]) -> str:
    return f"{ref['object_type']}:{ref['object_id']}"


__all__ = [
    "SCENE_COMPILER_INBOUND_STATE_SCHEMA_ID",
    "SceneCompilerRuntimeState",
    "advance_scene_compiler_runtime_state",
    "allowed_scene_knowledge_operations",
    "build_scene_compiler_runtime_state",
    "project_scene_compiler_inbound_state",
    "scene_compiler_runtime_state_hash",
]
