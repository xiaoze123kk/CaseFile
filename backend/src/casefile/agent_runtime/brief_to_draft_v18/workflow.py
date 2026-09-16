"""v18 adapter for Plan-Execute and Nag reminder execution."""

from __future__ import annotations

from casefile.agent_runtime.brief_to_draft_v8.workflow import ComponentCall, run_v8_generation
from casefile.agent_runtime.models import GenerationRequest, GenerationResult


async def run_v18_generation(
    request: GenerationRequest,
    *,
    call_component: ComponentCall,
) -> GenerationResult:
    if request.prompt_version != "brief-to-draft-v18":
        raise ValueError("run_v18_generation requires brief-to-draft-v18")
    return await run_v8_generation(request, call_component=call_component)


__all__ = ["run_v18_generation"]
