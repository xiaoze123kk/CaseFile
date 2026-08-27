"""Authoritative deterministic State Engine and replay linter for ScenePlanIR v2."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from casefile_contracts import SceneCompilerInputBundleV2, ScenePlanIRV2
from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)
from casefile.domain.narrative_compiler.scene_compiler_input import (
    build_scene_compiler_model_view,
    validate_scene_compiler_input_v2,
)
from casefile.domain.narrative_compiler.scene_fill import validate_scene_semantic_fill
from casefile.domain.narrative_compiler.scene_runtime_state import (
    allowed_scene_knowledge_operations,
)

SCENE_PLAN_V2_SCHEMA_ID = "compiler.scene-plan.v2"
SCENE_EXECUTION_COMPILER_V2_VERSION = "compiler.scene-execution.v2"
SCENE_STATE_ENGINE_VERSION = "compiler.scene-state-engine.v1"


@dataclass(frozen=True)
class ScenePlanV2Violation:
    code: str
    node_id: str | None = None


@dataclass(frozen=True)
class ScenePlanV2ValidationReport:
    violations: tuple[ScenePlanV2Violation, ...]

    @property
    def succeeded(self) -> bool:
        return not self.violations


def compile_scene_plan_v2(
    *,
    scene_compiler_input: SceneCompilerInputBundleV2 | dict[str, Any],
    semantic_fills: list[dict[str, Any]],
) -> dict[str, Any]:
    """Canonicalize validated model fills into an authoritative typed execution plan."""

    bundle = validate_scene_compiler_input_v2(scene_compiler_input).model_dump(mode="json")
    model_view = build_scene_compiler_model_view(bundle)
    if len(semantic_fills) != len(model_view["batches"]):
        raise CompilerContractError("compiler_scene_state_fill_batch_coverage_invalid")
    fills: list[dict[str, Any]] = []
    for batch, fill in zip(model_view["batches"], semantic_fills, strict=True):
        fills.append(validate_scene_semantic_fill(fill, batch_view=batch))
    output = _compile_scene_plan_v2_raw(bundle, fills)
    try:
        return ScenePlanIRV2.model_validate(output).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_plan_v2_contract_invalid") from error


def inspect_scene_plan_v2(
    scene_plan: ScenePlanIRV2 | dict[str, Any],
    *,
    scene_compiler_input: SceneCompilerInputBundleV2 | dict[str, Any],
    semantic_fills: list[dict[str, Any]],
) -> ScenePlanV2ValidationReport:
    try:
        actual = (
            scene_plan.model_dump(mode="json")
            if isinstance(scene_plan, ScenePlanIRV2)
            else ScenePlanIRV2.model_validate(scene_plan).model_dump(mode="json")
        )
        expected = compile_scene_plan_v2(
            scene_compiler_input=scene_compiler_input, semantic_fills=semantic_fills
        )
    except (ValidationError, CompilerContractError):
        return ScenePlanV2ValidationReport(
            (ScenePlanV2Violation("compiler_scene_plan_v2_contract_invalid"),)
        )
    checks = (
        ("source", "compiler_scene_plan_v2_source_mismatch"),
        ("chapters", "compiler_scene_plan_v2_chapters_mismatch"),
        ("scenes", "compiler_scene_plan_v2_scenes_mismatch"),
        ("beats", "compiler_scene_plan_v2_beats_mismatch"),
        ("edges", "compiler_scene_plan_v2_edges_mismatch"),
        ("initial_state", "compiler_scene_plan_v2_initial_state_mismatch"),
        ("final_state", "compiler_scene_plan_v2_final_state_mismatch"),
        ("indexes", "compiler_scene_plan_v2_indexes_mismatch"),
        ("diagnostics", "compiler_scene_plan_v2_diagnostics_mismatch"),
        ("metrics", "compiler_scene_plan_v2_metrics_mismatch"),
    )
    return ScenePlanV2ValidationReport(
        tuple(
            ScenePlanV2Violation(code) for field, code in checks if actual[field] != expected[field]
        )
    )


def validate_scene_plan_v2(
    scene_plan: ScenePlanIRV2 | dict[str, Any],
    *,
    scene_compiler_input: SceneCompilerInputBundleV2 | dict[str, Any],
    semantic_fills: list[dict[str, Any]],
) -> ScenePlanIRV2:
    try:
        parsed = (
            scene_plan
            if isinstance(scene_plan, ScenePlanIRV2)
            else ScenePlanIRV2.model_validate(scene_plan)
        )
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_plan_v2_contract_invalid") from error
    report = inspect_scene_plan_v2(
        parsed,
        scene_compiler_input=scene_compiler_input,
        semantic_fills=semantic_fills,
    )
    if not report.succeeded:
        raise CompilerContractError(report.violations[0].code)
    return parsed


def scene_plan_v2_component_fingerprint(
    *, scene_compiler_input_hash: str, semantic_fill_hash: str
) -> dict[str, str]:
    return {
        "component_version": SCENE_EXECUTION_COMPILER_V2_VERSION,
        "state_engine_version": SCENE_STATE_ENGINE_VERSION,
        "scene_plan_schema_id": SCENE_PLAN_V2_SCHEMA_ID,
        "scene_compiler_input_hash": scene_compiler_input_hash,
        "semantic_fill_hash": semantic_fill_hash,
    }


def _compile_scene_plan_v2_raw(
    bundle: dict[str, Any], fills: list[dict[str, Any]]
) -> dict[str, Any]:
    plan = bundle["novel_plan"]
    narrative = bundle["narrative_ir"]
    constraints = {item["scene_id"]: item for item in bundle["execution_constraints"]}
    fill_by_scene = {scene["scene_id"]: scene for fill in fills for scene in fill["scenes"]}
    source_refs = _source_ref_catalog(narrative)
    object_refs = _object_ref_catalog(narrative)
    state = _initial_state(bundle["state_seed"], object_refs)
    initial_state = _state_snapshot(state)
    diagnostics: list[dict[str, Any]] = []
    beats: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    scene_beat_ids: dict[str, list[str]] = {}
    beat_dependencies: dict[str, list[str]] = {}
    setup_beat_ids: dict[str, str] = {}
    payoff_beat_ids: dict[str, list[str]] = defaultdict(list)
    exposure_scene_ids: dict[str, list[str]] = defaultdict(list)
    resolution_scene_ids: dict[str, list[str]] = defaultdict(list)
    knowledge_count = 0
    location_count = 0

    for scene in sorted(plan["scenes"], key=lambda item: item["discourse_order"]):
        scene_id = scene["scene_id"]
        fill = fill_by_scene[scene_id]
        constraint = constraints[scene_id]
        obligations = {item["obligation_key"]: item for item in constraint["obligations"]}
        before = _state_snapshot(state)
        before_hash = canonical_json_sha256(before)
        local_to_beat: dict[str, str] = {}
        beat_ids: list[str] = []
        audience_before = list(before["audience_exposure"])
        for ordinal, beat_fill in enumerate(fill["beats"], start=1):
            beat_id = f"beat_{scene_id}_{ordinal:03d}"
            beat_ids.append(beat_id)
            dependencies = [local_to_beat[key] for key in beat_fill["depends_on"]]
            local_to_beat[beat_fill["local_key"]] = beat_id
            beat_dependencies[beat_id] = dependencies
            edges.extend(
                _edge("beat_causes_beat", dependency, beat_id) for dependency in dependencies
            )
            selected_obligations = [
                obligations[key] for key in beat_fill["fulfills_obligation_keys"]
            ]
            event_refs = [
                item["event_ref"] for item in selected_obligations if item["event_ref"] is not None
            ]
            exposure_actions = [
                item["exposure"] for item in selected_obligations if item["exposure"] is not None
            ]
            resolution_actions = [
                item["resolution"]
                for item in selected_obligations
                if item["resolution"] is not None
            ]
            for exposure in exposure_actions:
                _apply_exposure(state["audience"], exposure, scene_id)
                exposure_scene_ids[exposure["entry_key"]].append(scene_id)
            for resolution in resolution_actions:
                resolution_scene_ids[_ref_key(resolution["resolution_ref"])].append(scene_id)
            for transition in beat_fill["knowledge_transitions"]:
                _apply_knowledge_transition(
                    state,
                    transition,
                    scene=scene,
                    object_refs=object_refs,
                    observers=_observer_refs(bundle["state_seed"]),
                )
                knowledge_count += 1
            for assertion in beat_fill["location_assertions"]:
                diagnostic = _apply_location_assertion(
                    state,
                    assertion,
                    scene=scene,
                    narrative=narrative,
                    source_refs=source_refs,
                )
                if diagnostic is not None:
                    diagnostics.append(diagnostic)
                location_count += 1
            for setup_key in beat_fill["setup_keys"]:
                if setup_key in state["all_setups"]:
                    raise CompilerContractError("compiler_scene_setup_duplicate")
                setup = {
                    "setup_key": setup_key,
                    "setup_beat_id": beat_id,
                    "basis_refs": beat_fill["basis_refs"],
                }
                state["all_setups"].add(setup_key)
                state["open_setups"][setup_key] = setup
                setup_beat_ids[setup_key] = beat_id
            for payoff_key in beat_fill["payoff_keys"]:
                setup = state["open_setups"].get(payoff_key)
                if setup is None or setup["setup_beat_id"] == beat_id:
                    raise CompilerContractError("compiler_scene_payoff_without_prior_setup")
                edges.append(_edge("beat_pays_off_setup", setup["setup_beat_id"], beat_id))
                payoff_beat_ids[payoff_key].append(beat_id)
                del state["open_setups"][payoff_key]
            beat_source_refs = _refs_to_sources(beat_fill["basis_refs"], source_refs)
            beats.append(
                {
                    "beat_id": beat_id,
                    "scene_id": scene_id,
                    "ordinal": ordinal,
                    "kind": beat_fill["kind"],
                    "directive": beat_fill["directive"],
                    "actor_refs": beat_fill["actor_refs"],
                    "target_refs": beat_fill["target_refs"],
                    "basis_refs": beat_fill["basis_refs"],
                    "obligation_keys": beat_fill["fulfills_obligation_keys"],
                    "prerequisite_beat_ids": dependencies,
                    "event_refs": event_refs,
                    "exposure_actions": exposure_actions,
                    "resolution_actions": resolution_actions,
                    "state_delta": {
                        "knowledge_transitions": beat_fill["knowledge_transitions"],
                        "location_assertions": beat_fill["location_assertions"],
                    },
                    "setup_keys": beat_fill["setup_keys"],
                    "payoff_keys": beat_fill["payoff_keys"],
                    "source_refs": beat_source_refs,
                }
            )
        after = _state_snapshot(state)
        scenes.append(
            {
                "scene_id": scene_id,
                "chapter_id": scene["chapter_id"],
                "discourse_order": scene["discourse_order"],
                "purpose": scene["purpose"],
                "presentation_mode": scene["presentation_mode"],
                "objective": scene["intent"],
                "dramatic_goal": fill["dramatic_goal"],
                "conflict": fill["conflict"],
                "outcome": fill["outcome"],
                "pov_ref": scene["pov_ref"],
                "participant_refs": scene["participant_refs"],
                "location_ref": scene["location_ref"],
                "story_time_refs": scene["story_time_refs"],
                "audience_state_before": audience_before,
                "audience_state_after": after["audience_exposure"],
                "allowed_reveals": scene["exposure"],
                "forbidden_reveal_entry_keys": constraint["forbidden_reveal_entry_keys"],
                "resolution_actions": scene["resolutions"],
                "prerequisite_scene_ids": scene["prerequisite_scene_ids"],
                "beat_ids": beat_ids,
                "state_before_hash": before_hash,
                "state_after_hash": canonical_json_sha256(after),
                "source_refs": scene["source_refs"],
            }
        )
        scene_beat_ids[scene_id] = beat_ids
        edges.extend(_edge("scene_contains_beat", scene_id, beat_id) for beat_id in beat_ids)
        edges.extend(
            _edge("scene_enables_scene", dependency, scene_id)
            for dependency in scene["prerequisite_scene_ids"]
        )

    if state["open_setups"]:
        raise CompilerContractError("compiler_scene_setup_unpaid")
    chapters = []
    for chapter in sorted(plan["chapters"], key=lambda item: item["ordinal"]):
        scene_ids = list(plan["indexes"]["chapter_scene_ids"][chapter["chapter_id"]])
        chapters.append({**chapter, "scene_ids": scene_ids})
        edges.extend(
            _edge("chapter_contains_scene", chapter["chapter_id"], scene_id)
            for scene_id in scene_ids
        )
    final_state = _state_snapshot(state)
    semantic_fill_hash = canonical_json_sha256(fills)
    fingerprint = scene_plan_v2_component_fingerprint(
        scene_compiler_input_hash=bundle["source"]["input_hash"],
        semantic_fill_hash=semantic_fill_hash,
    )
    return {
        "schema_id": SCENE_PLAN_V2_SCHEMA_ID,
        "compiler_version": SCENE_EXECUTION_COMPILER_V2_VERSION,
        "source": {
            "novel_plan_hash": bundle["source"]["novel_plan_hash"],
            "narrative_ir_hash": bundle["source"]["narrative_ir_hash"],
            "scene_compiler_input_hash": bundle["source"]["input_hash"],
            "semantic_fill_hash": semantic_fill_hash,
            "component_fingerprint": canonical_json_sha256(fingerprint),
        },
        "chapters": chapters,
        "scenes": scenes,
        "beats": beats,
        "edges": sorted(edges, key=lambda item: item["edge_id"]),
        "initial_state": initial_state,
        "final_state": final_state,
        "indexes": {
            "chapter_scene_ids": plan["indexes"]["chapter_scene_ids"],
            "scene_beat_ids": dict(sorted(scene_beat_ids.items())),
            "scene_dependencies": plan["indexes"]["scene_dependencies"],
            "beat_dependencies": dict(sorted(beat_dependencies.items())),
            "exposure_scene_ids": dict(sorted(exposure_scene_ids.items())),
            "resolution_scene_ids": dict(sorted(resolution_scene_ids.items())),
            "setup_beat_ids": dict(sorted(setup_beat_ids.items())),
            "payoff_beat_ids": dict(sorted(payoff_beat_ids.items())),
        },
        "diagnostics": sorted(
            diagnostics,
            key=lambda item: (item["code"], item.get("node_id") or ""),
        ),
        "metrics": {
            "chapter_count": len(chapters),
            "scene_count": len(scenes),
            "beat_count": len(beats),
            "exposure_count": len(exposure_scene_ids),
            "resolution_action_count": sum(len(scene["resolutions"]) for scene in plan["scenes"]),
            "knowledge_transition_count": knowledge_count,
            "location_assertion_count": location_count,
            "setup_count": len(setup_beat_ids),
            "payoff_count": sum(len(values) for values in payoff_beat_ids.values()),
        },
    }


def _initial_state(seed: dict[str, Any], object_refs: dict[str, dict[str, str]]) -> dict[str, Any]:
    knowledge: dict[str, dict[str, set[str]]] = {}
    for item in seed["character_knowledge"]:
        subject_key = _ref_key(item["subject_ref"])
        current = knowledge.setdefault(
            subject_key, {"knows": set(), "believes": set(), "false_beliefs": set()}
        )
        current["knows"].update(_ref_key(ref) for ref in item["knows_refs"])
        current["believes"].update(_ref_key(ref) for ref in item["believes_refs"])
        current["false_beliefs"].update(_ref_key(ref) for ref in item["false_belief_refs"])
        current["believes"] -= current["knows"]
        current["false_beliefs"] -= current["knows"]
    locations: list[dict[str, Any]] = []
    for event in seed["events"]:
        if event["location_ref"] is None:
            continue
        for subject_ref in [*event["participant_refs"], *event["observer_refs"]]:
            locations.append(
                {
                    "subject_ref": subject_ref,
                    "location_ref": event["location_ref"],
                    "story_time_refs": [event["event_ref"]],
                    "basis_refs": [event["event_ref"]],
                }
            )
    return {
        "audience": {},
        "knowledge": knowledge,
        "locations": locations,
        "open_setups": {},
        "all_setups": set(),
        "object_refs": object_refs,
    }


def _state_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    object_refs = state["object_refs"]
    knowledge = []
    for subject_key, values in sorted(state["knowledge"].items()):
        knowledge.append(
            {
                "subject_ref": object_refs[subject_key],
                "knows_refs": [object_refs[key] for key in sorted(values["knows"])],
                "believes_refs": [object_refs[key] for key in sorted(values["believes"])],
                "false_belief_refs": [object_refs[key] for key in sorted(values["false_beliefs"])],
            }
        )
    locations = sorted(
        state["locations"],
        key=lambda item: (
            _ref_key(item["subject_ref"]),
            tuple(_ref_key(ref) for ref in item["story_time_refs"]),
            _ref_key(item["location_ref"]),
        ),
    )
    return {
        "audience_exposure": [dict(state["audience"][key]) for key in sorted(state["audience"])],
        "character_knowledge": knowledge,
        "locations": locations,
        "open_setups": [state["open_setups"][key] for key in sorted(state["open_setups"])],
    }


def _apply_knowledge_transition(
    state: dict[str, Any],
    transition: dict[str, Any],
    *,
    scene: dict[str, Any],
    object_refs: dict[str, dict[str, str]],
    observers: set[str],
) -> None:
    subject_key = _ref_key(transition["subject_ref"])
    allowed_subjects = {_ref_key(ref) for ref in scene["participant_refs"]} | observers
    if subject_key not in allowed_subjects:
        raise CompilerContractError("compiler_scene_knowledge_subject_invalid")
    object_key = _ref_key(transition["object_ref"])
    if object_key not in object_refs:
        raise CompilerContractError("compiler_scene_knowledge_object_invalid")
    current = state["knowledge"].setdefault(
        subject_key, {"knows": set(), "believes": set(), "false_beliefs": set()}
    )
    operation = transition["operation"]
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


def _apply_location_assertion(
    state: dict[str, Any],
    assertion: dict[str, Any],
    *,
    scene: dict[str, Any],
    narrative: dict[str, Any],
    source_refs: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    subject_key = _ref_key(assertion["subject_ref"])
    if subject_key not in {_ref_key(ref) for ref in scene["participant_refs"]}:
        raise CompilerContractError("compiler_scene_location_subject_invalid")
    if scene["location_ref"] is None or _ref_key(assertion["location_ref"]) != _ref_key(
        scene["location_ref"]
    ):
        raise CompilerContractError("compiler_scene_location_scope_invalid")
    if not {_ref_key(ref) for ref in assertion["story_time_refs"]} <= {
        _ref_key(ref) for ref in scene["story_time_refs"]
    }:
        raise CompilerContractError("compiler_scene_location_time_invalid")
    previous = next(
        (
            item
            for item in reversed(state["locations"])
            if _ref_key(item["subject_ref"]) == subject_key
        ),
        None,
    )
    diagnostic = None
    if previous is not None and _ref_key(previous["location_ref"]) != _ref_key(
        assertion["location_ref"]
    ):
        check = _travel_check(previous, assertion, narrative)
        if check == "impossible":
            raise CompilerContractError("compiler_scene_location_travel_impossible")
        if check == "unverifiable":
            diagnostic = {
                "severity": "warning",
                "code": "scene_location_continuity_unverifiable",
                "message": "地点变化缺少可比较时间或明确旅行规则，已保留显式断言。",
                "artifact_ref": None,
                "artifact_type": SCENE_PLAN_V2_SCHEMA_ID,
                "node_id": scene["scene_id"],
                "details": {"subject_ref": assertion["subject_ref"]},
                "source_refs": _refs_to_sources(assertion["basis_refs"], source_refs),
            }
    state["locations"].append(assertion)
    return diagnostic


def _travel_check(
    previous: dict[str, Any], current: dict[str, Any], narrative: dict[str, Any]
) -> str:
    event_values = {
        _ref_key(item["object_ref"]): item["value"] for item in narrative["objects"]["events"]
    }
    previous_time = _exact_time(previous["story_time_refs"], event_values)
    current_time = _exact_time(current["story_time_refs"], event_values)
    if previous_time is None or current_time is None or current_time <= previous_time:
        return "unverifiable"
    locations = {
        _ref_key(item["object_ref"]): item["value"] for item in narrative["objects"]["locations"]
    }
    source = locations.get(_ref_key(previous["location_ref"]))
    if source is None:
        return "unverifiable"
    travel = next(
        (
            item
            for item in source["travel_times"]
            if _ref_key(item["to_ref"]) == _ref_key(current["location_ref"])
        ),
        None,
    )
    if travel is None:
        return "unverifiable"
    elapsed_minutes = (current_time - previous_time).total_seconds() / 60
    return "ok" if elapsed_minutes >= float(travel["minutes"]) else "impossible"


def _exact_time(
    refs: list[dict[str, str]], event_values: dict[str, dict[str, Any]]
) -> datetime | None:
    if len(refs) != 1:
        return None
    event = event_values.get(_ref_key(refs[0]))
    if event is None or event["time"]["kind"] != "exact":
        return None
    try:
        return datetime.fromisoformat(event["time"]["value"])
    except ValueError:
        return None


def _apply_exposure(
    audience: dict[str, dict[str, str]], exposure: dict[str, str], scene_id: str
) -> None:
    existing = audience.get(exposure["entry_key"])
    audience[exposure["entry_key"]] = {
        "entry_key": exposure["entry_key"],
        "status": {
            "introduce": "introduced",
            "reinforce": "reinforced",
            "reinterpret": "reinterpreted",
        }[exposure["action"]],
        "first_scene_id": scene_id if existing is None else existing["first_scene_id"],
        "last_scene_id": scene_id,
    }


def _observer_refs(seed: dict[str, Any]) -> set[str]:
    return {_ref_key(ref) for event in seed["events"] for ref in event["observer_refs"]}


def _source_ref_catalog(narrative: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _ref_key(item["object_ref"]): item["source_ref"]
        for values in narrative["objects"].values()
        for item in values
    }


def _object_ref_catalog(narrative: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {
        _ref_key(item["object_ref"]): item["object_ref"]
        for values in narrative["objects"].values()
        for item in values
    }


def _refs_to_sources(
    refs: list[dict[str, str]], source_refs: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    try:
        return [source_refs[key] for key in sorted({_ref_key(ref) for ref in refs})]
    except KeyError as error:
        raise CompilerContractError("compiler_scene_state_provenance_invalid") from error


def _edge(relation: str, source: str, target: str) -> dict[str, str]:
    digest = canonical_json_sha256([relation, source, target])[:24]
    return {
        "edge_id": f"edge_{digest}",
        "relation": relation,
        "from_node_id": source,
        "to_node_id": target,
    }


def _ref_key(ref: dict[str, str]) -> str:
    return f"{ref['object_type']}:{ref['object_id']}"


__all__ = [
    "SCENE_EXECUTION_COMPILER_V2_VERSION",
    "SCENE_PLAN_V2_SCHEMA_ID",
    "SCENE_STATE_ENGINE_VERSION",
    "ScenePlanV2ValidationReport",
    "ScenePlanV2Violation",
    "compile_scene_plan_v2",
    "inspect_scene_plan_v2",
    "scene_plan_v2_component_fingerprint",
    "validate_scene_plan_v2",
]
