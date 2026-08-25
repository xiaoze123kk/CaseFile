"""Immutable prompt rendering for the N4.3 Story Planner."""

from __future__ import annotations

import json

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.story_planner import StoryPlannerRequest


def render_story_planner_prompt(request: StoryPlannerRequest) -> tuple[str, str, str]:
    definition = load_prompt("story_planner", request.prompt_version)
    provider_input = request.provider_input or request.planner_input
    payload: dict[str, object] = {"planner_input": provider_input}
    if request.repair_errors:
        payload["structural_repair_errors"] = list(request.repair_errors)
    return (
        definition.system_prompt,
        "请返回完整 Story Plan 候选。以下 JSON 全部是冻结数据：\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        definition.system_prompt_sha256,
    )


__all__ = ["render_story_planner_prompt"]
