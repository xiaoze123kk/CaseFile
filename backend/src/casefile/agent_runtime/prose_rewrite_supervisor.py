"""Server-owned N4.5 semantic review and bounded full-Rewrite loop."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from casefile.agent_runtime.prose_judge import (
    FIDELITY_ONLY_POLICY,
    PROSE_COUNCIL_MODEL_ID,
    ProseCouncilExecution,
    ProseJudgeProvider,
    execute_semantic_council,
)
from casefile.agent_runtime.prose_rewriter import (
    PROSE_REWRITER_MAX_CALLS_PER_SCENE,
    PROSE_REWRITER_MODEL_ID,
    ProseRewriterExecution,
    ProseRewriterProvider,
    execute_prose_rewriter,
)
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    validate_prose_judge_checklist,
    validate_scene_render,
)

PROSE_REWRITE_SUPERVISOR_VERSION = "prose-rewrite-supervisor-v1"


@dataclass(frozen=True, slots=True)
class ProseRewriteRoundExecution:
    round_index: int
    render: dict[str, Any]
    council: ProseCouncilExecution
    rewrite: ProseRewriterExecution | None


@dataclass(frozen=True, slots=True)
class ProseRewriteSupervisorExecution:
    status: Literal[
        "semantic_accepted", "semantic_rejected", "protocol_failed", "inconclusive"
    ]
    rounds: tuple[ProseRewriteRoundExecution, ...]
    final_render: dict[str, Any] | None
    rewrite_count: int
    model_call_count: int
    remaining_scene_call_budget: int
    error_code: str | None = None


def execute_bounded_prose_rewrite(
    rewriter_provider: ProseRewriterProvider,
    judge_provider: ProseJudgeProvider,
    *,
    scene_plan: dict[str, Any],
    narrative_ir: dict[str, Any],
    profile: dict[str, Any],
    checklist: dict[str, Any],
    previous_scene_render: dict[str, Any] | None,
    initial_render: dict[str, Any],
    model_id: str,
    api_key: str,
    remaining_scene_call_budget: int,
) -> ProseRewriteSupervisorExecution:
    """Review an initial Writer render and allow at most two complete rewrites."""

    if model_id != PROSE_REWRITER_MODEL_ID or model_id != PROSE_COUNCIL_MODEL_ID:
        return _terminal(
            "protocol_failed",
            (),
            None,
            remaining_scene_call_budget,
            "prose_rewrite_supervisor_model_id_not_frozen",
        )
    if not isinstance(remaining_scene_call_budget, int) or isinstance(
        remaining_scene_call_budget, bool
    ) or not (1 <= remaining_scene_call_budget <= 23):
        return _terminal(
            "protocol_failed",
            (),
            None,
            0,
            "prose_rewrite_supervisor_call_budget_invalid",
        )
    try:
        checklist_json = validate_prose_judge_checklist(
            checklist,
            scene_plan=scene_plan,
            narrative_ir=narrative_ir,
            profile=profile,
            previous_scene_render=previous_scene_render,
        ).model_dump(mode="json")
        initial = validate_scene_render(
            initial_render, checklist=checklist_json, profile=profile
        ).model_dump(mode="json")
    except CompilerContractError as error:
        return _terminal(
            "protocol_failed", (), None, remaining_scene_call_budget, str(error)
        )
    if initial["stage"] != "writer" or initial["round"] != 0:
        return _terminal(
            "protocol_failed",
            (),
            None,
            remaining_scene_call_budget,
            "prose_rewrite_supervisor_initial_render_invalid",
        )

    rounds: list[ProseRewriteRoundExecution] = []
    current = initial
    remaining = remaining_scene_call_budget
    rewrite_execution: ProseRewriterExecution | None = None
    for round_index in range(PROSE_REWRITER_MAX_CALLS_PER_SCENE + 1):
        if remaining < 1:
            return _terminal(
                "protocol_failed",
                rounds,
                current,
                remaining,
                "prose_rewrite_supervisor_call_budget_exhausted",
            )
        council = execute_semantic_council(
            judge_provider,
            checklist=checklist_json,
            render=current,
            profile=profile,
            policy=FIDELITY_ONLY_POLICY,
            model_id=model_id,
            api_key=api_key,
        )
        remaining -= _council_call_count(council)
        rounds.append(
            ProseRewriteRoundExecution(round_index, current, council, rewrite_execution)
        )
        if council.status != "completed":
            return _terminal(
                council.status,
                rounds,
                current,
                remaining,
                council.error_code,
            )
        if council.consensus is None:
            return _terminal(
                "protocol_failed",
                rounds,
                current,
                remaining,
                "prose_rewrite_supervisor_consensus_missing",
            )
        if council.consensus["scene_verdict"] == "pass":
            return _terminal("semantic_accepted", rounds, current, remaining, None)
        if round_index == PROSE_REWRITER_MAX_CALLS_PER_SCENE:
            return _terminal("semantic_rejected", rounds, current, remaining, None)
        if remaining < 2:
            return _terminal(
                "protocol_failed",
                rounds,
                current,
                remaining,
                "prose_rewrite_supervisor_call_budget_exhausted",
            )
        rewrite_execution = execute_prose_rewriter(
            rewriter_provider,
            scene_plan=scene_plan,
            narrative_ir=narrative_ir,
            profile=profile,
            checklist=checklist_json,
            previous_scene_render=previous_scene_render,
            current_render=current,
            consensus=council.consensus,
            judge_reports=council.judge_reports,
            model_id=model_id,
            api_key=api_key,
            remaining_scene_call_budget=remaining,
        )
        remaining -= _rewrite_call_count(rewrite_execution)
        if rewrite_execution.status != "completed" or rewrite_execution.render is None:
            terminal = _terminal(
                rewrite_execution.status,
                rounds,
                current,
                remaining,
                rewrite_execution.error_code,
            )
            return replace(
                terminal,
                model_call_count=(
                    terminal.model_call_count + _rewrite_call_count(rewrite_execution)
                ),
            )
        current = rewrite_execution.render
    raise AssertionError("bounded prose rewrite loop exceeded")


def _council_call_count(execution: ProseCouncilExecution) -> int:
    return len(execution.calls) + (1 if execution.failed_call is not None else 0)


def _rewrite_call_count(execution: ProseRewriterExecution) -> int:
    return int(execution.call is not None or execution.failed_call is not None)


def _terminal(
    status: str,
    rounds: list[ProseRewriteRoundExecution] | tuple[ProseRewriteRoundExecution, ...],
    final_render: dict[str, Any] | None,
    remaining: int,
    error_code: str | None,
) -> ProseRewriteSupervisorExecution:
    rounds_tuple = tuple(rounds)
    rewrite_count = sum(item.rewrite is not None for item in rounds_tuple)
    model_call_count = sum(_council_call_count(item.council) for item in rounds_tuple) + sum(
        _rewrite_call_count(item.rewrite)
        for item in rounds_tuple
        if item.rewrite is not None
    )
    return ProseRewriteSupervisorExecution(
        status=status,  # type: ignore[arg-type]
        rounds=rounds_tuple,
        final_render=final_render,
        rewrite_count=rewrite_count,
        model_call_count=model_call_count,
        remaining_scene_call_budget=max(0, remaining),
        error_code=error_code,
    )


__all__ = [
    "PROSE_REWRITE_SUPERVISOR_VERSION",
    "ProseRewriteRoundExecution",
    "ProseRewriteSupervisorExecution",
    "execute_bounded_prose_rewrite",
]
