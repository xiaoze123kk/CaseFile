"""Sources, versioned Briefs, provider settings, and durable Agent TaskRuns."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import rfc8785
from casefile_contracts import Brief as BriefContract
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.agent_runtime.credentials import encrypt_api_key
from casefile.agent_runtime.prompt import AGENT_VERSION, prompt_version_for_task
from casefile.agent_runtime.tools import TOOLSET_VERSION
from casefile.application.errors import ApplicationError, not_found
from casefile.contracts import CASEFILE_SCHEMA_VERSION
from casefile.data_postgres.models import (
    Brief,
    BriefVersion,
    CaseFileObject,
    SourceRecord,
    TaskEvent,
    TaskRun,
    UserProviderSetting,
)
from casefile.data_postgres.repositories import OwnedDraft, ProjectRepository

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-5.6-sol"
SUPPORTED_PROVIDERS = frozenset({"deepseek", "openai"})
DEFAULT_BUDGET: dict[str, Any] = {
    "max_turns": 12,
    "network_retries": 2,
    "structural_repair_attempts": 2,
}


class WorkflowService:
    """Transactional facade for the user-visible Agent generation workflow."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

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
            return None if setting is None else _provider_view(setting)

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
            self.session.flush()
            return _provider_view(setting)

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
                "Only human-authored SourceRecord kinds can be created through the API",
                status_code=422,
            )
        if not content_text.strip():
            raise ApplicationError(
                "source_content_blank",
                "SourceRecord content must not be blank",
                status_code=422,
            )
        if source_kind == "human_revision" and parent_source_record_id is None:
            raise ApplicationError(
                "source_parent_required",
                "A human revision must reference its parent SourceRecord",
                status_code=422,
            )
        if source_kind == "human_original" and parent_source_record_id is not None:
            raise ApplicationError(
                "source_parent_forbidden",
                "An original SourceRecord cannot reference a parent",
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
            return _brief_view(brief)

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
                    "Brief draft revision is stale",
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
                    "Brief draft revision is stale",
                    status_code=409,
                    details={"current_revision": brief.draft_revision},
                )
            content = _validate_brief(brief.draft_jsonb)
            self._validate_brief_sources(owned, content)
            _require_confirmed_atomics(content)
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

    def create_generation_task(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        brief_version_id: int,
        expected_draft_revision: int,
        provider: str = DEFAULT_PROVIDER,
    ) -> dict[str, Any]:
        provider = _supported_provider(provider)
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            if owned.draft.revision != expected_draft_revision:
                raise ApplicationError(
                    "draft_revision_conflict",
                    "Draft revision is stale",
                    status_code=409,
                    details={"current_revision": owned.draft.revision},
                )
            object_count = self.session.scalar(
                select(func.count(CaseFileObject.id)).where(
                    CaseFileObject.draft_id == owned.draft.id,
                    CaseFileObject.deleted_at.is_(None),
                )
            )
            if object_count:
                raise ApplicationError(
                    "draft_not_empty",
                    "Full CaseFile generation requires a strictly empty Draft",
                    status_code=409,
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
                    "Generation requires the current user-confirmed Brief version",
                    status_code=409,
                    details={"current_version_id": brief.current_version_id},
                )
            content = _validate_brief(version.content_jsonb)
            self._validate_brief_sources(owned, content)
            _require_confirmed_atomics(content)
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
                    "brief_public_id": brief.public_id,
                    "brief_version_no": version.version_no,
                },
            )
            return self._queue_task(
                task,
                message="Brief → Draft 任务已进入队列",
            )

    def create_polish_task(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        source_record_id: int,
        provider: str = DEFAULT_PROVIDER,
    ) -> dict[str, Any]:
        provider = _supported_provider(provider)
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
    ) -> dict[str, Any]:
        provider = _supported_provider(provider)
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            brief = self._brief(owned, lock=True)
            if brief.draft_revision != expected_brief_revision:
                raise ApplicationError(
                    "brief_revision_conflict",
                    "Brief draft revision is stale",
                    status_code=409,
                    details={
                        "current_revision": brief.draft_revision,
                        "received_revision": expected_brief_revision,
                    },
                )
            content = _validate_brief(brief.draft_jsonb)
            self._validate_brief_sources(owned, content)
            if not content["author_answer"] and not content["boundary_text"]:
                raise ApplicationError(
                    "brief_extraction_input_empty",
                    "Author answer or creative boundary is required for extraction",
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
                input_jsonb={"brief": content},
            )
            return self._queue_task(task, message="作者底牌与创作边界拆解任务已进入队列")

    def get_latest_task(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        task_type: str,
    ) -> dict[str, Any] | None:
        if task_type not in {"brief_polish", "brief_anchor_extract", "brief_to_draft"}:
            raise ApplicationError(
                "task_type_not_supported",
                f"Task type is not supported: {task_type}",
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
            select(UserProviderSetting).where(
                UserProviderSetting.user_id == actor_user_id,
                UserProviderSetting.provider == provider,
            )
        )
        if setting is None:
            raise ApplicationError(
                "provider_setting_required",
                f"Configure a {provider} API key before starting the task",
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
    ) -> TaskRun:
        return TaskRun(
            project_id=owned.project.id,
            casefile_id=owned.casefile.id,
            draft_id=owned.draft.id,
            brief_version_id=brief_version_id,
            input_source_record_id=input_source_record_id,
            input_brief_revision=input_brief_revision,
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
            agent_version=AGENT_VERSION,
            prompt_version=prompt_version_for_task(task_type),
            toolset_version=TOOLSET_VERSION,
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
                "Every Brief source must belong to the current Project",
                status_code=422,
                details={"missing_source_record_ids": missing},
            )

    def _owned(self, actor_user_id: int, project_id: int, *, lock: bool = False) -> OwnedDraft:
        owned = self.projects.get_owned(actor_user_id, project_id, lock=lock)
        if owned is None:
            raise not_found("Project")
        return owned

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


def append_task_event(
    session: Session,
    task: TaskRun,
    event_type: str,
    stage: str,
    payload: dict[str, Any],
) -> TaskEvent:
    """Append one replayable event; callers must own the surrounding transaction."""

    return _append_event(session, task, event_type, stage, payload)


def task_view(task: TaskRun) -> dict[str, Any]:
    return _task_view(task)


def source_view(source: SourceRecord) -> dict[str, Any]:
    return _source_view(source)


def event_view(event: TaskEvent) -> dict[str, Any]:
    return _event_view(event)


def _append_event(
    session: Session,
    task: TaskRun,
    event_type: str,
    stage: str,
    payload: dict[str, Any],
) -> TaskEvent:
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


def _validate_brief(content: dict[str, Any]) -> dict[str, Any]:
    try:
        model = BriefContract.model_validate(content)
    except ValidationError as error:
        raise ApplicationError(
            "brief_invalid",
            "Brief does not satisfy the v1 contract",
            status_code=422,
            details={"issues": error.errors(include_url=False)},
        ) from error
    validated = model.model_dump(mode="json", exclude_none=False)
    _validate_brief_semantics(validated)
    return validated


def _validate_brief_semantics(content: dict[str, Any]) -> None:
    text_fields = ("creative_intent", "reasoning_proposition")
    if any(not str(content[field]).strip() for field in text_fields):
        raise ApplicationError(
            "brief_invalid",
            "Brief intent and reasoning proposition must not be blank",
            status_code=422,
        )
    for field in ("author_answer", "boundary_text"):
        value = content[field]
        if value is not None and not str(value).strip():
            raise ApplicationError(
                "brief_invalid",
                f"{field} must be null or non-blank",
                status_code=422,
            )
    source_record_ids = content["source_record_ids"]
    if len(source_record_ids) != len(set(source_record_ids)):
        raise ApplicationError(
            "brief_source_record_duplicate",
            "Brief source record references must be unique",
            status_code=422,
        )
    resolution_mode = content["resolution_mode"]
    if resolution_mode == "author_anchored":
        if content["author_answer"] is None:
            raise ApplicationError(
                "brief_author_answer_required",
                "Author-anchored resolution mode requires an author answer",
                status_code=422,
            )
    elif content["author_answer"] is not None or content["author_anchors"]:
        raise ApplicationError(
            "brief_resolution_mode_conflict",
            "Non-anchored resolution modes cannot contain an author answer or anchors",
            status_code=422,
        )
    anchor_ids = [item["anchor_id"] for item in content["author_anchors"]]
    constraint_ids = [
        item["constraint_id"] for item in content["creative_constraints"]
    ]
    if len(anchor_ids) != len(set(anchor_ids)) or len(constraint_ids) != len(
        set(constraint_ids)
    ):
        raise ApplicationError(
            "brief_atomic_id_duplicate",
            "Brief atomic IDs must be unique within their collection",
            status_code=422,
        )
    statements = [
        *(item["statement"] for item in content["author_anchors"]),
        *(item["statement"] for item in content["creative_constraints"]),
    ]
    if any(not str(statement).strip() for statement in statements):
        raise ApplicationError(
            "brief_atomic_statement_blank",
            "Brief atomic statements must not be blank",
            status_code=422,
        )


def _require_confirmed_atomics(content: dict[str, Any]) -> None:
    if content["author_answer"] and not content["author_anchors"]:
        raise ApplicationError(
            "brief_author_anchors_required",
            "Author answer must be decomposed into at least one confirmed atomic anchor",
            status_code=422,
        )
    if content["boundary_text"] and not content["creative_constraints"]:
        raise ApplicationError(
            "brief_creative_constraints_required",
            "Creative boundary must be decomposed into at least one confirmed atomic constraint",
            status_code=422,
        )


def _json_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _provider_view(setting: UserProviderSetting) -> dict[str, Any]:
    return {
        "provider": setting.provider,
        "model_id": setting.model_id,
        "model_is_custom": setting.model_is_custom,
        "config_version": setting.config_version,
        "credential_status": setting.credential_status,
        "masked_api_key": f"••••••••{setting.secret_last_four}",
        "validated_at": _time(setting.validated_at),
        "validation_error_code": setting.validation_error_code,
        "default_budget": setting.default_budget_jsonb,
    }


def _supported_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ApplicationError(
            "provider_not_supported",
            f"Provider is not supported: {provider}",
            status_code=422,
            details={"supported_providers": sorted(SUPPORTED_PROVIDERS)},
        )
    return normalized


def _brief_view(brief: Brief) -> dict[str, Any]:
    return {
        "brief_id": brief.id,
        "public_id": brief.public_id,
        "draft_revision": brief.draft_revision,
        "content": brief.draft_jsonb,
        "current_version_id": brief.current_version_id,
        "updated_at": _time(brief.updated_at),
    }


def _brief_version_view(version: BriefVersion, public_id: str) -> dict[str, Any]:
    return {
        "brief_version_id": version.id,
        "brief_id": version.brief_id,
        "public_id": public_id,
        "version_no": version.version_no,
        "content": version.content_jsonb,
        "content_hash": version.content_hash,
        "confirmed_at": _time(version.confirmed_at),
    }


def _source_view(source: SourceRecord) -> dict[str, Any]:
    return {
        "source_record_id": source.id,
        "source_kind": source.source_kind,
        "content_text": source.content_text,
        "content_hash": source.content_hash,
        "parent_source_record_id": source.parent_source_record_id,
        "generated_by_task_run_id": source.generated_by_task_run_id,
        "created_at": _time(source.created_at),
    }


def _task_view(task: TaskRun) -> dict[str, Any]:
    return {
        "task_run_id": task.id,
        "project_id": task.project_id,
        "task_type": task.task_type,
        "status": task.status,
        "stage": task.stage,
        "provider": task.provider,
        "model_id": task.model_id,
        "input_draft_revision": task.input_draft_revision,
        "input_brief_revision": task.input_brief_revision,
        "input_source_record_id": task.input_source_record_id,
        "input_hash": task.input_hash,
        "attempt_count": task.attempt_count,
        "usage": task.usage_jsonb,
        "result_snapshot_id": task.result_snapshot_id,
        "result": task.result_jsonb,
        "error_code": task.error_code,
        "created_at": _time(task.created_at),
        "updated_at": _time(task.updated_at),
    }


def _event_view(event: TaskEvent) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "task_run_id": event.task_run_id,
        "sequence_no": event.sequence_no,
        "event_type": event.event_type,
        "stage": event.stage,
        "payload": event.payload_jsonb,
        "occurred_at": _time(event.occurred_at),
    }


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


__all__ = [
    "DEFAULT_BUDGET",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "SUPPORTED_PROVIDERS",
    "WorkflowService",
    "append_task_event",
    "event_view",
    "source_view",
    "task_view",
]
