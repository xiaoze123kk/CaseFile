"""Pure Constraint Compiler, PlanningSolver boundary, and reference backend."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import Any, Protocol

from casefile_contracts import (
    NovelPlanCandidate,
    PlanningProblem,
    PlanSkeleton,
    SemanticFillProposal,
    SkeletonProposal,
)
from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)
from casefile.domain.narrative_compiler.planner_constraints import (
    build_planner_constraint_ir_v2,
)
from casefile.domain.narrative_compiler.planner_input import (
    PLANNER_INPUT_V3_SCHEMA_ID,
    validate_planner_input_bundle,
)

PLANNING_PROBLEM_SCHEMA_ID = "compiler.planning-problem.v1"
PLANNING_PROBLEM_PROJECTION_VERSION = "compiler.planning-problem-projection.v1"
REFERENCE_SOLVER_VERSION = "compiler.planning-solver.reference.v2"
PLAN_SKELETON_SCHEMA_ID = "compiler.plan-skeleton.v1"
SKELETON_PROPOSAL_SCHEMA_ID = "compiler.skeleton-proposal.v1"
SEMANTIC_FILL_SCHEMA_ID = "compiler.semantic-fill.v1"


@dataclass(frozen=True, slots=True)
class PlanningSat:
    skeleton: dict[str, Any]
    changes: tuple[dict[str, Any], ...]
    proof: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PlanningUnsat:
    conflict_keys: tuple[str, ...]


PlanningResult = PlanningSat | PlanningUnsat


class PlanningSolverUnsupported(CompilerContractError):
    def __init__(self, reason: str) -> None:
        super().__init__("compiler_planning_solver_unsupported")
        self.details = {"reason": reason}


class PlanningSolverTimeout(CompilerContractError):
    def __init__(self) -> None:
        super().__init__("compiler_planning_solver_timeout")


class PlanningSolver(Protocol):
    def solve(
        self,
        problem: dict[str, Any],
        proposal: dict[str, Any],
    ) -> PlanningResult: ...


def compile_planning_problem(planner_input: dict[str, Any]) -> dict[str, Any]:
    """Compile frozen v3 input into canonical slots and solver-owned constraints."""

    parsed = validate_planner_input_bundle(planner_input)
    if parsed["schema_id"] != PLANNER_INPUT_V3_SCHEMA_ID:
        raise CompilerContractError("compiler_planning_problem_requires_input_v3")
    constraint_ir = build_planner_constraint_ir_v2(parsed)
    structure = constraint_ir["structure"]
    chapter_count = int(structure["target_chapters"])
    scene_count = int(structure["target_scenes"])
    chapter_slots = [
        {
            "chapter_id": f"chapter_{ordinal:03d}",
            "ordinal": ordinal,
            "act_ordinal": min(3, ((ordinal - 1) * 3 // chapter_count) + 1),
        }
        for ordinal in range(1, chapter_count + 1)
    ]
    scene_slots = []
    for discourse_order in range(1, scene_count + 1):
        chapter_index = min(
            chapter_count - 1,
            (discourse_order - 1) * chapter_count // scene_count,
        )
        scene_slots.append(
            {
                "scene_id": f"scene_{discourse_order:03d}",
                "chapter_id": chapter_slots[chapter_index]["chapter_id"],
                "discourse_order": discourse_order,
            }
        )
    object_refs = sorted(
        (
            envelope["object_ref"]
            for values in parsed["narrative_ir"]["objects"].values()
            for envelope in values
        ),
        key=_ref_key,
    )
    problem = {
        "schema_id": PLANNING_PROBLEM_SCHEMA_ID,
        "source": {
            "constraint_ir_schema_id": constraint_ir["schema_id"],
            "constraint_ir_hash": canonical_json_sha256(constraint_ir),
        },
        "chapter_slots": chapter_slots,
        "scene_slots": scene_slots,
        "object_refs": object_refs,
        "hard_constraints": {
            "structure": constraint_ir["structure"],
            "exposure": constraint_ir["exposure"],
            "temporal": constraint_ir["temporal"],
            "resolutions": constraint_ir["resolutions"],
            "semantic_obligations": constraint_ir["semantic_obligations"],
        },
    }
    try:
        return PlanningProblem.model_validate(problem).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_planning_problem_invalid") from error


def planning_problem_conflicts(problem: dict[str, Any]) -> tuple[str, ...]:
    """Return static conflicts before any Provider call."""

    try:
        parsed = PlanningProblem.model_validate(problem).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_planning_problem_invalid") from error
    catalog = {_ref_key(ref) for ref in parsed["object_refs"]}
    conflicts: set[str] = set()
    for obligation in parsed["hard_constraints"]["semantic_obligations"]:
        key = str(obligation["obligation_key"])
        refs = obligation.get("eligible_refs") or obligation.get("required_refs") or []
        if any(_ref_key(ref) not in catalog for ref in refs):
            conflicts.add(key)
        if obligation["kind"] == "participant_coverage" and int(
            obligation["min_distinct"]
        ) > len({_ref_key(ref) for ref in refs}):
            conflicts.add(key)
    resolution_keys = {
        _ref_key(ref)
        for ref in parsed["hard_constraints"]["resolutions"]["terminal_exactly_once"]
    }
    if not resolution_keys <= catalog:
        conflicts.add("resolution_catalog")
    return tuple(sorted(conflicts))


class ReferencePlanningSolver:
    """Deterministic bounded backend with no Provider, database, or solver dependency."""

    version = REFERENCE_SOLVER_VERSION

    def solve(
        self,
        problem: dict[str, Any],
        proposal: dict[str, Any],
    ) -> PlanningResult:
        conflicts = planning_problem_conflicts(problem)
        if conflicts:
            return PlanningUnsat(conflicts)
        parsed_problem = PlanningProblem.model_validate(problem).model_dump(mode="json")
        try:
            parsed_proposal = SkeletonProposal.model_validate(proposal).model_dump(mode="json")
        except ValidationError as error:
            raise PlanningSolverUnsupported("skeleton_proposal_schema_invalid") from error
        slots = parsed_problem["scene_slots"]
        proposed_by_id = {scene["scene_id"]: scene for scene in parsed_proposal["scenes"]}
        slot_ids = {slot["scene_id"] for slot in slots}
        if set(proposed_by_id) != slot_ids:
            raise PlanningSolverUnsupported("skeleton_proposal_slot_mismatch")

        catalog = {_ref_key(ref) for ref in parsed_problem["object_refs"]}
        fallback_basis = parsed_problem["object_refs"][0]
        allowed_modes = parsed_problem["hard_constraints"]["structure"][
            "allowed_presentation_modes"
        ]
        scenes: list[dict[str, Any]] = []
        changes: list[dict[str, Any]] = []
        for slot in slots:
            original = proposed_by_id[slot["scene_id"]]
            scene = copy.deepcopy(original)
            for field in ("chapter_id", "discourse_order"):
                scene[field] = slot[field]
            if scene["presentation_mode"] not in allowed_modes:
                scene["presentation_mode"] = allowed_modes[0]
            for field in ("participant_refs", "basis_refs"):
                scene[field] = _canonical_refs(
                    ref for ref in scene[field] if _ref_key(ref) in catalog
                )
            scene["story_time_refs"] = _canonical_refs(
                ref
                for ref in scene["story_time_refs"]
                if _ref_key(ref) in catalog and ref["object_type"] == "event"
            )
            if not scene["basis_refs"]:
                scene["basis_refs"] = [fallback_basis]
            scene["prerequisite_scene_ids"] = sorted(
                dependency
                for dependency in scene["prerequisite_scene_ids"]
                if dependency in slot_ids
                and dependency != scene["scene_id"]
                and proposed_by_id[dependency]["discourse_order"]
                < original["discourse_order"]
            )
            scenes.append(scene)

        _normalize_exposure(scenes, parsed_problem)
        _normalize_resolutions(scenes, parsed_problem)
        _apply_semantic_obligations(scenes, parsed_problem)
        _normalize_temporal_modes(scenes, parsed_problem)
        _prove_acyclic(scenes)

        for original in parsed_proposal["scenes"]:
            proposed = next(scene for scene in scenes if scene["scene_id"] == original["scene_id"])
            for field in sorted(set(original) | set(proposed)):
                if original.get(field) != proposed.get(field):
                    changes.append(
                        {
                            "scene_id": original["scene_id"],
                            "field": field,
                            "before": original.get(field),
                            "after": proposed.get(field),
                        }
                    )
        skeleton = {
            "schema_id": PLAN_SKELETON_SCHEMA_ID,
            "chapter_slots": parsed_problem["chapter_slots"],
            "scenes": sorted(scenes, key=lambda scene: scene["discourse_order"]),
        }
        try:
            canonical = PlanSkeleton.model_validate(skeleton).model_dump(mode="json")
        except ValidationError as error:
            raise CompilerContractError("compiler_plan_skeleton_invalid") from error
        proof = {
            "solver_version": self.version,
            "problem_hash": canonical_json_sha256(parsed_problem),
            "skeleton_hash": canonical_json_sha256(canonical),
            "constraint_keys": _constraint_keys(parsed_problem),
            "ranking": [
                "changed_fields",
                "preserve_nonlinear_modes",
                "canonical_lexicographic",
            ],
        }
        return PlanningSat(canonical, tuple(changes), proof)


def assemble_candidate_from_skeleton(
    skeleton: dict[str, Any],
    semantic_fill: dict[str, Any],
) -> dict[str, Any]:
    """Merge only model-owned fill fields; skeleton-owned fields cannot be overwritten."""

    try:
        parsed_skeleton = PlanSkeleton.model_validate(skeleton).model_dump(mode="json")
        parsed_fill = SemanticFillProposal.model_validate(semantic_fill).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_semantic_fill_invalid") from error
    chapter_fill = {item["chapter_id"]: item for item in parsed_fill["chapters"]}
    scene_fill = {item["scene_id"]: item for item in parsed_fill["scenes"]}
    chapter_ids = {item["chapter_id"] for item in parsed_skeleton["chapter_slots"]}
    scene_ids = {item["scene_id"] for item in parsed_skeleton["scenes"]}
    if set(chapter_fill) != chapter_ids or set(scene_fill) != scene_ids:
        raise CompilerContractError("compiler_semantic_fill_slot_mismatch")
    candidate = {
        "schema_id": "compiler.novel-plan-candidate.v1",
        "chapters": [
            {**slot, "title": chapter_fill[slot["chapter_id"]]["title"]}
            for slot in parsed_skeleton["chapter_slots"]
        ],
        "scenes": [
            {
                **scene,
                "intent": scene_fill[scene["scene_id"]]["intent"],
                "pov_ref": scene_fill[scene["scene_id"]]["pov_ref"],
                "location_ref": scene_fill[scene["scene_id"]]["location_ref"],
                "event_refs": scene_fill[scene["scene_id"]]["event_refs"],
            }
            for scene in parsed_skeleton["scenes"]
        ],
    }
    try:
        return NovelPlanCandidate.model_validate(candidate).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_story_plan_output_invalid") from error


def planning_component_fingerprint(
    *,
    planner_input: dict[str, Any],
    problem: dict[str, Any],
    solver_version: str,
    skeleton_prompt_version: str,
    skeleton_prompt_sha256: str,
    fill_prompt_version: str,
    fill_prompt_sha256: str,
) -> dict[str, Any]:
    return {
        "planner_input_schema_id": planner_input.get("schema_id"),
        "planner_input_hash": canonical_json_sha256(planner_input),
        "planning_problem_schema_id": problem.get("schema_id"),
        "planning_problem_projection_version": PLANNING_PROBLEM_PROJECTION_VERSION,
        "planning_problem_hash": canonical_json_sha256(problem),
        "solver_version": solver_version,
        "plan_skeleton_schema_id": PLAN_SKELETON_SCHEMA_ID,
        "skeleton_proposal_schema_id": SKELETON_PROPOSAL_SCHEMA_ID,
        "semantic_fill_schema_id": SEMANTIC_FILL_SCHEMA_ID,
        "skeleton_prompt_version": skeleton_prompt_version,
        "skeleton_prompt_sha256": skeleton_prompt_sha256,
        "semantic_fill_prompt_version": fill_prompt_version,
        "semantic_fill_prompt_sha256": fill_prompt_sha256,
    }


def _normalize_exposure(scenes: list[dict[str, Any]], problem: dict[str, Any]) -> None:
    expected = problem["hard_constraints"]["exposure"]["introduce_order"]
    actual = [
        placement["entry_key"]
        for scene in scenes
        for placement in scene["exposure"]
        if placement["action"] == "introduce"
    ]
    known = set(expected)
    introduced: set[str] = set()
    placements_valid = actual == expected
    for scene in scenes:
        for placement in scene["exposure"]:
            entry_key = placement["entry_key"]
            action = placement["action"]
            if entry_key not in known or (
                action != "introduce" and entry_key not in introduced
            ):
                placements_valid = False
            if action == "introduce":
                introduced.add(entry_key)
    if placements_valid:
        return
    for scene in scenes:
        scene["exposure"] = []
    for index, entry_key in enumerate(expected):
        scenes[min(index, len(scenes) - 1)]["exposure"].append(
            {"entry_key": entry_key, "action": "introduce"}
        )


def _normalize_resolutions(scenes: list[dict[str, Any]], problem: dict[str, Any]) -> None:
    required = problem["hard_constraints"]["resolutions"]["terminal_exactly_once"]
    terminal = [
        placement["resolution_ref"]
        for scene in scenes
        for placement in scene["resolutions"]
        if placement["action"] in {"resolve", "intentionally_unresolved"}
    ]
    known = {_ref_key(ref) for ref in required}
    placements = [
        placement
        for scene in scenes
        for placement in scene["resolutions"]
    ]
    if (
        sorted(map(_ref_key, terminal)) == sorted(known)
        and all(_ref_key(placement["resolution_ref"]) in known for placement in placements)
    ):
        return
    for scene in scenes:
        scene["resolutions"] = []
    scenes[-1]["resolutions"] = [
        {"resolution_ref": ref, "action": "resolve"} for ref in required
    ]


def _apply_semantic_obligations(
    scenes: list[dict[str, Any]], problem: dict[str, Any]
) -> None:
    for obligation in problem["hard_constraints"]["semantic_obligations"]:
        target = next(
            (
                scene
                for scene in scenes
                if any(
                    placement["entry_key"] == obligation["entry_key"]
                    for placement in scene["exposure"]
                )
            ),
            scenes[0],
        )
        if obligation["kind"] == "participant_coverage":
            existing = {_ref_key(ref) for ref in target["participant_refs"]}
            for ref in obligation["eligible_refs"]:
                if len(existing) >= int(obligation["min_distinct"]):
                    break
                if _ref_key(ref) not in existing:
                    target["participant_refs"].append(ref)
                    existing.add(_ref_key(ref))
            target["participant_refs"] = _canonical_refs(target["participant_refs"])
        else:
            target["basis_refs"] = _canonical_refs(
                [*target["basis_refs"], *obligation["required_refs"]]
            )


def _normalize_temporal_modes(
    scenes: list[dict[str, Any]], problem: dict[str, Any]
) -> None:
    anchors = problem["hard_constraints"]["temporal"]["anchors"]
    rank_by_ref = {
        _ref_key(anchor["event_ref"]): int(anchor["rank"])
        for anchor in anchors
    }
    allowed = problem["hard_constraints"]["structure"]["allowed_presentation_modes"]
    previous_rank: int | None = None
    for scene in sorted(scenes, key=lambda item: item["discourse_order"]):
        ranks = [
            rank_by_ref[_ref_key(ref)]
            for ref in scene["story_time_refs"]
            if _ref_key(ref) in rank_by_ref
        ]
        if not ranks:
            continue
        current_rank = min(ranks)
        if previous_rank is not None:
            mode = scene["presentation_mode"]
            valid = (
                (mode == "linear" and current_rank >= previous_rank)
                or (mode == "flashback" and current_rank <= previous_rank)
                or (mode == "flashforward" and current_rank >= previous_rank)
            )
            if not valid:
                replacement = _nonlinear_anchor_replacement(
                    mode=mode,
                    previous_rank=previous_rank,
                    anchors=anchors,
                )
                if replacement is not None:
                    scene["story_time_refs"] = [replacement]
                    current_rank = rank_by_ref[_ref_key(replacement)]
                    previous_rank = current_rank
                    continue
                preferred = "flashback" if current_rank < previous_rank else "linear"
                if preferred in allowed:
                    scene["presentation_mode"] = preferred
                else:
                    scene["story_time_refs"] = []
                    continue
        previous_rank = current_rank


def _nonlinear_anchor_replacement(
    *,
    mode: str,
    previous_rank: int,
    anchors: list[dict[str, Any]],
) -> dict[str, str] | None:
    if mode == "flashback":
        eligible = [anchor for anchor in anchors if int(anchor["rank"]) <= previous_rank]
        ordered = sorted(
            eligible,
            key=lambda item: (-int(item["rank"]), _ref_key(item["event_ref"])),
        )
    elif mode == "flashforward":
        eligible = [anchor for anchor in anchors if int(anchor["rank"]) >= previous_rank]
        ordered = sorted(
            eligible,
            key=lambda item: (int(item["rank"]), _ref_key(item["event_ref"])),
        )
    else:
        return None
    return copy.deepcopy(ordered[0]["event_ref"]) if ordered else None


def _prove_acyclic(scenes: list[dict[str, Any]]) -> None:
    graph = {
        scene["scene_id"]: set(scene["prerequisite_scene_ids"]) for scene in scenes
    }
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as error:  # pragma: no cover - earlier-only filtering proves this
        raise CompilerContractError("compiler_plan_skeleton_dependency_cycle") from error


def _constraint_keys(problem: dict[str, Any]) -> list[str]:
    keys = [
        "structure",
        "exposure_order",
        "temporal_modes",
        "resolution_closure",
    ]
    keys.extend(
        obligation["obligation_key"]
        for obligation in problem["hard_constraints"]["semantic_obligations"]
    )
    return sorted(keys)


def _canonical_refs(refs: Any) -> list[dict[str, str]]:
    unique = {_ref_key(ref): ref for ref in refs}
    return [unique[key] for key in sorted(unique)]


def _ref_key(ref: dict[str, Any]) -> tuple[str, str]:
    return str(ref.get("object_type", "")), str(ref.get("object_id", ""))


__all__ = [
    "PLAN_SKELETON_SCHEMA_ID",
    "PLANNING_PROBLEM_PROJECTION_VERSION",
    "PLANNING_PROBLEM_SCHEMA_ID",
    "REFERENCE_SOLVER_VERSION",
    "SEMANTIC_FILL_SCHEMA_ID",
    "SKELETON_PROPOSAL_SCHEMA_ID",
    "PlanningResult",
    "PlanningSat",
    "PlanningSolver",
    "PlanningSolverTimeout",
    "PlanningSolverUnsupported",
    "PlanningUnsat",
    "ReferencePlanningSolver",
    "assemble_candidate_from_skeleton",
    "compile_planning_problem",
    "planning_component_fingerprint",
    "planning_problem_conflicts",
]
