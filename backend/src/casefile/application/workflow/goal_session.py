"""M3.8 GoalSession HTTP-facing application use cases and public projections."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from casefile_contracts import PublicGoalDelivery, PublicGoalEvent, PublicGoalSession
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.agent_runtime.goal.contracts import FrozenGoal, GoalExecutionCheckpoint
from casefile.agent_runtime.goal.policy import stable_hash
from casefile.application.errors import ApplicationError
from casefile.application.goal_session_repository import GoalSessionRepository
from casefile.application.goal_session_state import (
    TERMINAL_GOAL_STATUSES,
    GoalSessionStateError,
    require_budget_available,
    require_expected_revision,
)
from casefile.application.task_cancellation import (
    TERMINAL_TASK_STATUSES,
    finalize_task_cancellation,
)
from casefile.application.workflow_common import _append_event, _json_hash
from casefile.data_postgres.models import (
    AgentGoalDelivery,
    AgentGoalObligation,
    AgentGoalObservation,
    AgentGoalRevision,
    AgentGoalSession,
    AgentGoalTaskRun,
    AgentGoalTransition,
    AgentMessage,
    AgentThread,
    TaskAttempt,
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
GOAL_CHECKPOINTED_PUBLIC_MESSAGE = "已保存当前进度，并将按你的新要求继续处理。"


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

    def initialize_agent_goal_task(
        self,
        task_run_id: int,
        frozen_goal: FrozenGoal,
    ) -> None:
        """Bind the first qualified TaskRun to revision 1 exactly once."""

        with self.session.begin():
            task = self.session.scalar(
                select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
            )
            if task is None or task.task_type != "casefile_chat":
                raise RuntimeError("Goal TaskRun disappeared or has an invalid task type")
            goal_id = _task_goal_id(task)
            if goal_id is None:
                raise RuntimeError("Goal TaskRun has no GoalSession lineage")
            repository = GoalSessionRepository(self.session)
            goal = repository.get_for_update(
                project_id=task.project_id,
                goal_session_id=goal_id,
            )
            existing = self.session.scalar(
                select(AgentGoalTaskRun)
                .where(AgentGoalTaskRun.task_run_id == task.id)
                .with_for_update()
            )
            if existing is not None:
                existing_revision = self.session.get(
                    AgentGoalRevision,
                    existing.goal_revision_id,
                )
                obligation_count = int(
                    self.session.scalar(
                        select(func.count(AgentGoalObligation.id)).where(
                            AgentGoalObligation.goal_revision_id == existing.goal_revision_id
                        )
                    )
                    or 0
                )
                if (
                    existing_revision is None
                    or existing_revision.obligations_hash != frozen_goal.obligations_hash
                    or obligation_count != len(frozen_goal.obligations)
                ):
                    raise RuntimeError("Bound GoalRevision obligations do not match")
                return
            if goal.status != "interpreting" or goal.revision_count != 0:
                raise GoalSessionStateError(
                    "agent_goal_state_conflict",
                    "GoalSession cannot initialize another first TaskRun",
                )
            source = self.session.get(AgentMessage, goal.source_message_id)
            if source is None or not source.content_text:
                raise RuntimeError("GoalSession source message disappeared")
            state_hash = stable_hash(
                {
                    "goal_id": goal.id,
                    "revision": 1,
                    "frozen_goal": frozen_goal.model_dump(mode="json"),
                    "baseline_hash": goal.baseline_hash,
                }
            )
            revision = repository.append_frozen_revision(
                goal,
                source_message_id=source.id,
                amendment_kind="initial",
                frozen_goal=frozen_goal,
                source_excerpt=source.content_text,
                state_hash=state_hash,
                baseline_draft_revision=goal.baseline_draft_revision,
                baseline_hash=goal.baseline_hash,
            )
            repository.bind_task_run(
                goal,
                goal_revision_id=revision.id,
                task_run_id=task.id,
                trigger_kind="initial",
            )
            repository.transition(
                goal,
                target_status="running",
                reason_code="goal_qualified",
                state_hash=state_hash,
                goal_revision_id=revision.id,
                source_message_id=source.id,
                task_run_id=task.id,
            )

    def has_pending_agent_goal_control(self, task_run_id: int) -> bool:
        """Read the FIFO queue without claiming it; the safe-point transaction rechecks."""

        if _goal_session_rollout() != "active":
            return False
        with self.session.begin():
            task = self.session.get(TaskRun, task_run_id)
            if task is None:
                return False
            goal_id = _task_goal_id(task)
            if goal_id is None:
                return False
            return self.session.scalar(
                select(AgentGoalDelivery.id)
                .where(
                    AgentGoalDelivery.goal_session_id == goal_id,
                    AgentGoalDelivery.mode == "steer",
                    AgentGoalDelivery.status == "queued",
                )
                .order_by(AgentGoalDelivery.message_sequence_no)
                .limit(1)
            ) is not None

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
    ) -> int:
        """Atomically end one slice, consume one steer, and queue its successor."""

        checkpoint_payload = checkpoint.model_dump(mode="json")
        checkpoint_hash = stable_hash(checkpoint_payload)
        if checkpoint.obligations_hash != frozen_goal.obligations_hash:
            raise GoalSessionStateError(
                "agent_goal_state_conflict",
                "Checkpoint does not match the frozen Goal obligations",
            )
        if any(item.capability == "propose_mutation" for item in checkpoint.observations):
            raise GoalSessionStateError(
                "agent_goal_state_conflict",
                "Mutation checkpoints require the M3.8-04 PatchSet boundary",
            )
        with self.session.begin():
            task = self.session.scalar(
                select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
            )
            attempt = self.session.scalar(
                select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
            )
            if task is None or attempt is None:
                raise RuntimeError("TaskRun or TaskAttempt disappeared at Goal safe point")
            if task.status != "running" or attempt.status != "running":
                raise RuntimeError("Goal safe point no longer owns a running TaskRun")
            if attempt.task_run_id != task.id:
                raise RuntimeError("TaskAttempt does not own the Goal TaskRun")
            goal_id = _task_goal_id(task)
            if goal_id is None:
                raise RuntimeError("Goal TaskRun has no GoalSession lineage")
            repository = GoalSessionRepository(self.session)
            goal = repository.get_for_update(
                project_id=task.project_id,
                goal_session_id=goal_id,
            )
            if goal.status != "running" or goal.current_revision_id is None:
                raise GoalSessionStateError(
                    "agent_goal_state_conflict",
                    "GoalSession is not running at the safe point",
                )
            current_revision = self.session.get(AgentGoalRevision, goal.current_revision_id)
            if (
                current_revision is None
                or current_revision.obligations_hash != checkpoint.obligations_hash
            ):
                raise GoalSessionStateError(
                    "agent_goal_state_conflict",
                    "Checkpoint does not match the current GoalRevision",
                )
            binding = self.session.scalar(
                select(AgentGoalTaskRun)
                .where(
                    AgentGoalTaskRun.goal_session_id == goal.id,
                    AgentGoalTaskRun.task_run_id == task.id,
                    AgentGoalTaskRun.status == "active",
                )
                .with_for_update()
            )
            if binding is None or binding.goal_revision_id != goal.current_revision_id:
                raise RuntimeError("Goal TaskRun binding is not the active revision")
            delivery = self.session.scalar(
                select(AgentGoalDelivery)
                .where(
                    AgentGoalDelivery.goal_session_id == goal.id,
                    AgentGoalDelivery.mode == "steer",
                    AgentGoalDelivery.status == "queued",
                )
                .order_by(AgentGoalDelivery.message_sequence_no)
                .limit(1)
                .with_for_update()
            )
            if delivery is None:
                raise GoalSessionStateError(
                    "agent_goal_delivery_conflict",
                    "No queued steer remains at the safe point",
                )
            require_budget_available(
                revision_count=goal.revision_count,
                task_run_slice_count=goal.task_run_slice_count,
                consumed_control_count=goal.consumed_control_count,
                add_revisions=1,
                add_task_run_slices=1,
                add_consumed_controls=1,
            )
            self._persist_goal_checkpoint_observations(
                goal=goal,
                binding=binding,
                task=task,
                checkpoint=checkpoint,
            )
            now = datetime.now(UTC)
            output_message = self.session.get(AgentMessage, task.output_message_id)
            source_message = self.session.get(AgentMessage, delivery.source_message_id)
            response_message = self.session.get(AgentMessage, delivery.response_message_id)
            thread = self.session.scalar(
                select(AgentThread)
                .where(
                    AgentThread.id == task.agent_thread_id,
                    AgentThread.project_id == task.project_id,
                )
                .with_for_update()
            )
            if (
                output_message is None
                or source_message is None
                or not source_message.content_text
                or response_message is None
                or thread is None
            ):
                raise RuntimeError("Goal continuation message lineage disappeared")
            if output_message.status != "pending" or response_message.status != "pending":
                raise RuntimeError("Goal continuation assistant message is not pending")

            output_message.status = "completed"
            output_message.content_text = GOAL_CHECKPOINTED_PUBLIC_MESSAGE
            binding.status = "checkpointed"
            binding.checkpoint_hash = checkpoint_hash
            binding.finished_at = now
            task.status = "succeeded"
            task.stage = "checkpointed"
            task.usage_jsonb = usage
            task.completed_at = now
            task.leased_by = None
            task.lease_expires_at = None
            self.session.flush()

            delivery.status = "consumed"
            delivery.consumed_at = now
            delivery.reason_code = "steer_checkpointed"
            goal.consumed_control_count += 1
            revision_state_hash = stable_hash(
                {
                    "goal_id": goal.id,
                    "parent_revision": goal.revision_count,
                    "delivery_id": delivery.id,
                    "checkpoint_hash": checkpoint_hash,
                }
            )
            revision = repository.append_frozen_revision(
                goal,
                source_message_id=source_message.id,
                amendment_kind="refine",
                frozen_goal=frozen_goal,
                source_excerpt=source_message.content_text,
                state_hash=revision_state_hash,
                baseline_draft_revision=goal.baseline_draft_revision,
                baseline_hash=goal.baseline_hash,
            )
            history_rows = list(
                self.session.scalars(
                    select(AgentMessage)
                    .where(
                        AgentMessage.thread_id == thread.id,
                        AgentMessage.sequence_no < source_message.sequence_no,
                        AgentMessage.status == "completed",
                    )
                    .order_by(AgentMessage.sequence_no)
                )
            )
            next_input = dict(task.input_jsonb)
            next_input.update(
                {
                    "history": [
                        {"role": message.role, "content": message.content_text}
                        for message in history_rows
                        if message.content_text is not None
                    ],
                    "message": source_message.content_text,
                    "goal_session": {
                        "goal_id": goal.id,
                        "goal_revision": revision.revision_no,
                        "runtime_version": goal.runtime_version,
                        "policy_version": goal.policy_version,
                        "capability_registry_version": goal.capability_registry_version,
                    },
                    "goal_checkpoint": checkpoint_payload,
                    "frozen_goal": frozen_goal.model_dump(mode="json"),
                }
            )
            continuation = _continuation_task(
                task,
                input_message_id=source_message.id,
                output_message_id=response_message.id,
                input_jsonb=next_input,
            )
            self.session.add(continuation)
            self.session.flush()
            repository.bind_task_run(
                goal,
                goal_revision_id=revision.id,
                task_run_id=continuation.id,
                trigger_kind="steer",
            )
            repository.transition(
                goal,
                target_status="running",
                reason_code="steer_checkpointed",
                state_hash=revision_state_hash,
                goal_revision_id=revision.id,
                source_message_id=source_message.id,
                task_run_id=continuation.id,
            )
            result_payload: dict[str, Any] = {
                "answer": GOAL_CHECKPOINTED_PUBLIC_MESSAGE,
                "referenced_object_ids": [],
                "referenced_event_ids": [],
                "referenced_validation_issue_ids": [],
                "suggested_view": None,
                "patch_set_id": None,
                "stale": False,
                "audit_findings": [],
                "tool_metrics": tools,
                "checkpointed": True,
                "continuation_run_id": continuation.id,
            }
            task.result_jsonb = result_payload
            attempt.status = "succeeded"
            attempt.candidate_jsonb = result_payload
            attempt.validation_errors_jsonb = []
            attempt.usage_jsonb = usage
            attempt.finished_at = now
            thread.last_message_at = now
            _append_event(
                self.session,
                task,
                "goal.checkpointed",
                "checkpointed",
                {
                    "safe_point": safe_point,
                    "observation_count": len(checkpoint.observations),
                    "continuation_run_id": continuation.id,
                },
            )
            _append_event(
                self.session,
                task,
                "task.succeeded",
                "checkpointed",
                {
                    "message": GOAL_CHECKPOINTED_PUBLIC_MESSAGE,
                    "task_type": task.task_type,
                    "checkpointed": True,
                },
            )
            _append_event(
                self.session,
                continuation,
                "task.queued",
                "queued",
                {
                    "message": "Goal continuation 已进入队列",
                    "task_type": continuation.task_type,
                    "model_id": continuation.model_id,
                    "input_hash": continuation.input_hash,
                },
            )
            self.session.flush()
            return continuation.id

    def _persist_goal_checkpoint_observations(
        self,
        *,
        goal: AgentGoalSession,
        binding: AgentGoalTaskRun,
        task: TaskRun,
        checkpoint: GoalExecutionCheckpoint,
    ) -> None:
        obligations = {
            row.obligation_key: row
            for row in self.session.scalars(
                select(AgentGoalObligation).where(
                    AgentGoalObligation.goal_revision_id == binding.goal_revision_id
                )
            )
        }
        prior_outputs: list[str] = []
        for observation in checkpoint.observations:
            if observation.status != "completed":
                raise RuntimeError("Checkpoint contains a non-completed Goal observation")
            upstream_hash = stable_hash(
                {
                    "obligations": checkpoint.obligations_hash,
                    "prior_outputs": prior_outputs,
                }
            )
            for obligation_key in observation.obligation_ids:
                obligation = obligations.get(obligation_key)
                if obligation is None:
                    raise RuntimeError("Checkpoint references an unknown Goal obligation")
                already_persisted = self.session.scalar(
                    select(AgentGoalObservation.id)
                    .where(
                        AgentGoalObservation.goal_session_id == goal.id,
                        AgentGoalObservation.capability == observation.capability,
                        AgentGoalObservation.action_hash == observation.action_hash,
                        AgentGoalObservation.output_hash == observation.output_hash,
                    )
                    .limit(1)
                )
                if already_persisted is not None:
                    continue
                if observation.capability == "propose_mutation":
                    # PatchSet identity is introduced by the M3.8-04 review boundary.
                    continue
                self.session.add(
                    AgentGoalObservation(
                        project_id=goal.project_id,
                        goal_session_id=goal.id,
                        goal_revision_id=binding.goal_revision_id,
                        obligation_id=obligation.id,
                        task_run_id=task.id,
                        agent_step_run_id=None,
                        capability=observation.capability,
                        target_state=observation.target_state,
                        status="succeeded",
                        draft_revision=task.input_draft_revision,
                        draft_hash=goal.baseline_hash,
                        action_hash=observation.action_hash,
                        input_hash=observation.input_hash,
                        upstream_hash=upstream_hash,
                        output_hash=observation.output_hash,
                        candidate_hash=observation.candidate_hash,
                        patch_set_id=None,
                        verification_run_id=None,
                        reused_from_observation_id=None,
                        summary_text=observation.summary,
                    )
                )
            prior_outputs.append(observation.output_hash)

    def _finalize_agent_goal_task_success(self, task: TaskRun, *, now: datetime) -> None:
        """Project a successful final slice to its binding and GoalSession."""

        goal_id = _task_goal_id(task)
        if goal_id is None:
            return
        repository = GoalSessionRepository(self.session)
        goal = repository.get_for_update(
            project_id=task.project_id,
            goal_session_id=goal_id,
        )
        if goal.status in TERMINAL_GOAL_STATUSES:
            return
        binding = self.session.scalar(
            select(AgentGoalTaskRun)
            .where(
                AgentGoalTaskRun.goal_session_id == goal.id,
                AgentGoalTaskRun.task_run_id == task.id,
            )
            .with_for_update()
        )
        if binding is not None:
            if binding.status != "active":
                raise RuntimeError("Final Goal TaskRun binding is not active")
            binding.status = "completed"
            binding.finished_at = now
        reason_code = "goal_completed" if binding is not None else "single_turn_fallback"
        repository.transition(
            goal,
            target_status="completed",
            reason_code=reason_code,
            state_hash=stable_hash(
                {
                    "goal_id": goal.id,
                    "status": "completed",
                    "task_run_id": task.id,
                    "goal_revision_id": goal.current_revision_id,
                }
            ),
            goal_revision_id=goal.current_revision_id,
            source_message_id=goal.source_message_id,
            task_run_id=task.id,
        )

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


def _task_goal_id(task: TaskRun) -> int | None:
    raw = task.input_jsonb.get("goal_session")
    if not isinstance(raw, dict):
        return None
    value = raw.get("goal_id")
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _continuation_task(
    previous: TaskRun,
    *,
    input_message_id: int,
    output_message_id: int,
    input_jsonb: dict[str, Any],
) -> TaskRun:
    """Create one immutable queued slice using the prior slice's frozen bindings."""

    return TaskRun(
        project_id=previous.project_id,
        casefile_id=previous.casefile_id,
        draft_id=previous.draft_id,
        brief_version_id=None,
        input_source_record_id=None,
        input_brief_revision=None,
        brief_intake_id=None,
        input_brief_intake_revision=None,
        base_brief_intake_candidate_id=None,
        agent_thread_id=previous.agent_thread_id,
        input_message_id=input_message_id,
        output_message_id=output_message_id,
        input_hash=_json_hash(input_jsonb),
        input_jsonb=input_jsonb,
        actor_user_id=previous.actor_user_id,
        provider_setting_id=previous.provider_setting_id,
        task_type="casefile_chat",
        status="queued",
        stage="queued",
        input_draft_revision=previous.input_draft_revision,
        provider=previous.provider,
        model_id=previous.model_id,
        provider_config_version=previous.provider_config_version,
        schema_version=previous.schema_version,
        agent_version=previous.agent_version,
        prompt_version=previous.prompt_version,
        toolset_version=previous.toolset_version,
        budget_jsonb=dict(previous.budget_jsonb),
        usage_jsonb={},
        attempt_count=0,
        result_jsonb=None,
        error_details_jsonb={},
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
