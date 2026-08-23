"""Render the immutable closure-repair Prompt Package."""

from __future__ import annotations

from casefile.agent_runtime.closure_repair import (
    CLOSURE_REPAIR_AGENT_VERSION,
    CLOSURE_REPAIR_COMPONENT_ID,
    CLOSURE_REPAIR_TOOLSET_VERSION,
    ClosureRepairPromptInputV1,
    ClosureRepairRequest,
)
from casefile.agent_runtime.prompt_package import RenderedPrompt, render_prompt_package
from casefile.agent_runtime.prompt_repository import load_prompt


def render_closure_repair_prompt(request: ClosureRepairRequest) -> RenderedPrompt:
    definition = load_prompt("closure_repair", request.prompt_version)
    if definition.package is None:
        raise ValueError("closure_repair_prompt_package_required")
    return render_prompt_package(
        definition.package,
        CLOSURE_REPAIR_COMPONENT_ID,
        ClosureRepairPromptInputV1(
            context=request.context,
            round_no=request.round_no,
        ),
        agent_version=CLOSURE_REPAIR_AGENT_VERSION,
        toolset_version=CLOSURE_REPAIR_TOOLSET_VERSION,
    )


__all__ = ["render_closure_repair_prompt"]
