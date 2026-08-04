"""Lease-based PostgreSQL queue consumer and generation task executor."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import rfc8785
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from casefile.agent_runtime import (
    AgentProvider,
    BriefAnchorExtractRequest,
    BriefAnchorExtractResult,
    BriefPolishRequest,
    BriefPolishResult,
    CaseFileChatRequest,
    CaseFileChatResult,
    DeepSeekAgentsProvider,
    GenerationRequest,
    GenerationResult,
    OpenAIAgentsProvider,
)
from casefile.agent_runtime.credentials import decrypt_api_key
from casefile.agent_runtime.providers import ProviderProtocolError
from casefile.application.casefile_v1 import (
    generation_candidate_summary,
    validate_generation_candidate_context,
)
from casefile.application.v1_editing import (
    editable_fields_by_collection as chat_editable_fields_by_collection,
)
from casefile.application.workflow_service import (
    WorkflowService,
    append_task_event,
    source_view,
    task_failure_view,
)
from casefile.contracts import (
    ContractValidationError,
    public_validation_issues,
    validate_casefile,
)
from casefile.data_postgres.models import (
    AgentMessage,
    Brief,
    BriefVersion,
    SourceRecord,
    TaskAttempt,
    TaskRun,
    UserProviderSetting,
)
from casefile.data_postgres.repositories import ProjectRepository

ProviderFactory = Callable[[TaskRun], AgentProvider]


def provider_for_task(task: TaskRun) -> AgentProvider:
    if task.provider == "openai":
        return OpenAIAgentsProvider()
    if task.provider == "deepseek":
        return DeepSeekAgentsProvider()
    raise RuntimeError(f"Unsupported provider frozen on TaskRun: {task.provider}")


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
        self.provider_factory = provider_factory or provider_for_task

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
        usage: dict[str, Any] = {}
        validation_errors: list[dict[str, Any]] = []
        sensitive_values: tuple[str, ...] = ()
        try:
            task_snapshot, api_key = self._load_task_context(task_run_id)
            sensitive_values = (api_key,)
            provider = self.provider_factory(task_snapshot)
            if task_snapshot.task_type == "brief_polish":
                source_text = _required_string(
                    task_snapshot.input_jsonb,
                    "source_text",
                )
                if _text_hash(source_text) != task_snapshot.input_hash:
                    raise RuntimeError("Frozen SourceRecord payload does not match its input hash")
                polish_request = BriefPolishRequest(
                    task_run_id=task_snapshot.id,
                    prompt_version=task_snapshot.prompt_version,
                    source_text=source_text,
                    input_hash=task_snapshot.input_hash,
                    model_id=task_snapshot.model_id,
                    api_key=api_key,
                    max_turns=int(task_snapshot.budget_jsonb.get("max_turns", 12)),
                    emit=lambda event_type, stage, payload: self._emit(
                        task_run_id, event_type, stage, payload
                    ),
                    network_retries=_network_retries(task_snapshot),
                )
                polish_result = provider.polish(polish_request)
                candidate = polish_result.candidate.model_dump(mode="json")
                usage = polish_result.usage
                self._complete_polish(
                    task_run_id,
                    attempt_id,
                    polish_result,
                )
                return
            if task_snapshot.task_type == "brief_anchor_extract":
                frozen_brief = _required_object(task_snapshot.input_jsonb, "brief")
                if _json_hash(frozen_brief) != task_snapshot.input_hash:
                    raise RuntimeError("Frozen Brief payload does not match its input hash")
                extract_request = BriefAnchorExtractRequest(
                    task_run_id=task_snapshot.id,
                    prompt_version=task_snapshot.prompt_version,
                    brief=frozen_brief,
                    input_hash=task_snapshot.input_hash,
                    model_id=task_snapshot.model_id,
                    api_key=api_key,
                    max_turns=int(task_snapshot.budget_jsonb.get("max_turns", 12)),
                    emit=lambda event_type, stage, payload: self._emit(
                        task_run_id, event_type, stage, payload
                    ),
                    network_retries=_network_retries(task_snapshot),
                )
                extract_result = provider.extract_anchors(extract_request)
                candidate = extract_result.candidate.model_dump(mode="json")
                usage = extract_result.usage
                self._complete_anchor_extract(
                    task_run_id,
                    attempt_id,
                    extract_result,
                )
                return
            if task_snapshot.task_type == "casefile_chat":
                chat_request = self._load_chat_request(task_snapshot, api_key)
                chat_result = provider.chat(chat_request)
                candidate = chat_result.candidate.model_dump(mode="json")
                usage = chat_result.usage
                self._complete_chat(
                    task_run_id,
                    attempt_id,
                    chat_result,
                )
                return
            if task_snapshot.task_type != "brief_to_draft":
                raise RuntimeError(f"Unsupported TaskRun type: {task_snapshot.task_type}")
            generation_request = self._load_generation_request(task_snapshot, api_key)
            result: GenerationResult | None = None
            repair_limit = int(task_snapshot.budget_jsonb.get("structural_repair_attempts", 2))
            feedback: tuple[dict[str, Any], ...] = ()
            feedback_history: list[dict[str, Any]] = []
            for repair_no in range(repair_limit + 1):
                if repair_no:
                    self._emit(
                        task_run_id,
                        "model.repair_started",
                        "repairing",
                        {"repair_no": repair_no, "max_repairs": repair_limit},
                    )
                try:
                    result = provider.generate(
                        replace(generation_request, repair_feedback=feedback)
                    )
                    candidate = result.candidate
                    validate_casefile(candidate)
                    break
                except ContractValidationError as error:
                    public_issues = public_validation_issues(error.errors)
                    validation_errors.append(
                        {"repair_no": repair_no, "issues": public_issues}
                    )
                    feedback_history.append(
                        {"repair_no": repair_no, "issues": error.errors}
                    )
                    feedback = tuple(feedback_history[-3:])
                    self._emit(
                        task_run_id,
                        "validation.failed",
                        "validating",
                        {
                            "repair_no": repair_no,
                            "issue_count": len(error.errors),
                            "issues": public_issues,
                        },
                    )
                    if repair_no >= repair_limit:
                        raise
            if result is None or candidate is None:
                raise RuntimeError("Provider returned no candidate")
            usage = result.usage
            self._complete_generation_candidate(
                task_run_id,
                attempt_id,
                candidate,
                result,
                validation_errors,
            )
        except Exception as error:
            self._fail(
                task_run_id,
                attempt_id,
                error,
                candidate=candidate,
                usage=usage,
                validation_errors=validation_errors,
                sensitive_values=sensitive_values,
            )

    def _load_task_context(self, task_run_id: int) -> tuple[TaskRun, str]:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(TaskRun).where(TaskRun.id == task_run_id))
            if task is None or task.status != "running" or task.leased_by != self.config.worker_id:
                raise RuntimeError("TaskRun lease is no longer owned by this worker")
            setting = session.get(UserProviderSetting, task.provider_setting_id)
            if setting is None:
                raise RuntimeError("Frozen provider setting is missing")
            if setting.user_id != task.actor_user_id or setting.provider != task.provider:
                raise RuntimeError("Frozen provider setting does not match TaskRun provenance")
            if setting.config_version != task.provider_config_version:
                raise RuntimeError(
                    "Frozen provider setting version no longer matches TaskRun"
                )
            if (
                setting.credential_status == "deleted"
                or setting.secret_ciphertext is None
                or setting.secret_nonce is None
                or setting.key_version is None
            ):
                raise RuntimeError("Frozen provider credential has been deleted")
            api_key = decrypt_api_key(
                setting.secret_ciphertext,
                setting.secret_nonce,
                user_id=setting.user_id,
                provider=setting.provider,
                key_version=setting.key_version,
            )
            session.expunge(task)
            return task, api_key

    def _load_generation_request(
        self,
        task: TaskRun,
        api_key: str,
    ) -> GenerationRequest:
        with self.session_factory() as session, session.begin():
            owned = ProjectRepository(session).get_owned(task.actor_user_id, task.project_id)
            brief_version = (
                None
                if task.brief_version_id is None
                else session.get(BriefVersion, task.brief_version_id)
            )
            if owned is None or brief_version is None:
                raise RuntimeError("Frozen generation dependencies are missing")
            brief = session.get(Brief, brief_version.brief_id)
            if brief is None:
                raise RuntimeError("Brief is missing")
            frozen_brief = _required_object(task.input_jsonb, "brief")
            if brief_version.content_hash != task.input_hash:
                raise RuntimeError("Frozen BriefVersion hash no longer matches TaskRun input")
            if _json_hash(frozen_brief) != task.input_hash:
                raise RuntimeError("Frozen TaskRun Brief payload does not match its input hash")
            frozen_version = _required_object(task.input_jsonb, "version")
            return GenerationRequest(
                task_run_id=task.id,
                prompt_version=task.prompt_version,
                brief=frozen_brief,
                casefile_id=_required_string(task.input_jsonb, "casefile_id"),
                brief_id=_required_string(task.input_jsonb, "brief_public_id"),
                brief_version=_required_integer(
                    task.input_jsonb,
                    "brief_version_no",
                ),
                version_id=_required_string(frozen_version, "version_id"),
                version_no=_required_integer(frozen_version, "version_no"),
                parent_version_id=_optional_string(
                    frozen_version,
                    "parent_version_id",
                ),
                model_id=task.model_id,
                api_key=api_key,
                max_turns=int(task.budget_jsonb.get("max_turns", 12)),
                emit=lambda event_type, stage, payload: self._emit(
                    task.id, event_type, stage, payload
                ),
                network_retries=_network_retries(task),
            )

    def _load_chat_request(
        self,
        task: TaskRun,
        api_key: str,
    ) -> CaseFileChatRequest:
        frozen_input = task.input_jsonb
        if _json_hash(frozen_input) != task.input_hash:
            raise RuntimeError("Frozen CaseFile chat payload does not match its input hash")
        casefile = _required_object(frozen_input, "casefile")
        message = _required_string(frozen_input, "message")
        raw_history = frozen_input.get("history")
        if not isinstance(raw_history, list):
            raise RuntimeError("Frozen CaseFile chat payload is missing history")
        history: list[dict[str, str]] = []
        for item in raw_history:
            if not isinstance(item, dict):
                raise RuntimeError("Frozen CaseFile chat history entry is invalid")
            role = item.get("role")
            content = item.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str) or not content:
                raise RuntimeError("Frozen CaseFile chat history entry is invalid")
            history.append({"role": role, "content": content})
        return CaseFileChatRequest(
            task_run_id=task.id,
            prompt_version=task.prompt_version,
            casefile=casefile,
            history=tuple(history),
            message=message,
            editable_fields_by_collection=chat_editable_fields_by_collection(),
            input_hash=task.input_hash,
            model_id=task.model_id,
            api_key=api_key,
            max_turns=int(task.budget_jsonb.get("max_turns", 12)),
            emit=lambda event_type, stage, payload: self._emit(
                task.id, event_type, stage, payload
            ),
            network_retries=_network_retries(task),
        )

    def _complete_chat(
        self,
        task_run_id: int,
        attempt_id: int,
        result: CaseFileChatResult,
    ) -> None:
        suggestions: list[dict[str, Any]] = []
        for suggestion in result.candidate.suggestions:
            try:
                value = json.loads(suggestion.value_json)
            except json.JSONDecodeError as error:
                raise ProviderProtocolError(
                    "CaseFile chat suggestion value_json is invalid"
                ) from error
            suggestions.append(
                {
                    "object_id": suggestion.object_id,
                    "path": suggestion.path,
                    "value": value,
                    "reason": suggestion.reason,
                }
            )
        with self.session_factory() as session:
            WorkflowService(session).complete_chat_task(
                task_run_id,
                attempt_id,
                answer=result.candidate.answer,
                referenced_object_ids=result.candidate.referenced_object_ids,
                suggestions=suggestions,
                usage=result.usage,
            )

    def _complete_polish(
        self,
        task_run_id: int,
        attempt_id: int,
        result: BriefPolishResult,
    ) -> None:
        with self.session_factory() as session, session.begin():
            task, attempt = self._locked_completion_rows(
                session,
                task_run_id,
                attempt_id,
                expected_task_type="brief_polish",
            )
            if task.input_source_record_id is None:
                raise RuntimeError("Polish TaskRun has no input SourceRecord")
            source = session.scalar(
                select(SourceRecord).where(
                    SourceRecord.id == task.input_source_record_id,
                    SourceRecord.project_id == task.project_id,
                )
            )
            if source is None:
                raise RuntimeError("Polish input SourceRecord disappeared")
            polished_text = result.candidate.polished_text
            proposal = SourceRecord(
                project_id=task.project_id,
                source_kind="agent_polish_proposal",
                content_text=polished_text,
                content_hash=_text_hash(polished_text),
                parent_source_record_id=source.id,
                generated_by_task_run_id=task.id,
                created_by_user_id=task.actor_user_id,
            )
            session.add(proposal)
            session.flush()
            result_json = {
                "input_hash": task.input_hash,
                **result.candidate.model_dump(mode="json"),
                "proposal_source_record": source_view(proposal),
            }
            self._finish_auxiliary_success(
                session,
                task,
                attempt,
                candidate=result_json,
                usage=result.usage,
                message="Agent 润色候选已生成，原稿未被覆盖",
            )

    def _complete_anchor_extract(
        self,
        task_run_id: int,
        attempt_id: int,
        result: BriefAnchorExtractResult,
    ) -> None:
        with self.session_factory() as session, session.begin():
            task, attempt = self._locked_completion_rows(
                session,
                task_run_id,
                attempt_id,
                expected_task_type="brief_anchor_extract",
            )
            result_json = {
                "input_hash": task.input_hash,
                **result.candidate.model_dump(mode="json"),
            }
            self._finish_auxiliary_success(
                session,
                task,
                attempt,
                candidate=result_json,
                usage=result.usage,
                message="原子拆解候选已生成，等待作者确认",
            )

    def _locked_completion_rows(
        self,
        session: Session,
        task_run_id: int,
        attempt_id: int,
        *,
        expected_task_type: str,
    ) -> tuple[TaskRun, TaskAttempt]:
        task = session.scalar(
            select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
        )
        attempt = session.scalar(
            select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
        )
        if task is None or attempt is None:
            raise RuntimeError("TaskRun or TaskAttempt disappeared")
        if task.task_type != expected_task_type:
            raise RuntimeError("TaskRun dispatch type changed")
        if task.leased_by != self.config.worker_id or task.status != "running":
            raise RuntimeError("TaskRun lease was lost before the final write")
        return task, attempt

    def _finish_auxiliary_success(
        self,
        session: Session,
        task: TaskRun,
        attempt: TaskAttempt,
        *,
        candidate: dict[str, Any],
        usage: dict[str, Any],
        message: str,
    ) -> None:
        now = datetime.now(UTC)
        attempt.status = "succeeded"
        attempt.candidate_jsonb = candidate
        attempt.validation_errors_jsonb = []
        attempt.usage_jsonb = usage
        attempt.finished_at = now
        task.status = "succeeded"
        task.stage = "completed"
        task.usage_jsonb = usage
        task.result_jsonb = candidate
        task.completed_at = now
        task.leased_by = None
        task.lease_expires_at = None
        append_task_event(
            session,
            task,
            "task.succeeded",
            "completed",
            {
                "message": message,
                "task_type": task.task_type,
                "input_hash": task.input_hash,
                "usage": usage,
            },
        )

    def _complete_generation_candidate(
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
            task, attempt = self._locked_completion_rows(
                session,
                task_run_id,
                attempt_id,
                expected_task_type="brief_to_draft",
            )
            owned = ProjectRepository(session).get_owned(
                task.actor_user_id, task.project_id, lock=True
            )
            brief_version = session.get(BriefVersion, task.brief_version_id)
            if owned is None or brief_version is None:
                raise RuntimeError("TaskRun aggregate disappeared")
            brief = session.get(Brief, brief_version.brief_id)
            if brief is None:
                raise RuntimeError("Brief disappeared")
            validate_generation_candidate_context(
                owned,
                candidate,
                brief=brief,
                brief_version=brief_version,
            )
            summary = generation_candidate_summary(candidate)
            now = datetime.now(UTC)
            attempt.status = "succeeded"
            attempt.candidate_jsonb = candidate
            attempt.validation_errors_jsonb = validation_errors
            attempt.usage_jsonb = {**result.usage, "tools": result.tools.as_dict()}
            attempt.finished_at = now
            task.status = "succeeded"
            task.stage = "completed"
            task.usage_jsonb = attempt.usage_jsonb
            task.result_snapshot_id = None
            task.result_jsonb = summary
            task.completed_at = now
            task.leased_by = None
            task.lease_expires_at = None
            append_task_event(
                session,
                task,
                "validation.completed",
                "validating",
                {"valid": True, "content_hash": summary["content_hash"]},
            )
            append_task_event(
                session,
                task,
                "task.succeeded",
                "completed",
                {
                    "message": "候选草稿已生成，等待作者采用",
                    "content_hash": summary["content_hash"],
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
            failure_issues = _failure_validation_issues(validation_errors)
            public_failure = task_failure_view(
                error_code,
                issues=failure_issues,
                network_retries=_network_retries(task),
            )
            details = {
                "exception_type": type(error).__name__,
                "message": _safe_error_message(error, sensitive_values),
                "public_failure": public_failure,
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
