"""Delivery-based compiler metrics; successful TaskRun alone is not novel delivery."""

from __future__ import annotations

from collections import Counter
from typing import Any

from casefile.data_postgres.models import AgentModelCall


def compiler_stability(
    *,
    task_status: str,
    attempt_count: int,
    prose_requested: bool,
    shadow_status: str,
    schema_ids: set[str],
    calls: list[AgentModelCall],
) -> dict[str, Any]:
    novel_ready = (
        task_status == "succeeded"
        and shadow_status == "succeeded"
        and "compiler.novel-candidate.v1" in schema_ids
    )
    plan_ready = task_status == "succeeded" and bool(
        schema_ids & {"compiler.scene-plan.v1", "compiler.scene-plan.v2"}
    )
    repairs = [
        call
        for call in calls
        if (call.prompt_component_id.startswith("scene_semantic_fill.") and call.call_no >= 10000)
        or "rewrite" in call.prompt_component_id
    ]
    failures: Counter[str] = Counter()
    for call in calls:
        if call.status != "failed":
            continue
        component = call.prompt_component_id
        stage = (
            "场景衔接审核"
            if component == "prose_continuity"
            else "正文评审"
            if "judge" in component or "arbiter" in component
            else "正文生成"
            if component.startswith("prose_")
            else "场景细化"
            if component.startswith("scene_semantic_fill")
            else "结构规划"
        )
        failures[stage] += 1
    delivered = novel_ready if prose_requested else plan_ready
    terminal = task_status in {"succeeded", "failed", "cancelled"}
    return {
        "novel_ready": novel_ready,
        "outcome": (
            "novel_ready"
            if novel_ready
            else "plan_ready"
            if delivered
            else "cancelled"
            if task_status == "cancelled"
            else "failed"
            if terminal
            else "running"
        ),
        "first_pass_success": delivered and attempt_count == 1 and not failures and not repairs,
        "repair_attempts": len(repairs),
        "repair_successes": sum(call.status == "succeeded" for call in repairs),
        "model_calls": len(calls),
        "failure_stages": dict(failures),
    }
