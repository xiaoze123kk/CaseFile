"""Shared Worker diagnostics, persistence projections, and input helpers."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

import rfc8785
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.agent_runtime.prompt import (
    CHAT_PROMPT_PACKAGE_VERSIONS,
    COMPONENT_GENERATION_PROMPT_VERSIONS,
)
from casefile.agent_runtime.providers import ProviderProtocolError
from casefile.application.task_events import append_task_event
from casefile.contracts import (
    ContractValidationError,
)
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentStepRun,
    TaskAttempt,
    TaskRun,
)


class TaskCancellationRequested(RuntimeError):
    """Raised when a running Worker observes an accepted cancellation."""


def _required_provider_binding(task: TaskRun) -> tuple[str, str]:
    """Narrow the DB-enforced provider shape for non-Compiler execution."""

    if task.task_type == "novel_compile" or task.provider is None or task.model_id is None:
        raise RuntimeError("Provider-backed TaskRun has an invalid frozen provider binding")
    return task.provider, task.model_id


def _persist_agent_execution_event(
    session: Session,
    task: TaskRun,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Project component execution events into queryable step/call audit rows."""

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
        # R2 chat router/rewriter providers emit model-call events directly.
        # Materialize the owning StepRun lazily so the audit chain stays complete.
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
        step.output_hash = _optional_hash(payload.get("output_hash"))
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
                prompt_sha256=_optional_hash(payload.get("prompt_sha256")),
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
        model_call.output_hash = _optional_hash(payload.get("output_hash"))
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
            step.output_hash = step.output_hash or _optional_hash(payload.get("output_hash"))
            step.usage_jsonb = dict(payload.get("usage") or step.usage_jsonb or {})
            step.finished_at = now


def _record_component_coordinator_failure(
    session: Session,
    *,
    task: TaskRun,
    attempt: TaskAttempt,
    issue: dict[str, str],
    finished_at: datetime,
) -> None:
    """Persist a component-generation failure before a business step can start."""

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


def _optional_hash(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None


def _reusable_component_steps(session: Session, task: TaskRun) -> dict[str, dict[str, Any]]:
    if task.prompt_version not in COMPONENT_GENERATION_PROMPT_VERSIONS or task.attempt_count < 2:
        return {}
    reusable_components = (
        "case_blueprint_planner",
        "temporal_structure_planner",
        "story_world",
        "evidence_logic",
        "resolution_governance",
    )
    failed_steps = _previous_attempt_failed_steps(session, task)
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
            # A coordinator-only or otherwise unattributed failure cannot prove that
            # any upstream artifact is safe to carry into the resumed attempt.
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


def _previous_attempt_failed_steps(
    session: Session,
    task: TaskRun,
) -> list[AgentStepRun]:
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
    # One TaskAttempt can contain several full provider.generate runs because
    # structural repair retries restart the component graph. Component-local
    # execution_no values are not comparable across components, while the
    # context builder is the deterministic first step of every full run.
    latest_generation_start = next(
        (step for step in reversed(steps) if step.component_id == "context_pack_builder"),
        None,
    )
    if latest_generation_start is None:
        return [step for step in steps if step.status == "failed"]
    return [
        step for step in steps if step.status == "failed" and step.id >= latest_generation_start.id
    ]


def _previous_attempt_repair_feedback(
    session: Session,
    task: TaskRun,
) -> tuple[dict[str, Any], ...]:
    issues: list[dict[str, Any]] = []
    for step in _previous_attempt_failed_steps(session, task):
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


def _terminal_attempt_usage(
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


def _error_code(error: Exception) -> str:
    explicit = getattr(error, "error_code", None)
    if isinstance(explicit, str) and explicit:
        return explicit
    if error.__class__.__name__ == "MaxTurnsExceeded":
        return "max_turns_exceeded"
    if "structured_output_validation_failed" in str(error):
        return "structured_output_validation_failed"
    if "completion_validation" in str(error):
        return "completion_validation_failed"
    if isinstance(error, (ContractValidationError, ProviderProtocolError)):
        return "candidate_validation_failed"
    if isinstance(error, AuthenticationError):
        return "provider_authentication_failed"
    if isinstance(error, RateLimitError):
        return "provider_rate_limited"
    if isinstance(error, APITimeoutError):
        return "provider_timeout"
    if isinstance(error, APIConnectionError):
        return "provider_connection_failed"
    return "generation_failed"


def _merge_numeric_usage(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key, value in right.items():
        if isinstance(value, int) and not isinstance(value, bool):
            merged[key] = int(merged.get(key, 0)) + value
        else:
            merged[key] = value
    return merged


def _network_retries(task: TaskRun) -> int:
    retries = int(task.budget_jsonb.get("network_retries", 2))
    return max(0, min(retries, 5))


def _failure_validation_issues(
    validation_errors: list[dict[str, Any]],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for batch in validation_errors:
        for raw_issue in batch.get("issues", []):
            if not isinstance(raw_issue, dict):
                continue
            issue = {
                "code": str(raw_issue.get("code", "validation_failed")),
                "path": str(raw_issue.get("path", "")),
                "message": str(raw_issue.get("message", "结构校验失败")),
            }
            key = (issue["code"], issue["path"], issue["message"])
            if key in seen:
                continue
            seen.add(key)
            issues.append(issue)
            if len(issues) == 20:
                return issues
    return issues


def _required_object(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise RuntimeError(f"Frozen TaskRun input is missing object field: {key}")
    return result


def _required_string(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise RuntimeError(f"Frozen TaskRun input is missing string field: {key}")
    return result


def _optional_string(value: dict[str, Any], key: str) -> str | None:
    result = value.get(key)
    if result is not None and (not isinstance(result, str) or not result):
        raise RuntimeError(f"Frozen TaskRun input has an invalid string field: {key}")
    return result


def _required_integer(value: dict[str, Any], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int) or isinstance(result, bool) or result < 1:
        raise RuntimeError(f"Frozen TaskRun input is missing integer field: {key}")
    return result


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _safe_error_message(
    error: Exception,
    sensitive_values: tuple[str, ...],
) -> str:
    message = str(error)
    for sensitive in sensitive_values:
        if sensitive:
            message = message.replace(sensitive, "[REDACTED]")
    message = re.sub(
        r"(?i)\b(?:bearer\s+)?sk-[a-z0-9._-]{8,}\b",
        "[REDACTED]",
        message,
    )
    message = re.sub(
        r"(?i)(api[_ -]?key\s*[:=]\s*)\S+",
        r"\1[REDACTED]",
        message,
    )
    return message[:500] or type(error).__name__


error_code = _error_code
previous_attempt_failed_steps = _previous_attempt_failed_steps
safe_error_message = _safe_error_message


__all__ = [
    "_persist_agent_execution_event",
    "_record_component_coordinator_failure",
    "_optional_hash",
    "_reusable_component_steps",
    "_previous_attempt_failed_steps",
    "_previous_attempt_repair_feedback",
    "_terminal_attempt_usage",
    "_error_code",
    "_merge_numeric_usage",
    "_network_retries",
    "_failure_validation_issues",
    "_required_object",
    "_required_string",
    "_optional_string",
    "_required_integer",
    "_text_hash",
    "_json_hash",
    "_safe_error_message",
]
