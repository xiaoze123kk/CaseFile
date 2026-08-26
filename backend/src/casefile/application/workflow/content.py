"""Provider, source, Brief, generation, and task lifecycle workflow use cases."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.agent_runtime.chat_tools import (
    CHAT_TOOLSET_V3_VERSION,
    CHAT_TOOLSET_V4_VERSION,
    CHAT_TOOLSET_VERSION,
)
from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_V2_VERSION,
    CHAT_CONTEXT_POLICY_V3_VERSION,
    CHAT_CONTEXT_POLICY_V4_VERSION,
    CHAT_CONTEXT_POLICY_V5_VERSION,
    CHAT_CONTEXT_POLICY_V6_VERSION,
    CHAT_CONTEXT_POLICY_VERSION,
    CHAT_CONTEXT_PROMPT_V2_VERSION,
    CHAT_CONTEXT_PROMPT_V4_VERSION,
    CHAT_CONTEXT_PROMPT_V5_VERSION,
    CHAT_CONTEXT_PROMPT_V6_VERSION,
    CHAT_CONTEXT_PROMPT_V9_VERSION,
    CHAT_CONTEXT_PROMPT_VERSION,
)
from casefile.agent_runtime.credentials import encrypt_api_key
from casefile.agent_runtime.models import (
    CANDIDATE_STRATEGY_LABELS,
    CANDIDATE_STRATEGY_VERSION,
    CandidateStrategy,
)
from casefile.agent_runtime.prompt import (
    COMPONENT_GENERATION_PROMPT_VERSIONS,
    agent_version_for_task,
)
from casefile.agent_runtime.prompt_repository import prompt_version_for_task
from casefile.agent_runtime.tools import TOOLSET_VERSION
from casefile.application.draft_candidates import DraftCandidateService
from casefile.application.errors import ApplicationError, not_found
from casefile.application.task_cancellation import (
    TERMINAL_TASK_STATUSES,
    finalize_task_cancellation,
)
from casefile.application.workflow_brief_validation import (
    require_confirmed_atomics as _require_confirmed_atomics,
)
from casefile.application.workflow_brief_validation import validate_brief as _validate_brief
from casefile.application.workflow_common import (
    DEFAULT_BUDGET,
    DEFAULT_PROVIDER,
    _append_event,
    _chat_context_policy_version,
    _event_view,
    _json_hash,
    _provider_view,
    _source_view,
    _supported_provider,
    _task_view,
    _text_hash,
)
from casefile.application.workflow_views import brief_version_view as _brief_version_view
from casefile.application.workflow_views import brief_view as _brief_view
from casefile.contracts import CASEFILE_SCHEMA_VERSION
from casefile.data_postgres.models import (
    Brief,
    BriefVersion,
    SourceRecord,
    TaskEvent,
    TaskRun,
    UserProviderSetting,
)
from casefile.data_postgres.repositories import OwnedDraft, ProjectRepository


class ContentWorkflowMixin:
    session: Session
    projects: ProjectRepository

    def get_provider_setting(
        self,
        actor_user_id: int,
        provider: str = DEFAULT_PROVIDER,
    ) -> dict[str, Any] | None:
        provider = _supported_provider(provider)
        with self.session.begin():
            setting = self.session.scalar(
                select(UserProviderSetting).where(
                    UserProviderSetting.user_id == actor_user_id,
                    UserProviderSetting.provider == provider,
                )
            )
            return (
                None
                if setting is None or setting.credential_status == "deleted"
                else _provider_view(setting)
            )

    def save_provider_setting(
        self,
        actor_user_id: int,
        *,
        provider: str = DEFAULT_PROVIDER,
        api_key: str,
        model_id: str,
        model_is_custom: bool,
    ) -> dict[str, Any]:
        provider = _supported_provider(provider)
        encrypted = encrypt_api_key(api_key, user_id=actor_user_id, provider=provider)
        with self.session.begin():
            setting = self.session.scalar(
                select(UserProviderSetting)
                .where(
                    UserProviderSetting.user_id == actor_user_id,
                    UserProviderSetting.provider == provider,
                )
                .with_for_update()
            )
            if setting is None:
                setting = UserProviderSetting(
                    user_id=actor_user_id,
                    provider=provider,
                    model_id=model_id,
                    model_is_custom=model_is_custom,
                    config_version=1,
                    secret_ciphertext=encrypted.ciphertext,
                    secret_nonce=encrypted.nonce,
                    key_version=encrypted.key_version,
                    secret_last_four=encrypted.last_four,
                    credential_status="unverified",
                    default_budget_jsonb=dict(DEFAULT_BUDGET),
                )
                self.session.add(setting)
            else:
                setting.model_id = model_id
                setting.model_is_custom = model_is_custom
                setting.config_version += 1
                setting.secret_ciphertext = encrypted.ciphertext
                setting.secret_nonce = encrypted.nonce
                setting.key_version = encrypted.key_version
                setting.secret_last_four = encrypted.last_four
                setting.credential_status = "unverified"
                setting.validated_at = None
                setting.validation_error_code = None
                setting.credential_deleted_at = None
            self.session.flush()
            return _provider_view(setting)

    def delete_provider_setting(
        self,
        actor_user_id: int,
        provider: str = DEFAULT_PROVIDER,
    ) -> None:
        provider = _supported_provider(provider)
        with self.session.begin():
            setting = self.session.scalar(
                select(UserProviderSetting)
                .where(
                    UserProviderSetting.user_id == actor_user_id,
                    UserProviderSetting.provider == provider,
                )
                .with_for_update()
            )
            if setting is None or setting.credential_status == "deleted":
                return
            active_task_count = self.session.scalar(
                select(func.count())
                .select_from(TaskRun)
                .where(
                    TaskRun.provider_setting_id == setting.id,
                    TaskRun.status.in_(("queued", "running", "cancelling")),
                )
            )
            if active_task_count:
                raise ApplicationError(
                    "provider_credential_in_use",
                    "当前 API 密钥仍被执行中的任务使用，请等待任务结束后再删除。",
                    status_code=409,
                    details={"provider": provider, "active_task_count": active_task_count},
                )
            setting.config_version += 1
            setting.secret_ciphertext = None
            setting.secret_nonce = None
            setting.key_version = None
            setting.secret_last_four = None
            setting.credential_status = "deleted"
            setting.validated_at = None
            setting.validation_error_code = None
            setting.credential_deleted_at = datetime.now(UTC)
            self.session.flush()

    def list_sources(
        self,
        actor_user_id: int,
        project_id: int,
    ) -> list[dict[str, Any]]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            rows = self.session.scalars(
                select(SourceRecord)
                .where(SourceRecord.project_id == owned.project.id)
                .order_by(SourceRecord.created_at, SourceRecord.id)
            )
            return [_source_view(row) for row in rows]

    def create_source(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        source_kind: str,
        content_text: str,
        parent_source_record_id: int | None,
    ) -> dict[str, Any]:
        if source_kind not in {"human_original", "human_revision"}:
            raise ApplicationError(
                "source_kind_not_writable",
                "只能通过接口创建作者撰写的来源记录。",
                status_code=422,
            )
        if not content_text.strip():
            raise ApplicationError(
                "source_content_blank",
                "来源记录内容不能为空。",
                status_code=422,
            )
        if source_kind == "human_revision" and parent_source_record_id is None:
            raise ApplicationError(
                "source_parent_required",
                "作者修订必须引用父来源记录。",
                status_code=422,
            )
        if source_kind == "human_original" and parent_source_record_id is not None:
            raise ApplicationError(
                "source_parent_forbidden",
                "原始来源记录不能引用父来源。",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            if parent_source_record_id is not None:
                parent = self.session.scalar(
                    select(SourceRecord).where(
                        SourceRecord.id == parent_source_record_id,
                        SourceRecord.project_id == owned.project.id,
                    )
                )
                if parent is None:
                    raise not_found("SourceRecord parent")
            source = SourceRecord(
                project_id=owned.project.id,
                source_kind=source_kind,
                content_text=content_text,
                content_hash=_text_hash(content_text),
                parent_source_record_id=parent_source_record_id,
                generated_by_task_run_id=None,
                created_by_user_id=actor_user_id,
            )
            self.session.add(source)
            self.session.flush()
            return _source_view(source)

    def get_brief(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            brief = self._brief(owned)
            view = _brief_view(brief)
            if brief.current_version_id is not None:
                version = self.session.get(BriefVersion, brief.current_version_id)
                view["current_version_no"] = version.version_no if version else None
            return view

    def update_brief(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        expected_revision: int,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        validated = _validate_brief(content)
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            brief = self._brief(owned, lock=True)
            if brief.draft_revision != expected_revision:
                raise ApplicationError(
                    "brief_revision_conflict",
                    "创作简报草稿版本已过期，请刷新后重试。",
                    status_code=409,
                    details={
                        "current_revision": brief.draft_revision,
                        "received_revision": expected_revision,
                    },
                )
            self._validate_brief_sources(owned, validated)
            if brief.draft_jsonb == validated:
                return _brief_view(brief)
            brief.draft_jsonb = validated
            brief.draft_revision += 1
            brief.current_version_id = None
            self.session.flush()
            return _brief_view(brief)

    def confirm_brief(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            brief = self._brief(owned, lock=True)
            if brief.draft_revision != expected_revision:
                raise ApplicationError(
                    "brief_revision_conflict",
                    "创作简报草稿版本已过期，请刷新后重试。",
                    status_code=409,
                    details={"current_revision": brief.draft_revision},
                )
            content = _validate_brief(brief.draft_jsonb)
            self._validate_brief_sources(owned, content)
            _require_confirmed_atomics(content)
            if brief.current_version_id is not None:
                existing = self.session.get(BriefVersion, brief.current_version_id)
                if existing is not None and existing.content_hash == _json_hash(content):
                    # 幂等确认：同一份草稿重复冻结时返回已有版本，不再递增版本号。
                    return _brief_version_view(existing, brief.public_id)
                raise ApplicationError(
                    "brief_already_confirmed_content_changed",
                    "创作简报已经冻结；修改内容请先建立简报修订。",
                    status_code=409,
                )
            next_version = int(
                self.session.scalar(
                    select(func.coalesce(func.max(BriefVersion.version_no), 0) + 1).where(
                        BriefVersion.brief_id == brief.id
                    )
                )
                or 1
            )
            version = BriefVersion(
                project_id=owned.project.id,
                brief_id=brief.id,
                version_no=next_version,
                content_jsonb=content,
                content_hash=_json_hash(content),
                confirmed_by_user_id=actor_user_id,
            )
            self.session.add(version)
            self.session.flush()
            brief.current_version_id = version.id
            self.session.flush()
            return _brief_version_view(version, brief.public_id)

    def create_strategy_options_task(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        brief_version_id: int,
        provider: str = DEFAULT_PROVIDER,
        refresh: bool = False,
    ) -> dict[str, Any]:
        provider = _supported_provider(provider)
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            brief = self._brief(owned, lock=True)
            version = self.session.scalar(
                select(BriefVersion).where(
                    BriefVersion.id == brief_version_id,
                    BriefVersion.project_id == owned.project.id,
                    BriefVersion.brief_id == brief.id,
                )
            )
            if version is None:
                raise not_found("BriefVersion")
            if brief.current_version_id != version.id:
                raise ApplicationError(
                    "brief_version_not_current",
                    "策略分析需要使用当前已确认的创作简报版本。",
                    status_code=409,
                    details={"current_version_id": brief.current_version_id},
                )
            content = _validate_brief(version.content_jsonb)
            self._validate_brief_sources(owned, content)
            _require_confirmed_atomics(content)
            if not refresh:
                existing = self.session.scalar(
                    select(TaskRun)
                    .where(
                        TaskRun.project_id == owned.project.id,
                        TaskRun.task_type == "brief_strategy_options",
                        TaskRun.brief_version_id == version.id,
                        TaskRun.input_hash == version.content_hash,
                        TaskRun.status.in_(("queued", "running", "succeeded")),
                    )
                    .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
                    .limit(1)
                )
                if existing is not None:
                    return _task_view(existing)
            setting = self._provider_setting(actor_user_id, provider)
            task = self._new_task(
                owned,
                actor_user_id=actor_user_id,
                setting=setting,
                task_type="brief_strategy_options",
                brief_version_id=version.id,
                input_source_record_id=None,
                input_brief_revision=brief.draft_revision,
                input_hash=version.content_hash,
                input_jsonb={
                    "brief": content,
                    "brief_public_id": brief.public_id,
                    "brief_version_no": version.version_no,
                    "strategy_version": CANDIDATE_STRATEGY_VERSION,
                },
            )
            return self._queue_task(
                task,
                message="冻结 Brief 的三策略分析任务已进入队列",
            )

    def create_generation_task(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        brief_version_id: int,
        expected_draft_id: int,
        expected_draft_revision: int,
        provider: str = DEFAULT_PROVIDER,
        candidate_strategy: str = CandidateStrategy.BALANCED.value,
        candidate_strategy_attempt: int = 1,
    ) -> dict[str, Any]:
        provider = _supported_provider(provider)
        try:
            strategy = CandidateStrategy(candidate_strategy)
        except ValueError as error:
            raise ApplicationError(
                "unsupported_candidate_strategy",
                "不支持的候选策略。",
                status_code=422,
                details={"candidate_strategy": candidate_strategy},
            ) from error
        if candidate_strategy_attempt < 1:
            raise ApplicationError(
                "candidate_strategy_attempt_invalid",
                "候选策略尝试序号必须大于等于 1。",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            if (
                owned.draft.id != expected_draft_id
                or owned.draft.revision != expected_draft_revision
            ):
                raise ApplicationError(
                    "draft_revision_conflict",
                    "当前工作稿已切换或更新，请刷新后重新提交。",
                    status_code=409,
                    details={
                        "current_draft_id": owned.draft.id,
                        "current_revision": owned.draft.revision,
                    },
                )
            if owned.draft.schema_version != CASEFILE_SCHEMA_VERSION:
                raise ApplicationError(
                    "draft_schema_upgrade_required",
                    "这份工作稿仍使用历史 CaseFile 契约；请先升级时间契约再重新生成。",
                    status_code=409,
                    details={
                        "draft_schema_version": owned.draft.schema_version,
                        "required_schema_version": CASEFILE_SCHEMA_VERSION,
                    },
                )
            brief = self._brief(owned, lock=True)
            version = self.session.scalar(
                select(BriefVersion).where(
                    BriefVersion.id == brief_version_id,
                    BriefVersion.project_id == owned.project.id,
                    BriefVersion.brief_id == brief.id,
                )
            )
            if version is None:
                raise not_found("BriefVersion")
            if brief.current_version_id != version.id:
                raise ApplicationError(
                    "brief_version_not_current",
                    "生成任务需要使用当前已确认的创作简报版本。",
                    status_code=409,
                    details={"current_version_id": brief.current_version_id},
                )
            content = _validate_brief(version.content_jsonb)
            self._validate_brief_sources(owned, content)
            _require_confirmed_atomics(content)
            if strategy != CandidateStrategy.BALANCED:
                self._ensure_candidate_strategy_available(
                    owned,
                    brief_version_id=version.id,
                    candidate_strategy=strategy,
                    candidate_strategy_attempt=candidate_strategy_attempt,
                )
            setting = self._provider_setting(actor_user_id, provider)
            task = self._new_task(
                owned,
                actor_user_id=actor_user_id,
                setting=setting,
                task_type="brief_to_draft",
                brief_version_id=version.id,
                input_source_record_id=None,
                input_brief_revision=brief.draft_revision,
                input_hash=version.content_hash,
                input_jsonb={
                    "brief": content,
                    "schema_version": owned.draft.schema_version,
                    "casefile_id": owned.casefile.object_id,
                    "brief_public_id": brief.public_id,
                    "brief_version_no": version.version_no,
                    "candidate_strategy": strategy.value,
                    "candidate_strategy_version": CANDIDATE_STRATEGY_VERSION,
                    "candidate_strategy_attempt": candidate_strategy_attempt,
                    "version": {
                        "version_id": owned.draft.version_id,
                        "version_no": owned.draft.version_no,
                        "parent_version_id": owned.draft.parent_version_id,
                    },
                },
            )
            return self._queue_task(
                task,
                message="Brief → Draft 任务已进入队列",
            )

    def _ensure_candidate_strategy_available(
        self,
        owned: OwnedDraft,
        *,
        brief_version_id: int,
        candidate_strategy: CandidateStrategy,
        candidate_strategy_attempt: int,
    ) -> None:
        tasks = self.session.scalars(
            select(TaskRun).where(
                TaskRun.project_id == owned.project.id,
                TaskRun.draft_id == owned.draft.id,
                TaskRun.task_type == "brief_to_draft",
                TaskRun.brief_version_id == brief_version_id,
                TaskRun.input_draft_revision == owned.draft.revision,
                TaskRun.status.in_(("queued", "running", "cancelling", "succeeded")),
            )
        )
        for task in tasks:
            raw_strategy = task.input_jsonb.get(
                "candidate_strategy",
                CandidateStrategy.BALANCED.value,
            )
            if raw_strategy != candidate_strategy.value:
                continue
            existing_attempt = int(task.input_jsonb.get("candidate_strategy_attempt", 1))
            if existing_attempt < candidate_strategy_attempt:
                continue
            raise ApplicationError(
                "candidate_strategy_exists",
                f"{CANDIDATE_STRATEGY_LABELS[candidate_strategy]}候选已存在或正在生成",
                status_code=409,
                details={
                    "candidate_strategy": candidate_strategy.value,
                    "task_run_id": task.id,
                },
            )

    def list_generation_candidates(
        self,
        actor_user_id: int,
        project_id: int,
    ) -> list[dict[str, Any]]:
        return DraftCandidateService(self.session).list_candidates(
            actor_user_id,
            project_id,
        )

    def get_generation_candidate(
        self,
        actor_user_id: int,
        project_id: int,
        task_run_id: int,
    ) -> dict[str, Any]:
        return DraftCandidateService(self.session).get_candidate(
            actor_user_id,
            project_id,
            task_run_id,
        )

    def adopt_generation_candidate(
        self,
        actor_user_id: int,
        project_id: int,
        task_run_id: int,
        *,
        expected_current_draft_id: int,
    ) -> dict[str, Any]:
        return DraftCandidateService(self.session).adopt_candidate(
            actor_user_id,
            project_id,
            task_run_id,
            expected_current_draft_id=expected_current_draft_id,
        )

    def create_polish_task(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        source_record_id: int,
        provider: str = DEFAULT_PROVIDER,
        polish_mode: str = "rewrite",
    ) -> dict[str, Any]:
        provider = _supported_provider(provider)
        if polish_mode not in {"proofread", "rewrite", "narrative_enhance"}:
            raise ApplicationError(
                "unsupported_polish_mode",
                "不支持的润色方式。",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            source = self.session.scalar(
                select(SourceRecord).where(
                    SourceRecord.id == source_record_id,
                    SourceRecord.project_id == owned.project.id,
                )
            )
            if source is None:
                raise not_found("SourceRecord")
            setting = self._provider_setting(actor_user_id, provider)
            task = self._new_task(
                owned,
                actor_user_id=actor_user_id,
                setting=setting,
                task_type="brief_polish",
                brief_version_id=None,
                input_source_record_id=source.id,
                input_brief_revision=None,
                input_hash=source.content_hash,
                input_jsonb={
                    "source_record_id": source.id,
                    "source_text": source.content_text,
                    "polish_mode": polish_mode,
                },
            )
            return self._queue_task(task, message="Agent 润色候选任务已进入队列")

    def create_anchor_extract_task(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        expected_brief_revision: int,
        provider: str = DEFAULT_PROVIDER,
        mode: str = "extract",
        content: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider = _supported_provider(provider)
        if mode not in {"extract", "suggest_author_answer"}:
            raise ApplicationError(
                "unsupported_anchor_extract_mode",
                "不支持的作者底牌生成方式。",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            brief = self._brief(owned, lock=True)
            if brief.draft_revision != expected_brief_revision:
                raise ApplicationError(
                    "brief_revision_conflict",
                    "创作简报草稿版本已过期，请刷新后重试。",
                    status_code=409,
                    details={
                        "current_revision": brief.draft_revision,
                        "received_revision": expected_brief_revision,
                    },
                )
            brief_content = _validate_brief(brief.draft_jsonb)
            content = _validate_brief(content) if content is not None else brief_content
            self._validate_brief_sources(owned, content)
            if mode == "extract" and not content["author_answer"] and not content["boundary_text"]:
                raise ApplicationError(
                    "brief_extraction_input_empty",
                    "请先填写作者底牌或创作边界，再进行拆解。",
                    status_code=422,
                )
            setting = self._provider_setting(actor_user_id, provider)
            input_hash = _json_hash(content)
            task = self._new_task(
                owned,
                actor_user_id=actor_user_id,
                setting=setting,
                task_type="brief_anchor_extract",
                brief_version_id=None,
                input_source_record_id=None,
                input_brief_revision=brief.draft_revision,
                input_hash=input_hash,
                input_jsonb={"brief": content, "mode": mode},
            )
            message = (
                "作者底牌候选生成任务已进入队列"
                if mode == "suggest_author_answer"
                else "作者底牌与创作边界拆解任务已进入队列"
            )
            return self._queue_task(task, message=message)

    def get_latest_task(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        task_type: str,
    ) -> dict[str, Any] | None:
        if task_type not in {
            "brief_polish",
            "brief_anchor_extract",
            "brief_intake_questions",
            "brief_intake_synthesize",
            "brief_strategy_options",
            "brief_to_draft",
            "casefile_chat",
        }:
            raise ApplicationError(
                "task_type_not_supported",
                "不支持当前任务类型。",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            task = self.session.scalar(
                select(TaskRun)
                .where(
                    TaskRun.project_id == owned.project.id,
                    TaskRun.task_type == task_type,
                )
                .order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
                .limit(1)
            )
            return None if task is None else _task_view(task)

    def get_task(self, actor_user_id: int, project_id: int, task_run_id: int) -> dict[str, Any]:
        with self.session.begin():
            task = self._task(actor_user_id, project_id, task_run_id)
            return _task_view(task)

    def require_generic_task_type(
        self,
        actor_user_id: int,
        project_id: int,
        task_type: str,
    ) -> None:
        with self.session.begin():
            self._owned(actor_user_id, project_id)
            if task_type == "casefile_chat":
                _reject_casefile_chat_task_route()

    def require_generic_task_access(
        self,
        actor_user_id: int,
        project_id: int,
        task_run_id: int,
    ) -> None:
        with self.session.begin():
            task = self._task(actor_user_id, project_id, task_run_id)
            if task.task_type == "casefile_chat":
                _reject_casefile_chat_task_route()

    def cancel_task(
        self,
        actor_user_id: int,
        project_id: int,
        task_run_id: int,
    ) -> dict[str, Any]:
        """Request cooperative cancellation without discarding frozen task history."""

        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            task = self.session.scalar(
                select(TaskRun)
                .where(
                    TaskRun.id == task_run_id,
                    TaskRun.project_id == owned.project.id,
                )
                .with_for_update()
            )
            if task is None:
                raise not_found("TaskRun")
            if task.status in TERMINAL_TASK_STATUSES:
                return _task_view(task)

            now = datetime.now(UTC)
            if task.cancel_requested_at is None:
                task.cancel_requested_at = now
            if task.status == "queued":
                finalize_task_cancellation(self.session, task, now=now)
                _append_event(
                    self.session,
                    task,
                    "task.cancelled",
                    "cancelled",
                    {"message": "任务已取消，尚未开始生成。"},
                )
            elif task.status == "running":
                task.status = "cancelling"
                task.stage = "cancelling"
                _append_event(
                    self.session,
                    task,
                    "task.cancel_requested",
                    "cancelling",
                    {"message": "已请求停止任务，正在安全结束当前步骤。"},
                )
            return _task_view(task)

    def resume_generation_task(
        self,
        actor_user_id: int,
        project_id: int,
        task_run_id: int,
        *,
        expected_draft_id: int,
        expected_draft_revision: int,
        expected_brief_revision: int,
    ) -> dict[str, Any]:
        """Queue a new attempt on one failed v8 generation TaskRun."""

        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            task = self.session.scalar(
                select(TaskRun)
                .where(
                    TaskRun.id == task_run_id,
                    TaskRun.project_id == owned.project.id,
                )
                .with_for_update()
            )
            if task is None:
                raise not_found("TaskRun")
            if (
                task.task_type != "brief_to_draft"
                or task.prompt_version not in COMPONENT_GENERATION_PROMPT_VERSIONS
            ):
                raise ApplicationError(
                    "task_resume_not_supported",
                    "只有部件化深稿生成任务支持从失败阶段恢复。",
                    status_code=409,
                )
            if task.status != "failed":
                raise ApplicationError(
                    "task_resume_status_invalid",
                    "只有失败的深稿生成任务可以恢复。",
                    status_code=409,
                )
            brief = self._brief(owned, lock=True)
            if (
                owned.draft.id != expected_draft_id
                or task.draft_id != expected_draft_id
                or owned.draft.revision != expected_draft_revision
                or task.input_draft_revision != expected_draft_revision
            ):
                raise ApplicationError(
                    "task_resume_draft_stale",
                    "工作稿已更新，请重新生成整份候选。",
                    status_code=409,
                    details={"current_revision": owned.draft.revision},
                )
            if (
                brief.draft_revision != expected_brief_revision
                or task.input_brief_revision != expected_brief_revision
                or brief.current_version_id != task.brief_version_id
            ):
                raise ApplicationError(
                    "task_resume_brief_stale",
                    "创作简报已更新，请重新生成整份候选。",
                    status_code=409,
                    details={"current_revision": brief.draft_revision},
                )
            task.status = "queued"
            task.stage = "queued"
            task.completed_at = None
            task.error_code = None
            task.error_details_jsonb = {}
            task.leased_by = None
            task.lease_expires_at = None
            task.cancel_requested_at = None
            _append_event(
                self.session,
                task,
                "task.resumed",
                "queued",
                {
                    "message": "已恢复任务，将复用输入与上游哈希完全一致的成功部件。",
                    "previous_attempt_count": task.attempt_count,
                },
            )
            return _task_view(task)

    def list_task_events(
        self,
        actor_user_id: int,
        project_id: int,
        task_run_id: int,
        *,
        after_sequence: int = 0,
    ) -> list[dict[str, Any]]:
        with self.session.begin():
            task = self._task(actor_user_id, project_id, task_run_id)
            rows = self.session.scalars(
                select(TaskEvent)
                .where(
                    TaskEvent.task_run_id == task.id,
                    TaskEvent.sequence_no > after_sequence,
                )
                .order_by(TaskEvent.sequence_no)
            )
            return [_event_view(row) for row in rows]

    def _provider_setting(
        self,
        actor_user_id: int,
        provider: str,
    ) -> UserProviderSetting:
        setting = self.session.scalar(
            select(UserProviderSetting)
            .where(
                UserProviderSetting.user_id == actor_user_id,
                UserProviderSetting.provider == provider,
            )
            .with_for_update()
        )
        if setting is None or setting.credential_status == "deleted":
            raise ApplicationError(
                "provider_setting_required",
                f"开始任务前请先配置 {provider} API 密钥。",
                status_code=409,
                details={"provider": provider},
            )
        return setting

    def _new_task(
        self,
        owned: OwnedDraft,
        *,
        actor_user_id: int,
        setting: UserProviderSetting,
        task_type: str,
        brief_version_id: int | None,
        input_source_record_id: int | None,
        input_brief_revision: int | None,
        input_hash: str,
        input_jsonb: dict[str, Any],
        agent_thread_id: int | None = None,
        input_message_id: int | None = None,
        output_message_id: int | None = None,
    ) -> TaskRun:
        prompt_version = prompt_version_for_task(task_type)
        policy_version: str | None = None
        if task_type == "casefile_chat":
            policy_version = _chat_context_policy_version()
            if policy_version == CHAT_CONTEXT_POLICY_VERSION:
                prompt_version = CHAT_CONTEXT_PROMPT_VERSION
            elif policy_version == CHAT_CONTEXT_POLICY_V2_VERSION:
                prompt_version = CHAT_CONTEXT_PROMPT_V2_VERSION
            elif policy_version == CHAT_CONTEXT_POLICY_V3_VERSION:
                prompt_version = CHAT_CONTEXT_PROMPT_V4_VERSION
            elif policy_version == CHAT_CONTEXT_POLICY_V4_VERSION:
                prompt_version = CHAT_CONTEXT_PROMPT_V5_VERSION
            elif policy_version == CHAT_CONTEXT_POLICY_V5_VERSION:
                prompt_version = CHAT_CONTEXT_PROMPT_V6_VERSION
            elif policy_version == CHAT_CONTEXT_POLICY_V6_VERSION:
                prompt_version = CHAT_CONTEXT_PROMPT_V9_VERSION
            else:
                prompt_version = "casefile-chat-v3"
            rollout_prompt = os.environ.get("CASEFILE_CHAT_PROMPT_ROLLOUT", "").strip()
            if rollout_prompt in {
                "casefile-chat-v13",
                "casefile-chat-v14",
                "casefile-chat-v15",
            }:
                # Explicit gray entry only; registry/current default remains unchanged.
                prompt_version = rollout_prompt
        return TaskRun(
            project_id=owned.project.id,
            casefile_id=owned.casefile.id,
            draft_id=owned.draft.id,
            brief_version_id=brief_version_id,
            input_source_record_id=input_source_record_id,
            input_brief_revision=input_brief_revision,
            brief_intake_id=None,
            input_brief_intake_revision=None,
            base_brief_intake_candidate_id=None,
            agent_thread_id=agent_thread_id,
            input_message_id=input_message_id,
            output_message_id=output_message_id,
            input_hash=input_hash,
            input_jsonb=input_jsonb,
            actor_user_id=actor_user_id,
            provider_setting_id=setting.id,
            task_type=task_type,
            status="queued",
            stage="queued",
            input_draft_revision=owned.draft.revision,
            provider=setting.provider,
            model_id=setting.model_id,
            provider_config_version=setting.config_version,
            schema_version=CASEFILE_SCHEMA_VERSION,
            agent_version=agent_version_for_task(task_type, prompt_version),
            prompt_version=prompt_version,
            toolset_version=(
                CHAT_TOOLSET_V4_VERSION
                if task_type == "casefile_chat"
                and policy_version
                in {
                    CHAT_CONTEXT_POLICY_V4_VERSION,
                    CHAT_CONTEXT_POLICY_V5_VERSION,
                    CHAT_CONTEXT_POLICY_V6_VERSION,
                }
                else (
                    CHAT_TOOLSET_V3_VERSION
                    if task_type == "casefile_chat"
                    and policy_version == CHAT_CONTEXT_POLICY_V3_VERSION
                    else (CHAT_TOOLSET_VERSION if task_type == "casefile_chat" else TOOLSET_VERSION)
                )
            ),
            budget_jsonb=dict(setting.default_budget_jsonb),
            usage_jsonb={},
            attempt_count=0,
            result_jsonb=None,
            error_details_jsonb={},
        )

    def _queue_task(self, task: TaskRun, *, message: str) -> dict[str, Any]:
        self.session.add(task)
        self.session.flush()
        _append_event(
            self.session,
            task,
            "task.queued",
            "queued",
            {
                "message": message,
                "task_type": task.task_type,
                "model_id": task.model_id,
                "input_hash": task.input_hash,
            },
        )
        return _task_view(task)

    def _validate_brief_sources(
        self,
        owned: OwnedDraft,
        content: dict[str, Any],
    ) -> None:
        source_ids = list(content["source_record_ids"])
        rows = set(
            self.session.scalars(
                select(SourceRecord.id).where(
                    SourceRecord.project_id == owned.project.id,
                    SourceRecord.id.in_(source_ids),
                )
            )
        )
        missing = sorted(set(source_ids) - rows)
        if missing:
            raise ApplicationError(
                "brief_source_invalid",
                "创作简报的每条来源都必须属于当前项目。",
                status_code=422,
                details={"missing_source_record_ids": missing},
            )

    def _owned(self, actor_user_id: int, project_id: int, *, lock: bool = False) -> OwnedDraft:
        owned = self.projects.get_owned(actor_user_id, project_id, lock=lock)
        if owned is None:
            raise not_found("Project")
        return owned

    @staticmethod
    def _require_current_draft(
        owned: OwnedDraft,
        *,
        expected_draft_id: int,
        expected_draft_revision: int,
    ) -> None:
        if owned.draft.id == expected_draft_id and owned.draft.revision == expected_draft_revision:
            return
        raise ApplicationError(
            "draft_revision_conflict",
            "当前工作稿已切换或更新，请刷新后重新提交。",
            status_code=409,
            details={
                "current_draft_id": owned.draft.id,
                "current_revision": owned.draft.revision,
            },
        )

    def _brief(self, owned: OwnedDraft, *, lock: bool = False) -> Brief:
        statement = select(Brief).where(Brief.project_id == owned.project.id)
        if lock:
            statement = statement.with_for_update()
        brief = self.session.scalar(statement)
        if brief is None:
            raise not_found("Brief")
        return brief

    def _task(self, actor_user_id: int, project_id: int, task_run_id: int) -> TaskRun:
        owned = self._owned(actor_user_id, project_id)
        task = self.session.scalar(
            select(TaskRun).where(
                TaskRun.id == task_run_id,
                TaskRun.project_id == owned.project.id,
            )
        )
        if task is None:
            raise not_found("TaskRun")
        return task


def _reject_casefile_chat_task_route() -> None:
    raise ApplicationError(
        "casefile_chat_public_route_required",
        "对话任务只能通过 Agent Run 接口访问。",
        status_code=409,
    )


__all__ = ["ContentWorkflowMixin"]
