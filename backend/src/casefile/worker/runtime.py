"""Lease-based PostgreSQL queue consumer and generation task executor."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

import rfc8785
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from casefile.agent_runtime import (
    CANDIDATE_STRATEGY_VERSION,
    AgentProvider,
    BriefAnchorExtractRequest,
    BriefAnchorExtractResult,
    BriefIntakeQuestionsRequest,
    BriefIntakeQuestionsResult,
    BriefIntakeSynthesizeRequest,
    BriefIntakeSynthesizeResult,
    BriefPolishRequest,
    BriefPolishResult,
    BriefStrategyOptionsRequest,
    BriefStrategyOptionsResult,
    CandidateStrategy,
    CaseFileChatRequest,
    CaseFileChatResult,
    DeepSeekAgentsProvider,
    GenerationRequest,
    GenerationResult,
    OpenAIAgentsProvider,
    PolishMode,
)
from casefile.agent_runtime.credentials import decrypt_api_key
from casefile.agent_runtime.providers import ProviderProtocolError
from casefile.application.brief_intake_service import BriefIntakeService
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
    AgentModelCall,
    AgentStepRun,
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
                polish_mode = _required_string(
                    task_snapshot.input_jsonb,
                    "polish_mode",
                )
                if polish_mode not in {"proofread", "rewrite", "narrative_enhance"}:
                    raise RuntimeError("Frozen polish mode is invalid")
                polish_request = BriefPolishRequest(
                    task_run_id=task_snapshot.id,
                    prompt_version=task_snapshot.prompt_version,
                    source_text=source_text,
                    polish_mode=cast(PolishMode, polish_mode),
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
                mode = task_snapshot.input_jsonb.get("mode", "extract")
                if mode not in {"extract", "suggest_author_answer"}:
                    raise RuntimeError("Frozen Brief anchor extraction mode is invalid")
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
                    mode=cast(Literal["extract", "suggest_author_answer"], mode),
                )
                extract_result = provider.extract_anchors(extract_request)
                candidate = extract_result.candidate.model_dump(
                    mode="json",
                    exclude_none=True,
                )
                usage = extract_result.usage
                self._complete_anchor_extract(
                    task_run_id,
                    attempt_id,
                    extract_result,
                )
                return
            if task_snapshot.task_type == "brief_strategy_options":
                strategy_request = self._load_strategy_options_request(
                    task_snapshot,
                    api_key,
                )
                strategy_result = provider.strategy_options(strategy_request)
                candidate = strategy_result.candidate.model_dump(mode="json")
                usage = strategy_result.usage
                self._complete_strategy_options(
                    task_run_id,
                    attempt_id,
                    strategy_result,
                )
                return
            if task_snapshot.task_type == "brief_intake_questions":
                if _json_hash(task_snapshot.input_jsonb) != task_snapshot.input_hash:
                    raise RuntimeError(
                        "Frozen Brief Intake question payload does not match its input hash"
                    )
                frozen_source = _required_object(task_snapshot.input_jsonb, "source")
                mode = task_snapshot.input_jsonb.get("mode", "initial")
                if mode not in ("initial", "additional"):
                    raise RuntimeError("Frozen Brief Intake question mode is invalid")
                existing_questions = task_snapshot.input_jsonb.get("existing_questions", [])
                if not isinstance(existing_questions, list):
                    raise RuntimeError("Frozen Brief Intake existing questions must be an array")
                questions_request = BriefIntakeQuestionsRequest(
                    task_run_id=task_snapshot.id,
                    prompt_version=task_snapshot.prompt_version,
                    source_text=_required_string(frozen_source, "content_text"),
                    existing_questions=deepcopy(existing_questions),
                    mode=mode,
                    input_hash=task_snapshot.input_hash,
                    model_id=task_snapshot.model_id,
                    api_key=api_key,
                    max_turns=int(task_snapshot.budget_jsonb.get("max_turns", 12)),
                    emit=lambda event_type, stage, payload: self._emit(
                        task_run_id, event_type, stage, payload
                    ),
                    network_retries=_network_retries(task_snapshot),
                )
                questions_result = provider.intake_questions(questions_request)
                candidate = questions_result.candidate.model_dump(mode="json")
                usage = questions_result.usage
                self._complete_intake_questions(
                    task_run_id,
                    attempt_id,
                    questions_result,
                )
                return
            if task_snapshot.task_type == "brief_intake_synthesize":
                if _json_hash(task_snapshot.input_jsonb) != task_snapshot.input_hash:
                    raise RuntimeError(
                        "Frozen Brief Intake synthesis payload does not match its input hash"
                    )
                synthesize_request = BriefIntakeSynthesizeRequest(
                    task_run_id=task_snapshot.id,
                    prompt_version=task_snapshot.prompt_version,
                    input_data=task_snapshot.input_jsonb,
                    input_hash=task_snapshot.input_hash,
                    model_id=task_snapshot.model_id,
                    api_key=api_key,
                    max_turns=int(task_snapshot.budget_jsonb.get("max_turns", 12)),
                    emit=lambda event_type, stage, payload: self._emit(
                        task_run_id, event_type, stage, payload
                    ),
                    network_retries=_network_retries(task_snapshot),
                )
                synthesize_result = provider.synthesize_intake(synthesize_request)
                candidate = synthesize_result.candidate.model_dump(mode="json")
                usage = synthesize_result.usage
                self._complete_intake_synthesize(
                    task_run_id,
                    attempt_id,
                    synthesize_result,
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
            repair_limit = (
                0
                if task_snapshot.prompt_version == "brief-to-draft-v7"
                else int(task_snapshot.budget_jsonb.get("structural_repair_attempts", 2))
            )
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
                    validation_errors.append({"repair_no": repair_no, "issues": public_issues})
                    feedback_history.append({"repair_no": repair_no, "issues": error.errors})
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
                raise RuntimeError("Frozen provider setting version no longer matches TaskRun")
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

    def _load_strategy_options_request(
        self,
        task: TaskRun,
        api_key: str,
    ) -> BriefStrategyOptionsRequest:
        with self.session_factory() as session, session.begin():
            brief_version = (
                None
                if task.brief_version_id is None
                else session.get(BriefVersion, task.brief_version_id)
            )
            if brief_version is None:
                raise RuntimeError("Frozen strategy BriefVersion is missing")
            frozen_brief = _required_object(task.input_jsonb, "brief")
            if brief_version.content_hash != task.input_hash:
                raise RuntimeError("Frozen strategy BriefVersion hash changed")
            if _json_hash(frozen_brief) != task.input_hash:
                raise RuntimeError("Frozen strategy Brief payload does not match its hash")
            return BriefStrategyOptionsRequest(
                task_run_id=task.id,
                prompt_version=task.prompt_version,
                brief=frozen_brief,
                input_hash=task.input_hash,
                model_id=task.model_id,
                api_key=api_key,
                max_turns=int(task.budget_jsonb.get("max_turns", 12)),
                emit=lambda event_type, stage, payload: self._emit(
                    task.id, event_type, stage, payload
                ),
                network_retries=_network_retries(task),
            )

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
            raw_strategy = task.input_jsonb.get(
                "candidate_strategy",
                CandidateStrategy.BALANCED.value,
            )
            try:
                candidate_strategy = CandidateStrategy(raw_strategy)
            except ValueError as error:
                raise RuntimeError("Frozen candidate strategy is invalid") from error
            candidate_strategy_version = task.input_jsonb.get(
                "candidate_strategy_version",
                CANDIDATE_STRATEGY_VERSION,
            )
            if candidate_strategy_version != CANDIDATE_STRATEGY_VERSION:
                raise RuntimeError("Frozen candidate strategy version is invalid")
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
                candidate_strategy=candidate_strategy,
                candidate_strategy_version=candidate_strategy_version,
                reusable_steps=_reusable_component_steps(session, task),
                agent_version=task.agent_version,
                toolset_version=task.toolset_version,
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
            emit=lambda event_type, stage, payload: self._emit(task.id, event_type, stage, payload),
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
            frozen_mode = _required_string(task.input_jsonb, "polish_mode")
            if frozen_mode != result.polish_mode:
                raise RuntimeError("Polish result mode does not match its frozen task input")
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
                "polish_mode": result.polish_mode,
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

    def _complete_strategy_options(
        self,
        task_run_id: int,
        attempt_id: int,
        result: BriefStrategyOptionsResult,
    ) -> None:
        with self.session_factory() as session, session.begin():
            task, attempt = self._locked_completion_rows(
                session,
                task_run_id,
                attempt_id,
                expected_task_type="brief_strategy_options",
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
                message="三种定制策略已形成，等待作者选择。",
            )

    def _complete_intake_questions(
        self,
        task_run_id: int,
        attempt_id: int,
        result: BriefIntakeQuestionsResult,
    ) -> None:
        payload = result.candidate.model_dump(mode="json")
        with self.session_factory() as session:
            BriefIntakeService(session).complete_questions_task(
                task_run_id,
                attempt_id,
                questions=list(payload["questions"]),
                usage=result.usage,
            )

    def _complete_intake_synthesize(
        self,
        task_run_id: int,
        attempt_id: int,
        result: BriefIntakeSynthesizeResult,
    ) -> None:
        with self.session_factory() as session:
            BriefIntakeService(session).complete_synthesize_task(
                task_run_id,
                attempt_id,
                content=result.candidate.model_dump(mode="json"),
                usage=result.usage,
            )

    def _locked_completion_rows(
        self,
        session: Session,
        task_run_id: int,
        attempt_id: int,
        *,
        expected_task_type: str,
    ) -> tuple[TaskRun, TaskAttempt]:
        task = session.scalar(select(TaskRun).where(TaskRun.id == task_run_id).with_for_update())
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
            raw_strategy = task.input_jsonb.get(
                "candidate_strategy",
                CandidateStrategy.BALANCED.value,
            )
            try:
                candidate_strategy = CandidateStrategy(raw_strategy)
            except ValueError as error:
                raise RuntimeError("Frozen candidate strategy is invalid") from error
            summary.update(
                {
                    "candidate_strategy": candidate_strategy.value,
                    "candidate_strategy_version": task.input_jsonb.get(
                        "candidate_strategy_version",
                        CANDIDATE_STRATEGY_VERSION,
                    ),
                }
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
            safe_message = _safe_error_message(error, sensitive_values)
            if task.prompt_version == "brief-to-draft-v8":
                underlying_error_code = error_code
                error_code = "agent_component_failed"
            failure_issues = _failure_validation_issues(validation_errors)
            if task.prompt_version == "brief-to-draft-v8":
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
                    _record_v8_coordinator_failure(
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
            _persist_agent_execution_event(session, task, event_type, payload)
            public_payload = {
                key: value for key, value in payload.items() if not key.startswith("_")
            }
            append_task_event(session, task, event_type, stage, public_payload)


def _persist_agent_execution_event(
    session: Session,
    task: TaskRun,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Project component execution events into queryable step/call audit rows."""

    if task.prompt_version not in {"brief-to-draft-v8", "brief-to-draft-v9"}:
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
                    if component_id in {"story_world", "evidence_logic", "resolution_governance"}
                    else None
                ),
                execution_no=execution_no,
                status="running",
                input_hash=str(payload.get("input_hash") or task.input_hash),
                upstream_hashes_jsonb=dict(payload.get("upstream_hashes") or {}),
                ir_schema_id=str(payload.get("schema_id") or "unknown"),
                component_version=task.prompt_version,
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
                prompt_version=task.prompt_version,
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


def _record_v8_coordinator_failure(
    session: Session,
    *,
    task: TaskRun,
    attempt: TaskAttempt,
    issue: dict[str, str],
    finished_at: datetime,
) -> None:
    """Persist a v8 failure that happened before a business component could start."""

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
    if task.prompt_version != "brief-to-draft-v8" or task.attempt_count < 2:
        return {}
    rows = session.scalars(
        select(AgentStepRun)
        .where(
            AgentStepRun.task_run_id == task.id,
            AgentStepRun.status.in_(("succeeded", "reused")),
            AgentStepRun.component_version == task.prompt_version,
            AgentStepRun.component_id.in_(
                (
                    "case_blueprint_planner",
                    "story_world",
                    "evidence_logic",
                    "resolution_governance",
                )
            ),
        )
        .order_by(AgentStepRun.id.desc())
    )
    reusable: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.component_id in reusable or not isinstance(row.output_jsonb, dict):
            continue
        reusable[row.component_id] = {
            "step_run_id": row.id,
            "input_hash": row.input_hash,
            "output_hash": row.output_hash,
            "schema_id": row.ir_schema_id,
            "output": row.output_jsonb,
        }
    return reusable


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
