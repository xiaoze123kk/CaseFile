"""Worker event projections and terminal usage persistence."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.agent_runtime.prompt import (
    CHAT_PROMPT_PACKAGE_VERSIONS,
    COMPONENT_GENERATION_PROMPT_VERSIONS,
)
from casefile.application.task_events import append_task_event
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentStepRun,
    TaskAttempt,
    TaskRun,
)


def persist_agent_execution_event(
    session: Session,
    task: TaskRun,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    if (
        task.prompt_version not in COMPONENT_GENERATION_PROMPT_VERSIONS
        and task.prompt_version not in CHAT_PROMPT_PACKAGE_VERSIONS
    ):
        return
    component_id = payload.get("component_id")
    if not isinstance(component_id, str) or not component_id:
        return
    attempt = session.scalar(
        select(TaskAttempt)
        .where(TaskAttempt.task_run_id == task.id, TaskAttempt.status == "running")
        .order_by(TaskAttempt.attempt_no.desc())
    )
    if attempt is None:
        return
    now = datetime.now(UTC)
    if event_type == "agent.step.started":
        execution_no = int(
            session.scalar(
                select(func.coalesce(func.max(AgentStepRun.execution_no), 0) + 1).where(
                    AgentStepRun.task_attempt_id == attempt.id,
                    AgentStepRun.component_id == component_id,
                )
            )
            or 1
        )
        session.add(
            AgentStepRun(
                project_id=task.project_id,
                task_run_id=task.id,
                task_attempt_id=attempt.id,
                component_id=component_id,
                parent_component_id=(
                    "domain_drafters"
                    if component_id
                    in {"story_world", "evidence_logic", "evidence_matrix", "resolution_governance"}
                    else None
                ),
                execution_no=execution_no,
                status="running",
                input_hash=str(payload.get("input_hash") or task.input_hash),
                upstream_hashes_jsonb=dict(payload.get("upstream_hashes") or {}),
                ir_schema_id=str(payload.get("schema_id") or "unknown"),
                component_version=str(payload.get("component_version") or task.prompt_version),
            )
        )
        session.flush()
        return
    step = session.scalar(
        select(AgentStepRun)
        .where(
            AgentStepRun.task_attempt_id == attempt.id,
            AgentStepRun.component_id == component_id,
            AgentStepRun.status == "running",
        )
        .order_by(AgentStepRun.execution_no.desc())
        .with_for_update(of=AgentStepRun)
    )
    if step is None and event_type == "agent.model_call.started":
        execution_no = int(
            session.scalar(
                select(func.coalesce(func.max(AgentStepRun.execution_no), 0) + 1).where(
                    AgentStepRun.task_attempt_id == attempt.id,
                    AgentStepRun.component_id == component_id,
                )
            )
            or 1
        )
        step = AgentStepRun(
            project_id=task.project_id,
            task_run_id=task.id,
            task_attempt_id=attempt.id,
            component_id=component_id,
            parent_component_id=None,
            execution_no=execution_no,
            status="running",
            input_hash=str(payload.get("input_hash") or task.input_hash),
            upstream_hashes_jsonb=dict(payload.get("upstream_hashes") or {}),
            ir_schema_id=str(payload.get("schema_id") or "unknown"),
            component_version=str(payload.get("component_version") or task.prompt_version),
        )
        session.add(step)
        session.flush()
    if step is None:
        return
    if event_type in {"agent.step.completed", "agent.step.failed", "agent.step.reused"}:
        step.status = {
            "agent.step.completed": "succeeded",
            "agent.step.failed": "failed",
            "agent.step.reused": "reused",
        }[event_type]
        step.output_hash = optional_hash(payload.get("output_hash"))
        artifact = payload.get("_artifact")
        if isinstance(artifact, (dict, list)):
            step.output_jsonb = artifact
        step.diagnostic_jsonb = {
            "failure_layer": payload.get("failure_layer"),
            "schema_id": payload.get("schema_id"),
            "error_code": payload.get("error_code"),
            "issues": payload.get("issues", []),
            "recoverable": payload.get("recoverable", False),
        }
        step.usage_jsonb = dict(payload.get("usage") or {})
        step.finished_at = now
        resumed_from = payload.get("resumed_from_step_run_id")
        if isinstance(resumed_from, int):
            step.resumed_from_step_run_id = resumed_from
        return
    if event_type == "agent.model_call.started":
        call_no = int(payload.get("attempt_no") or 1)
        session.add(
            AgentModelCall(
                project_id=task.project_id,
                task_run_id=task.id,
                task_attempt_id=attempt.id,
                agent_step_run_id=step.id,
                call_no=call_no,
                status="running",
                provider=task.provider,
                model_id=task.model_id,
                output_protocol=str(payload.get("protocol") or "unknown"),
                prompt_version=step.component_version,
                prompt_component_id=component_id,
                prompt_sha256=optional_hash(payload.get("prompt_sha256")),
                target_schema_id=str(payload.get("schema_id") or step.ir_schema_id),
                input_hash=step.input_hash,
            )
        )
        session.flush()
        return
    if event_type in {"agent.model_call.completed", "agent.model_call.failed"}:
        call_no = int(payload.get("attempt_no") or 1)
        model_call = session.scalar(
            select(AgentModelCall)
            .where(
                AgentModelCall.agent_step_run_id == step.id,
                AgentModelCall.call_no == call_no,
                AgentModelCall.status == "running",
            )
            .with_for_update(of=AgentModelCall)
        )
        if model_call is None:
            return
        model_call.status = "succeeded" if event_type == "agent.model_call.completed" else "failed"
        model_call.output_hash = optional_hash(payload.get("output_hash"))
        raw_size = payload.get("output_size_bytes")
        model_call.output_size_bytes = raw_size if isinstance(raw_size, int) else None
        model_call.raw_output_text = (
            payload.get("_raw_output") if isinstance(payload.get("_raw_output"), str) else None
        )
        model_call.raw_output_truncated = bool(payload.get("raw_output_truncated"))
        model_call.issues_jsonb = list(payload.get("issues") or [])
        model_call.usage_jsonb = dict(payload.get("usage") or {})
        model_call.error_code = (
            str(payload.get("error_code") or "model_call_failed")
            if event_type == "agent.model_call.failed"
            else None
        )
        model_call.finished_at = now
        if (
            step.status == "running"
            and event_type == "agent.model_call.completed"
            and component_id in {"intent_router", "query_rewriter"}
        ):
            step.status = "succeeded"
            step.output_hash = step.output_hash or optional_hash(payload.get("output_hash"))
            step.usage_jsonb = dict(payload.get("usage") or step.usage_jsonb or {})
            step.finished_at = now


def record_component_coordinator_failure(
    session: Session,
    *,
    task: TaskRun,
    attempt: TaskAttempt,
    issue: dict[str, str],
    finished_at: datetime,
) -> None:
    execution_no = int(
        session.scalar(
            select(func.coalesce(func.max(AgentStepRun.execution_no), 0) + 1).where(
                AgentStepRun.task_attempt_id == attempt.id,
                AgentStepRun.component_id == "run_coordinator",
            )
        )
        or 1
    )
    session.add(
        AgentStepRun(
            project_id=task.project_id,
            task_run_id=task.id,
            task_attempt_id=attempt.id,
            component_id="run_coordinator",
            parent_component_id=None,
            execution_no=execution_no,
            status="failed",
            input_hash=task.input_hash,
            upstream_hashes_jsonb={},
            ir_schema_id=issue["schema_id"],
            component_version=task.prompt_version,
            diagnostic_jsonb={
                "failure_layer": issue["failure_layer"],
                "schema_id": issue["schema_id"],
                "error_code": issue["code"],
                "issues": [issue],
                "recoverable": False,
            },
            usage_jsonb={},
            finished_at=finished_at,
        )
    )
    append_task_event(
        session,
        task,
        "agent.step.failed",
        "preflight",
        {
            "component_id": "run_coordinator",
            "failure_layer": issue["failure_layer"],
            "schema_id": issue["schema_id"],
            "error_code": issue["code"],
            "issues": [issue],
            "recoverable": False,
        },
    )


def terminal_attempt_usage(
    session: Session,
    attempt_id: int,
    usage: dict[str, Any],
) -> dict[str, Any]:
    if usage:
        return usage
    rows = list(
        session.scalars(
            select(AgentModelCall)
            .where(AgentModelCall.task_attempt_id == attempt_id)
            .order_by(AgentModelCall.agent_step_run_id, AgentModelCall.call_no)
        )
    )
    rows_by_step: dict[int, list[AgentModelCall]] = {}
    for row in rows:
        rows_by_step.setdefault(row.agent_step_run_id, []).append(row)
    usage_keys = (
        "requests",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "reasoning_tokens",
    )
    snapshots: list[dict[str, Any]] = []
    for step_rows in rows_by_step.values():
        rows_with_usage = [
            row for row in step_rows if any(key in row.usage_jsonb for key in usage_keys)
        ]
        if rows_with_usage:
            snapshots.append(max(rows_with_usage, key=lambda row: row.call_no).usage_jsonb)
    if not snapshots:
        return {}
    merged: dict[str, Any] = {"requests": 0}
    for snapshot in snapshots:
        requests = snapshot.get("requests")
        merged["requests"] += (
            requests if isinstance(requests, int) and not isinstance(requests, bool) else 1
        )
        for key in usage_keys[1:]:
            value = snapshot.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                merged[key] = int(merged.get(key, 0)) + value
    return merged


def optional_hash(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None


__all__ = [
    "optional_hash",
    "persist_agent_execution_event",
    "record_component_coordinator_failure",
    "terminal_attempt_usage",
]
