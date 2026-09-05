"""v16 adapter for semantic relationship coverage."""

from __future__ import annotations

from casefile.agent_runtime.brief_to_draft_v8.workflow import ComponentCall, run_v8_generation
from casefile.agent_runtime.models import GenerationRequest, GenerationResult


async def run_v16_generation(
    request: GenerationRequest,
    *,
    call_component: ComponentCall,
) -> GenerationResult:
    if request.prompt_version != "brief-to-draft-v16":
        raise ValueError("run_v16_generation requires brief-to-draft-v16")
    return await run_v8_generation(request, call_component=call_component)


__all__ = ["run_v16_generation"]
