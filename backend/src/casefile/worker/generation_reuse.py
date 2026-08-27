"""Brief-to-Draft attempt recovery and component reuse policy adapters."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.agent_runtime.prompt import COMPONENT_GENERATION_PROMPT_VERSIONS
from casefile.data_postgres.models import AgentStepRun, TaskAttempt, TaskRun


def reusable_component_steps(session: Session, task: TaskRun) -> dict[str, dict[str, Any]]:
    if task.prompt_version not in COMPONENT_GENERATION_PROMPT_VERSIONS or task.attempt_count < 2:
        return {}
    reusable_components = (
        "case_blueprint_planner",
        "temporal_structure_planner",
        "story_world",
        "evidence_logic",
        "resolution_governance",
    )
    failed_steps = previous_attempt_failed_steps(session, task)
    invalidated: set[str] = set()
    if failed_steps:
        for step in failed_steps:
            if step.component_id in reusable_components:
                invalidated.add(step.component_id)
            diagnostics = step.diagnostic_jsonb
            raw_issues = diagnostics.get("issues") if isinstance(diagnostics, dict) else None
            if not isinstance(raw_issues, list):
                continue
            for issue in raw_issues:
                if not isinstance(issue, dict):
                    continue
                component_id = issue.get("component_id")
                if component_id in reusable_components:
                    invalidated.add(str(component_id))
        if not invalidated:
            return {}
        if "case_blueprint_planner" in invalidated:
            invalidated = set(reusable_components)
        elif "temporal_structure_planner" in invalidated:
            invalidated.add("story_world")

    rows = session.scalars(
        select(AgentStepRun)
        .where(
            AgentStepRun.task_run_id == task.id,
            AgentStepRun.status.in_(("succeeded", "reused")),
            AgentStepRun.component_version == task.prompt_version,
            AgentStepRun.component_id.in_(reusable_components),
        )
        .order_by(AgentStepRun.id.desc())
    )
    reusable: dict[str, dict[str, Any]] = {}
    for row in rows:
        if (
            row.component_id in reusable
            or row.component_id in invalidated
            or not isinstance(row.output_jsonb, dict)
        ):
            continue
        reusable[row.component_id] = {
            "step_run_id": row.id,
            "input_hash": row.input_hash,
            "output_hash": row.output_hash,
            "schema_id": row.ir_schema_id,
            "output": row.output_jsonb,
        }
    return reusable


def previous_attempt_failed_steps(session: Session, task: TaskRun) -> list[AgentStepRun]:
    if task.attempt_count < 2:
        return []
    previous_attempt = session.scalar(
        select(TaskAttempt).where(
            TaskAttempt.task_run_id == task.id,
            TaskAttempt.attempt_no == task.attempt_count - 1,
            TaskAttempt.status == "failed",
        )
    )
    if previous_attempt is None:
        return []
    steps = list(
        session.scalars(
            select(AgentStepRun)
            .where(AgentStepRun.task_attempt_id == previous_attempt.id)
            .order_by(AgentStepRun.id)
        )
    )
    latest_generation_start = next(
        (step for step in reversed(steps) if step.component_id == "context_pack_builder"),
        None,
    )
    if latest_generation_start is None:
        return [step for step in steps if step.status == "failed"]
    return [
        step for step in steps if step.status == "failed" and step.id >= latest_generation_start.id
    ]


def previous_attempt_repair_feedback(
    session: Session,
    task: TaskRun,
) -> tuple[dict[str, Any], ...]:
    issues: list[dict[str, Any]] = []
    for step in previous_attempt_failed_steps(session, task):
        diagnostics = step.diagnostic_jsonb
        raw_issues = diagnostics.get("issues") if isinstance(diagnostics, dict) else None
        if not isinstance(raw_issues, list):
            continue
        for issue in raw_issues:
            if isinstance(issue, dict):
                issues.append(dict(issue))
                if len(issues) == 50:
                    return ({"issues": issues},)
    return ({"issues": issues},) if issues else ()


__all__ = [
    "previous_attempt_failed_steps",
    "previous_attempt_repair_feedback",
    "reusable_component_steps",
]
