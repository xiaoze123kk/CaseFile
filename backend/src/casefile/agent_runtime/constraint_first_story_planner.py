"""Provider-neutral Constraint-First Story Planner experimental pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol

from pydantic import ValidationError

from casefile.domain.narrative_compiler import (
    CompilerContractError,
    PlanningSat,
    PlanningSolver,
    PlanningUnsat,
    assemble_candidate_from_skeleton,
    compile_planning_problem,
    planning_problem_conflicts,
    repair_novel_plan_candidate,
    validate_novel_plan_candidate,
)
from casefile_contracts import SemanticFillProposal, SkeletonProposal

CONSTRAINT_FIRST_PIPELINE_VERSION = "compiler.story-planner.constraint-first.v1"
SKELETON_PROMPT_VERSION = "story-planner-skeleton-v1"
SEMANTIC_FILL_PROMPT_VERSION = "story-planner-semantic-fill-v1"


@dataclass(frozen=True, slots=True)
class SkeletonProposalRequest:
    task_run_id: int
    prompt_version: str
    planning_problem: dict[str, Any]
    model_view: dict[str, Any]
    input_hash: str
    model_id: str
    api_key: str
    max_turns: int = 1
    network_retries: int = 0
    emit: Callable[[str, str, dict[str, Any]], None] = lambda *_: None


@dataclass(frozen=True, slots=True)
class SkeletonProposalResult:
    proposal: dict[str, Any]
    usage: dict[str, Any]
    raw_output: str | None = None


@dataclass(frozen=True, slots=True)
class SemanticFillRequest:
    task_run_id: int
    prompt_version: str
    skeleton: dict[str, Any]
    model_view: dict[str, Any]
    input_hash: str
    model_id: str
    api_key: str
    max_turns: int = 1
    network_retries: int = 0
    emit: Callable[[str, str, dict[str, Any]], None] = lambda *_: None


@dataclass(frozen=True, slots=True)
class SemanticFillResult:
    fill: dict[str, Any]
    usage: dict[str, Any]
    raw_output: str | None = None


class ConstraintFirstStoryPlannerProvider(Protocol):
    def propose_skeleton(self, request: SkeletonProposalRequest) -> SkeletonProposalResult: ...

    def fill_semantics(self, request: SemanticFillRequest) -> SemanticFillResult: ...


@dataclass(frozen=True, slots=True)
class ConstraintFirstStage:
    stage: str
    input_hash: str
    output: dict[str, Any]
    usage: dict[str, Any]
    latency_ms: float
    raw_output: str | None
    recovered: bool = False


@dataclass(frozen=True, slots=True)
class ConstraintFirstExecution:
    candidate: dict[str, Any]
    planning_problem: dict[str, Any]
    skeleton: dict[str, Any]
    solver_changes: tuple[dict[str, Any], ...]
    solver_proof: dict[str, Any]
    stages: tuple[ConstraintFirstStage, ...]


def execute_constraint_first_story_planner(
    provider: ConstraintFirstStoryPlannerProvider,
    solver: PlanningSolver,
    *,
    task_run_id: int,
    planner_input: dict[str, Any],
    model_view: dict[str, Any],
    component_hash: str,
    model_id: str,
    api_key: str,
    network_retries: int = 0,
    recover_stage: Callable[[str, str], dict[str, Any] | None] | None = None,
    before_stage: Callable[[str, str, str, str], None] | None = None,
    after_stage: Callable[[ConstraintFirstStage], None] | None = None,
) -> ConstraintFirstExecution:
    """Execute preflight, proposal, deterministic solve, fill, assemble, and revalidation."""

    problem = compile_planning_problem(planner_input)
    conflicts = planning_problem_conflicts(problem)
    if conflicts:
        failure = CompilerContractError("compiler_planning_problem_unsat")
        failure.conflict_keys = conflicts  # type: ignore[attr-defined]
        raise failure

    stages: list[ConstraintFirstStage] = []
    skeleton_input_hash = _stage_hash(component_hash, "skeleton_proposal", problem)
    skeleton_request = SkeletonProposalRequest(
        task_run_id=task_run_id,
        prompt_version=SKELETON_PROMPT_VERSION,
        planning_problem=problem,
        model_view=model_view,
        input_hash=skeleton_input_hash,
        model_id=model_id,
        api_key=api_key,
        network_retries=network_retries,
    )
    recovered_proposal = (
        None if recover_stage is None else recover_stage("skeleton_proposal", skeleton_input_hash)
    )
    skeleton_result: SkeletonProposalResult | None = None
    latency_ms = 0.0
    if recovered_proposal is None:
        if before_stage is not None:
            before_stage(
                "skeleton_proposal",
                skeleton_input_hash,
                SKELETON_PROMPT_VERSION,
                "compiler.skeleton-proposal.v1",
            )
        started = perf_counter()
        skeleton_result = provider.propose_skeleton(skeleton_request)
        latency_ms = (perf_counter() - started) * 1000
        proposal_value = skeleton_result.proposal
    else:
        proposal_value = recovered_proposal
    try:
        proposal = SkeletonProposal.model_validate(proposal_value).model_dump(
            mode="json"
        )
    except ValidationError as error:
        raise CompilerContractError("compiler_skeleton_proposal_invalid") from error
    skeleton_stage = ConstraintFirstStage(
        stage="skeleton_proposal",
        input_hash=skeleton_input_hash,
        output=proposal,
        usage={} if skeleton_result is None else skeleton_result.usage,
        latency_ms=latency_ms,
        raw_output=None if skeleton_result is None else skeleton_result.raw_output,
        recovered=skeleton_result is None,
    )
    stages.append(skeleton_stage)
    if after_stage is not None:
        after_stage(skeleton_stage)

    solved = solver.solve(problem, proposal)
    if isinstance(solved, PlanningUnsat):
        failure = CompilerContractError("compiler_planning_solver_unsat")
        failure.conflict_keys = solved.conflict_keys  # type: ignore[attr-defined]
        raise failure
    assert isinstance(solved, PlanningSat)

    fill_input_hash = _stage_hash(component_hash, "semantic_fill", solved.skeleton)
    fill_request = SemanticFillRequest(
        task_run_id=task_run_id,
        prompt_version=SEMANTIC_FILL_PROMPT_VERSION,
        skeleton=solved.skeleton,
        model_view=model_view,
        input_hash=fill_input_hash,
        model_id=model_id,
        api_key=api_key,
        network_retries=network_retries,
    )
    recovered_fill = (
        None if recover_stage is None else recover_stage("semantic_fill", fill_input_hash)
    )
    fill_result: SemanticFillResult | None = None
    latency_ms = 0.0
    if recovered_fill is None:
        if before_stage is not None:
            before_stage(
                "semantic_fill",
                fill_input_hash,
                SEMANTIC_FILL_PROMPT_VERSION,
                "compiler.semantic-fill.v1",
            )
        started = perf_counter()
        fill_result = provider.fill_semantics(fill_request)
        latency_ms = (perf_counter() - started) * 1000
        fill_value = fill_result.fill
    else:
        fill_value = recovered_fill
    try:
        fill = SemanticFillProposal.model_validate(fill_value).model_dump(mode="json")
    except ValidationError as error:
        raise CompilerContractError("compiler_semantic_fill_invalid") from error
    fill_stage = ConstraintFirstStage(
        stage="semantic_fill",
        input_hash=fill_input_hash,
        output=fill,
        usage={} if fill_result is None else fill_result.usage,
        latency_ms=latency_ms,
        raw_output=None if fill_result is None else fill_result.raw_output,
        recovered=fill_result is None,
    )
    stages.append(fill_stage)
    if after_stage is not None:
        after_stage(fill_stage)
    candidate = assemble_candidate_from_skeleton(solved.skeleton, fill)
    repair = repair_novel_plan_candidate(candidate, planner_input=planner_input)
    candidate = repair.candidate
    validate_novel_plan_candidate(candidate, planner_input=planner_input)
    return ConstraintFirstExecution(
        candidate=candidate,
        planning_problem=problem,
        skeleton=solved.skeleton,
        solver_changes=solved.changes,
        solver_proof=solved.proof,
        stages=tuple(stages),
    )


def _stage_hash(component_hash: str, stage: str, value: dict[str, Any]) -> str:
    from casefile.domain.narrative_compiler import canonical_json_sha256

    return canonical_json_sha256(
        {"component_hash": component_hash, "stage": stage, "input": value}
    )


__all__ = [
    "CONSTRAINT_FIRST_PIPELINE_VERSION",
    "SEMANTIC_FILL_PROMPT_VERSION",
    "SKELETON_PROMPT_VERSION",
    "ConstraintFirstExecution",
    "ConstraintFirstStage",
    "ConstraintFirstStoryPlannerProvider",
    "SemanticFillRequest",
    "SemanticFillResult",
    "SkeletonProposalRequest",
    "SkeletonProposalResult",
    "execute_constraint_first_story_planner",
]
