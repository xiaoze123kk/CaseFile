"""PostgreSQL lease claiming for the stable Worker runtime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from casefile.application.task_cancellation import finalize_task_cancellation
from casefile.application.task_events import append_task_event
from casefile.data_postgres.models import (
    TaskAttempt,
    TaskRun,
)
from casefile.worker.executors.chat import _chat_intent_event_payload as _chat_intent_event_payload
from casefile.worker.executors.chat import (
    _chat_rewrite_event_payload as _chat_rewrite_event_payload,
)
from casefile.worker.executors.chat import _resolve_chat_route as _resolve_chat_route
from casefile.worker.support import (
    _previous_attempt_failed_steps as _previous_attempt_failed_steps,
)


class QueueMixin:
    session_factory: sessionmaker[Session]
    config: Any

    def _claim_next(self) -> tuple[int, int] | Literal["cancelled"] | None:
        now = datetime.now(UTC)
        with self.session_factory() as session, session.begin():
            task = session.scalar(
                select(TaskRun)
                .where(
                    or_(
                        TaskRun.status == "queued",
                        (TaskRun.status == "running") & (TaskRun.lease_expires_at < now),
                        (TaskRun.status == "cancelling")
                        & (TaskRun.lease_expires_at.is_(None) | (TaskRun.lease_expires_at < now)),
                    )
                )
                .order_by(TaskRun.created_at, TaskRun.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if task is None:
                return None
            if task.status == "cancelling":
                attempt = session.scalar(
                    select(TaskAttempt)
                    .where(
                        TaskAttempt.task_run_id == task.id,
                        TaskAttempt.status == "running",
                    )
                    .order_by(TaskAttempt.attempt_no.desc())
                    .limit(1)
                    .with_for_update()
                )
                finalize_task_cancellation(
                    session,
                    task,
                    now=now,
                    attempt=attempt,
                )
                append_task_event(
                    session,
                    task,
                    "task.cancelled",
                    "cancelled",
                    {"message": "任务已安全停止。"},
                )
                return "cancelled"
            if task.status == "running":
                previous = session.scalar(
                    select(TaskAttempt)
                    .where(
                        TaskAttempt.task_run_id == task.id,
                        TaskAttempt.status == "running",
                    )
                    .order_by(TaskAttempt.attempt_no.desc())
                    .limit(1)
                    .with_for_update()
                )
                if previous is not None:
                    previous.status = "failed"
                    previous.error_code = "worker_lease_expired"
                    previous.error_details_jsonb = {"restarted": True}
                    previous.finished_at = now
                append_task_event(
                    session,
                    task,
                    "task.recovered",
                    "preparing",
                    {"message": "检测到过期租约，任务将从新 Attempt 重新执行"},
                )
            task.status = "running"
            task.stage = "preparing"
            task.attempt_count += 1
            task.leased_by = self.config.worker_id
            task.lease_expires_at = now + timedelta(seconds=self.config.lease_seconds)
            task.error_code = None
            task.error_details_jsonb = {}
            attempt = TaskAttempt(
                project_id=task.project_id,
                task_run_id=task.id,
                attempt_no=task.attempt_count,
                status="running",
                candidate_jsonb=None,
                validation_errors_jsonb=[],
                usage_jsonb={},
                error_details_jsonb={},
            )
            session.add(attempt)
            session.flush()
            append_task_event(
                session,
                task,
                "task.started",
                "preparing",
                {"attempt_no": attempt.attempt_no, "worker_id": self.config.worker_id},
            )
            return task.id, attempt.id


__all__ = ["QueueMixin"]
