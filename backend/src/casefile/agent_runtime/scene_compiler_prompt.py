"""Immutable prompt rendering for bounded N4.4 Scene Semantic Fill."""

from __future__ import annotations

import json

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.scene_compiler import SceneFillBatchRequest


def render_scene_fill_prompt(
    request: SceneFillBatchRequest,
) -> tuple[str, str, str]:
    definition = load_prompt("scene_compiler_semantic_fill", request.prompt_version)
    payload = {
        "batch": request.batch_view,
        "inbound_state_hash": request.inbound_state_hash,
        "inbound_state": request.inbound_state,
    }
    return (
        definition.system_prompt,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        definition.system_prompt_sha256,
    )


__all__ = ["render_scene_fill_prompt"]
