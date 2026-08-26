"""Immutable prompt rendering for the N4.3 Story Planner."""

from __future__ import annotations

import json

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.story_planner import (
    StoryPlannerPatchRequest,
    StoryPlannerRequest,
)


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


def render_story_planner_patch_prompt(
    request: StoryPlannerPatchRequest,
) -> tuple[str, str, str]:
    definition = load_prompt("story_planner", request.prompt_version)
    payload = {
        "candidate": request.candidate,
        "structural_errors": list(request.structural_errors),
        "previous_patch_errors": list(request.previous_patch_errors),
        "expected_scene_ids": list(request.expected_scene_ids),
    }
    instructions = (
        definition.system_prompt
        + "\n\n当前调用只允许返回 compiler.story-plan-structural-patch.v1。"
        "每个 patch 的 op 必须是 replace_scene_purpose；scene_id 必须来自 expected_scene_ids；"
        "purpose 必须使用 ScenePurpose 枚举。不得返回完整 Story Plan 或修改其他字段。"
    )
    return (
        instructions,
        "请只返回最小结构补丁：\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        definition.system_prompt_sha256,
    )


__all__ = ["render_story_planner_patch_prompt", "render_story_planner_prompt"]
