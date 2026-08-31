"""Transactional persistence boundary for M3.8 GoalSession state."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.agent_runtime.goal.contracts import FrozenGoal
from casefile.application.goal_session_state import (
    GoalSessionStateError,
    require_budget_available,
    require_transition,
)
from casefile.data_postgres.models.goal_session import (
    AgentGoalDelivery,
    AgentGoalObligation,
    AgentGoalObligationDependency,
    AgentGoalRevision,
    AgentGoalSession,
    AgentGoalTaskRun,
    AgentGoalTransition,
)


class GoalSessionRepository:
    """Locks the aggregate root before counters, pointers, or status are changed."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_update(self, *, project_id: int, goal_session_id: int) -> AgentGoalSession:
        row = self.session.scalar(
            select(AgentGoalSession)
            .where(
                AgentGoalSession.project_id == project_id,
                AgentGoalSession.id == goal_session_id,
            )
            .with_for_update()
        )
        if row is None:
            raise GoalSessionStateError("agent_goal_not_found", "GoalSession was not found")
        return row

    def create_interpreting(
        self,
        *,
        project_id: int,
        casefile_id: int,
        draft_id: int,
        thread_id: int,
        source_message_id: int,
        actor_user_id: int,
        runtime_version: str,
        policy_version: str,
        capability_registry_version: str,
        baseline_draft_revision: int,
        baseline_hash: str,
        initial_state_hash: str,
        predecessor_goal_session_id: int | None = None,
    ) -> AgentGoalSession:
        row = AgentGoalSession(
            project_id=project_id,
            casefile_id=casefile_id,
            draft_id=draft_id,
            thread_id=thread_id,
            source_message_id=source_message_id,
            created_by_user_id=actor_user_id,
            predecessor_goal_session_id=predecessor_goal_session_id,
            status="interpreting",
            runtime_version=runtime_version,
            policy_version=policy_version,
            capability_registry_version=capability_registry_version,
            baseline_draft_revision=baseline_draft_revision,
            baseline_hash=baseline_hash,
        )
        self.session.add(row)
        self.session.flush()
        self._append_transition(
            row,
            from_status=None,
            to_status="interpreting",
            reason_code="goal_created",
            state_hash=initial_state_hash,
            source_message_id=source_message_id,
        )
        return row

    def append_revision(
        self,
        row: AgentGoalSession,
        *,
        source_message_id: int,
        amendment_kind: str,
        goal_text: str,
        source_excerpt: str,
        obligations_hash: str,
        state_hash: str,
        baseline_draft_revision: int,
        baseline_hash: str,
    ) -> AgentGoalRevision:
        require_budget_available(
            revision_count=row.revision_count,
            task_run_slice_count=row.task_run_slice_count,
            consumed_control_count=row.consumed_control_count,
            add_revisions=1,
        )
        parent_revision_id = row.current_revision_id
        revision_no = row.revision_count + 1
        revision = AgentGoalRevision(
            project_id=row.project_id,
            goal_session_id=row.id,
            revision_no=revision_no,
            parent_revision_id=parent_revision_id,
            source_message_id=source_message_id,
            amendment_kind=amendment_kind,
            goal_text=goal_text,
            source_excerpt=source_excerpt,
            obligations_hash=obligations_hash,
            state_hash=state_hash,
            baseline_draft_revision=baseline_draft_revision,
            baseline_hash=baseline_hash,
        )
        self.session.add(revision)
        self.session.flush()
        row.current_revision_id = revision.id
        row.revision_count = revision_no
        row.baseline_draft_revision = baseline_draft_revision
        row.baseline_hash = baseline_hash
        self.session.flush()
        return revision

    def append_frozen_revision(
        self,
        row: AgentGoalSession,
        *,
        source_message_id: int,
        amendment_kind: str,
        frozen_goal: FrozenGoal,
        source_excerpt: str,
        state_hash: str,
        baseline_draft_revision: int,
        baseline_hash: str,
    ) -> AgentGoalRevision:
        """Append one normalized revision and its immutable obligation DAG."""

        revision = self.append_revision(
            row,
            source_message_id=source_message_id,
            amendment_kind=amendment_kind,
            goal_text=frozen_goal.goal,
            source_excerpt=source_excerpt,
            obligations_hash=frozen_goal.obligations_hash,
            state_hash=state_hash,
            baseline_draft_revision=baseline_draft_revision,
            baseline_hash=baseline_hash,
        )
        capability_by_kind = {
            "analysis": "analyze",
            "audit": "audit",
            "mutation_proposal": "propose_mutation",
        }
        obligation_rows: dict[str, AgentGoalObligation] = {}
        for ordinal, obligation in enumerate(frozen_goal.obligations, start=1):
            obligation_row = AgentGoalObligation(
                project_id=row.project_id,
                goal_session_id=row.id,
                goal_revision_id=revision.id,
                obligation_key=obligation.obligation_id,
                ordinal=ordinal,
                capability=capability_by_kind[obligation.kind],
                target_state=obligation.target_state,
                instruction=obligation.source_excerpt,
                source_excerpt=obligation.source_excerpt,
            )
            self.session.add(obligation_row)
            obligation_rows[obligation.obligation_id] = obligation_row
        self.session.flush()
        for obligation in frozen_goal.obligations:
            child = obligation_rows[obligation.obligation_id]
            for dependency_key in obligation.depends_on:
                self.session.add(
                    AgentGoalObligationDependency(
                        project_id=row.project_id,
                        goal_session_id=row.id,
                        goal_revision_id=revision.id,
                        obligation_id=child.id,
                        depends_on_obligation_id=obligation_rows[dependency_key].id,
                    )
                )
        self.session.flush()
        return revision

    def bind_task_run(
        self,
        row: AgentGoalSession,
        *,
        goal_revision_id: int,
        task_run_id: int,
        trigger_kind: str,
    ) -> AgentGoalTaskRun:
        require_budget_available(
            revision_count=row.revision_count,
            task_run_slice_count=row.task_run_slice_count,
            consumed_control_count=row.consumed_control_count,
            add_task_run_slices=1,
        )
        slice_no = row.task_run_slice_count + 1
        binding = AgentGoalTaskRun(
            project_id=row.project_id,
            goal_session_id=row.id,
            goal_revision_id=goal_revision_id,
            task_run_id=task_run_id,
            slice_no=slice_no,
            trigger_kind=trigger_kind,
            status="active",
        )
        self.session.add(binding)
        row.task_run_slice_count = slice_no
        self.session.flush()
        return binding

    def claim_next_control(
        self,
        row: AgentGoalSession,
        *,
        claim_owner: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> AgentGoalDelivery | None:
        """Claim or recover the FIFO steer/replace at a TaskRun safe point.

        The first non-terminal control is authoritative.  A later queued item
        cannot bypass an unexpired claim, while an expired claim can be fenced
        to the current TaskAttempt without returning it to a raceable queued
        state.
        """

        delivery = self.session.scalar(
            select(AgentGoalDelivery)
            .where(
                AgentGoalDelivery.goal_session_id == row.id,
                AgentGoalDelivery.mode.in_(("steer", "replace")),
                AgentGoalDelivery.status.in_(("queued", "claimed")),
            )
            .order_by(AgentGoalDelivery.message_sequence_no)
            .limit(1)
            .with_for_update()
        )
        if delivery is None:
            return None
        if delivery.status == "claimed":
            if delivery.claimed_by == claim_owner:
                delivery.lease_expires_at = lease_expires_at
                self.session.flush()
                return delivery
            if delivery.lease_expires_at is None or delivery.lease_expires_at >= now:
                return None
            reason_code = "delivery_claim_recovered"
        else:
            reason_code = "delivery_claimed"
        delivery.status = "claimed"
        delivery.claimed_by = claim_owner
        delivery.claimed_at = now
        delivery.lease_expires_at = lease_expires_at
        delivery.reason_code = reason_code
        self.session.flush()
        return delivery

    def require_claimed_control(
        self,
        row: AgentGoalSession,
        *,
        delivery_id: int,
        claim_owner: str,
        mode: str,
    ) -> AgentGoalDelivery:
        """Lock and fence the claimed FIFO head before atomic consumption."""

        delivery = self.session.scalar(
            select(AgentGoalDelivery)
            .where(
                AgentGoalDelivery.goal_session_id == row.id,
                AgentGoalDelivery.mode.in_(("steer", "replace")),
                AgentGoalDelivery.status.in_(("queued", "claimed")),
            )
            .order_by(AgentGoalDelivery.message_sequence_no)
            .limit(1)
            .with_for_update()
        )
        if (
            delivery is None
            or delivery.id != delivery_id
            or delivery.mode != mode
            or delivery.status != "claimed"
            or delivery.claimed_by != claim_owner
        ):
            raise GoalSessionStateError(
                "agent_goal_delivery_conflict",
                "The FIFO Goal control claim is no longer owned by this TaskAttempt",
            )
        return delivery

    def transition(
        self,
        row: AgentGoalSession,
        *,
        target_status: str,
        reason_code: str,
        state_hash: str,
        goal_revision_id: int | None = None,
        source_message_id: int | None = None,
        task_run_id: int | None = None,
    ) -> AgentGoalTransition:
        require_transition(row.status, target_status)
        previous = row.status
        row.status = target_status
        if target_status in {"completed", "cancelled", "superseded", "failed"}:
            row.terminal_reason_code = reason_code
        transition = self._append_transition(
            row,
            from_status=previous,
            to_status=target_status,
            reason_code=reason_code,
            state_hash=state_hash,
            goal_revision_id=goal_revision_id,
            source_message_id=source_message_id,
            task_run_id=task_run_id,
        )
        self.session.flush()
        return transition

    def _append_transition(
        self,
        row: AgentGoalSession,
        *,
        from_status: str | None,
        to_status: str,
        reason_code: str,
        state_hash: str,
        goal_revision_id: int | None = None,
        source_message_id: int | None = None,
        task_run_id: int | None = None,
    ) -> AgentGoalTransition:
        sequence_no = int(
            self.session.scalar(
                select(func.coalesce(func.max(AgentGoalTransition.sequence_no), 0) + 1).where(
                    AgentGoalTransition.goal_session_id == row.id
                )
            )
            or 1
        )
        transition = AgentGoalTransition(
            project_id=row.project_id,
            goal_session_id=row.id,
            sequence_no=sequence_no,
            from_status=from_status,
            to_status=to_status,
            reason_code=reason_code,
            goal_revision_id=goal_revision_id,
            source_message_id=source_message_id,
            task_run_id=task_run_id,
            state_hash=state_hash,
        )
        self.session.add(transition)
        return transition


__all__ = ["GoalSessionRepository"]
