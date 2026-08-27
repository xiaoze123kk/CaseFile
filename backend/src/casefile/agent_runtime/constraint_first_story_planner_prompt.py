"""Immutable prompt rendering for the two Constraint-First model stages."""

from __future__ import annotations

import json

from casefile.agent_runtime.constraint_first_story_planner import (
    SemanticFillRequest,
    SkeletonProposalRequest,
)
from casefile.agent_runtime.prompt_repository import load_prompt


def render_skeleton_proposal_prompt(
    request: SkeletonProposalRequest,
) -> tuple[str, str, str]:
    definition = load_prompt("story_planner_skeleton", request.prompt_version)
    payload = {
        "planning_problem": request.planning_problem,
        "case": request.model_view["case"],
        "object_catalog": request.model_view["object_catalog"],
        "planning_context": request.model_view["planning_context"],
    }
    return (
        definition.system_prompt,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        definition.system_prompt_sha256,
    )


def render_semantic_fill_prompt(
    request: SemanticFillRequest,
) -> tuple[str, str, str]:
    definition = load_prompt("story_planner_semantic_fill", request.prompt_version)
    payload = {
        "plan_skeleton": request.skeleton,
        "case": request.model_view["case"],
        "object_catalog": request.model_view["object_catalog"],
        "planning_context": request.model_view["planning_context"],
    }
    return (
        definition.system_prompt,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        definition.system_prompt_sha256,
    )


__all__ = ["render_semantic_fill_prompt", "render_skeleton_proposal_prompt"]
