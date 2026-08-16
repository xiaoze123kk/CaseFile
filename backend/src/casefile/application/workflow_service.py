"""Sources, versioned Briefs, provider settings, and durable Agent TaskRuns."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import rfc8785
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.agent_runtime.chat_intent import (
    INTENT_ROUTER_VERSION,
    normalize_routing_hint,
    route_allows_suggestions,
    route_public_payload,
    route_result_summary,
    route_suggestion_policy,
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
from casefile.application.agent_collaboration import (
    auto_thread_title as _auto_thread_title,
)
from casefile.application.agent_collaboration import (
    find_frozen_object as _find_frozen_object,
)
from casefile.application.agent_collaboration import (
    focused_patch_target_ids as _focused_patch_target_ids,
)
from casefile.application.agent_collaboration import (
    freeze_agent_focus as _freeze_agent_focus,
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
from casefile.application.task_cancellation import (
    TERMINAL_TASK_STATUSES,
    finalize_task_cancellation,
)
from casefile.application.task_events import append_task_event
from casefile.application.v1_editing import EDITABLE_FIELDS, V1EditingService
from casefile.application.workbench_read_model import WorkbenchReadModel
from casefile.application.workflow_brief_validation import (
    require_confirmed_atomics as _require_confirmed_atomics,
)
from casefile.application.workflow_brief_validation import validate_brief as _validate_brief
from casefile.application.workflow_views import (
    agent_message_view as _agent_message_view,
)
from casefile.application.workflow_views import agent_thread_view as _agent_thread_view
from casefile.application.workflow_views import brief_version_view as _brief_version_view
from casefile.application.workflow_views import brief_view as _brief_view
from casefile.application.workflow_views import (
    event_view,
    provider_view,
    source_view,
    task_failure_view,
    task_view,
    time_view,
)
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
SUPPORTED_CHAT_VIEWS = frozenset(
    {"timeline", "relations", "reasoning", "map", "export", "compile", "evidence"}
)
DEFAULT_BUDGET: dict[str, Any] = {
    "max_turns": 12,
    "network_retries": 2,
    "structural_repair_attempts": 5,
}

_append_event = append_task_event
_event_view = event_view
_provider_view = provider_view
_source_view = source_view
_task_view = task_view
_time = time_view


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
        expected_draft_id: int,
        expected_draft_revision: int,
        title: str | None = None,
    ) -> dict[str, Any]:
        normalized_title = None if title is None else title.strip()
        if title is not None and not normalized_title:
            raise ApplicationError(
                "agent_thread_title_invalid",
                "Agent 对话标题不能为空。",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            self._require_current_draft(
                owned,
                expected_draft_id=expected_draft_id,
                expected_draft_revision=expected_draft_revision,
            )
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
        expected_draft_id: int,
        expected_draft_revision: int,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            self._require_current_draft(
                owned,
                expected_draft_id=expected_draft_id,
                expected_draft_revision=expected_draft_revision,
            )
            thread = self._agent_thread(owned, thread_id, lock=True)
            if "title" in changes:
                title = changes["title"]
                normalized_title = "" if title is None else str(title).strip()
                if not normalized_title:
                    raise ApplicationError(
                        "agent_thread_title_invalid",
                        "Agent 对话标题不能为空。",
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
                            "当前对话仍有 Agent 任务在执行。",
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
                "消息序号必须是非负整数。",
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
                if not any(patch.status == "applied" for patch in patch_sets_by_message.values())
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
        expected_draft_id: int,
        expected_draft_revision: int,
        content: str,
        provider: str = DEFAULT_PROVIDER,
        focus: dict[str, Any] | None = None,
        routing_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider = _supported_provider(provider)
        content = content.strip()
        if not content:
            raise ApplicationError(
                "agent_message_empty",
                "Agent 消息不能为空。",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            self._require_current_draft(
                owned,
                expected_draft_id=expected_draft_id,
                expected_draft_revision=expected_draft_revision,
            )
            thread = self._agent_thread(owned, thread_id, lock=True)
            if thread.status != "active":
                raise ApplicationError(
                    "agent_thread_archived",
                    "已归档的 Agent 对话不能接收新消息。",
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
                    "请等待当前 Agent 回复后再发送下一条消息。",
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
            casefile = build_casefile_document(self.session, owned)
            focus_values = focus if isinstance(focus, dict) else None
            validation_snapshot = WorkbenchReadModel(
                self.session
            ).validation_snapshot(owned)
            known_issue_ids = frozenset(
                str(item["issue_id"])
                for item in validation_snapshot.get("issues", [])
                if isinstance(item, dict) and item.get("issue_id")
            )
            frozen_focus = _freeze_agent_focus(
                casefile,
                focus_values,
                known_issue_ids,
            )
            frozen_input = {
                "casefile": casefile,
                "history": [
                    {
                        "role": message.role,
                        "content": message.content_text,
                    }
                    for message in history
                    if message.content_text is not None
                ],
                "message": content,
                "focus": frozen_focus,
                "validation": validation_snapshot,
                "context_policy_version": "agent-focus-v1",
                "routing_hint": (
                    {"entrypoint": "free_text", "preset_id": None}
                    if routing_hint is None
                    else normalize_routing_hint(routing_hint)
                ),
                "router_version": INTENT_ROUTER_VERSION,
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
        referenced_event_ids: list[str],
        referenced_validation_issue_ids: list[str],
        suggested_view: str | None = None,
        suggestions: list[dict[str, Any]],
        usage: dict[str, Any],
        route: dict[str, Any] | None = None,
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

            # Route permission enforcement runs before reference/whitelist checks:
            # a denied route must not let a model's extra suggestions fail the task.
            suppressed_count = 0
            suggestion_policy = (
                None if route is None else route_suggestion_policy(route)
            )
            if route is not None and not route_allows_suggestions(route):
                if suggestions:
                    suppressed_count = len(suggestions)
                    suggestions = []
                    _append_event(
                        self.session,
                        task,
                        "route.suggestions_suppressed",
                        "routing",
                        {
                            **route_public_payload(route),
                            "suggestion_policy": suggestion_policy,
                            "suppressed_count": suppressed_count,
                        },
                    )

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
                raise RuntimeError(f"Chat result references unknown objects: {missing_references}")

            referenced_events = _unique_strings(referenced_event_ids)
            frozen_event_ids = {
                str(value["id"])
                for value in frozen_casefile.get("events", [])
                if isinstance(value, dict) and isinstance(value.get("id"), str)
            }
            missing_events = sorted(set(referenced_events) - frozen_event_ids)
            if missing_events:
                raise RuntimeError(
                    f"Chat result references unknown events: {missing_events}"
                )

            referenced_issues = _unique_strings(referenced_validation_issue_ids)
            known_issue_ids = WorkbenchReadModel(
                self.session
            ).validation_issue_ids(owned)
            missing_issues = sorted(set(referenced_issues) - known_issue_ids)
            if missing_issues:
                raise RuntimeError(
                    f"Chat result references unknown validation issues: {missing_issues}"
                )

            focused_target_ids = _focused_patch_target_ids(
                task.input_jsonb.get("focus")
            )
            if focused_target_ids is not None:
                off_focus_targets = sorted(
                    {
                        str(item["object_id"])
                        for item in suggestions
                        if isinstance(item, dict)
                        and isinstance(item.get("object_id"), str)
                        and item["object_id"] not in focused_target_ids
                    }
                )
                if off_focus_targets:
                    raise RuntimeError(
                        "Chat suggestions must target objects bound to the "
                        f"focused validation issue: {off_focus_targets}"
                    )

            resolved_view = (
                None if suggested_view is None else str(suggested_view).strip() or None
            )
            if resolved_view is not None and resolved_view not in SUPPORTED_CHAT_VIEWS:
                raise RuntimeError(
                    f"Chat result suggests an unsupported view: {resolved_view}"
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
                        raise RuntimeError(f"Chat suggestion targets unknown object: {object_id}")
                    field_path = str(suggestion["path"])
                    top_field = _pointer_top_field(field_path)
                    if top_field not in EDITABLE_FIELDS.get(registry.object_type, set()):
                        raise RuntimeError(
                            f"Chat suggestion targets a read-only field: {object_id}{field_path}"
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
            routing_summary = (
                None
                if route is None
                else route_result_summary(
                    route,
                    suggestion_policy=suggestion_policy,
                    suppressed_count=suppressed_count,
                )
            )
            result_payload: dict[str, Any] = {
                "answer": answer,
                "referenced_object_ids": referenced,
                "referenced_event_ids": referenced_events,
                "referenced_validation_issue_ids": referenced_issues,
                "suggested_view": resolved_view,
                "patch_set_id": None if patch_set is None else patch_set.id,
                "stale": stale,
            }
            if routing_summary is not None:
                result_payload["routing"] = routing_summary
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
            if route is not None:
                assert routing_summary is not None
                _append_event(
                    self.session,
                    task,
                    "route.outcome",
                    "completed",
                    {**routing_summary, "succeeded": True},
                )
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
                    **({} if routing_summary is None else {"routing": routing_summary}),
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
        expected_draft_id: int,
        expected_revision: int,
        operation_ids: list[int] | None,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            patch_set = self._agent_patch_set(owned, patch_set_id, lock=True)
            if patch_set.status != "pending":
                raise ApplicationError(
                    "agent_patch_not_pending",
                    "只有待处理的 Agent 建议才能被采用。",
                    status_code=409,
                    details={"status": patch_set.status},
                )
            if (
                owned.draft.id != expected_draft_id
                or owned.draft.revision != expected_revision
                or owned.draft.revision != patch_set.base_draft_revision
            ):
                raise ApplicationError(
                    "agent_patch_stale",
                    "生成这条建议后，CaseFile 已发生变化。",
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
                    "已接受的操作必须属于所选 Agent 修改批次。",
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
            revision, group_no, applied = V1EditingService(self.session).apply_operation_batch(
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
        expected_draft_id: int,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            patch_set = self._agent_patch_set(owned, patch_set_id, lock=True)
            if patch_set.status != "applied":
                raise ApplicationError(
                    "agent_patch_not_applied",
                    "只有已应用的 Agent 修改批次才能撤销。",
                    status_code=409,
                    details={"status": patch_set.status},
                )
            if (
                patch_set.applied_to_revision is None
                or owned.draft.id != expected_draft_id
                or expected_revision != patch_set.applied_to_revision
                or owned.draft.revision != patch_set.applied_to_revision
            ):
                raise ApplicationError(
                    "agent_patch_undo_stale",
                    "只有在后续没有修改草稿前才能撤销。",
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
                existing = self.session.get(
                    BriefVersion, brief.current_version_id
                )
                if existing is not None and existing.content_hash == _json_hash(
                    content
                ):
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
        if candidate_strategy_attempt not in {1, 2}:
            raise ApplicationError(
                "candidate_strategy_attempt_invalid",
                "候选策略最多只能额外重试一次。",
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
                    CaseFileObject.id.in_(operation.target_object_id for operation in operations)
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
                    if operation.decision == "accepted" and operation.target_object_id in registries
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
                    "object_type": (None if registry is None else registry.object_type),
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
        if (
            owned.draft.id == expected_draft_id
            and owned.draft.revision == expected_draft_revision
        ):
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


def _json_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _supported_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ApplicationError(
            "provider_not_supported",
            f"不支持的模型服务：{provider}。",
            status_code=422,
            details={"supported_providers": sorted(SUPPORTED_PROVIDERS)},
        )
    return normalized


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
