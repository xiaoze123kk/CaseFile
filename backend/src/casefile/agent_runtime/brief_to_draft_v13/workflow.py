"""v13 adapter for the temporal-structure generation graph."""

from __future__ import annotations

from casefile.agent_runtime.brief_to_draft_v8.workflow import ComponentCall, run_v8_generation
from casefile.agent_runtime.models import GenerationRequest, GenerationResult


async def run_v13_generation(
    request: GenerationRequest,
    *,
    call_component: ComponentCall,
) -> GenerationResult:
    if request.prompt_version != "brief-to-draft-v13":
        raise ValueError("run_v13_generation requires brief-to-draft-v13")
    return await run_v8_generation(request, call_component=call_component)


__all__ = ["run_v13_generation"]
