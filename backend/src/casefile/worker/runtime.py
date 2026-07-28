"""Lease-based PostgreSQL queue consumer and generation task executor."""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from casefile.agent_runtime import (
    GenerationRequest,
    GenerationResult,
    OpenAIAgentsProvider,
)
from casefile.agent_runtime.credentials import decrypt_api_key
from casefile.agent_runtime.providers import GenerationProvider
from casefile.application.casefile_v1 import write_generated_casefile
from casefile.application.workflow_service import append_task_event
from casefile.contracts import ContractValidationError, validate_casefile
from casefile.data_postgres.models import (
    Brief,
    BriefVersion,
    TaskAttempt,
    TaskRun,
    UserProviderSetting,
)
from casefile.data_postgres.repositories import ProjectRepository

ProviderFactory = Callable[[TaskRun], GenerationProvider]


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    worker_id: str
    poll_seconds: float = 1.0
    lease_seconds: int = 600

    @classmethod
    def from_environment(cls) -> WorkerConfig:
        default_id = f"{socket.gethostname()}-{os.getpid()}"
        return cls(
            worker_id=os.environ.get("CASEFILE_WORKER_ID", default_id),
            poll_seconds=float(os.environ.get("CASEFILE_WORKER_POLL_SECONDS", "1")),
            lease_seconds=int(os.environ.get("CASEFILE_WORKER_LEASE_SECONDS", "600")),
        )


class Worker:
    """Consume TaskRuns with `FOR UPDATE SKIP LOCKED`; one instance executes serially."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        config: WorkerConfig,
        provider_factory: ProviderFactory | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.config = config
        self.provider_factory = provider_factory or (lambda _task: OpenAIAgentsProvider())

    def run_forever(self) -> None:
        while True:
            if not self.run_once():
                time.sleep(self.config.poll_seconds)

    def run_once(self) -> bool:
        claimed = self._claim_next()
        if claimed is None:
            return False
        task_run_id, attempt_id = claimed
        self._execute(task_run_id, attempt_id)
        return True

    def _claim_next(self) -> tuple[int, int] | None:
        now = datetime.now(UTC)
        with self.session_factory() as session, session.begin():
            task = session.scalar(
                select(TaskRun)
                .where(
                    or_(
                        TaskRun.status == "queued",
                        (TaskRun.status == "running") & (TaskRun.lease_expires_at < now),
                    )
                )
                .order_by(TaskRun.created_at, TaskRun.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if task is None:
                return None
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

    def _execute(self, task_run_id: int, attempt_id: int) -> None:
        candidate: dict[str, Any] | None = None
        result: GenerationResult | None = None
        validation_errors: list[dict[str, Any]] = []
        try:
            request, task_snapshot = self._load_request(task_run_id)
            provider = self.provider_factory(task_snapshot)
            repair_limit = int(task_snapshot.budget_jsonb.get("structural_repair_attempts", 2))
            feedback: tuple[str, ...] = ()
            for repair_no in range(repair_limit + 1):
                if repair_no:
                    self._emit(
                        task_run_id,
                        "model.repair_started",
                        "repairing",
                        {"repair_no": repair_no, "max_repairs": repair_limit},
                    )
                try:
                    result = provider.generate(replace(request, repair_feedback=feedback))
                    candidate = result.candidate
                    validate_casefile(candidate)
                    break
                except ContractValidationError as error:
                    issue = {"repair_no": repair_no, "error": str(error)}
                    validation_errors.append(issue)
                    feedback = tuple(item["error"] for item in validation_errors[-3:])
                    if repair_no >= repair_limit:
                        raise
            if result is None or candidate is None:
                raise RuntimeError("Provider returned no candidate")
            self._complete(task_run_id, attempt_id, candidate, result, validation_errors)
        except Exception as error:
            self._fail(
                task_run_id,
                attempt_id,
                error,
                candidate=candidate,
                usage={} if result is None else result.usage,
                validation_errors=validation_errors,
            )

    def _load_request(self, task_run_id: int) -> tuple[GenerationRequest, TaskRun]:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(TaskRun).where(TaskRun.id == task_run_id))
            if task is None or task.status != "running" or task.leased_by != self.config.worker_id:
                raise RuntimeError("TaskRun lease is no longer owned by this worker")
            owned = ProjectRepository(session).get_owned(task.actor_user_id, task.project_id)
            brief_version = session.get(BriefVersion, task.brief_version_id)
            setting = session.get(UserProviderSetting, task.provider_setting_id)
            if owned is None or brief_version is None or setting is None:
                raise RuntimeError("Frozen TaskRun dependencies are missing")
            brief = session.get(Brief, brief_version.brief_id)
            if brief is None:
                raise RuntimeError("Brief is missing")
            if setting.config_version != task.provider_config_version:
                # The TaskRun freezes model settings but references the latest encrypted credential.
                pass
            api_key = decrypt_api_key(
                setting.secret_ciphertext,
                setting.secret_nonce,
                user_id=setting.user_id,
                provider=setting.provider,
                key_version=setting.key_version,
            )
            request = GenerationRequest(
                task_run_id=task.id,
                brief=brief_version.content_jsonb,
                casefile_id=owned.casefile.object_id,
                brief_id=brief.public_id,
                brief_version=brief_version.version_no,
                project_profile=owned.project.profile_jsonb,
                version_id=owned.draft.version_id,
                version_no=owned.draft.version_no,
                parent_version_id=owned.draft.parent_version_id,
                model_id=task.model_id,
                api_key=api_key,
                max_turns=int(task.budget_jsonb.get("max_turns", 12)),
                emit=lambda event_type, stage, payload: self._emit(
                    task_run_id, event_type, stage, payload
                ),
            )
            session.expunge(task)
            return request, task

    def _complete(
        self,
        task_run_id: int,
        attempt_id: int,
        candidate: dict[str, Any],
        result: GenerationResult,
        validation_errors: list[dict[str, Any]],
    ) -> None:
        self._emit(
            task_run_id,
            "validation.started",
            "validating",
            {"layers": ["schema", "id", "refs", "db_mapping", "revision"]},
        )
        with self.session_factory() as session, session.begin():
            task = session.scalar(
                select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
            )
            attempt = session.scalar(
                select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
            )
            if task is None or attempt is None:
                raise RuntimeError("TaskRun or TaskAttempt disappeared")
            if task.leased_by != self.config.worker_id or task.status != "running":
                raise RuntimeError("TaskRun lease was lost before the final write")
            owned = ProjectRepository(session).get_owned(
                task.actor_user_id, task.project_id, lock=True
            )
            brief_version = session.get(BriefVersion, task.brief_version_id)
            if owned is None or brief_version is None:
                raise RuntimeError("TaskRun aggregate disappeared")
            brief = session.get(Brief, brief_version.brief_id)
            if brief is None:
                raise RuntimeError("Brief disappeared")
            if owned.draft.revision != task.input_draft_revision:
                raise RuntimeError("Draft revision changed while generation was running")
            snapshot = write_generated_casefile(
                session,
                owned,
                candidate=candidate,
                brief=brief,
                brief_version=brief_version,
                task_run_id=task.id,
                actor_user_id=task.actor_user_id,
            )
            now = datetime.now(UTC)
            attempt.status = "succeeded"
            attempt.candidate_jsonb = candidate
            attempt.validation_errors_jsonb = validation_errors
            attempt.usage_jsonb = {**result.usage, "tools": result.tools.as_dict()}
            attempt.finished_at = now
            task.status = "succeeded"
            task.stage = "completed"
            task.usage_jsonb = attempt.usage_jsonb
            task.result_snapshot_id = snapshot.id
            task.completed_at = now
            task.leased_by = None
            task.lease_expires_at = None
            append_task_event(
                session,
                task,
                "validation.completed",
                "validating",
                {"valid": True, "content_hash": snapshot.content_hash},
            )
            append_task_event(
                session,
                task,
                "task.succeeded",
                "completed",
                {
                    "message": "CaseFile 已生成并写入工作稿",
                    "snapshot_id": snapshot.id,
                    "draft_revision": owned.draft.revision,
                    "usage": task.usage_jsonb,
                },
            )

    def _fail(
        self,
        task_run_id: int,
        attempt_id: int,
        error: Exception,
        *,
        candidate: dict[str, Any] | None,
        usage: dict[str, Any],
        validation_errors: list[dict[str, Any]],
    ) -> None:
        error_code = _error_code(error)
        details = {"exception_type": type(error).__name__, "message": str(error)[:500]}
        with self.session_factory() as session, session.begin():
            task = session.scalar(
                select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
            )
            attempt = session.scalar(
                select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
            )
            if task is None or attempt is None:
                return
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
            append_task_event(
                session,
                task,
                "task.failed",
                "failed",
                {"message": "生成任务失败", "error_code": error_code},
            )

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
            if task is None or task.status != "running" or task.leased_by != self.config.worker_id:
                raise RuntimeError("TaskRun lease was lost")
            task.stage = stage
            task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=self.config.lease_seconds)
            append_task_event(session, task, event_type, stage, payload)


def _error_code(error: Exception) -> str:
    if isinstance(error, ContractValidationError):
        return "candidate_validation_failed"
    name = type(error).__name__.lower()
    if "authentication" in name:
        return "provider_authentication_failed"
    if "ratelimit" in name:
        return "provider_rate_limited"
    if "timeout" in name:
        return "provider_timeout"
    return "generation_failed"
