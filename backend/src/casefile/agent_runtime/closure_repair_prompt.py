"""Render the immutable closure-repair Prompt Package."""

from __future__ import annotations

from pydantic import BaseModel

from casefile.agent_runtime.closure_repair import (
    CLOSURE_REPAIR_COMPONENT_ID,
    ClosureRepairPromptInputV1,
    ClosureRepairPromptInputV2,
    ClosureRepairRequest,
)
from casefile.agent_runtime.prompt_package import (
    OUTPUT_SCHEMAS,
    RenderedPrompt,
    render_prompt_package,
)
from casefile.agent_runtime.prompt_repository import load_prompt


def render_closure_repair_prompt(request: ClosureRepairRequest) -> RenderedPrompt:
    definition = load_prompt("closure_repair", request.prompt_version)
    if definition.package is None:
        raise ValueError("closure_repair_prompt_package_required")
    input_type = (
        ClosureRepairPromptInputV1
        if request.prompt_version == "closure-repair-v1"
        else ClosureRepairPromptInputV2
    )
    return render_prompt_package(
        definition.package,
        CLOSURE_REPAIR_COMPONENT_ID,
        input_type(
            context=request.context,
            round_no=request.round_no,
        ),
        agent_version=definition.package.runtime_agent_version,
        toolset_version=definition.package.runtime_toolset_version,
    )


def closure_repair_output_type(rendered: RenderedPrompt) -> type[BaseModel]:
    """Resolve the output model from the immutable package binding."""

    return OUTPUT_SCHEMAS[rendered.output_schema_id]


__all__ = ["closure_repair_output_type", "render_closure_repair_prompt"]
