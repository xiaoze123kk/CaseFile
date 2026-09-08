"""Render the immutable General Mutation Planner prompt package."""

from __future__ import annotations

import json

from casefile.agent_runtime.general_mutation import (
    GENERAL_MUTATION_COMPONENT_ID,
    GeneralMutationPlannerRequest,
    GeneralMutationPromptInput,
    GeneralMutationPromptInputV2,
    MutationPlanV1,
    MutationPlanV2,
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
        _prompt_input(request),
        agent_version=definition.package.runtime_agent_version,
        toolset_version=definition.package.runtime_toolset_version,
    )


def general_mutation_input(request: GeneralMutationPlannerRequest) -> str:
    return json.dumps(
        _prompt_input(request).model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _prompt_input(request: GeneralMutationPlannerRequest) -> GeneralMutationPromptInput:
    common = GeneralMutationPromptInput(
        message=request.message,
        casefile=request.casefile,
        editable_fields_by_collection=request.editable_fields_by_collection,
    )
    if request.prompt_version != "general-mutation-planner-v8":
        return common
    return GeneralMutationPromptInputV2(
        **common.model_dump(),
        thread_history=list(request.thread_history)[-20:],
        focus=request.focus,
        validation_issues=list(request.validation_issues),
        canonical_query=request.canonical_query,
    )


def general_mutation_output_type(
    rendered: RenderedPrompt,
) -> type[MutationPlanV1] | type[MutationPlanV2]:
    output_types: dict[str, type[MutationPlanV1] | type[MutationPlanV2]] = {
        "general-mutation-plan-v1": MutationPlanV1,
        "general-mutation-plan-v2": MutationPlanV2,
    }
    try:
        return output_types[rendered.output_schema_id]
    except KeyError as error:
        raise ValueError("general_mutation_output_schema_unknown") from error


__all__ = [
    "general_mutation_input",
    "general_mutation_output_type",
    "render_general_mutation_prompt",
]
