"""Worker terminal state transitions and replayable event persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile.agent_runtime.prompt import (
    COMPONENT_GENERATION_PROMPT_VERSIONS,
)
from casefile.application.task_cancellation import finalize_task_cancellation
from casefile.application.task_events import append_task_event
from casefile.application.workflow_views import task_failure_view
from casefile.data_postgres.models import (
    AgentMessage,
    AgentStepRun,
    TaskAttempt,
    TaskEvent,
    TaskRun,
)
from casefile.worker.executors.chat import _chat_intent_event_payload as _chat_intent_event_payload
from casefile.worker.executors.chat import (
    _chat_rewrite_event_payload as _chat_rewrite_event_payload,
)
from casefile.worker.executors.chat import _resolve_chat_route as _resolve_chat_route
from casefile.worker.support import (
    TaskCancellationRequested,
    _error_code,
    _failure_validation_issues,
    _network_retries,
    _persist_agent_execution_event,
    _record_component_coordinator_failure,
    _safe_error_message,
    _terminal_attempt_usage,
)
from casefile.worker.support import (
    _previous_attempt_failed_steps as _previous_attempt_failed_steps,
)


class TaskFinalizationMixin:
    session_factory: sessionmaker[Session]
    config: Any

    def _cancel(
        self,
        task_run_id: int,
        attempt_id: int,
        *,
        usage: dict[str, Any],
        validation_errors: list[dict[str, Any]],
    ) -> bool:
        with self.session_factory() as session, session.begin():
            task = session.scalar(
                select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
            )
            attempt = session.scalar(
                select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
            )
            if task is None or attempt is None or task.status != "cancelling":
                return False
            if task.leased_by != self.config.worker_id or attempt.status != "running":
                return False
            usage = _terminal_attempt_usage(session, attempt.id, usage)
            now = datetime.now(UTC)
            finalize_task_cancellation(
                session,
                task,
                now=now,
                attempt=attempt,
                usage=usage,
                validation_errors=validation_errors,
            )
            append_task_event(
                session,
                task,
                "task.cancelled",
                "cancelled",
                {
                    "message": "任务已安全停止，Current Draft 未被修改。",
                    "task_type": task.task_type,
                    "usage": usage,
                },
            )
            return True

    def _fail(
        self,
        task_run_id: int,
        attempt_id: int,
        error: Exception,
        *,
        candidate: dict[str, Any] | None,
        usage: dict[str, Any],
        validation_errors: list[dict[str, Any]],
        sensitive_values: tuple[str, ...],
    ) -> None:
        error_code = _error_code(error)
        with self.session_factory() as session, session.begin():
            task = session.scalar(
                select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
            )
            attempt = session.scalar(
                select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
            )
            if task is None or attempt is None:
                return
            if (
                task.status != "running"
                or task.leased_by != self.config.worker_id
                or attempt.status != "running"
            ):
                return
            usage = _terminal_attempt_usage(session, attempt.id, usage)
            safe_message = _safe_error_message(error, sensitive_values)
            underlying_error_code = error_code
            if task.prompt_version in COMPONENT_GENERATION_PROMPT_VERSIONS:
                error_code = "agent_component_failed"
            failure_issues = _failure_validation_issues(validation_errors)
            if task.prompt_version in COMPONENT_GENERATION_PROMPT_VERSIONS:
                coordinator_issue = {
                    "component_id": "run_coordinator",
                    "failure_layer": "frozen_context",
                    "schema_id": "task-run-v1",
                    "code": underlying_error_code,
                    "path": "",
                    "message": safe_message,
                }
                if not failure_issues:
                    failure_issues = [
                        {
                            "code": underlying_error_code,
                            "path": "",
                            "message": safe_message,
                        }
                    ]
                has_failed_step = session.scalar(
                    select(AgentStepRun.id).where(
                        AgentStepRun.task_attempt_id == attempt.id,
                        AgentStepRun.status == "failed",
                    )
                )
                if has_failed_step is None:
                    _record_component_coordinator_failure(
                        session,
                        task=task,
                        attempt=attempt,
                        issue=coordinator_issue,
                        finished_at=datetime.now(UTC),
                    )
            public_failure = task_failure_view(
                error_code,
                issues=failure_issues,
                network_retries=_network_retries(task),
            )
            details = {
                "exception_type": type(error).__name__,
                "message": safe_message,
                "public_failure": public_failure,
                **({"repair_attempted": True} if getattr(error, "repair_attempted", False) else {}),
            }
            now = datetime.now(UTC)
            attempt.status = "failed"
            attempt.candidate_jsonb = candidate
            attempt.validation_errors_jsonb = validation_errors
            attempt.usage_jsonb = usage
            attempt.error_code = error_code
            attempt.error_details_jsonb = details
            attempt.finished_at = now
            task.status = "failed"
            task.stage = "failed"
            task.usage_jsonb = usage
            task.error_code = error_code
            task.error_details_jsonb = details
            task.completed_at = now
            task.leased_by = None
            task.lease_expires_at = None
            if task.task_type == "casefile_chat" and task.output_message_id is not None:
                output_message = session.get(AgentMessage, task.output_message_id)
                if output_message is not None and output_message.status == "pending":
                    output_message.status = "failed"
                    output_message.content_text = (
                        public_failure["message"]
                        if public_failure is not None
                        else "Agent 本次没有完成回复，请稍后重试。"
                    )
            verification_started = session.scalar(
                select(TaskEvent.id)
                .where(
                    TaskEvent.task_run_id == task.id,
                    TaskEvent.event_type == "verification.started",
                )
                .limit(1)
            )
            if verification_started is not None:
                verification_trigger = str(task.input_jsonb.get("verification_trigger", "chat"))
                append_task_event(
                    session,
                    task,
                    "verification.failed",
                    "verification",
                    {
                        "trigger": verification_trigger,
                        "profile": "balanced",
                        "error_code": error_code,
                        "message": (
                            public_failure["message"]
                            if public_failure is not None
                            else "验证复查没有完成，未更改当前工作稿。"
                        ),
                    },
                )
            append_task_event(
                session,
                task,
                "task.failed",
                "failed",
                {
                    "message": (
                        public_failure["message"]
                        if public_failure is not None
                        else (
                            "Agent 本次没有完成回复，工作稿未被修改"
                            if task.task_type == "casefile_chat"
                            else "Agent 任务失败，原始素材与已确认 Brief 未被修改"
                        )
                    ),
                    "task_type": task.task_type,
                    "error_code": error_code,
                    "failure": public_failure,
                },
            )

    def _emit_after_completion(
        self,
        task_run_id: int,
        event_type: str,
        stage: str,
        payload: dict[str, Any],
    ) -> None:
        """Append a replayable event after the task reached a terminal state.

        Post-completion maintenance (rolling compaction) must not re-assert the
        running lease or move ``task.stage``, so it bypasses ``_emit`` and only
        appends the immutable TaskEvent.
        """

        with self.session_factory() as session, session.begin():
            task = session.get(TaskRun, task_run_id)
            if task is None:
                raise RuntimeError("TaskRun disappeared before post-completion event")
            public_payload = {
                key: value for key, value in payload.items() if not key.startswith("_")
            }
            append_task_event(session, task, event_type, stage, public_payload)

    def _emit(
        self,
        task_run_id: int,
        event_type: str,
        stage: str,
        payload: dict[str, Any],
    ) -> None:
        with self.session_factory() as session, session.begin():
            task = session.scalar(
                select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
            )
            if task is not None and task.status == "cancelling":
                raise TaskCancellationRequested
            if task is None or task.status != "running" or task.leased_by != self.config.worker_id:
                raise RuntimeError("TaskRun lease was lost")
            task.stage = stage
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.config.lease_seconds)
            _persist_agent_execution_event(session, task, event_type, payload)
            public_payload = {
                key: value for key, value in payload.items() if not key.startswith("_")
            }
            append_task_event(session, task, event_type, stage, public_payload)


__all__ = ["TaskFinalizationMixin"]
