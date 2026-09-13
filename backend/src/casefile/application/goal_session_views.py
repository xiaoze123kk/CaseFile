"""Read-only Goal queries and public session, transition and delivery projections."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.application.goal_session_state import TERMINAL_GOAL_STATUSES
from casefile.data_postgres.models import (
    AgentGoalDelivery,
    AgentGoalSession,
    AgentGoalTaskRun,
    AgentGoalTransition,
    TaskRun,
)
from casefile_contracts import PublicGoalDelivery, PublicGoalEvent, PublicGoalSession

_ACTIVE_TASK_STATUSES = ("queued", "running", "cancelling")


_WAITING_BY_STATUS = {
    "waiting_clarification": "clarification",
    "waiting_patch_review": "patch_review",
    "stale": "stale",
}


def public_goal_session(session: Session, goal: AgentGoalSession) -> PublicGoalSession:
    active_run_id: int | None = None
    if goal.status not in TERMINAL_GOAL_STATUSES:
        active_run_id = session.scalar(
            select(TaskRun.id)
            .where(
                TaskRun.project_id == goal.project_id,
                TaskRun.status.in_(_ACTIVE_TASK_STATUSES),
                (
                    (TaskRun.input_message_id == goal.source_message_id)
                    | (TaskRun.input_jsonb["goal_session"]["goal_id"].as_integer() == goal.id)
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


def public_goal_event(
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
        "can_follow_up": status == "completed" and revision >= 1,
        "can_replace": not terminal and revision >= 1,
        "cancellable": not terminal,
    }


def _waiting_for(status: str) -> str:
    return _WAITING_BY_STATUS.get(status, "none")
