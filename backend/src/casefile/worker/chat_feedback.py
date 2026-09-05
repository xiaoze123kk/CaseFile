"""Non-interrupting, Attempt-fenced persistence for public Chat previews."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from casefile.agent_runtime.public_language import public_language_rule_ids
from casefile.application.task_events import append_task_event
from casefile.data_postgres.models import TaskAttempt, TaskEvent, TaskRun


class ChatFeedbackWriter:
    def __init__(self, factory: sessionmaker[Session], run_id: int, attempt_id: int):
        self.factory = factory
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.preview_sequence: int | None = None

    def __call__(self, event: str, payload: dict[str, Any]) -> None:
        try:
            self._append(event, payload)
        except SQLAlchemyError:
            # Feedback is best effort; never change Provider retry/cancellation semantics.
            self.preview_sequence = None

    def _append(self, event: str, payload: dict[str, Any]) -> None:
        if event not in {
            "message.preview_started",
            "message.preview_delta",
            "message.preview_invalidated",
        }:
            return
        with self.factory() as session, session.begin():
            task = session.scalar(
                select(TaskRun).where(TaskRun.id == self.run_id).with_for_update()
            )
            attempt = session.get(TaskAttempt, self.attempt_id)
            if (
                task is None
                or attempt is None
                or task.status != "running"
                or attempt.task_run_id != task.id
                or attempt.status != "running"
                or attempt.attempt_no != task.attempt_count
                or task.lease_expires_at is None
                or task.lease_expires_at <= datetime.now(UTC)
            ):
                return
            clean: dict[str, Any] = {}
            if event == "message.preview_delta":
                if self.preview_sequence is None:
                    return
                text = payload.get("text")
                offset = payload.get("offset")
                if not isinstance(text, str) or not text or type(offset) is not int:
                    return
                if public_language_rule_ids(text):
                    return
                previous = list(
                    session.scalars(
                        select(TaskEvent).where(
                            TaskEvent.task_run_id == task.id,
                            TaskEvent.event_type == "message.preview_delta",
                        )
                    )
                )
                if (
                    len(previous) >= 128
                    or sum(
                        len(str(row.payload_jsonb.get("text", "")).encode("utf-8"))
                        for row in previous
                    )
                    + len(text.encode("utf-8"))
                    > 65536
                ):
                    return
                current_length = sum(
                    len(str(row.payload_jsonb.get("text", "")))
                    for row in previous
                    if row.payload_jsonb.get("preview_sequence") == self.preview_sequence
                )
                if offset != current_length:
                    return
                clean = {
                    "text": text,
                    "offset": offset,
                    "preview_sequence": self.preview_sequence,
                    "final": payload.get("final") is True,
                }
            elif event == "message.preview_invalidated":
                clean = {"discard": payload.get("discard") is not False}
            row = append_task_event(session, task, event, "feedback", clean)
            if event == "message.preview_started":
                self.preview_sequence = row.sequence_no
            elif event == "message.preview_invalidated":
                self.preview_sequence = None
