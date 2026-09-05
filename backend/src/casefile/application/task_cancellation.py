"""Shared state transition for cooperatively cancelled TaskRuns."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from casefile.data_postgres.models import AgentMessage, TaskAttempt, TaskRun

TERMINAL_TASK_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
CANCELLED_CHAT_MESSAGE = "任务已由你取消，Current Draft 未被修改。"


def finalize_task_cancellation(
    session: Session,
    task: TaskRun,
    *,
    now: datetime,
    attempt: TaskAttempt | None = None,
    usage: dict[str, Any] | None = None,
    validation_errors: list[dict[str, Any]] | None = None,
) -> None:
    """Move one queued/running cancellation to its durable terminal shape."""

    if task.task_type == "novel_compile" and task.input_jsonb.get("prose_renderer_shadow"):
        from casefile.application.compiler.prose_projection import finalize_prose_cancellation

        finalize_prose_cancellation(session, task, attempt, now)

    if attempt is not None:
        attempt.status = "cancelled"
        if validation_errors is not None:
            attempt.validation_errors_jsonb = validation_errors
        if usage is not None:
            attempt.usage_jsonb = usage
        attempt.finished_at = now

    task.status = "cancelled"
    task.stage = "cancelled"
    if usage is not None:
        task.usage_jsonb = usage
    if task.cancel_requested_at is None:
        task.cancel_requested_at = now
    task.completed_at = now
    task.leased_by = None
    task.lease_expires_at = None

    if task.task_type != "casefile_chat" or task.output_message_id is None:
        return
    output_message = session.get(AgentMessage, task.output_message_id)
    if output_message is not None and output_message.status == "pending":
        output_message.status = "failed"
        output_message.content_text = CANCELLED_CHAT_MESSAGE


__all__ = [
    "CANCELLED_CHAT_MESSAGE",
    "TERMINAL_TASK_STATUSES",
    "finalize_task_cancellation",
]
