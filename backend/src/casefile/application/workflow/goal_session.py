"""M3.8 GoalSession HTTP-facing application use cases and public projections."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from casefile_contracts import PublicGoalDelivery, PublicGoalEvent, PublicGoalSession
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.application.errors import ApplicationError
from casefile.application.goal_session_repository import GoalSessionRepository
from casefile.application.goal_session_state import (
    TERMINAL_GOAL_STATUSES,
    GoalSessionStateError,
    require_expected_revision,
)
from casefile.application.task_cancellation import (
    TERMINAL_TASK_STATUSES,
    finalize_task_cancellation,
)
from casefile.application.workflow_common import _append_event, _json_hash
from casefile.data_postgres.models import (
    AgentGoalDelivery,
    AgentGoalSession,
    AgentGoalTaskRun,
    AgentGoalTransition,
    AgentMessage,
    AgentThread,
    TaskRun,
)
from casefile.data_postgres.repositories import OwnedDraft, ProjectRepository

_CONTROL_MODES = frozenset({"steer", "follow_up", "replace"})
_ACTIVE_TASK_STATUSES = ("queued", "running", "cancelling")
_WAITING_BY_STATUS = {
    "waiting_clarification": "clarification",
    "waiting_patch_review": "patch_review",
    "stale": "stale",
}


class GoalSessionWorkflowMixin:
    """Goal API behavior mixed into the stable ``WorkflowService`` facade."""

    session: Session
    projects: ProjectRepository
    _owned: Any
    _agent_thread: Any

    def get_agent_goal(
        self,
        actor_user_id: int,
        project_id: int,
        goal_id: int,
    ) -> PublicGoalSession:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            goal = self._goal_session(owned, goal_id)
            return self._public_goal_session(goal)

    def list_agent_goal_events(
        self,
        actor_user_id: int,
        project_id: int,
        goal_id: int,
        *,
        after_sequence: int = 0,
    ) -> list[PublicGoalEvent]:
        if after_sequence < 0:
            raise ApplicationError(
                "agent_goal_event_cursor_invalid",
                "Goal 事件序号必须是非负整数。",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            goal = self._goal_session(owned, goal_id)
            transitions = self.session.scalars(
                select(AgentGoalTransition)
                .where(
                    AgentGoalTransition.goal_session_id == goal.id,
                    AgentGoalTransition.sequence_no > after_sequence,
                )
                .order_by(AgentGoalTransition.sequence_no)
            )
            current = self._public_goal_session(goal)
            return [self._public_goal_event(row, current) for row in transitions]

    def cancel_agent_goal(
        self,
        actor_user_id: int,
        project_id: int,
        goal_id: int,
    ) -> PublicGoalSession:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            goal = self._goal_session(owned, goal_id, lock=True)
            if goal.status in TERMINAL_GOAL_STATUSES:
                return self._public_goal_session(goal)

            now = datetime.now(UTC)
            deliveries = list(
                self.session.scalars(
                    select(AgentGoalDelivery)
                    .where(
                        AgentGoalDelivery.goal_session_id == goal.id,
                        AgentGoalDelivery.status.in_(("queued", "claimed")),
                    )
                    .with_for_update()
                )
            )
            for delivery in deliveries:
                delivery.status = "cancelled"
                delivery.cancelled_at = now
                delivery.reason_code = "goal_cancelled"
                response = self.session.get(AgentMessage, delivery.response_message_id)
                if response is not None and response.status == "pending":
                    response.status = "cancelled"
                    response.content_text = None

            tasks = self._goal_tasks(goal, lock=True)
            for task in tasks:
                if task.status in TERMINAL_TASK_STATUSES:
                    continue
                if task.cancel_requested_at is None:
                    task.cancel_requested_at = now
                if task.status == "queued":
                    finalize_task_cancellation(self.session, task, now=now)
                    output = (
                        None
                        if task.output_message_id is None
                        else self.session.get(AgentMessage, task.output_message_id)
                    )
                    if output is not None:
                        output.status = "cancelled"
                        output.content_text = None
                    _append_event(
                        self.session,
                        task,
                        "task.cancelled",
                        "cancelled",
                        {"message": "Goal 已取消，任务尚未开始生成。"},
                    )
                elif task.status == "running":
                    task.status = "cancelling"
                    task.stage = "cancelling"
                    _append_event(
                        self.session,
                        task,
                        "task.cancel_requested",
                        "cancelling",
                        {"message": "Goal 已取消，正在安全结束当前步骤。"},
                    )

            try:
                GoalSessionRepository(self.session).transition(
                    goal,
                    target_status="cancelled",
                    reason_code="user_cancelled",
                    state_hash=_json_hash(
                        {
                            "goal_session_id": goal.id,
                            "status": "cancelled",
                            "reason": "user_cancelled",
                        }
                    ),
                    goal_revision_id=goal.current_revision_id,
                    source_message_id=goal.source_message_id,
                )
            except GoalSessionStateError as exc:
                raise _goal_state_error(exc) from exc
            return self._public_goal_session(goal)

    def _resolve_goal_delivery(
        self,
        thread: AgentThread,
        *,
        delivery_mode: str | None,
        expected_goal_id: int | None,
        expected_goal_revision: int | None,
    ) -> tuple[str | None, AgentGoalSession | None]:
        rollout = _goal_session_rollout()
        if rollout == "active" and _bounded_goal_rollout() != "active":
            raise ApplicationError(
                "agent_goal_state_conflict",
                "GoalSession active rollout 要求 bounded Goal runtime 同时启用。",
                status_code=409,
            )
        active = self.session.scalar(
            select(AgentGoalSession)
            .where(
                AgentGoalSession.project_id == thread.project_id,
                AgentGoalSession.thread_id == thread.id,
                AgentGoalSession.status.not_in(tuple(TERMINAL_GOAL_STATUSES)),
            )
            .with_for_update()
        )
        if rollout == "off":
            if delivery_mode is not None:
                raise ApplicationError(
                    "agent_goal_state_conflict",
                    "GoalSession 功能尚未启用。",
                    status_code=409,
                )
            return None, None
        if delivery_mode is None:
            if active is not None:
                if rollout == "shadow":
                    raise ApplicationError(
                        "agent_thread_busy",
                        "当前 Agent 对话仍有任务正在运行。",
                        status_code=409,
                        details={"goal_id": active.id},
                    )
                raise ApplicationError(
                    "agent_goal_delivery_mode_required",
                    "当前对话有进行中的 Goal，请明确选择继续、跟进或替换。",
                    status_code=409,
                    details={
                        "goal_id": active.id,
                        "current_revision": active.revision_count,
                    },
                )
            if _goal_session_rollout() == "off":
                return None, None
            return "new_goal", None

        if delivery_mode == "new_goal":
            if active is not None:
                raise ApplicationError(
                    "agent_goal_state_conflict",
                    "当前对话已有进行中的 Goal。",
                    status_code=409,
                    details={
                        "goal_id": active.id,
                        "current_revision": active.revision_count,
                    },
                )
            return delivery_mode, None

        if delivery_mode not in _CONTROL_MODES:
            raise ApplicationError(
                "agent_goal_delivery_mode_invalid",
                "不支持该 Goal 投递模式。",
                status_code=422,
            )
        if rollout != "active":
            raise ApplicationError(
                "agent_thread_busy",
                "当前 Agent 对话仍有任务正在运行。",
                status_code=409,
                details={} if active is None else {"goal_id": active.id},
            )
        if expected_goal_id is None or expected_goal_revision is None:
            raise ApplicationError(
                "agent_goal_revision_conflict",
                "继续 Goal 时必须提供期望 Goal 和 revision。",
                status_code=409,
            )
        goal = self.session.scalar(
            select(AgentGoalSession)
            .where(
                AgentGoalSession.id == expected_goal_id,
                AgentGoalSession.project_id == thread.project_id,
                AgentGoalSession.thread_id == thread.id,
            )
            .with_for_update()
        )
        if goal is None:
            raise ApplicationError(
                "agent_goal_not_found",
                "找不到该 Goal。",
                status_code=404,
            )
        if active is not None and active.id != goal.id:
            raise ApplicationError(
                "agent_goal_state_conflict",
                "当前对话的活跃 Goal 与请求不一致。",
                status_code=409,
            )
        try:
            require_expected_revision(goal.revision_count, expected_goal_revision)
        except GoalSessionStateError as exc:
            raise _goal_state_error(exc, goal=goal) from exc
        if delivery_mode in {"steer", "replace"} and goal.status in TERMINAL_GOAL_STATUSES:
            raise ApplicationError(
                "agent_goal_state_conflict",
                "终态 Goal 不能继续或替换。",
                status_code=409,
            )
        if delivery_mode == "follow_up" and goal.status in {
            "cancelled",
            "superseded",
            "failed",
        }:
            raise ApplicationError(
                "agent_goal_state_conflict",
                "该 Goal 未成功完成，不能排队跟进。",
                status_code=409,
            )
        return delivery_mode, goal

    def _create_goal_session(
        self,
        owned: OwnedDraft,
        thread: AgentThread,
        *,
        source_message_id: int,
        actor_user_id: int,
        baseline_hash: str,
    ) -> AgentGoalSession:
        return GoalSessionRepository(self.session).create_interpreting(
            project_id=owned.project.id,
            casefile_id=owned.casefile.id,
            draft_id=owned.draft.id,
            thread_id=thread.id,
            source_message_id=source_message_id,
            actor_user_id=actor_user_id,
            runtime_version="goal-session-runtime.v1",
            policy_version="goal-session-policy.v1",
            capability_registry_version="casefile-chat-goal-capabilities.v1",
            baseline_draft_revision=owned.draft.revision,
            baseline_hash=baseline_hash,
            initial_state_hash=_json_hash(
                {
                    "project_id": owned.project.id,
                    "thread_id": thread.id,
                    "source_message_id": source_message_id,
                    "baseline_draft_revision": owned.draft.revision,
                    "baseline_hash": baseline_hash,
                    "status": "interpreting",
                }
            ),
        )

    def _queue_goal_delivery(
        self,
        goal: AgentGoalSession,
        *,
        thread_id: int,
        source_message_id: int,
        response_message_id: int,
        message_sequence_no: int,
        mode: str,
    ) -> AgentGoalDelivery:
        delivery = AgentGoalDelivery(
            project_id=goal.project_id,
            thread_id=thread_id,
            goal_session_id=goal.id,
            source_message_id=source_message_id,
            response_message_id=response_message_id,
            message_sequence_no=message_sequence_no,
            mode=mode,
            status="queued",
            expected_goal_revision=goal.revision_count,
        )
        self.session.add(delivery)
        self.session.flush()
        return delivery

    def _goal_session(
        self,
        owned: OwnedDraft,
        goal_id: int,
        *,
        lock: bool = False,
    ) -> AgentGoalSession:
        statement = select(AgentGoalSession).where(
            AgentGoalSession.id == goal_id,
            AgentGoalSession.project_id == owned.project.id,
            AgentGoalSession.casefile_id == owned.casefile.id,
            AgentGoalSession.draft_id == owned.draft.id,
        )
        if lock:
            statement = statement.with_for_update()
        goal = self.session.scalar(statement)
        if goal is None:
            raise ApplicationError(
                "agent_goal_not_found",
                "找不到该 Goal。",
                status_code=404,
            )
        return goal

    def _goal_tasks(
        self,
        goal: AgentGoalSession,
        *,
        lock: bool = False,
    ) -> list[TaskRun]:
        linked_ids = select(AgentGoalTaskRun.task_run_id).where(
            AgentGoalTaskRun.goal_session_id == goal.id
        )
        statement = select(TaskRun).where(
            TaskRun.project_id == goal.project_id,
            (TaskRun.id.in_(linked_ids) | (TaskRun.input_message_id == goal.source_message_id)),
        )
        if lock:
            statement = statement.with_for_update()
        return list(self.session.scalars(statement.order_by(TaskRun.id)))

    def _public_goal_session(self, goal: AgentGoalSession) -> PublicGoalSession:
        active_run_id: int | None = None
        if goal.status not in TERMINAL_GOAL_STATUSES:
            active_run_id = self.session.scalar(
                select(TaskRun.id)
                .where(
                    TaskRun.project_id == goal.project_id,
                    TaskRun.status.in_(_ACTIVE_TASK_STATUSES),
                    (
                        (TaskRun.input_message_id == goal.source_message_id)
                        | TaskRun.id.in_(
                            select(AgentGoalTaskRun.task_run_id).where(
                                AgentGoalTaskRun.goal_session_id == goal.id
                            )
                        )
                    ),
                )
                .order_by(TaskRun.id.desc())
                .limit(1)
            )
        return _public_goal_session_row(goal, active_run_id=active_run_id)

    def _public_goal_event(
        self,
        transition: AgentGoalTransition,
        current: PublicGoalSession,
    ) -> PublicGoalEvent:
        historical = PublicGoalSession.model_validate(
            {
                **current.model_dump(mode="json"),
                "status": transition.to_status,
                "waiting_for": _waiting_for(transition.to_status),
                **_goal_actions(transition.to_status, current.revision),
            }
        )
        return PublicGoalEvent.model_validate(
            {
                "sequence": transition.sequence_no,
                "event": "goal.transition",
                "status": transition.to_status,
                "waiting_for": _waiting_for(transition.to_status),
                "goal": historical,
            }
        )


def public_goal_delivery_view(delivery: AgentGoalDelivery) -> PublicGoalDelivery:
    return PublicGoalDelivery.model_validate(
        {
            "delivery_id": delivery.id,
            "goal_id": delivery.goal_session_id,
            "successor_goal_id": None,
            "mode": delivery.mode,
            "status": delivery.status,
            "message_id": delivery.source_message_id,
            "response_message_id": delivery.response_message_id,
            "expected_goal_revision": delivery.expected_goal_revision,
            "created_at": delivery.created_at,
            "updated_at": delivery.updated_at,
        }
    )


def _public_goal_session_row(
    goal: AgentGoalSession,
    *,
    active_run_id: int | None,
) -> PublicGoalSession:
    return PublicGoalSession.model_validate(
        {
            "goal_id": goal.id,
            "status": goal.status,
            "revision": goal.revision_count,
            "waiting_for": _waiting_for(goal.status),
            "active_run_id": active_run_id,
            "active_patch_id": goal.active_patch_set_id,
            **_goal_actions(goal.status, goal.revision_count),
            "created_at": goal.created_at,
            "updated_at": goal.updated_at,
        }
    )


def _goal_actions(status: str, revision: int) -> dict[str, bool]:
    terminal = status in TERMINAL_GOAL_STATUSES
    return {
        "can_steer": not terminal and revision >= 1,
        "can_follow_up": status not in {"cancelled", "superseded", "failed"},
        "can_replace": not terminal and revision >= 1,
        "cancellable": not terminal,
    }


def _waiting_for(status: str) -> str:
    return _WAITING_BY_STATUS.get(status, "none")


def _goal_session_rollout() -> str:
    value = os.getenv("CASEFILE_CHAT_GOAL_SESSION_ROLLOUT", "off").strip().lower()
    return value if value in {"off", "shadow", "active"} else "off"


def _bounded_goal_rollout() -> str:
    value = os.getenv("CASEFILE_CHAT_GOAL_ROLLOUT", "active").strip().lower()
    return value if value in {"off", "shadow", "active"} else "off"


def _goal_state_error(
    error: GoalSessionStateError,
    *,
    goal: AgentGoalSession | None = None,
) -> ApplicationError:
    messages = {
        "agent_goal_not_found": "找不到该 Goal。",
        "agent_goal_revision_conflict": "Goal 已被更新，请刷新后重试。",
        "agent_goal_budget_exhausted": "本轮协作已达到上限，请开始新目标。",
        "agent_goal_transition_invalid": "当前 Goal 状态不接受该操作。",
    }
    details = {} if goal is None else {"goal_id": goal.id, "current_revision": goal.revision_count}
    return ApplicationError(
        error.code,
        messages.get(error.code, "当前 Goal 状态不接受该操作。"),
        status_code=404 if error.code == "agent_goal_not_found" else 409,
        details=details,
    )


__all__ = ["GoalSessionWorkflowMixin", "public_goal_delivery_view"]
