"""Deterministic NovelPlan-to-ScenePlan execution compilation and linting."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any

import networkx as nx
from casefile_contracts import (
    NarrativeIR,
    NovelPlanIR,
    SceneCompilerInputBundle,
    ScenePlanCandidate,
    ScenePlanIR,
)
from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)

SCENE_PLAN_SCHEMA_ID = "compiler.scene-plan.v1"
SCENE_EXECUTION_COMPILER_VERSION = "compiler.scene-execution.v1"
SCENE_COMPILER_INPUT_SCHEMA_ID = "compiler.scene-compiler-input.v1"
SCENE_PLAN_CANDIDATE_SCHEMA_ID = "compiler.scene-plan-candidate.v1"


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


@dataclass(frozen=True)
class ScenePlanCandidateViolation:
    code: str
    category: str
    scene_id: str | None = None
    beat_ordinal: int | None = None
    evidence: dict[str, Any] | None = None


@dataclass(frozen=True)
class ScenePlanCandidateValidationReport:
    contract_valid: bool
    violations: tuple[ScenePlanCandidateViolation, ...]

    @property
    def succeeded(self) -> bool:
        return self.contract_valid and not self.violations


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


def build_scene_compiler_input(
    *, novel_plan: NovelPlanIR | dict[str, Any], narrative_ir: NarrativeIR | dict[str, Any]
) -> dict[str, Any]:
    """Bind the complete audited N4.3 output to its matching lossless source IR."""

    parsed_plan = _validated_novel_plan(novel_plan)
    try:
        parsed_narrative = (
            narrative_ir
            if isinstance(narrative_ir, NarrativeIR)
            else NarrativeIR.model_validate(narrative_ir)
        )
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_plan_input_narrative_ir_invalid") from error
    plan_value = parsed_plan.model_dump(mode="json")
    narrative_value = parsed_narrative.model_dump(mode="json")
    novel_plan_hash = canonical_json_sha256(plan_value)
    narrative_ir_hash = canonical_json_sha256(narrative_value)
    if plan_value["source"]["narrative_ir_hash"] != narrative_ir_hash:
        raise CompilerContractError("compiler_scene_plan_input_narrative_ir_hash_mismatch")
    payload = {"novel_plan": plan_value, "narrative_ir": narrative_value}
    bundle = {
        "schema_id": SCENE_COMPILER_INPUT_SCHEMA_ID,
        "source": {
            "novel_plan_hash": novel_plan_hash,
            "narrative_ir_hash": narrative_ir_hash,
            "input_hash": canonical_json_sha256(payload),
        },
        **payload,
    }
    return validate_scene_compiler_input(bundle).model_dump(mode="json")


def validate_scene_compiler_input(
    bundle: SceneCompilerInputBundle | dict[str, Any],
) -> SceneCompilerInputBundle:
    try:
        parsed = (
            bundle
            if isinstance(bundle, SceneCompilerInputBundle)
            else SceneCompilerInputBundle.model_validate(bundle)
        )
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_plan_input_contract_invalid") from error
    value = parsed.model_dump(mode="json")
    plan = value["novel_plan"]
    narrative = value["narrative_ir"]
    if value["source"]["novel_plan_hash"] != canonical_json_sha256(plan):
        raise CompilerContractError("compiler_scene_plan_input_novel_plan_hash_mismatch")
    narrative_hash = canonical_json_sha256(narrative)
    if value["source"]["narrative_ir_hash"] != narrative_hash:
        raise CompilerContractError("compiler_scene_plan_input_narrative_ir_hash_mismatch")
    if plan["source"]["narrative_ir_hash"] != narrative_hash:
        raise CompilerContractError("compiler_scene_plan_input_narrative_ir_hash_mismatch")
    expected_input_hash = canonical_json_sha256(
        {"novel_plan": plan, "narrative_ir": narrative}
    )
    if value["source"]["input_hash"] != expected_input_hash:
        raise CompilerContractError("compiler_scene_plan_input_hash_mismatch")
    _validate_novel_plan_execution_structure(plan)
    return parsed


def build_baseline_scene_plan_candidate(
    novel_plan: NovelPlanIR | dict[str, Any],
) -> dict[str, Any]:
    """Mechanical candidate used only for reference replay and grader self-tests."""

    plan = _validated_novel_plan(novel_plan).model_dump(mode="json")
    scenes: list[dict[str, Any]] = []
    for scene in sorted(plan["scenes"], key=lambda item: item["discourse_order"]):
        beats: list[dict[str, Any]] = []
        for event_ref in scene["event_refs"]:
            beats.append(
                _candidate_beat(
                    scene,
                    kind="event",
                    directive=(
                        f"执行事件 {event_ref['object_id']}，"
                        f"服务于场景目标：{scene['intent']}"
                    ),
                    event_ref=event_ref,
                )
            )
        for exposure in scene["exposure"]:
            beats.append(
                _candidate_beat(
                    scene,
                    kind="exposure",
                    directive=(
                        f"{exposure['action']} 读者信息 {exposure['entry_key']}，"
                        f"并保持场景目标不变。"
                    ),
                    exposure=exposure,
                )
            )
        for resolution in scene["resolutions"]:
            beats.append(
                _candidate_beat(
                    scene,
                    kind="resolution",
                    directive=(
                        f"{resolution['action']} 结论 "
                        f"{resolution['resolution_ref']['object_id']}。"
                    ),
                    resolution=resolution,
                )
            )
        if not beats:
            beats.append(
                _candidate_beat(
                    scene,
                    kind="transition",
                    directive=f"完成场景过渡并推进目标：{scene['intent']}",
                )
            )
        scenes.append(
            {
                "scene_id": scene["scene_id"],
                "beats": [
                    {**beat, "ordinal": ordinal}
                    for ordinal, beat in enumerate(beats, start=1)
                ],
            }
        )
    return ScenePlanCandidate.model_validate(
        {"schema_id": SCENE_PLAN_CANDIDATE_SCHEMA_ID, "scenes": scenes}
    ).model_dump(mode="json")


def inspect_scene_plan_candidate(
    candidate: ScenePlanCandidate | dict[str, Any],
    *,
    scene_compiler_input: SceneCompilerInputBundle | dict[str, Any],
) -> ScenePlanCandidateValidationReport:
    try:
        parsed_input = validate_scene_compiler_input(scene_compiler_input)
    except CompilerContractError as error:
        return ScenePlanCandidateValidationReport(
            contract_valid=False,
            violations=(
                ScenePlanCandidateViolation(
                    error.reason_code,
                    "Dependency"
                    if "dependency" in error.reason_code
                    else "Contract",
                ),
            ),
        )
    try:
        parsed_candidate = (
            candidate
            if isinstance(candidate, ScenePlanCandidate)
            else ScenePlanCandidate.model_validate(candidate)
        )
    except ValidationError:
        return ScenePlanCandidateValidationReport(
            contract_valid=False,
            violations=(
                ScenePlanCandidateViolation(
                    "compiler_scene_plan_candidate_contract_invalid", "Contract"
                ),
            ),
        )

    value = parsed_candidate.model_dump(mode="json")
    bundle = parsed_input.model_dump(mode="json")
    planned_scenes = sorted(
        bundle["novel_plan"]["scenes"], key=lambda item: item["discourse_order"]
    )
    planned_ids = [scene["scene_id"] for scene in planned_scenes]
    actual_ids = [scene["scene_id"] for scene in value["scenes"]]
    violations: list[ScenePlanCandidateViolation] = []
    if actual_ids != planned_ids:
        violations.append(
            ScenePlanCandidateViolation(
                "compiler_scene_plan_candidate_scene_coverage_invalid",
                "Planning Transfer",
                evidence={"expected": planned_ids, "actual": actual_ids},
            )
        )
    plan_by_id = {scene["scene_id"]: scene for scene in planned_scenes}
    catalog = _narrative_source_ref_catalog(bundle["narrative_ir"])
    for scene_candidate in value["scenes"]:
        scene_id = scene_candidate["scene_id"]
        scene = plan_by_id.get(scene_id)
        if scene is None:
            continue
        violations.extend(
            _candidate_scene_violations(scene_candidate, scene=scene, catalog=catalog)
        )
    return ScenePlanCandidateValidationReport(
        contract_valid=True, violations=tuple(violations)
    )


def validate_scene_plan_candidate(
    candidate: ScenePlanCandidate | dict[str, Any],
    *,
    scene_compiler_input: SceneCompilerInputBundle | dict[str, Any],
) -> ScenePlanCandidate:
    report = inspect_scene_plan_candidate(
        candidate, scene_compiler_input=scene_compiler_input
    )
    if not report.succeeded:
        raise CompilerContractError(report.violations[0].code)
    return (
        candidate
        if isinstance(candidate, ScenePlanCandidate)
        else ScenePlanCandidate.model_validate(candidate)
    )


def canonicalize_scene_plan_candidate(
    candidate: ScenePlanCandidate | dict[str, Any],
    *,
    scene_compiler_input: SceneCompilerInputBundle | dict[str, Any],
) -> dict[str, Any]:
    parsed_input = validate_scene_compiler_input(scene_compiler_input)
    parsed_candidate = validate_scene_plan_candidate(
        candidate, scene_compiler_input=parsed_input
    )
    bundle = parsed_input.model_dump(mode="json")
    candidate_value = parsed_candidate.model_dump(mode="json")
    output = _compile_candidate_raw(bundle, candidate_value)
    try:
        return ScenePlanIR.model_validate(output).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_plan_canonicalization_failed") from error


def scene_plan_semantic_signature(scene_plan: ScenePlanIR | dict[str, Any]) -> str:
    try:
        value = (
            scene_plan.model_dump(mode="json")
            if isinstance(scene_plan, ScenePlanIR)
            else ScenePlanIR.model_validate(scene_plan).model_dump(mode="json")
        )
    except ValidationError as error:
        raise CompilerContractError("compiler_scene_plan_contract_invalid") from error
    projection = {
        "chapters": value["chapters"],
        "scenes": value["scenes"],
        "beats": [
            {key: item[key] for key in item if key not in {"directive", "source_refs"}}
            for item in value["beats"]
        ],
        "edges": value["edges"],
        "indexes": value["indexes"],
    }
    return canonical_json_sha256(projection)


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


def _validate_novel_plan_execution_structure(plan: dict[str, Any]) -> None:
    chapters = sorted(plan["chapters"], key=lambda item: item["ordinal"])
    scenes = sorted(plan["scenes"], key=lambda item: item["discourse_order"])
    chapter_ids = [item["chapter_id"] for item in chapters]
    scene_ids = [item["scene_id"] for item in scenes]
    if len(chapter_ids) != len(set(chapter_ids)) or len(scene_ids) != len(set(scene_ids)):
        raise CompilerContractError("compiler_scene_plan_input_identity_invalid")
    expected_chapter_index: dict[str, list[str]] = {key: [] for key in chapter_ids}
    expected_dependencies: dict[str, list[str]] = {}
    graph: dict[str, set[str]] = {}
    scene_id_set = set(scene_ids)
    for scene in scenes:
        if scene["chapter_id"] not in expected_chapter_index:
            raise CompilerContractError("compiler_scene_plan_input_chapter_reference_invalid")
        dependencies = list(scene["prerequisite_scene_ids"])
        if scene["scene_id"] in dependencies or not set(dependencies) <= scene_id_set:
            raise CompilerContractError("compiler_scene_plan_input_dependency_invalid")
        expected_chapter_index[scene["chapter_id"]].append(scene["scene_id"])
        expected_dependencies[scene["scene_id"]] = sorted(dependencies)
        graph[scene["scene_id"]] = set(dependencies)
    if plan["indexes"]["chapter_scene_ids"] != expected_chapter_index:
        raise CompilerContractError("compiler_scene_plan_input_chapter_index_invalid")
    if plan["indexes"]["scene_dependencies"] != expected_dependencies:
        raise CompilerContractError("compiler_scene_plan_input_dependency_index_invalid")
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as error:
        raise CompilerContractError("compiler_scene_plan_input_dependency_cycle") from error


def _candidate_beat(
    scene: dict[str, Any],
    *,
    kind: str,
    directive: str,
    event_ref: dict[str, str] | None = None,
    exposure: dict[str, str] | None = None,
    resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "directive": directive,
        "participant_refs": scene["participant_refs"],
        "location_ref": scene["location_ref"],
        "story_time_refs": scene["story_time_refs"],
        "basis_refs": scene["basis_refs"],
        "event_ref": event_ref,
        "exposure": exposure,
        "resolution": resolution,
    }


def _candidate_scene_violations(
    candidate: dict[str, Any],
    *,
    scene: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
) -> list[ScenePlanCandidateViolation]:
    scene_id = scene["scene_id"]
    violations: list[ScenePlanCandidateViolation] = []
    beats = candidate["beats"]
    if [item["ordinal"] for item in beats] != list(range(1, len(beats) + 1)):
        violations.append(
            ScenePlanCandidateViolation(
                "compiler_scene_plan_candidate_beat_order_invalid",
                "Temporal",
                scene_id=scene_id,
            )
        )
    allowed_participants = {_ref_key(item) for item in scene["participant_refs"]}
    allowed_basis = {_ref_key(item) for item in scene["basis_refs"]}
    allowed_times = {_ref_key(item) for item in scene["story_time_refs"]}
    allowed_events = Counter(_ref_key(item) for item in scene["event_refs"])
    allowed_exposure = Counter(_placement_key(item) for item in scene["exposure"])
    allowed_resolution = Counter(_resolution_key(item) for item in scene["resolutions"])
    seen_participants: set[str] = set()
    seen_basis: set[str] = set()
    seen_times: set[str] = set()
    seen_events: Counter[str] = Counter()
    seen_exposure: Counter[str] = Counter()
    seen_resolution: Counter[str] = Counter()
    saw_location = False
    for beat in beats:
        ordinal = beat["ordinal"]
        kind_payloads = {
            "event": beat["event_ref"],
            "exposure": beat["exposure"],
            "resolution": beat["resolution"],
        }
        populated = {key for key, payload in kind_payloads.items() if payload is not None}
        expected = set() if beat["kind"] == "transition" else {beat["kind"]}
        if populated != expected:
            violations.append(
                ScenePlanCandidateViolation(
                    "compiler_scene_plan_candidate_beat_kind_invalid",
                    "Contract",
                    scene_id=scene_id,
                    beat_ordinal=ordinal,
                )
            )
        all_refs = [
            *beat["participant_refs"],
            *beat["story_time_refs"],
            *beat["basis_refs"],
        ]
        if beat["location_ref"] is not None:
            all_refs.append(beat["location_ref"])
        if beat["event_ref"] is not None:
            all_refs.append(beat["event_ref"])
        if beat["resolution"] is not None:
            all_refs.append(beat["resolution"]["resolution_ref"])
        unknown = sorted({_ref_key(item) for item in all_refs} - set(catalog))
        if unknown:
            violations.append(
                ScenePlanCandidateViolation(
                    "compiler_scene_plan_candidate_reference_invalid",
                    "Grounding",
                    scene_id=scene_id,
                    beat_ordinal=ordinal,
                    evidence={"refs": unknown},
                )
            )
        participant_keys = {_ref_key(item) for item in beat["participant_refs"]}
        if not participant_keys <= allowed_participants:
            violations.append(
                ScenePlanCandidateViolation(
                    "compiler_scene_plan_candidate_participant_invalid",
                    "Grounding",
                    scene_id=scene_id,
                    beat_ordinal=ordinal,
                )
            )
        seen_participants.update(participant_keys)
        location = beat["location_ref"]
        if location is not None:
            if scene["location_ref"] is None or _ref_key(location) != _ref_key(
                scene["location_ref"]
            ):
                violations.append(
                    ScenePlanCandidateViolation(
                        "compiler_scene_plan_candidate_location_invalid",
                        "Grounding",
                        scene_id=scene_id,
                        beat_ordinal=ordinal,
                    )
                )
            else:
                saw_location = True
        time_keys = {_ref_key(item) for item in beat["story_time_refs"]}
        if not time_keys <= allowed_times:
            violations.append(
                ScenePlanCandidateViolation(
                    "compiler_scene_plan_candidate_temporal_invalid",
                    "Temporal",
                    scene_id=scene_id,
                    beat_ordinal=ordinal,
                )
            )
        seen_times.update(time_keys)
        basis_keys = {_ref_key(item) for item in beat["basis_refs"]}
        if not basis_keys <= allowed_basis:
            violations.append(
                ScenePlanCandidateViolation(
                    "compiler_scene_plan_candidate_provenance_invalid",
                    "Provenance",
                    scene_id=scene_id,
                    beat_ordinal=ordinal,
                )
            )
        seen_basis.update(basis_keys)
        if beat["event_ref"] is not None:
            seen_events[_ref_key(beat["event_ref"])] += 1
        if beat["exposure"] is not None:
            seen_exposure[_placement_key(beat["exposure"])] += 1
        if beat["resolution"] is not None:
            seen_resolution[_resolution_key(beat["resolution"])] += 1
    coverage_checks = (
        (
            seen_events == allowed_events,
            "compiler_scene_plan_candidate_event_coverage_invalid",
            "Planning Transfer",
        ),
        (
            seen_exposure == allowed_exposure,
            "compiler_scene_plan_candidate_exposure_invalid",
            "Reveal",
        ),
        (
            seen_resolution == allowed_resolution,
            "compiler_scene_plan_candidate_resolution_invalid",
            "Resolution",
        ),
        (
            seen_basis == allowed_basis,
            "compiler_scene_plan_candidate_provenance_coverage_invalid",
            "Provenance",
        ),
        (
            seen_participants == allowed_participants,
            "compiler_scene_plan_candidate_participant_coverage_invalid",
            "Grounding",
        ),
        (
            seen_times == allowed_times,
            "compiler_scene_plan_candidate_temporal_coverage_invalid",
            "Temporal",
        ),
        (
            scene["location_ref"] is None or saw_location,
            "compiler_scene_plan_candidate_location_coverage_invalid",
            "Grounding",
        ),
    )
    for passed, code, category in coverage_checks:
        if not passed:
            violations.append(
                ScenePlanCandidateViolation(code, category, scene_id=scene_id)
            )
    return violations


def _compile_candidate_raw(
    bundle: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    plan = bundle["novel_plan"]
    output = _compile_raw(plan)
    source_catalog = _narrative_source_ref_catalog(bundle["narrative_ir"])
    candidate_by_scene = {item["scene_id"]: item for item in candidate["scenes"]}
    beats: list[dict[str, Any]] = []
    scene_beat_ids: dict[str, list[str]] = {}
    for scene in output["scenes"]:
        scene_id = scene["scene_id"]
        beat_ids: list[str] = []
        for beat in candidate_by_scene[scene_id]["beats"]:
            beat_id = f"beat_{scene_id}_{beat['ordinal']:03d}"
            beat_ids.append(beat_id)
            source_refs = [
                source_catalog[_ref_key(ref)]
                for ref in sorted(beat["basis_refs"], key=_ref_key)
            ]
            beats.append(
                {
                    "beat_id": beat_id,
                    "scene_id": scene_id,
                    **beat,
                    "audience_delta": []
                    if beat["exposure"] is None
                    else [beat["exposure"]],
                    "source_refs": source_refs,
                }
            )
        scene["beat_ids"] = beat_ids
        scene_beat_ids[scene_id] = beat_ids
    retained_edges = [
        edge for edge in output["edges"] if edge["relation"] != "scene_contains_beat"
    ]
    retained_edges.extend(
        _edge("scene_contains_beat", scene_id, beat_id)
        for scene_id, beat_ids in scene_beat_ids.items()
        for beat_id in beat_ids
    )
    candidate_hash = canonical_json_sha256(candidate)
    output["source"] = {
        **output["source"],
        "scene_compiler_input_hash": bundle["source"]["input_hash"],
        "candidate_hash": candidate_hash,
        "component_fingerprint": canonical_json_sha256(
            {
                "component_version": SCENE_EXECUTION_COMPILER_VERSION,
                "input_hash": bundle["source"]["input_hash"],
                "candidate_hash": candidate_hash,
                "candidate_schema_id": SCENE_PLAN_CANDIDATE_SCHEMA_ID,
                "scene_plan_schema_id": SCENE_PLAN_SCHEMA_ID,
            }
        ),
    }
    output["beats"] = beats
    output["edges"] = sorted(retained_edges, key=lambda item: item["edge_id"])
    output["indexes"]["scene_beat_ids"] = dict(sorted(scene_beat_ids.items()))
    output["metrics"]["beat_count"] = len(beats)
    return output


def _narrative_source_ref_catalog(
    narrative_ir: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for envelopes in narrative_ir["objects"].values():
        for envelope in envelopes:
            result[_ref_key(envelope["object_ref"])] = envelope["source_ref"]
    return result


def _placement_key(value: dict[str, str]) -> str:
    return f"{value['entry_key']}:{value['action']}"


def _resolution_key(value: dict[str, Any]) -> str:
    return f"{_ref_key(value['resolution_ref'])}:{value['action']}"


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
            "scene_compiler_input_hash": None,
            "candidate_hash": None,
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
            "participant_refs": scene["participant_refs"],
            "location_ref": scene["location_ref"],
            "story_time_refs": scene["story_time_refs"],
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
    "SCENE_COMPILER_INPUT_SCHEMA_ID",
    "SCENE_EXECUTION_COMPILER_VERSION",
    "SCENE_PLAN_CANDIDATE_SCHEMA_ID",
    "SCENE_PLAN_SCHEMA_ID",
    "ScenePlanCandidateValidationReport",
    "ScenePlanCandidateViolation",
    "ScenePlanValidationReport",
    "ScenePlanViolation",
    "build_baseline_scene_plan_candidate",
    "build_scene_compiler_input",
    "canonicalize_scene_plan_candidate",
    "compile_scene_plan",
    "compile_scene_plan_json",
    "inspect_scene_plan",
    "inspect_scene_plan_candidate",
    "scene_plan_semantic_signature",
    "scene_plan_component_fingerprint",
    "validate_scene_compiler_input",
    "validate_scene_plan",
    "validate_scene_plan_candidate",
]
