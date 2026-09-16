"""Frozen public inputs for cost smoke; historical suite gates are not bypassed."""

from __future__ import annotations

import json
from typing import Any

from casefile.agent_runtime.model_policy import DEEPSEEK_MODEL_ID
from casefile.agent_runtime.prose_rewriter import build_prose_rewriter_request
from casefile.agent_runtime.prose_writer import build_prose_writer_request
from casefile.benchmark.prose_rewrite_eval import (
    DEFAULT_SUITE,
    load_prose_rewrite_development_task,
)
from casefile.benchmark.prose_writer_eval import load_prose_writer_dev_suite


def smoke_tasks() -> list[dict[str, Any]]:
    writers = load_prose_writer_dev_suite()["tasks"]
    selected = []
    for task_id, kind in [
        ("b1_01_beat_realization_basic", "ordinary"),
        ("b1_02_beat_realization_implicit_friendly", "continuity"),
    ]:
        task = next(t for t in writers if t["descriptor"]["task_id"] == task_id)
        selected.append({**task, "role": "writer", "kind": kind})
    historical = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    descriptor = next(
        t
        for t in historical["tasks"]
        if t["defect_family"] == "missing_required_beat" and t["variant"] == "basic"
    )
    selected.append(
        {
            **load_prose_rewrite_development_task(descriptor),
            "role": "rewriter",
            "kind": "local_revision",
            "historical_suite_prompt": historical["rewriter_prompt_version"],
        }
    )
    return selected


def smoke_request(task: dict[str, Any], version: str, api_key: str) -> Any:
    asset = task["asset"]
    kwargs = dict(
        scene_plan=task["scene_plan"],
        narrative_ir=task["narrative_ir"],
        profile=asset["profile"],
        checklist=task["checklist"],
        previous_scene_render=asset.get("previous_scene_render"),
        model_id=DEEPSEEK_MODEL_ID,
        api_key=api_key,
        remaining_scene_call_budget=22,
        prompt_version=version,
    )
    if task["role"] == "writer":
        return build_prose_writer_request(**kwargs)
    return build_prose_rewriter_request(
        **kwargs,
        current_render=asset["initial_render"],
        consensus=asset["initial_consensus"],
        judge_reports=(asset["initial_judge_report"],),
        revision_decision={
            "mode": "local_revision",
            "instruction": (
                "修复 repair_findings 指出的缺失，保留有效内容与衔接，返回完整替代正文。"
            ),
        },
    )
