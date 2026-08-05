"""Sources, versioned Briefs, provider settings, and durable Agent TaskRuns."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import rfc8785
from casefile_contracts import Brief as BriefContract
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.agent_runtime.credentials import encrypt_api_key
from casefile.agent_runtime.prompt import AGENT_VERSION
from casefile.agent_runtime.prompt_repository import prompt_version_for_task
from casefile.agent_runtime.tools import TOOLSET_VERSION
from casefile.application.agent_collaboration import (
    auto_thread_title as _auto_thread_title,
)
from casefile.application.agent_collaboration import (
    find_frozen_object as _find_frozen_object,
)
from casefile.application.agent_collaboration import (
    frozen_object_ids as _frozen_object_ids,
)
from casefile.application.agent_collaboration import (
    frozen_pointer_value as _frozen_pointer_value,
)
from casefile.application.agent_collaboration import (
    nonblocking_validator_issues as _nonblocking_validator_issues,
)
from casefile.application.agent_collaboration import (
    pointer_top_field as _pointer_top_field,
)
from casefile.application.agent_collaboration import unique_strings as _unique_strings
from casefile.application.casefile_v1 import build_casefile_document
from casefile.application.draft_candidates import DraftCandidateService
from casefile.application.errors import ApplicationError, not_found
from casefile.application.v1_editing import EDITABLE_FIELDS, V1EditingService
from casefile.contracts import CASEFILE_SCHEMA_VERSION
from casefile.data_postgres.models import (
    AgentMessage,
    AgentPatchOperation,
    AgentPatchSet,
    AgentThread,
    Brief,
    BriefVersion,
    CaseFileObject,
    SourceRecord,
    TaskAttempt,
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

_FAILURE_MESSAGES = {
    "candidate_validation_failed": "模型输出未通过 CaseFile 结构校验，已停止写入 Draft。",
    "provider_connection_failed": "无法连接模型服务，网络重试已耗尽。",
    "provider_timeout": "模型服务响应超时，网络重试已耗尽。",
    "provider_rate_limited": "模型服务当前限流，请稍后重试。",
    "provider_authentication_failed": "模型服务认证失败，请检查 API Key 与模型权限。",
    "generation_failed": "Agent 生成失败，Draft 未被修改。",
}
_RETRYABLE_FAILURES = frozenset(
    {
        "candidate_validation_failed",
        "provider_connection_failed",
        "provider_timeout",
        "provider_rate_limited",
    }
)


class WorkflowService:
    """Transactional facade for the user-visible Agent generation workflow."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

    def list_agent_threads(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        query: str | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            statement = select(AgentThread).where(
                AgentThread.project_id == owned.project.id,
                AgentThread.draft_id == owned.draft.id,
            )
            if not include_archived:
                statement = statement.where(AgentThread.status == "active")
            normalized_query = None if query is None else query.strip()
            if normalized_query:
                statement = statement.where(
                    AgentThread.title.contains(normalized_query, autoescape=True)
                )
            rows = self.session.scalars(
                statement.order_by(
                    AgentThread.is_pinned.desc(),
                    AgentThread.updated_at.desc(),
                    AgentThread.id.desc(),
                )
            )
            return [_agent_thread_view(row) for row in rows]

    def create_agent_thread(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        title: str | None = None,
    ) -> dict[str, Any]:
        normalized_title = None if title is None else title.strip()
        if title is not None and not normalized_title:
            raise ApplicationError(
                "agent_thread_title_invalid",
                "Agent thread title cannot be blank",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            thread = AgentThread(
                project_id=owned.project.id,
                casefile_id=owned.casefile.id,
                draft_id=owned.draft.id,
                created_by_user_id=actor_user_id,
                title=normalized_title or "新对话",
                title_source="user" if normalized_title else "auto",
                is_pinned=False,
                status="active",
                archived_at=None,
                last_message_at=None,
            )
            self.session.add(thread)
            self.session.flush()
            return _agent_thread_view(thread)

    def update_agent_thread(
        self,
        actor_user_id: int,
        project_id: int,
        thread_id: int,
        *,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            thread = self._agent_thread(owned, thread_id, lock=True)
            if "title" in changes:
                title = changes["title"]
                normalized_title = "" if title is None else str(title).strip()
                if not normalized_title:
                    raise ApplicationError(
                        "agent_thread_title_invalid",
                        "Agent thread title cannot be blank",
                        status_code=422,
                    )
                thread.title = normalized_title
                thread.title_source = "user"
            if "is_pinned" in changes:
                thread.is_pinned = bool(changes["is_pinned"])
            if "archived" in changes:
                archived = bool(changes["archived"])
                if archived and thread.status != "archived":
                    active_task = self.session.scalar(
                        select(TaskRun.id)
                        .where(
                            TaskRun.agent_thread_id == thread.id,
                            TaskRun.status.in_(("queued", "running", "cancelling")),
                        )
                        .limit(1)
                    )
                    if active_task is not None:
                        raise ApplicationError(
                            "agent_thread_busy",
                            "An Agent task is still running in this thread",
                            status_code=409,
                            details={"task_run_id": active_task},
                        )
                    thread.status = "archived"
                    thread.archived_at = datetime.now(UTC)
                elif not archived and thread.status == "archived":
                    thread.status = "active"
                    thread.archived_at = None
            self.session.flush()
            return _agent_thread_view(thread)

    def list_agent_messages(
        self,
        actor_user_id: int,
        project_id: int,
        thread_id: int,
        *,
        after_sequence: int = 0,
    ) -> list[dict[str, Any]]:
        if after_sequence < 0:
            raise ApplicationError(
                "agent_message_cursor_invalid",
                "after_sequence must be a non-negative integer",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            thread = self._agent_thread(owned, thread_id)
            messages = list(
                self.session.scalars(
                    select(AgentMessage)
                    .where(
                        AgentMessage.thread_id == thread.id,
                        AgentMessage.sequence_no > after_sequence,
                    )
                    .order_by(AgentMessage.sequence_no)
                )
            )
            message_ids = [message.id for message in messages]
            tasks_by_output = {
                task.output_message_id: task
                for task in (
                    []
                    if not message_ids
                    else self.session.scalars(
                        select(TaskRun).where(TaskRun.output_message_id.in_(message_ids))
                    )
                )
                if task.output_message_id is not None
            }
            patch_sets_by_message = {
                patch.source_message_id: patch
                for patch in (
                    []
                    if not message_ids
                    else self.session.scalars(
                        select(AgentPatchSet).where(
                            AgentPatchSet.source_message_id.in_(message_ids)
                        )
                    )
                )
            }
            current_document = (
                None
                if not any(
                    patch.status == "applied" for patch in patch_sets_by_message.values()
                )
                else build_casefile_document(self.session, owned)
            )
            return [
                _agent_message_view(
                    message,
                    task=tasks_by_output.get(message.id),
                    patch_set=(
                        None
                        if (patch := patch_sets_by_message.get(message.id)) is None
                        else self._patch_set_view(
                            owned,
                            patch,
                            current_document=current_document,
                        )
                    ),
                )
                for message in messages
            ]

    def send_agent_message(
        self,
        actor_user_id: int,
        project_id: int,
        thread_id: int,
        *,
        content: str,
        provider: str = DEFAULT_PROVIDER,
    ) -> dict[str, Any]:
        provider = _supported_provider(provider)
        content = content.strip()
        if not content:
            raise ApplicationError(
                "agent_message_empty",
                "Agent message cannot be blank",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            thread = self._agent_thread(owned, thread_id, lock=True)
            if thread.status != "active":
                raise ApplicationError(
                    "agent_thread_archived",
                    "Archived Agent threads cannot accept new messages",
                    status_code=409,
                )
            active_task = self.session.scalar(
                select(TaskRun.id)
                .where(
                    TaskRun.agent_thread_id == thread.id,
                    TaskRun.status.in_(("queued", "running", "cancelling")),
                )
                .limit(1)
            )
            if active_task is not None:
                raise ApplicationError(
                    "agent_thread_busy",
                    "Wait for the current Agent response before sending another message",
                    status_code=409,
                    details={"task_run_id": active_task},
                )
            setting = self._provider_setting(actor_user_id, provider)
            history = list(
                self.session.scalars(
                    select(AgentMessage)
                    .where(
                        AgentMessage.thread_id == thread.id,
                        AgentMessage.status == "completed",
                    )
                    .order_by(AgentMessage.sequence_no)
                )
            )
            sequence_no = int(
                self.session.scalar(
                    select(func.coalesce(func.max(AgentMessage.sequence_no), 0) + 1).where(
                        AgentMessage.thread_id == thread.id
                    )
                )
                or 1
            )
            user_message = AgentMessage(
                project_id=owned.project.id,
                thread_id=thread.id,
                sequence_no=sequence_no,
                role="user",
                status="completed",
                content_text=content,
                created_by_user_id=actor_user_id,
            )
            assistant_message = AgentMessage(
                project_id=owned.project.id,
                thread_id=thread.id,
                sequence_no=sequence_no + 1,
                role="assistant",
                status="pending",
                content_text=None,
                created_by_user_id=None,
            )
            self.session.add_all([user_message, assistant_message])
            self.session.flush()

            if thread.title_source == "auto" and not any(
                message.role == "user" for message in history
            ):
                thread.title = _auto_thread_title(content)
            now = datetime.now(UTC)
            thread.last_message_at = now
            frozen_input = {
                "casefile": build_casefile_document(self.session, owned),
                "history": [
                    {
                        "role": message.role,
                        "content": message.content_text,
                    }
                    for message in history
                    if message.content_text is not None
                ],
                "message": content,
            }
            task = self._new_task(
                owned,
                actor_user_id=actor_user_id,
                setting=setting,
                task_type="casefile_chat",
                brief_version_id=None,
                input_source_record_id=None,
                input_brief_revision=None,
                input_hash=_json_hash(frozen_input),
                input_jsonb=frozen_input,
                agent_thread_id=thread.id,
                input_message_id=user_message.id,
                output_message_id=assistant_message.id,
            )
            task_result = self._queue_task(
                task,
                message="Agent 对话任务已进入队列",
            )
            return {
                "thread": _agent_thread_view(thread),
                "user_message": _agent_message_view(user_message),
                "assistant_message": _agent_message_view(
                    assistant_message,
                    task=task,
                ),
                "task": task_result,
            }

    def complete_chat_task(
        self,
        task_run_id: int,
        attempt_id: int,
        *,
        answer: str,
        referenced_object_ids: list[str],
        suggestions: list[dict[str, Any]],
        usage: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist one structured chat result; intended as the Worker completion hook."""

        answer = answer.strip()
        if not answer:
            raise RuntimeError("CaseFile chat answer cannot be blank")
        with self.session.begin():
            task = self.session.scalar(
                select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
            )
            attempt = self.session.scalar(
                select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
            )
            if task is None or attempt is None:
                raise RuntimeError("TaskRun or TaskAttempt disappeared")
            if task.task_type != "casefile_chat":
                raise RuntimeError("TaskRun is not a CaseFile chat task")
            if attempt.task_run_id != task.id or attempt.status != "running":
                raise RuntimeError("TaskAttempt does not own the chat completion")
            if task.status != "running":
                raise RuntimeError("CaseFile chat TaskRun is not running")
            if task.output_message_id is None or task.agent_thread_id is None:
                raise RuntimeError("CaseFile chat TaskRun has no message lineage")
            output_message = self.session.scalar(
                select(AgentMessage)
                .where(
                    AgentMessage.id == task.output_message_id,
                    AgentMessage.project_id == task.project_id,
                )
                .with_for_update()
            )
            thread = self.session.scalar(
                select(AgentThread)
                .where(
                    AgentThread.id == task.agent_thread_id,
                    AgentThread.project_id == task.project_id,
                )
                .with_for_update()
            )
            owned = self.projects.get_owned(
                task.actor_user_id,
                task.project_id,
                lock=True,
            )
            if output_message is None or thread is None or owned is None:
                raise RuntimeError("CaseFile chat aggregate disappeared")
            frozen_casefile = task.input_jsonb.get("casefile")
            if not isinstance(frozen_casefile, dict):
                raise RuntimeError("CaseFile chat TaskRun has no frozen CaseFile")

            registry_rows = list(
                self.session.scalars(
                    select(CaseFileObject).where(
                        CaseFileObject.draft_id == task.draft_id,
                    )
                )
            )
            registries = {row.object_id: row for row in registry_rows}
            referenced = _unique_strings(referenced_object_ids)
            frozen_object_ids = _frozen_object_ids(frozen_casefile)
            missing_references = sorted(set(referenced) - frozen_object_ids)
            if missing_references:
                raise RuntimeError(
                    f"Chat result references unknown objects: {missing_references}"
                )

            stale = owned.draft.revision != task.input_draft_revision
            patch_set: AgentPatchSet | None = None
            patch_operations: list[AgentPatchOperation] = []
            if suggestions:
                reasons = [
                    str(item.get("reason", "")).strip()
                    for item in suggestions
                    if str(item.get("reason", "")).strip()
                ]
                patch_set = AgentPatchSet(
                    project_id=task.project_id,
                    casefile_id=task.casefile_id,
                    draft_id=task.draft_id,
                    thread_id=thread.id,
                    source_message_id=output_message.id,
                    task_run_id=task.id,
                    base_draft_revision=task.input_draft_revision,
                    reason_summary=(
                        reasons[0]
                        if len(reasons) == 1
                        else f"Agent 建议修改 {len(suggestions)} 个字段"
                    ),
                    status="stale" if stale else "pending",
                    applied_operation_group_no=None,
                    applied_from_revision=None,
                    applied_to_revision=None,
                    applied_at=None,
                    undone_operation_group_no=None,
                    undone_to_revision=None,
                    undone_at=None,
                )
                self.session.add(patch_set)
                self.session.flush()
                for ordinal, suggestion in enumerate(suggestions, start=1):
                    object_id = str(suggestion["object_id"])
                    registry = registries.get(object_id)
                    if registry is None:
                        raise RuntimeError(
                            f"Chat suggestion targets unknown object: {object_id}"
                        )
                    field_path = str(suggestion["path"])
                    top_field = _pointer_top_field(field_path)
                    if top_field not in EDITABLE_FIELDS.get(registry.object_type, set()):
                        raise RuntimeError(
                            f"Chat suggestion targets a read-only field: "
                            f"{object_id}{field_path}"
                        )
                    frozen_object = _find_frozen_object(frozen_casefile, object_id)
                    old_value = _frozen_pointer_value(frozen_object, field_path)
                    reason = str(suggestion.get("reason", "")).strip()
                    if not reason:
                        raise RuntimeError("Chat suggestions require a reason")
                    patch_operation = AgentPatchOperation(
                        project_id=task.project_id,
                        casefile_id=task.casefile_id,
                        draft_id=task.draft_id,
                        patch_set_id=patch_set.id,
                        target_object_id=registry.id,
                        ordinal=ordinal,
                        operation_id=f"op_t{task.id}_{ordinal:02d}",
                        operation_type="replace",
                        field_path=field_path,
                        expected_object_revision=int(frozen_object["revision"]),
                        old_value_jsonb=old_value,
                        new_value_jsonb=deepcopy(suggestion.get("value")),
                        reason=reason,
                        decision="pending",
                        reviewed_at=None,
                    )
                    self.session.add(patch_operation)
                    patch_operations.append(patch_operation)

            now = datetime.now(UTC)
            result_payload = {
                "answer": answer,
                "referenced_object_ids": referenced,
                "patch_set_id": None if patch_set is None else patch_set.id,
                "stale": stale,
            }
            output_message.status = "completed"
            output_message.content_text = answer
            thread.last_message_at = now
            attempt.status = "succeeded"
            attempt.candidate_jsonb = {
                **result_payload,
                "suggestion_count": len(suggestions),
            }
            attempt.validation_errors_jsonb = []
            attempt.usage_jsonb = usage
            attempt.finished_at = now
            task.status = "succeeded"
            task.stage = "completed"
            task.usage_jsonb = usage
            task.result_jsonb = result_payload
            task.completed_at = now
            task.leased_by = None
            task.lease_expires_at = None
            _append_event(
                self.session,
                task,
                "task.succeeded",
                "completed",
                {
                    "message": "Agent 已完成卷宗分析",
                    "task_type": task.task_type,
                    "stale": stale,
                    "suggestion_count": len(suggestions),
                    "usage": usage,
                },
            )
            self.session.flush()
            return {
                "message": _agent_message_view(
                    output_message,
                    task=task,
                    patch_set=(
                        None
                        if patch_set is None
                        else self._patch_set_view(
                            owned,
                            patch_set,
                            operations=patch_operations,
                        )
                    ),
                ),
                "task": _task_view(task),
            }

    def apply_agent_patch_set(
        self,
        actor_user_id: int,
        project_id: int,
        patch_set_id: int,
        *,
        expected_revision: int,
        operation_ids: list[int] | None,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            patch_set = self._agent_patch_set(owned, patch_set_id, lock=True)
            if patch_set.status != "pending":
                raise ApplicationError(
                    "agent_patch_not_pending",
                    "Only pending Agent suggestions can be applied",
                    status_code=409,
                    details={"status": patch_set.status},
                )
            if (
                owned.draft.revision != expected_revision
                or owned.draft.revision != patch_set.base_draft_revision
            ):
                raise ApplicationError(
                    "agent_patch_stale",
                    "The CaseFile changed after this suggestion was generated",
                    status_code=409,
                    details={
                        "current_revision": owned.draft.revision,
                        "base_revision": patch_set.base_draft_revision,
                    },
                )
            operations = list(
                self.session.scalars(
                    select(AgentPatchOperation)
                    .where(AgentPatchOperation.patch_set_id == patch_set.id)
                    .order_by(AgentPatchOperation.ordinal)
                    .with_for_update()
                )
            )
            selected = (
                {operation.id for operation in operations}
                if operation_ids is None
                else set(operation_ids)
            )
            known = {operation.id for operation in operations}
            if not selected.issubset(known):
                raise ApplicationError(
                    "agent_patch_selection_invalid",
                    "Accepted operations must belong to the selected Agent patch",
                    status_code=422,
                    details={"unknown_operation_ids": sorted(selected - known)},
                )
            if not selected:
                if operation_ids is None:
                    raise RuntimeError("Agent patch set has no operations")
                reviewed_at = datetime.now(UTC)
                for operation in operations:
                    operation.decision = "rejected"
                    operation.reviewed_at = reviewed_at
                patch_set.status = "rejected"
                self.session.flush()
                return {
                    **self._patch_set_view(
                        owned,
                        patch_set,
                        operations=operations,
                        validator_issues=[],
                    ),
                    "draft_revision": owned.draft.revision,
                }
            registries = {
                row.id: row
                for row in self.session.scalars(
                    select(CaseFileObject).where(
                        CaseFileObject.id.in_(
                            operation.target_object_id for operation in operations
                        )
                    )
                )
            }
            reviewed_at = datetime.now(UTC)
            batch: list[dict[str, Any]] = []
            for operation in operations:
                operation.decision = "accepted" if operation.id in selected else "rejected"
                operation.reviewed_at = reviewed_at
                if operation.id not in selected:
                    continue
                registry = registries.get(operation.target_object_id)
                if registry is None:
                    raise RuntimeError("Agent patch target object disappeared")
                batch.append(
                    {
                        "operation_id": operation.operation_id,
                        "operation_type": operation.operation_type,
                        "object_id": registry.object_id,
                        "field_path": operation.field_path,
                        "expected_object_revision": operation.expected_object_revision,
                        "old_value": operation.old_value_jsonb,
                        "new_value": operation.new_value_jsonb,
                    }
                )
            revision, group_no, applied = V1EditingService(
                self.session
            ).apply_operation_batch(
                owned,
                operations=batch,
                actor_user_id=actor_user_id,
                operation_type="agent_patch_apply",
                patch_set_id=patch_set.id,
            )
            patch_set.status = "applied"
            patch_set.applied_operation_group_no = group_no
            patch_set.applied_from_revision = expected_revision
            patch_set.applied_to_revision = revision
            patch_set.applied_at = reviewed_at
            current_document = build_casefile_document(self.session, owned)
            validator_issues = _nonblocking_validator_issues(current_document, applied)
            self.session.flush()
            return {
                **self._patch_set_view(
                    owned,
                    patch_set,
                    operations=operations,
                    current_document=current_document,
                    validator_issues=validator_issues,
                ),
                "draft_revision": revision,
            }

    def undo_agent_patch_set(
        self,
        actor_user_id: int,
        project_id: int,
        patch_set_id: int,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            patch_set = self._agent_patch_set(owned, patch_set_id, lock=True)
            if patch_set.status != "applied":
                raise ApplicationError(
                    "agent_patch_not_applied",
                    "Only an applied Agent patch can be undone",
                    status_code=409,
                    details={"status": patch_set.status},
                )
            if (
                patch_set.applied_to_revision is None
                or expected_revision != patch_set.applied_to_revision
                or owned.draft.revision != patch_set.applied_to_revision
            ):
                raise ApplicationError(
                    "agent_patch_undo_stale",
                    "Undo is available only before any later Draft edit",
                    status_code=409,
                    details={
                        "current_revision": owned.draft.revision,
                        "applied_revision": patch_set.applied_to_revision,
                    },
                )
            operations = list(
                self.session.scalars(
                    select(AgentPatchOperation)
                    .where(
                        AgentPatchOperation.patch_set_id == patch_set.id,
                        AgentPatchOperation.decision == "accepted",
                    )
                    .order_by(AgentPatchOperation.ordinal.desc())
                )
            )
            registries = {
                row.id: row
                for row in self.session.scalars(
                    select(CaseFileObject).where(
                        CaseFileObject.id.in_(
                            operation.target_object_id for operation in operations
                        )
                    )
                )
            }
            inverse: list[dict[str, Any]] = []
            for operation in operations:
                registry = registries.get(operation.target_object_id)
                if registry is None:
                    raise RuntimeError("Agent patch target object disappeared")
                inverse.append(
                    {
                        "operation_id": operation.operation_id,
                        "operation_type": "replace",
                        "object_id": registry.object_id,
                        "field_path": operation.field_path,
                        "expected_object_revision": registry.revision,
                        "old_value": operation.new_value_jsonb,
                        "new_value": operation.old_value_jsonb,
                    }
                )
            revision, group_no, _ = V1EditingService(self.session).apply_operation_batch(
                owned,
                operations=inverse,
                actor_user_id=actor_user_id,
                operation_type="agent_patch_undo",
                patch_set_id=patch_set.id,
            )
            now = datetime.now(UTC)
            patch_set.status = "undone"
            patch_set.undone_operation_group_no = group_no
            patch_set.undone_to_revision = revision
            patch_set.undone_at = now
            self.session.flush()
            return {
                **self._patch_set_view(
                    owned,
                    patch_set,
                    operations=operations,
                    validator_issues=[],
                ),
                "draft_revision": revision,
            }

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
                    "The API key is still used by an active task",
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
                    "casefile_id": owned.casefile.object_id,
                    "brief_public_id": brief.public_id,
                    "brief_version_no": version.version_no,
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
        expected_draft_revision: int,
    ) -> dict[str, Any]:
        return DraftCandidateService(self.session).adopt_candidate(
            actor_user_id,
            project_id,
            task_run_id,
            expected_draft_revision=expected_draft_revision,
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
        if task_type not in {
            "brief_polish",
            "brief_anchor_extract",
            "brief_intake_questions",
            "brief_intake_synthesize",
            "brief_to_draft",
            "casefile_chat",
        }:
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

    def _agent_thread(
        self,
        owned: OwnedDraft,
        thread_id: int,
        *,
        lock: bool = False,
    ) -> AgentThread:
        statement = select(AgentThread).where(
            AgentThread.id == thread_id,
            AgentThread.project_id == owned.project.id,
            AgentThread.draft_id == owned.draft.id,
        )
        if lock:
            statement = statement.with_for_update()
        thread = self.session.scalar(statement)
        if thread is None:
            raise not_found("AgentThread")
        return thread

    def _agent_patch_set(
        self,
        owned: OwnedDraft,
        patch_set_id: int,
        *,
        lock: bool = False,
    ) -> AgentPatchSet:
        statement = select(AgentPatchSet).where(
            AgentPatchSet.id == patch_set_id,
            AgentPatchSet.project_id == owned.project.id,
            AgentPatchSet.draft_id == owned.draft.id,
        )
        if lock:
            statement = statement.with_for_update()
        patch_set = self.session.scalar(statement)
        if patch_set is None:
            raise not_found("AgentPatchSet")
        return patch_set

    def _patch_set_view(
        self,
        owned: OwnedDraft,
        patch_set: AgentPatchSet,
        *,
        operations: list[AgentPatchOperation] | None = None,
        current_document: dict[str, Any] | None = None,
        validator_issues: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if operations is None:
            operations = list(
                self.session.scalars(
                    select(AgentPatchOperation)
                    .where(AgentPatchOperation.patch_set_id == patch_set.id)
                    .order_by(AgentPatchOperation.ordinal)
                )
            )
        registries = {
            row.id: row
            for row in self.session.scalars(
                select(CaseFileObject).where(
                    CaseFileObject.id.in_(
                        operation.target_object_id for operation in operations
                    )
                )
            )
        }
        if validator_issues is None:
            validator_issues = []
            if patch_set.status == "applied":
                document = current_document or build_casefile_document(self.session, owned)
                accepted = [
                    {
                        "object_id": registries[operation.target_object_id].object_id,
                        "field_path": operation.field_path,
                        "old_value": operation.old_value_jsonb,
                        "new_value": operation.new_value_jsonb,
                    }
                    for operation in operations
                    if operation.decision == "accepted"
                    and operation.target_object_id in registries
                ]
                validator_issues = _nonblocking_validator_issues(document, accepted)
        return {
            "patch_set_id": patch_set.id,
            "thread_id": patch_set.thread_id,
            "source_message_id": patch_set.source_message_id,
            "task_run_id": patch_set.task_run_id,
            "base_draft_revision": patch_set.base_draft_revision,
            "reason_summary": patch_set.reason_summary,
            "status": patch_set.status,
            "is_stale": (
                patch_set.status == "stale"
                or (
                    patch_set.status == "pending"
                    and owned.draft.revision != patch_set.base_draft_revision
                )
            ),
            "applied_from_revision": patch_set.applied_from_revision,
            "applied_to_revision": patch_set.applied_to_revision,
            "undone_to_revision": patch_set.undone_to_revision,
            "operations": [
                {
                    "operation_id": operation.id,
                    "operation_key": operation.operation_id,
                    "ordinal": operation.ordinal,
                    "object_id": (
                        None
                        if (registry := registries.get(operation.target_object_id)) is None
                        else registry.object_id
                    ),
                    "object_type": (
                        None if registry is None else registry.object_type
                    ),
                    "operation_type": operation.operation_type,
                    "field_path": operation.field_path,
                    "expected_object_revision": operation.expected_object_revision,
                    "old_value": operation.old_value_jsonb,
                    "new_value": operation.new_value_jsonb,
                    "reason": operation.reason,
                    "decision": operation.decision,
                    "reviewed_at": _time(operation.reviewed_at),
                }
                for operation in operations
            ],
            "validation_warning": bool(validator_issues),
            "validator_issues": validator_issues,
            "created_at": _time(patch_set.created_at),
            "updated_at": _time(patch_set.updated_at),
        }

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
        agent_thread_id: int | None = None,
        input_message_id: int | None = None,
        output_message_id: int | None = None,
    ) -> TaskRun:
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
    if setting.secret_last_four is None or setting.credential_status == "deleted":
        raise RuntimeError("Deleted provider credentials do not have a public view")
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


def _agent_thread_view(thread: AgentThread) -> dict[str, Any]:
    return {
        "thread_id": thread.id,
        "title": thread.title,
        "title_source": thread.title_source,
        "is_pinned": thread.is_pinned,
        "status": thread.status,
        "last_message_at": _time(thread.last_message_at),
        "created_at": _time(thread.created_at),
        "updated_at": _time(thread.updated_at),
    }


def _agent_message_view(
    message: AgentMessage,
    *,
    task: TaskRun | None = None,
    patch_set: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "message_id": message.id,
        "thread_id": message.thread_id,
        "sequence_no": message.sequence_no,
        "role": message.role,
        "status": message.status,
        "content": message.content_text,
        "task": None if task is None else _task_view(task),
        "referenced_object_ids": (
            []
            if task is None or not isinstance(task.result_jsonb, dict)
            else list(task.result_jsonb.get("referenced_object_ids", []))
        ),
        "patch_set": patch_set,
        "created_at": _time(message.created_at),
        "updated_at": _time(message.updated_at),
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
        "input_brief_intake_id": task.brief_intake_id,
        "input_brief_intake_revision": task.input_brief_intake_revision,
        "base_brief_intake_candidate_id": task.base_brief_intake_candidate_id,
        "agent_thread_id": task.agent_thread_id,
        "input_message_id": task.input_message_id,
        "output_message_id": task.output_message_id,
        "input_hash": task.input_hash,
        "attempt_count": task.attempt_count,
        "usage": task.usage_jsonb,
        "result_snapshot_id": task.result_snapshot_id,
        "result": task.result_jsonb,
        "error_code": task.error_code,
        "failure": _task_failure_from_row(task),
        "created_at": _time(task.created_at),
        "updated_at": _time(task.updated_at),
    }


def task_failure_view(
    error_code: str | None,
    *,
    issues: list[dict[str, str]] | None = None,
    network_retries: int | None = None,
) -> dict[str, Any] | None:
    if error_code is None:
        return None
    message = _FAILURE_MESSAGES.get(error_code, _FAILURE_MESSAGES["generation_failed"])
    if (
        network_retries is not None
        and error_code in {"provider_connection_failed", "provider_timeout"}
    ):
        message = f"{message}（已自动重试 {network_retries} 次）"
    return {
        "code": error_code,
        "message": message,
        "retryable": error_code in _RETRYABLE_FAILURES,
        "issues": list(issues or []),
    }


def _task_failure_from_row(task: TaskRun) -> dict[str, Any] | None:
    stored = task.error_details_jsonb.get("public_failure")
    if isinstance(stored, dict):
        return stored
    if task.status != "failed":
        return None
    return task_failure_view(task.error_code)


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
    "task_failure_view",
    "task_view",
]
