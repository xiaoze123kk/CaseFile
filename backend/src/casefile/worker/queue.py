"""PostgreSQL lease claiming for the stable Worker runtime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from casefile.application.task_cancellation import finalize_task_cancellation
from casefile.application.task_events import append_task_event
from casefile.data_postgres.models import (
    TaskAttempt,
    TaskRun,
)


class TaskQueue:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        worker_id: str,
        lease_seconds: int,
    ) -> None:
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def _claim_next(self) -> tuple[int, int] | Literal["cancelled"] | None:
        return self._claim(task_run_id=None)

    def _claim_specific(
        self, task_run_id: int
    ) -> tuple[int, int] | Literal["cancelled"] | None:
        """Claim one known TaskRun while preserving the normal lease transaction."""

        return self._claim(task_run_id=task_run_id)

    def _claim(
        self, *, task_run_id: int | None
    ) -> tuple[int, int] | Literal["cancelled"] | None:
        now = datetime.now(UTC)
        with self.session_factory() as session, session.begin():
            statement = (
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
            if task_run_id is not None:
                statement = statement.where(TaskRun.id == task_run_id)
            task = session.scalar(statement)
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
            task.leased_by = self.worker_id
            task.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
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
                {"attempt_no": attempt.attempt_no, "worker_id": self.worker_id},
            )
            return task.id, attempt.id


__all__ = ["TaskQueue"]
