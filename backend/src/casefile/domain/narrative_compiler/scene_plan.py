"""Deterministic NovelPlan-to-ScenePlan execution compilation and linting."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import networkx as nx
from casefile_contracts import NovelPlanIR, ScenePlanIR
from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)

SCENE_PLAN_SCHEMA_ID = "compiler.scene-plan.v1"
SCENE_EXECUTION_COMPILER_VERSION = "compiler.scene-execution.v1"


@dataclass(frozen=True)
class ScenePlanViolation:
    code: str
    node_id: str | None = None
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class ScenePlanValidationReport:
    violations: tuple[ScenePlanViolation, ...]

    @property
    def succeeded(self) -> bool:
        return not self.violations


class NarrativeExecutionGraph:
    """Domain graph wrapper; public contracts never expose NetworkX values."""

    def __init__(self, scene_plan: ScenePlanIR | dict[str, Any]) -> None:
        try:
            parsed = (
                scene_plan
                if isinstance(scene_plan, ScenePlanIR)
                else ScenePlanIR.model_validate(scene_plan)
            )
        except ValidationError as error:
            raise CompilerContractError("compiler_scene_plan_contract_invalid") from error
        self._graph: nx.MultiDiGraph[str] = nx.MultiDiGraph()
        value = parsed.model_dump(mode="json")
        for chapter in value["chapters"]:
            self._graph.add_node(chapter["chapter_id"], kind="chapter")
        for scene in value["scenes"]:
            self._graph.add_node(scene["scene_id"], kind="scene")
        for beat in value["beats"]:
            self._graph.add_node(beat["beat_id"], kind="beat")
        for edge in value["edges"]:
            self._graph.add_edge(
                edge["from_node_id"],
                edge["to_node_id"],
                key=edge["edge_id"],
                relation=edge["relation"],
            )

    def descendants(self, node_id: str) -> tuple[str, ...]:
        if node_id not in self._graph:
            return ()
        return tuple(sorted(nx.descendants(self._graph, node_id)))

    def ancestors(self, node_id: str) -> tuple[str, ...]:
        if node_id not in self._graph:
            return ()
        return tuple(sorted(nx.ancestors(self._graph, node_id)))

    def affected_nodes(self, node_id: str) -> tuple[str, ...]:
        return self.descendants(node_id)

    def dependency_cycles(self) -> tuple[tuple[str, ...], ...]:
        dependency_graph: nx.DiGraph[str] = nx.DiGraph()
        for source, target, data in self._graph.edges(data=True):
            if data.get("relation") == "scene_enables_scene":
                dependency_graph.add_edge(source, target)
        cycles = [tuple(cycle) for cycle in nx.simple_cycles(dependency_graph)]
        return tuple(sorted(cycles))


def scene_plan_component_fingerprint(novel_plan: NovelPlanIR | dict[str, Any]) -> dict[str, str]:
    parsed = _validated_novel_plan(novel_plan)
    value = parsed.model_dump(mode="json")
    return {
        "component_version": SCENE_EXECUTION_COMPILER_VERSION,
        "scene_plan_schema_id": SCENE_PLAN_SCHEMA_ID,
        "novel_plan_schema_id": value["schema_id"],
        "novel_plan_hash": canonical_json_sha256(value),
        "narrative_ir_hash": value["source"]["narrative_ir_hash"],
    }


def compile_scene_plan(novel_plan: NovelPlanIR | dict[str, Any]) -> ScenePlanIR:
    """Compile one canonical NovelPlan into a deterministic execution plan."""

    parsed_plan = _validated_novel_plan(novel_plan)
    try:
        compiled = ScenePlanIR.model_validate(_compile_raw(parsed_plan.model_dump(mode="json")))
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_plan_contract_invalid") from error
    validate_scene_plan(compiled, novel_plan=parsed_plan)
    return compiled


def compile_scene_plan_json(novel_plan: NovelPlanIR | dict[str, Any]) -> dict[str, Any]:
    return compile_scene_plan(novel_plan).model_dump(mode="json")


def inspect_scene_plan(
    scene_plan: ScenePlanIR | dict[str, Any], *, novel_plan: NovelPlanIR | dict[str, Any]
) -> ScenePlanValidationReport:
    try:
        parsed_plan = _validated_novel_plan(novel_plan)
        parsed_scene_plan = (
            scene_plan
            if isinstance(scene_plan, ScenePlanIR)
            else ScenePlanIR.model_validate(scene_plan)
        )
    except (ValidationError, CompilerContractError):
        return ScenePlanValidationReport(
            (ScenePlanViolation("compiler_scene_plan_contract_invalid"),)
        )

    actual = parsed_scene_plan.model_dump(mode="json")
    expected = _compile_raw(parsed_plan.model_dump(mode="json"))
    checks = (
        ("source", "compiler_scene_plan_source_mismatch"),
        ("chapters", "compiler_scene_plan_chapter_coverage_invalid"),
        ("scenes", "compiler_scene_plan_scene_coverage_invalid"),
        ("beats", "compiler_scene_plan_beat_coverage_invalid"),
        ("edges", "compiler_scene_plan_graph_invalid"),
        ("indexes", "compiler_scene_plan_indexes_invalid"),
        ("metrics", "compiler_scene_plan_metrics_invalid"),
        ("diagnostics", "compiler_scene_plan_diagnostics_invalid"),
    )
    violations = [
        ScenePlanViolation(code)
        for field, code in checks
        if actual[field] != expected[field]
    ]
    if NarrativeExecutionGraph(parsed_scene_plan).dependency_cycles():
        violations.append(ScenePlanViolation("compiler_scene_plan_dependency_cycle"))
    return ScenePlanValidationReport(tuple(violations))


def validate_scene_plan(
    scene_plan: ScenePlanIR | dict[str, Any], *, novel_plan: NovelPlanIR | dict[str, Any]
) -> ScenePlanIR:
    try:
        parsed = (
            scene_plan
            if isinstance(scene_plan, ScenePlanIR)
            else ScenePlanIR.model_validate(scene_plan)
        )
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_plan_contract_invalid") from error
    report = inspect_scene_plan(parsed, novel_plan=novel_plan)
    if not report.succeeded:
        raise CompilerContractError(report.violations[0].code)
    return parsed


def _validated_novel_plan(novel_plan: NovelPlanIR | dict[str, Any]) -> NovelPlanIR:
    try:
        return (
            novel_plan
            if isinstance(novel_plan, NovelPlanIR)
            else NovelPlanIR.model_validate(novel_plan)
        )
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_plan_novel_plan_invalid") from error


def _compile_raw(novel_plan: dict[str, Any]) -> dict[str, Any]:
    fingerprint = scene_plan_component_fingerprint(novel_plan)
    scenes_in_order = sorted(novel_plan["scenes"], key=lambda item: item["discourse_order"])
    future_introductions = _introduction_order(scenes_in_order)
    audience_state: dict[str, dict[str, str]] = {}
    scenes: list[dict[str, Any]] = []
    beats: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    scene_beat_ids: dict[str, list[str]] = {}
    exposure_scene_ids: dict[str, list[str]] = defaultdict(list)
    resolution_scene_ids: dict[str, list[str]] = defaultdict(list)

    for scene in scenes_in_order:
        scene_id = scene["scene_id"]
        scene_beats = _compile_beats(scene)
        beat_ids = [beat["beat_id"] for beat in scene_beats]
        before = _audience_snapshot(audience_state)
        for placement in scene["exposure"]:
            _apply_exposure(audience_state, placement, scene_id)
            exposure_scene_ids[placement["entry_key"]].append(scene_id)
        after = _audience_snapshot(audience_state)
        for resolution in scene["resolutions"]:
            resolution_scene_ids[_ref_key(resolution["resolution_ref"])].append(scene_id)
        forbidden = sorted(
            key
            for key, order in future_introductions.items()
            if order > scene["discourse_order"]
        )
        scenes.append(
            {
                "scene_id": scene_id,
                "chapter_id": scene["chapter_id"],
                "discourse_order": scene["discourse_order"],
                "purpose": scene["purpose"],
                "presentation_mode": scene["presentation_mode"],
                "objective": scene["intent"],
                "pov_ref": scene["pov_ref"],
                "participant_refs": scene["participant_refs"],
                "location_ref": scene["location_ref"],
                "story_time_refs": scene["story_time_refs"],
                "audience_state_before": before,
                "audience_state_after": after,
                "allowed_reveals": scene["exposure"],
                "forbidden_reveal_entry_keys": forbidden,
                "resolution_actions": scene["resolutions"],
                "prerequisite_scene_ids": scene["prerequisite_scene_ids"],
                "beat_ids": beat_ids,
                "source_refs": scene["source_refs"],
            }
        )
        beats.extend(scene_beats)
        scene_beat_ids[scene_id] = beat_ids
        edges.extend(
            _edge("scene_contains_beat", scene_id, beat_id) for beat_id in beat_ids
        )
        edges.extend(
            _edge("scene_enables_scene", prerequisite, scene_id)
            for prerequisite in scene["prerequisite_scene_ids"]
        )

    chapters = []
    for chapter in sorted(novel_plan["chapters"], key=lambda item: item["ordinal"]):
        scene_ids = list(novel_plan["indexes"]["chapter_scene_ids"][chapter["chapter_id"]])
        chapters.append({**chapter, "scene_ids": scene_ids})
        edges.extend(
            _edge("chapter_contains_scene", chapter["chapter_id"], scene_id)
            for scene_id in scene_ids
        )

    return {
        "schema_id": SCENE_PLAN_SCHEMA_ID,
        "compiler_version": SCENE_EXECUTION_COMPILER_VERSION,
        "source": {
            "novel_plan_hash": fingerprint["novel_plan_hash"],
            "narrative_ir_hash": fingerprint["narrative_ir_hash"],
            "component_fingerprint": canonical_json_sha256(fingerprint),
        },
        "chapters": chapters,
        "scenes": scenes,
        "beats": beats,
        "edges": sorted(edges, key=lambda item: item["edge_id"]),
        "indexes": {
            "chapter_scene_ids": novel_plan["indexes"]["chapter_scene_ids"],
            "scene_beat_ids": dict(sorted(scene_beat_ids.items())),
            "scene_dependencies": novel_plan["indexes"]["scene_dependencies"],
            "exposure_scene_ids": dict(sorted(exposure_scene_ids.items())),
            "resolution_scene_ids": dict(sorted(resolution_scene_ids.items())),
        },
        "diagnostics": [],
        "metrics": {
            "chapter_count": len(chapters),
            "scene_count": len(scenes),
            "beat_count": len(beats),
            "exposure_count": len(exposure_scene_ids),
            "resolution_action_count": sum(len(scene["resolutions"]) for scene in scenes_in_order),
        },
    }


def _compile_beats(scene: dict[str, Any]) -> list[dict[str, Any]]:
    raw: list[dict[str, Any]] = []
    for event_ref in scene["event_refs"]:
        raw.append(
            {
                "kind": "event",
                "directive": f"execute:event:{event_ref['object_id']}",
                "basis_refs": [event_ref],
                "event_ref": event_ref,
                "exposure": None,
                "resolution": None,
                "audience_delta": [],
            }
        )
    for placement in scene["exposure"]:
        raw.append(
            {
                "kind": "exposure",
                "directive": f"exposure:{placement['action']}:{placement['entry_key']}",
                "basis_refs": scene["basis_refs"],
                "event_ref": None,
                "exposure": placement,
                "resolution": None,
                "audience_delta": [placement],
            }
        )
    for resolution in scene["resolutions"]:
        raw.append(
            {
                "kind": "resolution",
                "directive": (
                    f"resolution:{resolution['action']}:"
                    f"{resolution['resolution_ref']['object_id']}"
                ),
                "basis_refs": [resolution["resolution_ref"]],
                "event_ref": None,
                "exposure": None,
                "resolution": resolution,
                "audience_delta": [],
            }
        )
    if not raw:
        raw.append(
            {
                "kind": "transition",
                "directive": f"transition:{scene['scene_id']}:{scene['intent']}",
                "basis_refs": scene["basis_refs"],
                "event_ref": None,
                "exposure": None,
                "resolution": None,
                "audience_delta": [],
            }
        )
    return [
        {
            "beat_id": f"beat_{scene['scene_id']}_{ordinal:03d}",
            "scene_id": scene["scene_id"],
            "ordinal": ordinal,
            **beat,
            "source_refs": scene["source_refs"],
        }
        for ordinal, beat in enumerate(raw, start=1)
    ]


def _introduction_order(scenes: list[dict[str, Any]]) -> dict[str, int]:
    return {
        placement["entry_key"]: scene["discourse_order"]
        for scene in scenes
        for placement in scene["exposure"]
        if placement["action"] == "introduce"
    }


def _apply_exposure(
    state: dict[str, dict[str, str]], placement: dict[str, str], scene_id: str
) -> None:
    status = {
        "introduce": "introduced",
        "reinforce": "reinforced",
        "reinterpret": "reinterpreted",
    }[placement["action"]]
    existing = state.get(placement["entry_key"])
    state[placement["entry_key"]] = {
        "entry_key": placement["entry_key"],
        "status": status,
        "first_scene_id": scene_id if existing is None else existing["first_scene_id"],
        "last_scene_id": scene_id,
    }


def _audience_snapshot(state: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    return [dict(state[key]) for key in sorted(state)]


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
    "NarrativeExecutionGraph",
    "SCENE_EXECUTION_COMPILER_VERSION",
    "SCENE_PLAN_SCHEMA_ID",
    "ScenePlanValidationReport",
    "ScenePlanViolation",
    "compile_scene_plan",
    "compile_scene_plan_json",
    "inspect_scene_plan",
    "scene_plan_component_fingerprint",
    "validate_scene_plan",
]
