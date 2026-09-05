"""Stable transactional façade for Workflow application use cases.

Owns dependency construction and the public ``WorkflowService(session)`` API.
Does not own validation, projections, event serialization, or individual use-
case implementations; those live in the delegated workflow modules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from casefile_contracts import PublicGoalDelivery, PublicGoalEvent, PublicGoalSession
from sqlalchemy.orm import Session

from casefile.agent_runtime.goal.contracts import FrozenGoal, GoalExecutionCheckpoint
from casefile.application.task_events import append_task_event
from casefile.application.workflow.agent import AgentWorkflowMixin
from casefile.application.workflow.content import ContentWorkflowMixin
from casefile.application.workflow.goal_session import GoalSessionService
from casefile.application.workflow.mutation_history import redo_agent_patch_set
from casefile.application.workflow_common import (
    DEFAULT_BUDGET,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    SUPPORTED_PROVIDERS,
)
from casefile.application.workflow_views import (
    event_view,
    source_view,
    task_failure_view,
    task_view,
)
from casefile.data_postgres.models import TaskRun
from casefile.data_postgres.repositories import ProjectRepository


class WorkflowService(
    ContentWorkflowMixin,
    AgentWorkflowMixin,
):
    """Transactional facade for the user-visible Agent generation workflow."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.goals = GoalSessionService(session, self.projects)

    def redo_agent_patch_set(
        self,
        actor_user_id: int,
        project_id: int,
        patch_set_id: int,
        *,
        expected_draft_id: int,
        expected_revision: int,
    ) -> dict[str, Any]:
        return redo_agent_patch_set(
            self.session,
            self.projects,
            actor_user_id,
            project_id,
            patch_set_id,
            expected_draft_id=expected_draft_id,
            expected_revision=expected_revision,
        )

    def get_agent_goal(
        self,
        actor_user_id: int,
        project_id: int,
        goal_id: int,
    ) -> PublicGoalSession:
        return self.goals.get_agent_goal(
            actor_user_id,
            project_id,
            goal_id,
        )

    def list_agent_goal_events(
        self,
        actor_user_id: int,
        project_id: int,
        goal_id: int,
        *,
        after_sequence: int = 0,
    ) -> list[PublicGoalEvent]:
        return self.goals.list_agent_goal_events(
            actor_user_id,
            project_id,
            goal_id,
            after_sequence=after_sequence,
        )

    def list_agent_goal_deliveries(
        self,
        actor_user_id: int,
        project_id: int,
        goal_id: int,
    ) -> list[PublicGoalDelivery]:
        return self.goals.list_agent_goal_deliveries(
            actor_user_id,
            project_id,
            goal_id,
        )

    def cancel_agent_goal(
        self,
        actor_user_id: int,
        project_id: int,
        goal_id: int,
    ) -> PublicGoalSession:
        return self.goals.cancel_agent_goal(
            actor_user_id,
            project_id,
            goal_id,
        )

    def initialize_agent_goal_task(
        self,
        task_run_id: int,
        frozen_goal: FrozenGoal,
    ) -> None:
        return self.goals.initialize_agent_goal_task(
            task_run_id,
            frozen_goal,
        )

    def pause_agent_goal_task_for_clarification(
        self,
        task_run_id: int,
        attempt_id: int,
        *,
        missing_info: list[str],
        usage: dict[str, Any],
    ) -> None:
        return self.goals.pause_agent_goal_task_for_clarification(
            task_run_id,
            attempt_id,
            missing_info=missing_info,
            usage=usage,
        )

    def initialize_waiting_goal_amendment_task(
        self,
        task_run_id: int,
        *,
        delivery_id: int,
        amended_goal: FrozenGoal,
        amendment_kind: str,
    ) -> None:
        return self.goals.initialize_waiting_goal_amendment_task(
            task_run_id,
            delivery_id=delivery_id,
            amended_goal=amended_goal,
            amendment_kind=amendment_kind,
        )

    def has_pending_agent_goal_control(self, task_run_id: int) -> bool:
        return self.goals.has_pending_agent_goal_control(
            task_run_id,
        )

    def pending_agent_goal_control_mode(self, task_run_id: int) -> str | None:
        return self.goals.pending_agent_goal_control_mode(
            task_run_id,
        )

    def claim_agent_goal_control(
        self,
        task_run_id: int,
        attempt_id: int,
    ) -> dict[str, Any] | None:
        return self.goals.claim_agent_goal_control(
            task_run_id,
            attempt_id,
        )

    def checkpoint_agent_goal_task(
        self,
        task_run_id: int,
        attempt_id: int,
        *,
        frozen_goal: FrozenGoal,
        checkpoint: GoalExecutionCheckpoint,
        safe_point: str,
        usage: dict[str, Any],
        tools: dict[str, Any],
        delivery_id: int | None = None,
        amended_goal: FrozenGoal | None = None,
        amendment_kind: str | None = None,
    ) -> int:
        return self.goals.checkpoint_agent_goal_task(
            task_run_id,
            attempt_id,
            frozen_goal=frozen_goal,
            checkpoint=checkpoint,
            safe_point=safe_point,
            usage=usage,
            tools=tools,
            delivery_id=delivery_id,
            amended_goal=amended_goal,
            amendment_kind=amendment_kind,
        )

    def replace_agent_goal_task(
        self,
        task_run_id: int,
        attempt_id: int,
        *,
        frozen_goal: FrozenGoal,
        checkpoint: GoalExecutionCheckpoint,
        delivery_id: int,
        replacement_goal: FrozenGoal,
        safe_point: str,
        usage: dict[str, Any],
        tools: dict[str, Any],
    ) -> int:
        return self.goals.replace_agent_goal_task(
            task_run_id,
            attempt_id,
            frozen_goal=frozen_goal,
            checkpoint=checkpoint,
            delivery_id=delivery_id,
            replacement_goal=replacement_goal,
            safe_point=safe_point,
            usage=usage,
            tools=tools,
        )

    def finalize_agent_goal_task_failure(
        self,
        task: TaskRun,
        *,
        now: datetime,
        reason_code: str,
    ) -> None:
        return self.goals.finalize_agent_goal_task_failure(
            task,
            now=now,
            reason_code=reason_code,
        )


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
