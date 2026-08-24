"""Render the immutable General Mutation Planner prompt package."""

from __future__ import annotations

import json

from casefile.agent_runtime.general_mutation import (
    GENERAL_MUTATION_COMPONENT_ID,
    GeneralMutationPlannerRequest,
    GeneralMutationPromptInput,
    MutationPlanV1,
)
from casefile.agent_runtime.prompt_package import RenderedPrompt, render_prompt_package
from casefile.agent_runtime.prompt_repository import load_prompt


def render_general_mutation_prompt(
    request: GeneralMutationPlannerRequest,
) -> RenderedPrompt:
    definition = load_prompt("general_mutation_planner", request.prompt_version)
    if definition.package is None:
        raise ValueError("general_mutation_prompt_package_required")
    return render_prompt_package(
        definition.package,
        GENERAL_MUTATION_COMPONENT_ID,
        GeneralMutationPromptInput(
            message=request.message,
            casefile=request.casefile,
            editable_fields_by_collection=request.editable_fields_by_collection,
        ),
        agent_version=definition.package.runtime_agent_version,
        toolset_version=definition.package.runtime_toolset_version,
    )


def general_mutation_input(request: GeneralMutationPlannerRequest) -> str:
    return json.dumps(
        {
            "message": request.message,
            "casefile": request.casefile,
            "editable_fields_by_collection": request.editable_fields_by_collection,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def general_mutation_output_type() -> type[MutationPlanV1]:
    return MutationPlanV1


__all__ = [
    "general_mutation_input",
    "general_mutation_output_type",
    "render_general_mutation_prompt",
]
