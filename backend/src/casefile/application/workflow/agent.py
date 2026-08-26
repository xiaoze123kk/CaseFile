"""Agent thread, Chat completion, verification, and patch workflow use cases."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.agent_runtime.chat_audit_validation import (
    audit_findings_suppressed_for,
    normalize_audit_findings,
    route_primary_intent,
)
from casefile.agent_runtime.chat_intent import (
    INTENT_ROUTER_VERSION,
    normalize_routing_hint,
    route_allows_suggestions,
    route_public_payload,
    route_result_summary,
    route_suggestion_policy,
)
from casefile.agent_runtime.chat_reference_autofill import (
    autofill_chat_references,
    reference_autofill_enabled,
)
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
from casefile.application.agent_mutation import general_mutation_impact_hash
from casefile.application.agent_patch_mutation import (
    AgentPatchMutationMixin,
    exact_history_restore_authorization,
    general_mutation_patch_operation,
    general_mutation_repair_validation,
    mutation_reason_summary,
    patch_operation_count,
    repair_provenance_by_target,
)
from casefile.application.casefile_v1 import build_casefile_document, casefile_content_hash
from casefile.application.chat_public_contracts import (
    public_agent_run_view,
    public_routing_interpretation,
)
from casefile.application.chat_public_events import public_agent_event_view
from casefile.application.closure_repair import (
    ValidatedClosureRepair,
    prepare_chat_repair_suggestions,
    repair_completion_payload,
)
from casefile.application.errors import ApplicationError, not_found
from casefile.application.task_cancellation import (
    TERMINAL_TASK_STATUSES,
    finalize_task_cancellation,
)
from casefile.application.v1_editing import COLLECTIONS, EDITABLE_FIELDS, V1EditingService
from casefile.application.verification_engine import (
    MutationSimulation,
    VerificationEngine,
)
from casefile.application.verification_service import VerificationService
from casefile.application.workbench_read_model import WorkbenchReadModel
from casefile.application.workflow_common import (
    DEFAULT_PROVIDER,
    SUPPORTED_CHAT_VIEWS,
    ChatReferenceValidationError,
    _append_event,
    _chat_context_policy_version,
    _event_view,
    _json_hash,
    _latest_context_state_ref,
    _supported_provider,
    _task_view,
    _time,
)
from casefile.application.workflow_views import (
    agent_message_view as _agent_message_view,
)
from casefile.application.workflow_views import agent_thread_view as _agent_thread_view
from casefile.contracts import validate_casefile
from casefile.data_postgres.models import (
    AgentMessage,
    AgentPatchOperation,
    AgentPatchSet,
    AgentThread,
    CaseFileObject,
    DraftOperation,
    TaskAttempt,
    TaskEvent,
    TaskRun,
    VerificationFindingPatchOperation,
)
from casefile.data_postgres.repositories import OwnedDraft, ProjectRepository
from casefile.domain.logical_mutation import (
    CLOSURE_POLICY_VERSION,
    CreateObject,
    DeleteObject,
    MutationSet,
    UpdateField,
)
from casefile_contracts import PublicAgentEvent, PublicAgentRun


class AgentWorkflowMixin(AgentPatchMutationMixin):
    session: Session
    projects: ProjectRepository
    _new_task: Any
    _owned: Any
    _provider_setting: Any
    _queue_task: Any
    _require_current_draft: Any

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

    def get_agent_run(
        self,
        actor_user_id: int,
        project_id: int,
        run_id: int,
    ) -> PublicAgentRun:
        with self.session.begin():
            task = self._agent_run_task(actor_user_id, project_id, run_id)
            return public_agent_run_view(_task_view(task))

    def cancel_agent_run(
        self,
        actor_user_id: int,
        project_id: int,
        run_id: int,
    ) -> PublicAgentRun:
        with self.session.begin():
            task = self._agent_run_task(
                actor_user_id,
                project_id,
                run_id,
                lock=True,
            )
            if task.status not in TERMINAL_TASK_STATUSES:
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
            return public_agent_run_view(_task_view(task))

    def list_agent_run_events(
        self,
        actor_user_id: int,
        project_id: int,
        run_id: int,
        *,
        after_sequence: int = 0,
    ) -> list[PublicAgentEvent]:
        if after_sequence < 0:
            raise ApplicationError(
                "agent_event_cursor_invalid",
                "事件序号必须是非负整数。",
                status_code=422,
            )
        with self.session.begin():
            task = self._agent_run_task(actor_user_id, project_id, run_id)
            run = public_agent_run_view(_task_view(task))
            rows = self.session.scalars(
                select(TaskEvent)
                .where(
                    TaskEvent.task_run_id == task.id,
                    TaskEvent.sequence_no > after_sequence,
                )
                .order_by(TaskEvent.sequence_no)
            )
            projected = (public_agent_event_view(_event_view(row), run) for row in rows)
            return [event for event in projected if event is not None]

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
        verification_trigger: str = "chat",
    ) -> dict[str, Any]:
        provider = _supported_provider(provider)
        content = content.strip()
        if verification_trigger not in {"chat", "manual"}:
            raise ValueError("Unsupported verification trigger")
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
            validation_snapshot = WorkbenchReadModel(self.session).validation_snapshot(owned)
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
            context_policy_version = _chat_context_policy_version()
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
                "context_policy_version": context_policy_version,
                "routing_hint": (
                    {"entrypoint": "free_text", "preset_id": None}
                    if routing_hint is None
                    else normalize_routing_hint(routing_hint)
                ),
                "verification_trigger": verification_trigger,
                "router_version": INTENT_ROUTER_VERSION,
                "context_state": _latest_context_state_ref(
                    self.session,
                    project_id=owned.project.id,
                    thread_id=thread.id,
                ),
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

    def rerun_verification(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        expected_draft_id: int,
        expected_draft_revision: int,
        provider: str = DEFAULT_PROVIDER,
    ) -> dict[str, Any]:
        """Queue one balanced logic-audit TaskRun without writing the Draft.

        VerificationRun remains a result observation.  This method intentionally
        creates an ordinary casefile_chat TaskRun so provider/model snapshots,
        leases and SSE recovery stay in the existing task infrastructure.
        """

        thread = self.create_agent_thread(
            actor_user_id,
            project_id,
            expected_draft_id=expected_draft_id,
            expected_draft_revision=expected_draft_revision,
            title="验证复查",
        )
        return self.send_agent_message(
            actor_user_id,
            project_id,
            int(thread["thread_id"]),
            expected_draft_id=expected_draft_id,
            expected_draft_revision=expected_draft_revision,
            content=(
                "对当前工作稿执行一次平衡验证复查：先运行确定性检查，再复查"
                "矛盾、断链、时序错误和动机缺口。所有发现必须绑定现有证据；"
                "不要自动修改工作稿。"
            ),
            provider=provider,
            focus={"view": "evidence"},
            routing_hint={"entrypoint": "preset", "preset_id": "audit"},
            verification_trigger="manual",
        )

    def submit_agent_routing_feedback(
        self,
        actor_user_id: int,
        project_id: int,
        thread_id: int,
        message_id: int,
        *,
        correct_intent: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Record one human correction for a completed, route-bearing assistant message."""

        resolved_note = None if note is None else note.strip() or None
        if correct_intent is None and resolved_note is None:
            raise ApplicationError(
                "agent_routing_feedback_empty",
                "路由反馈必须包含正确意图或备注。",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            thread = self._agent_thread(owned, thread_id)
            message = self.session.scalar(
                select(AgentMessage).where(
                    AgentMessage.id == message_id,
                    AgentMessage.thread_id == thread.id,
                    AgentMessage.project_id == owned.project.id,
                )
            )
            if message is None:
                raise ApplicationError(
                    "agent_message_not_found",
                    "找不到该 Agent 消息。",
                    status_code=404,
                )
            if message.role != "assistant" or message.status != "completed":
                raise ApplicationError(
                    "agent_routing_feedback_unavailable",
                    "只有已完成的 Agent 回复可以提交路由反馈。",
                    status_code=409,
                )
            task = self.session.scalar(
                select(TaskRun).where(TaskRun.output_message_id == message.id)
            )
            if (
                task is None
                or task.task_type != "casefile_chat"
                or not isinstance(task.result_jsonb, dict)
                or not isinstance(task.result_jsonb.get("routing"), dict)
            ):
                raise ApplicationError(
                    "agent_routing_not_available",
                    "这条回复没有可纠错的路由结果。",
                    status_code=409,
                )
            existing = self.session.scalar(
                select(TaskEvent.id)
                .where(
                    TaskEvent.task_run_id == task.id,
                    TaskEvent.event_type == "router.feedback",
                )
                .limit(1)
            )
            if existing is not None:
                raise ApplicationError(
                    "agent_routing_feedback_exists",
                    "这条回复已经提交过路由反馈。",
                    status_code=409,
                )
            latest_payload: dict[str, dict[str, Any] | None] = {
                "query.rewritten": None,
                "intent.understood": None,
            }
            for event_type in tuple(latest_payload):
                event = self.session.scalar(
                    select(TaskEvent)
                    .where(
                        TaskEvent.task_run_id == task.id,
                        TaskEvent.event_type == event_type,
                    )
                    .order_by(TaskEvent.sequence_no.desc())
                    .limit(1)
                )
                if event is not None and isinstance(event.payload_jsonb, dict):
                    latest_payload[event_type] = event.payload_jsonb
            payload = {
                "message_id": message.id,
                "task_run_id": task.id,
                "correct_intent": correct_intent,
                "note": resolved_note,
                "original": {
                    "query": task.input_jsonb.get("message"),
                    "routing_hint": task.input_jsonb.get("routing_hint") or {},
                    "intent": latest_payload["intent.understood"],
                    "rewrite": latest_payload["query.rewritten"],
                    "route": task.result_jsonb["routing"],
                },
            }
            _append_event(
                self.session,
                task,
                "router.feedback",
                "feedback",
                payload,
            )
            effective_intent = correct_intent or task.result_jsonb["routing"].get("intent")
            interpretation = public_routing_interpretation(effective_intent)
            if interpretation is None:
                raise RuntimeError("Routing feedback has no public interpretation")
            return {
                "message_id": message.id,
                "task_run_id": task.id,
                "acknowledged": True,
                "interpretation": interpretation,
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
        audit_findings: list[dict[str, Any]] | None = None,
        usage: dict[str, Any],
        route: dict[str, Any] | None = None,
        tools: dict[str, Any] | None = None,
        repair_envelope: dict[str, Any] | None = None,
        general_mutation_envelope: dict[str, Any] | None = None,
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

            suppressed_count = 0
            suggestion_policy = None if route is None else route_suggestion_policy(route)
            route_intent = route_primary_intent(route)
            suppress_general_mutation = (
                general_mutation_envelope is not None
                and general_mutation_envelope.get("status") == "ready"
                and (suggestion_policy == "deny" or route_intent == "clarify")
            )
            if suppress_general_mutation:
                assert route is not None
                _append_event(
                    self.session,
                    task,
                    "route.general_mutation_suppressed",
                    "routing",
                    {
                        **route_public_payload(route),
                        "suggestion_policy": suggestion_policy,
                        "route_intent": route_intent,
                        "suppressed_count": patch_operation_count(general_mutation_envelope, []),
                    },
                )
                general_mutation_envelope = {"status": "blocked"}
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
                else:
                    suppression_event = self.session.scalar(
                        select(TaskEvent)
                        .where(
                            TaskEvent.task_run_id == task.id,
                            TaskEvent.event_type == "route.suggestions_suppressed",
                        )
                        .order_by(TaskEvent.sequence_no.desc())
                        .limit(1)
                    )
                    if suppression_event is not None:
                        suppressed_count = int(
                            suppression_event.payload_jsonb.get("suppressed_count", 0)
                        )

            # Only the audit executor owns structured logic-audit findings;
            # other routes drop the optional slot without failing the answer.
            audit_findings = list(audit_findings or [])
            suppressed_findings_count = 0
            if audit_findings_suppressed_for(route):
                assert route is not None
                if audit_findings:
                    suppressed_findings_count = len(audit_findings)
                    _append_event(
                        self.session,
                        task,
                        "route.audit_findings_suppressed",
                        "routing",
                        {
                            **route_public_payload(route),
                            "route_intent": route_primary_intent(route),
                            "suppressed_count": suppressed_findings_count,
                        },
                    )
                    audit_findings = []

            registry_rows = list(
                self.session.scalars(
                    select(CaseFileObject).where(
                        CaseFileObject.draft_id == task.draft_id,
                    )
                )
            )
            registries = {row.object_id: row for row in registry_rows}

            # Conservative safety net for F1: only an empty slot is repaired,
            # only frozen CaseFile record labels are consulted, and an ID is
            # added only on a unique label match. Never delete or re-rank.
            autofilled_object_ids: list[str] = []
            autofilled_event_ids: list[str] = []
            if reference_autofill_enabled():
                object_refs = _unique_strings(referenced_object_ids)
                event_refs = _unique_strings(referenced_event_ids)
                if not object_refs:
                    autofilled_object_ids = autofill_chat_references(
                        answer,
                        frozen_casefile,
                    )[0]
                    referenced_object_ids = [
                        *referenced_object_ids,
                        *autofilled_object_ids,
                    ]
                if not event_refs:
                    autofilled_event_ids = autofill_chat_references(
                        answer,
                        frozen_casefile,
                    )[1]
                    referenced_event_ids = [
                        *referenced_event_ids,
                        *autofilled_event_ids,
                    ]
                if autofilled_object_ids or autofilled_event_ids:
                    _append_event(
                        self.session,
                        task,
                        "context.reference_autofilled",
                        "context",
                        {
                            "object_ids": autofilled_object_ids,
                            "event_ids": autofilled_event_ids,
                        },
                    )

            referenced = _unique_strings(referenced_object_ids)
            frozen_object_ids = _frozen_object_ids(frozen_casefile)
            missing_references = sorted(set(referenced) - frozen_object_ids)

            referenced_events = _unique_strings(referenced_event_ids)
            frozen_event_ids = {
                str(value["id"])
                for value in frozen_casefile.get("events", [])
                if isinstance(value, dict) and isinstance(value.get("id"), str)
            }
            missing_events = sorted(set(referenced_events) - frozen_event_ids)

            referenced_issues = _unique_strings(referenced_validation_issue_ids)
            known_issue_ids = WorkbenchReadModel(self.session).validation_issue_ids(owned)
            missing_issues = sorted(set(referenced_issues) - known_issue_ids)

            suggestion_finding_refs = [
                str(item.get("finding_ref")).strip() or None
                if isinstance(item, dict)
                and isinstance(item.get("finding_ref"), str)
                and str(item["finding_ref"]).strip()
                else None
                for item in suggestions
            ]
            try:
                (
                    audit_findings,
                    missing_finding_objects,
                    missing_finding_events,
                    missing_finding_issues,
                ) = normalize_audit_findings(
                    audit_findings,
                    frozen_object_ids=frozen_object_ids,
                    frozen_event_ids=frozen_event_ids,
                    known_issue_ids=known_issue_ids,
                    suggestion_finding_refs=suggestion_finding_refs,
                )
            except ValueError as error:
                raise RuntimeError(f"Invalid audit_findings: {error}") from error

            # Evidence IDs are contractually part of the answer's public
            # references: fold them in so every finding stays clickable in the
            # workbench even when a model omitted them from the top-level slot.
            for finding in audit_findings:
                referenced_object_ids = [
                    *referenced_object_ids,
                    *finding["evidence_object_ids"],
                ]
                referenced_event_ids = [
                    *referenced_event_ids,
                    *finding["evidence_event_ids"],
                ]
                referenced_validation_issue_ids = [
                    *referenced_validation_issue_ids,
                    *finding["evidence_validation_issue_ids"],
                ]
            if (
                referenced_object_ids != _unique_strings(referenced_object_ids)
                or referenced_event_ids != _unique_strings(referenced_event_ids)
                or referenced_validation_issue_ids
                != _unique_strings(referenced_validation_issue_ids)
            ):
                _append_event(
                    self.session,
                    task,
                    "context.audit_evidence_references_added",
                    "context",
                    {
                        "finding_count": len(audit_findings),
                    },
                )
            referenced = _unique_strings(referenced_object_ids)
            missing_references = sorted(
                (set(referenced) - frozen_object_ids) | set(missing_finding_objects)
            )
            referenced_events = _unique_strings(referenced_event_ids)
            missing_events = sorted(
                (set(referenced_events) - frozen_event_ids) | set(missing_finding_events)
            )
            referenced_issues = _unique_strings(referenced_validation_issue_ids)
            missing_issues = sorted(
                (set(referenced_issues) - known_issue_ids) | set(missing_finding_issues)
            )
            if missing_references or missing_events or missing_issues:
                raise ChatReferenceValidationError(
                    object_ids=tuple(missing_references),
                    event_ids=tuple(missing_events),
                    issue_ids=tuple(missing_issues),
                )

            # General Mutation is the sole PatchSet source in suggest mode.
            if general_mutation_envelope is not None:
                suggestions = []

            focused_target_ids = _focused_patch_target_ids(task.input_jsonb.get("focus"))
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
            resolved_view = None if suggested_view is None else str(suggested_view).strip() or None
            if resolved_view is not None and resolved_view not in SUPPORTED_CHAT_VIEWS:
                raise RuntimeError(f"Chat result suggests an unsupported view: {resolved_view}")
            primary_suggestion_count = len(suggestions)
            repair_validation: ValidatedClosureRepair | None
            if general_mutation_envelope is None:
                suggestions, repair_validation = prepare_chat_repair_suggestions(
                    frozen_casefile, task, suggestions, repair_envelope
                )
            else:
                repair_validation, primary_suggestion_count = general_mutation_repair_validation(
                    frozen_casefile,
                    general_mutation_envelope,
                    repair_envelope,
                    original_intent=str(task.input_jsonb.get("message", "")),
                )
            stale = owned.draft.revision != task.input_draft_revision
            patch_set: AgentPatchSet | None = None
            patch_operations: list[AgentPatchOperation] = []
            if (
                general_mutation_envelope is not None
                and general_mutation_envelope.get("status") == "ready"
            ):
                bound = general_mutation_envelope["bound"]
                simulation = general_mutation_envelope["simulation"]
                reasons = [item.reason for item in bound.operations]
                patch_set = AgentPatchSet(
                    project_id=task.project_id,
                    casefile_id=task.casefile_id,
                    draft_id=task.draft_id,
                    thread_id=thread.id,
                    source_message_id=output_message.id,
                    task_run_id=task.id,
                    base_draft_revision=task.input_draft_revision,
                    closure_policy_version=bound.mutation_set.closure_policy_version,
                    mutation_mode="normal",
                    plan_version=bound.plan_version,
                    capability_policy_version=bound.capability_policy_version,
                    binder_version=bound.binder_version,
                    review_mode="atomic",
                    plan_hash=bound.plan_hash,
                    impact_hash=general_mutation_envelope["impact_hash"],
                    contains_delete=bound.contains_delete,
                    baseline_hash=simulation.baseline_hash,
                    candidate_hash=simulation.candidate_hash,
                    reason_summary=mutation_reason_summary(reasons),
                    status="stale" if stale else "pending",
                )
                self.session.add(patch_set)
                self.session.flush()
                companion_by_target = repair_provenance_by_target(repair_validation)
                for ordinal, item in enumerate(bound.operations, start=1):
                    registry = registries.get(item.target_object_key)
                    companion = companion_by_target.get((item.target_object_key, item.field_path))
                    patch_operation = general_mutation_patch_operation(
                        task=task,
                        patch_set=patch_set,
                        item=item,
                        registry=registry,
                        ordinal=ordinal,
                        companion=companion,
                    )
                    self.session.add(patch_operation)
                    patch_operations.append(patch_operation)
            elif suggestions:
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
                    closure_policy_version=CLOSURE_POLICY_VERSION,
                    mutation_mode="normal",
                    baseline_hash=_json_hash(frozen_casefile),
                    candidate_hash=None,
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
                        target_object_key=registry.object_id,
                        target_collection=COLLECTIONS[registry.object_type],
                        ordinal=ordinal,
                        operation_id=f"op_t{task.id}_{ordinal:02d}",
                        operation_type="replace",
                        field_path=field_path,
                        expected_object_revision=int(frozen_object["revision"]),
                        old_value_jsonb=old_value,
                        new_value_jsonb=deepcopy(suggestion.get("value")),
                        reason=reason,
                        origin=str(suggestion.get("origin", "primary")),
                        repair_round=suggestion.get("repair_round"),
                        repair_obligation_keys=list(suggestion.get("repair_obligation_keys", [])),
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
            tool_metrics = tools or usage.get("tool_metrics") or {}
            if routing_summary is not None:
                routing_summary["tool_metrics"] = tool_metrics
            verification_trigger = str(task.input_jsonb.get("verification_trigger", "chat"))
            if verification_trigger not in {"chat", "manual"}:
                raise RuntimeError("CaseFile chat TaskRun has an invalid verification trigger")
            verification_observation = VerificationService(self.session).record_chat_result(
                owned=owned,
                task_run_id=task.id,
                document=frozen_casefile,
                audit_findings=audit_findings,
                profile=("balanced" if route_primary_intent(route) == "logic_audit" else "fast"),
                trigger=verification_trigger,
                draft_revision=task.input_draft_revision,
                patch_set_id=None if patch_set is None else patch_set.id,
            )
            if verification_observation is not None:
                verification_run, verification_result = verification_observation
                total_findings = len(verification_result.findings)
                for finding_index, verification_finding in enumerate(
                    verification_result.findings,
                    start=1,
                ):
                    _append_event(
                        self.session,
                        task,
                        "verification.finding",
                        "verification",
                        {
                            "verification_run_id": verification_run.id,
                            "finding_key": verification_finding.finding_key,
                            "kind": verification_finding.kind,
                            "severity": verification_finding.severity,
                            "status": verification_finding.status,
                            "title": verification_finding.title,
                            "rule_code": verification_finding.rule_code,
                            "confidence": verification_finding.confidence,
                            "refs": [ref.as_dict() for ref in verification_finding.refs],
                            "current": finding_index,
                            "total": total_findings,
                        },
                    )
                _append_event(
                    self.session,
                    task,
                    "verification.completed",
                    "verification",
                    {
                        "verification_run_id": verification_run.id,
                        "trigger": verification_run.trigger,
                        "profile": verification_run.profile,
                        "draft_revision": verification_run.draft_revision,
                        "finding_count": len(verification_result.findings),
                        "deterministic_finding_count": sum(
                            item.kind == "deterministic" for item in verification_result.findings
                        ),
                        "llm_finding_count": sum(
                            item.kind == "llm" for item in verification_result.findings
                        ),
                    },
                )
            result_payload: dict[str, Any] = {
                "answer": answer,
                "referenced_object_ids": referenced,
                "referenced_event_ids": referenced_events,
                "referenced_validation_issue_ids": referenced_issues,
                "suggested_view": resolved_view,
                "patch_set_id": None if patch_set is None else patch_set.id,
                "stale": stale,
                "audit_findings": audit_findings,
                "tool_metrics": tool_metrics,
                "closure_repair": repair_completion_payload(
                    repair_validation,
                    primary_suggestion_count,
                    patch_operation_count(general_mutation_envelope, suggestions),
                    repair_envelope,
                ),
            }
            if verification_observation is not None:
                verification_run, _ = verification_observation
                result_payload["verification_run_id"] = verification_run.id
                if patch_set is not None:
                    self.session.flush()
                    VerificationService(self.session).link_patch_operations(
                        project_id=task.project_id,
                        verification_run_id=verification_run.id,
                        operations=patch_operations,
                        finding_refs_by_operation_id={
                            operation.id: (
                                str(suggestions[index].get("finding_ref"))
                                if index < len(suggestions)
                                and suggestions[index].get("finding_ref") is not None
                                else None
                            )
                            for index, operation in enumerate(patch_operations)
                        },
                    )
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
        confirmed_impact_hash: str | None = None,
        target_finding_ids: list[int] | None = None,
        accepted_debt_finding_keys: list[str] | None = None,
        debt_acceptance_reason: str | None = None,
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
            self._validate_patch_selection(patch_set, operation_ids)
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
                            operation.target_object_id
                            for operation in operations
                            if operation.target_object_id is not None
                        )
                    )
                )
            }
            reviewed_at = datetime.now(UTC)
            batch: list[dict[str, Any]] = []
            for operation in operations:
                if operation.id not in selected:
                    continue
                registry = (
                    None
                    if operation.target_object_id is None
                    else registries.get(operation.target_object_id)
                )
                if registry is None and operation.operation_type != "create_object":
                    raise RuntimeError("Agent patch target object disappeared")
                batch.append(
                    {
                        "operation_id": operation.operation_id,
                        "operation_type": operation.operation_type,
                        "object_id": operation.target_object_key,
                        "field_path": operation.field_path,
                        "expected_object_revision": operation.expected_object_revision,
                        "old_value": operation.old_value_jsonb,
                        "new_value": operation.new_value_jsonb,
                    }
                )
            current_document = build_casefile_document(self.session, owned)
            target_finding_keys = self._target_finding_keys(
                project_id,
                owned.draft.id,
                target_finding_ids,
            )
            impact_simulation = self._simulate_patch_batch(
                current_document,
                operations,
                selected,
                registries,
                target_finding_keys=target_finding_keys,
            )
            if patch_set.review_mode == "atomic":
                current_impact_hash = general_mutation_impact_hash(impact_simulation)
                if (
                    patch_set.impact_hash is None
                    or current_impact_hash != patch_set.impact_hash
                    or (
                        confirmed_impact_hash is not None
                        and confirmed_impact_hash != patch_set.impact_hash
                    )
                ):
                    raise ApplicationError(
                        "agent_patch_impact_hash_mismatch",
                        "影响范围已变化，请重新审阅整组修改。",
                        status_code=409,
                        details={
                            "current_impact_hash": current_impact_hash,
                            "patch_impact_hash": patch_set.impact_hash,
                        },
                    )
                if patch_set.contains_delete and confirmed_impact_hash is None:
                    raise ApplicationError(
                        "agent_patch_delete_impact_confirmation_required",
                        "删除操作需要确认当前影响范围。",
                        status_code=422,
                        details={"impact_hash": patch_set.impact_hash},
                    )
            simulation = (
                self._simulate_patch_batch(
                    current_document,
                    operations,
                    selected,
                    registries,
                    target_finding_keys=target_finding_keys,
                    accepted_debt_finding_keys=accepted_debt_finding_keys or [],
                    debt_acceptance_reason=debt_acceptance_reason,
                )
                if accepted_debt_finding_keys
                else impact_simulation
            )
            if not simulation.can_apply:
                if simulation.reason_code == "post_document_invalid":
                    validate_casefile(dict(simulation.document))
                raise ApplicationError(
                    simulation.reason_code or "agent_patch_verification_blocked",
                    "应用前验证未通过，Draft 未发生变化。",
                    status_code=409,
                    details={"simulation": simulation.as_dict()},
                )
            pre_result = VerificationEngine(
                profile="fast",
                draft_revision=expected_revision,
                closure_policy_version=CLOSURE_POLICY_VERSION,
            ).verify(current_document)
            pre_run = (
                VerificationService(self.session).record_result(
                    owned=owned,
                    document=current_document,
                    result=pre_result,
                    profile="fast",
                    trigger="pre_apply",
                    patch_set_id=patch_set.id,
                )
                if VerificationService.enabled_for_persistence()
                else None
            )
            for operation in operations:
                operation.decision = "accepted" if operation.id in selected else "rejected"
                operation.reviewed_at = reviewed_at
            mutation_set = self._mutation_set_from_patch_operations(
                owned,
                patch_set,
                operations,
                selected,
                registries,
            )
            revision, group_no, applied_simulation = V1EditingService(
                self.session
            ).apply_mutation_set(
                owned,
                mutation_set=mutation_set,
                actor_user_id=actor_user_id,
                draft_operation_type="logical_mutation_apply",
                expected_candidate_hash=patch_set.candidate_hash,
                accepted_debt_finding_keys=tuple(accepted_debt_finding_keys or ()),
                debt_acceptance_reason=debt_acceptance_reason,
                target_finding_keys=tuple(target_finding_keys),
                source_patch_set_id=patch_set.id,
            )
            applied = batch
            patch_set.status = "applied"
            patch_set.baseline_hash = applied_simulation.baseline_hash
            patch_set.candidate_hash = applied_simulation.candidate_hash
            patch_set.applied_operation_group_no = group_no
            patch_set.applied_from_revision = expected_revision
            patch_set.applied_to_revision = revision
            patch_set.applied_at = reviewed_at
            current_document = build_casefile_document(self.session, owned)
            post_result = VerificationEngine(
                profile="fast",
                draft_revision=revision,
                closure_policy_version=CLOSURE_POLICY_VERSION,
            ).verify(current_document)
            post_run = (
                VerificationService(self.session).record_result(
                    owned=owned,
                    document=current_document,
                    result=post_result,
                    profile="fast",
                    trigger="post_apply",
                    patch_set_id=patch_set.id,
                    draft_revision=revision,
                )
                if VerificationService.enabled_for_persistence()
                else None
            )
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
                "pre_apply_verification_run_id": None if pre_run is None else pre_run.id,
                "post_apply_verification_run_id": None if post_run is None else post_run.id,
                "simulation": applied_simulation.as_dict(),
            }

    def simulate_agent_patch_set(
        self,
        actor_user_id: int,
        project_id: int,
        patch_set_id: int,
        *,
        expected_draft_id: int,
        base_revision: int,
        operation_ids: list[int] | None,
        target_finding_ids: list[int] | None = None,
        accepted_debt_finding_keys: list[str] | None = None,
        debt_acceptance_reason: str | None = None,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            patch_set = self._agent_patch_set(owned, patch_set_id, lock=True)
            if patch_set.status != "pending":
                raise ApplicationError(
                    "agent_patch_not_pending",
                    "只有待处理的 Agent 建议才能模拟。",
                    status_code=409,
                    details={"status": patch_set.status},
                )
            if (
                owned.draft.id != expected_draft_id
                or owned.draft.revision != base_revision
                or patch_set.base_draft_revision != base_revision
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
                )
            )
            selected = (
                {operation.id for operation in operations}
                if operation_ids is None
                else set(operation_ids)
            )
            known = {operation.id for operation in operations}
            self._validate_patch_selection(patch_set, operation_ids)
            if not selected.issubset(known):
                raise ApplicationError(
                    "agent_patch_selection_invalid",
                    "已选择的操作不属于当前 Agent 修改批次。",
                    status_code=422,
                    details={"unknown_operation_ids": sorted(selected - known)},
                )
            registries = {
                row.id: row
                for row in self.session.scalars(
                    select(CaseFileObject).where(
                        CaseFileObject.id.in_(
                            operation.target_object_id
                            for operation in operations
                            if operation.target_object_id is not None
                        )
                    )
                )
            }
            document = build_casefile_document(self.session, owned)
            target_finding_keys = self._target_finding_keys(
                project_id,
                owned.draft.id,
                target_finding_ids,
            )
            simulation = self._simulate_patch_batch(
                document,
                operations,
                selected,
                registries,
                target_finding_keys=target_finding_keys,
                accepted_debt_finding_keys=accepted_debt_finding_keys or [],
                debt_acceptance_reason=debt_acceptance_reason,
            )
            patch_set.baseline_hash = simulation.baseline_hash
            patch_set.candidate_hash = simulation.candidate_hash
            return {
                "patch_set_id": patch_set.id,
                "draft_id": owned.draft.id,
                "base_revision": base_revision,
                "contains_delete": patch_set.contains_delete,
                "status": patch_set.status,
                "simulation": simulation.as_dict(),
                "can_apply": simulation.can_apply,
                "impact_hash": (
                    general_mutation_impact_hash(simulation)
                    if patch_set.review_mode == "atomic"
                    else None
                ),
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
            current_document = build_casefile_document(self.session, owned)
            target_document: dict[str, Any] | None = None
            if (
                patch_set.candidate_hash is not None
                and casefile_content_hash(current_document) != patch_set.candidate_hash
            ):
                raise ApplicationError(
                    "agent_patch_undo_hash_conflict",
                    "当前 Draft 与撤销栈顶不一致，请改用 Revert 审阅。",
                    status_code=409,
                )
            registries = {
                row.id: row
                for row in self.session.scalars(
                    select(CaseFileObject).where(
                        CaseFileObject.id.in_(
                            operation.target_object_id
                            for operation in operations
                            if operation.target_object_id is not None
                        )
                    )
                )
            }
            if patch_set.review_mode == "atomic":
                original_apply = self.session.scalar(
                    select(DraftOperation).where(
                        DraftOperation.draft_id == owned.draft.id,
                        DraftOperation.operation_group_no == patch_set.applied_operation_group_no,
                    )
                )
                before_payload = (
                    original_apply.old_value_jsonb
                    if original_apply is not None
                    and isinstance(original_apply.old_value_jsonb, dict)
                    else {}
                )
                target_document = before_payload.get("document")
                if not isinstance(target_document, dict):
                    raise RuntimeError("Atomic patch history has no before document")
                inverse_mutation = self._mutation_from_document_history(
                    current_document,
                    target_document,
                    mutation_set_id=f"agent_patch_undo_{patch_set.id}",
                    draft_id=owned.draft.id,
                    base_revision=owned.draft.revision,
                )
            else:
                inverse_mutation = MutationSet(
                    mutation_set_id=f"agent_patch_undo_{patch_set.id}_{owned.draft.revision}",
                    base_draft_id=owned.draft.id,
                    base_revision=owned.draft.revision,
                    operations=tuple(
                        self._inverse_logical_operations_from_patch(
                            operations, registries, current_document
                        )
                    ),
                    actor="author",
                    closure_policy_version=CLOSURE_POLICY_VERSION,
                )
            engine = VerificationEngine(
                profile="fast",
                closure_policy_version=CLOSURE_POLICY_VERSION,
            )
            simulation = engine.simulate_mutation_set(current_document, inverse_mutation)
            history_debt_keys: tuple[str, ...] = ()
            history_debt_reason: str | None = None
            if patch_set.review_mode == "atomic":
                assert target_document is not None
                history_debt_keys, history_debt_reason = exact_history_restore_authorization(
                    simulation,
                    target_document,
                )
                if history_debt_keys:
                    simulation = engine.simulate_mutation_set(
                        current_document,
                        inverse_mutation,
                        accepted_debt_finding_keys=history_debt_keys,
                        debt_acceptance_reason=history_debt_reason,
                        allow_author_debt_acceptance=True,
                    )
            if not simulation.can_apply:
                if simulation.reason_code == "post_document_invalid":
                    validate_casefile(dict(simulation.document))
                raise ApplicationError(
                    simulation.reason_code or "agent_patch_undo_verification_blocked",
                    "撤销前验证未通过，Draft 未发生变化。",
                    status_code=409,
                    details={"simulation": simulation.as_dict()},
                )
            pre_result = VerificationEngine(
                profile="fast",
                draft_revision=expected_revision,
                closure_policy_version=CLOSURE_POLICY_VERSION,
            ).verify(current_document)
            pre_run = (
                VerificationService(self.session).record_result(
                    owned=owned,
                    document=current_document,
                    result=pre_result,
                    profile="fast",
                    trigger="pre_apply",
                    patch_set_id=patch_set.id,
                )
                if VerificationService.enabled_for_persistence()
                else None
            )
            revision, group_no, simulation = V1EditingService(self.session).apply_mutation_set(
                owned,
                mutation_set=inverse_mutation,
                actor_user_id=actor_user_id,
                draft_operation_type="logical_mutation_undo",
                source_patch_set_id=patch_set.id,
                source_closure_policy_version=patch_set.closure_policy_version,
                accepted_debt_finding_keys=history_debt_keys,
                debt_acceptance_reason=history_debt_reason,
            )
            now = datetime.now(UTC)
            patch_set.status = "undone"
            patch_set.undone_operation_group_no = group_no
            patch_set.undone_to_revision = revision
            patch_set.undone_at = now
            current_document = build_casefile_document(self.session, owned)
            patch_set.baseline_hash = casefile_content_hash(current_document)
            post_result = VerificationEngine(
                profile="fast",
                draft_revision=revision,
                closure_policy_version=CLOSURE_POLICY_VERSION,
            ).verify(current_document)
            post_run = (
                VerificationService(self.session).record_result(
                    owned=owned,
                    document=current_document,
                    result=post_result,
                    profile="fast",
                    trigger="post_apply",
                    patch_set_id=patch_set.id,
                    draft_revision=revision,
                )
                if VerificationService.enabled_for_persistence()
                else None
            )
            self.session.flush()
            return {
                **self._patch_set_view(
                    owned,
                    patch_set,
                    operations=operations,
                    validator_issues=[],
                ),
                "draft_revision": revision,
                "pre_apply_verification_run_id": None if pre_run is None else pre_run.id,
                "post_apply_verification_run_id": None if post_run is None else post_run.id,
                "simulation": simulation.as_dict(),
            }

    def _simulate_patch_batch(
        self,
        document: dict[str, Any],
        operations: list[AgentPatchOperation],
        selected: set[int],
        registries: dict[int, CaseFileObject],
        *,
        inverse: bool = False,
        target_finding_keys: list[str] | None = None,
        accepted_debt_finding_keys: list[str] | None = None,
        debt_acceptance_reason: str | None = None,
    ) -> MutationSimulation:
        if inverse:
            logical: list[CreateObject | UpdateField | DeleteObject] = []
            for operation in operations:
                if operation.id not in selected:
                    continue
                registry = (
                    None
                    if operation.target_object_id is None
                    else registries.get(operation.target_object_id)
                )
                if registry is None:
                    raise RuntimeError("Agent patch target object disappeared")
                logical.append(
                    UpdateField(
                        operation_id=f"undo_{operation.operation_id}",
                        object_id=operation.target_object_key,
                        field_path=operation.field_path,
                        old_value=operation.new_value_jsonb,
                        new_value=operation.old_value_jsonb,
                        expected_object_revision=registry.revision,
                    )
                )
            mutation = MutationSet(
                mutation_set_id=f"agent_patch_undo_{operations[0].patch_set_id}",
                base_draft_id=operations[0].draft_id,
                base_revision=max(
                    (registry.revision for registry in registries.values()), default=1
                ),
                operations=tuple(logical),
                actor="author",
            )
        else:
            patch_set = self.session.get(AgentPatchSet, operations[0].patch_set_id)
            if patch_set is None:
                raise RuntimeError("Agent patch set disappeared")
            owned_stub_revision = patch_set.base_draft_revision
            logical = self._logical_operations_from_patch(operations, selected, registries)
            mutation = MutationSet(
                mutation_set_id=f"agent_patch_{patch_set.id}_{owned_stub_revision}",
                base_draft_id=patch_set.draft_id,
                base_revision=owned_stub_revision,
                operations=tuple(logical),
                actor="agent",
                mode=patch_set.mutation_mode,  # type: ignore[arg-type]
                closure_policy_version=patch_set.closure_policy_version,
            )
        return VerificationEngine(
            profile="fast",
            closure_policy_version=CLOSURE_POLICY_VERSION,
        ).simulate_mutation_set(
            document,
            mutation,
            target_finding_keys=target_finding_keys or (),
            accepted_debt_finding_keys=accepted_debt_finding_keys or (),
            debt_acceptance_reason=debt_acceptance_reason,
            allow_author_debt_acceptance=True,
        )

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

    def _agent_run_task(
        self,
        actor_user_id: int,
        project_id: int,
        run_id: int,
        *,
        lock: bool = False,
    ) -> TaskRun:
        owned = self._owned(actor_user_id, project_id)
        statement = select(TaskRun).where(
            TaskRun.id == run_id,
            TaskRun.project_id == owned.project.id,
            TaskRun.task_type == "casefile_chat",
        )
        if lock:
            statement = statement.with_for_update()
        task = self.session.scalar(statement)
        if task is None:
            raise ApplicationError(
                "agent_run_not_found",
                "找不到该对话任务。",
                status_code=404,
            )
        return task

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
        projection_document = current_document or build_casefile_document(self.session, owned)
        object_labels = _patch_object_labels(projection_document)
        registries = {
            row.id: row
            for row in self.session.scalars(
                select(CaseFileObject).where(
                    CaseFileObject.id.in_(
                        operation.target_object_id
                        for operation in operations
                        if operation.target_object_id is not None
                    )
                )
            )
        }
        finding_ids_by_operation: dict[int, list[int]] = {}
        operation_ids = [operation.id for operation in operations]
        if operation_ids:
            links = list(
                self.session.scalars(
                    select(VerificationFindingPatchOperation).where(
                        VerificationFindingPatchOperation.project_id == owned.project.id,
                        VerificationFindingPatchOperation.patch_operation_id.in_(operation_ids),
                    )
                )
            )
            for link in links:
                finding_ids_by_operation.setdefault(link.patch_operation_id, []).append(
                    link.finding_id
                )
        if validator_issues is None:
            validator_issues = []
            if patch_set.status == "applied":
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
                validator_issues = _nonblocking_validator_issues(
                    projection_document, accepted
                )
        return {
            "patch_set_id": patch_set.id,
            "thread_id": patch_set.thread_id,
            "source_message_id": patch_set.source_message_id,
            "task_run_id": patch_set.task_run_id,
            "base_draft_revision": patch_set.base_draft_revision,
            "closure_policy_version": patch_set.closure_policy_version,
            "mutation_mode": patch_set.mutation_mode,
            "review_mode": patch_set.review_mode,
            "plan_version": patch_set.plan_version,
            "capability_policy_version": patch_set.capability_policy_version,
            "binder_version": patch_set.binder_version,
            "plan_hash": patch_set.plan_hash,
            "impact_hash": patch_set.impact_hash,
            "contains_delete": patch_set.contains_delete,
            "baseline_hash": patch_set.baseline_hash,
            "candidate_hash": patch_set.candidate_hash,
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
                        if (
                            registry := (
                                None
                                if operation.target_object_id is None
                                else registries.get(operation.target_object_id)
                            )
                        )
                        is None
                        else registry.object_id
                    ),
                    "object_type": (None if registry is None else registry.object_type),
                    "target_collection": operation.target_collection,
                    "target_object_key": operation.target_object_key,
                    "operation_type": operation.operation_type,
                    "field_path": operation.field_path,
                    "expected_object_revision": operation.expected_object_revision,
                    "old_value": operation.old_value_jsonb,
                    "new_value": operation.new_value_jsonb,
                    "reason": operation.reason,
                    "origin": operation.origin,
                    "decision": operation.decision,
                    "reviewed_at": _time(operation.reviewed_at),
                    "finding_ids": finding_ids_by_operation.get(operation.id, []),
                }
                for operation in operations
            ],
            "object_labels": object_labels,
            "validation_warning": bool(validator_issues),
            "validator_issues": validator_issues,
            "created_at": _time(patch_set.created_at),
            "updated_at": _time(patch_set.updated_at),
        }


def _patch_object_labels(document: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    labels: dict[str, dict[str, str | None]] = {}
    for object_type, collection in COLLECTIONS.items():
        values = document.get(collection)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                continue
            name = next(
                (
                    candidate.strip()[:240]
                    for key in ("name", "title")
                    if isinstance((candidate := value.get(key)), str)
                    and candidate.strip()
                ),
                None,
            )
            labels[str(value["id"])] = {
                "object_type": object_type,
                "name": name,
            }
    return labels


__all__ = ["AgentWorkflowMixin"]
