"""Provider settings, versioned Briefs, and durable generation TaskRuns."""

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
from casefile.agent_runtime.prompt import AGENT_VERSION, PROMPT_VERSION
from casefile.agent_runtime.tools import TOOLSET_VERSION
from casefile.application.errors import ApplicationError, not_found
from casefile.contracts import CASEFILE_SCHEMA_VERSION
from casefile.data_postgres.models import (
    Brief,
    BriefVersion,
    CaseFileObject,
    TaskEvent,
    TaskRun,
    UserProviderSetting,
)
from casefile.data_postgres.repositories import OwnedDraft, ProjectRepository

DEFAULT_PROVIDER = "openai"
DEFAULT_MODEL = "gpt-5.6-sol"
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

    def get_provider_setting(self, actor_user_id: int) -> dict[str, Any] | None:
        with self.session.begin():
            setting = self.session.scalar(
                select(UserProviderSetting).where(
                    UserProviderSetting.user_id == actor_user_id,
                    UserProviderSetting.provider == DEFAULT_PROVIDER,
                )
            )
            return None if setting is None else _provider_view(setting)

    def save_provider_setting(
        self,
        actor_user_id: int,
        *,
        api_key: str,
        model_id: str,
        model_is_custom: bool,
    ) -> dict[str, Any]:
        encrypted = encrypt_api_key(api_key, user_id=actor_user_id, provider=DEFAULT_PROVIDER)
        with self.session.begin():
            setting = self.session.scalar(
                select(UserProviderSetting)
                .where(
                    UserProviderSetting.user_id == actor_user_id,
                    UserProviderSetting.provider == DEFAULT_PROVIDER,
                )
                .with_for_update()
            )
            if setting is None:
                setting = UserProviderSetting(
                    user_id=actor_user_id,
                    provider=DEFAULT_PROVIDER,
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
            brief.draft_jsonb = validated
            brief.draft_revision += 1
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
    ) -> dict[str, Any]:
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
            version = self.session.scalar(
                select(BriefVersion).where(
                    BriefVersion.id == brief_version_id,
                    BriefVersion.project_id == owned.project.id,
                )
            )
            if version is None:
                raise not_found("BriefVersion")
            setting = self.session.scalar(
                select(UserProviderSetting).where(
                    UserProviderSetting.user_id == actor_user_id,
                    UserProviderSetting.provider == DEFAULT_PROVIDER,
                )
            )
            if setting is None:
                raise ApplicationError(
                    "provider_setting_required",
                    "Configure an OpenAI API key before starting generation",
                    status_code=409,
                )
            task = TaskRun(
                project_id=owned.project.id,
                casefile_id=owned.casefile.id,
                draft_id=owned.draft.id,
                brief_version_id=version.id,
                actor_user_id=actor_user_id,
                provider_setting_id=setting.id,
                task_type="brief_to_draft",
                status="queued",
                stage="queued",
                input_draft_revision=owned.draft.revision,
                provider=setting.provider,
                model_id=setting.model_id,
                provider_config_version=setting.config_version,
                schema_version=CASEFILE_SCHEMA_VERSION,
                agent_version=AGENT_VERSION,
                prompt_version=PROMPT_VERSION,
                toolset_version=TOOLSET_VERSION,
                budget_jsonb=setting.default_budget_jsonb,
                usage_jsonb={},
                attempt_count=0,
                error_details_jsonb={},
            )
            self.session.add(task)
            self.session.flush()
            _append_event(
                self.session,
                task,
                "task.queued",
                "queued",
                {"message": "生成任务已进入队列", "model_id": task.model_id},
            )
            return _task_view(task)

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
    return model.model_dump(mode="json", exclude_none=True)


def _json_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


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


def _task_view(task: TaskRun) -> dict[str, Any]:
    return {
        "task_run_id": task.id,
        "project_id": task.project_id,
        "task_type": task.task_type,
        "status": task.status,
        "stage": task.stage,
        "model_id": task.model_id,
        "input_draft_revision": task.input_draft_revision,
        "attempt_count": task.attempt_count,
        "usage": task.usage_jsonb,
        "result_snapshot_id": task.result_snapshot_id,
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
    "WorkflowService",
    "append_task_event",
    "event_view",
    "task_view",
]
