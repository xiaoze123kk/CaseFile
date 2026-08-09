"""Append-only TaskRun event helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.data_postgres.models import TaskEvent, TaskRun


def append_task_event(
    session: Session,
    task: TaskRun,
    event_type: str,
    stage: str,
    payload: dict[str, Any],
) -> TaskEvent:
    """Append one replayable event; callers must own the surrounding transaction."""

    sequence = int(
        session.scalar(
            select(func.coalesce(func.max(TaskEvent.sequence_no), 0) + 1).where(
                TaskEvent.task_run_id == task.id
            )
        )
        or 1
    )
    event = TaskEvent(
        project_id=task.project_id,
        task_run_id=task.id,
        sequence_no=sequence,
        event_type=event_type,
        stage=stage,
        payload_jsonb=payload,
    )
    session.add(event)
    session.flush()
    return event


__all__ = ["append_task_event"]
