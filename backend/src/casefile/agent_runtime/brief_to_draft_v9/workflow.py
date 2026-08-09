"""Prompt Package adapter for the stable brief-to-draft execution graph."""

from __future__ import annotations

from casefile.agent_runtime.brief_to_draft_v8.workflow import ComponentCall, run_v8_generation
from casefile.agent_runtime.models import GenerationRequest, GenerationResult


async def run_v9_generation(
    request: GenerationRequest,
    *,
    call_component: ComponentCall,
) -> GenerationResult:
    if request.prompt_version != "brief-to-draft-v9":
        raise ValueError("run_v9_generation requires brief-to-draft-v9")
    return await run_v8_generation(request, call_component=call_component)


__all__ = ["run_v9_generation"]
