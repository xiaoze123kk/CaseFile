"""M3.8 checkpoint, safe-point, delivery recovery, and continuation integration."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from application_services_test_support import (
    RichFixtureProvider,
    _adopt_candidate,
    _prepare_task,
)
from casefile.agent_runtime.goal.contracts import (
    GoalAmendmentOutput,
    GoalDecisionOutput,
    GoalExecutionCheckpoint,
    GoalUnderstandingOutput,
)
from casefile.agent_runtime.goal.policy import freeze_goal
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.application.goal_session_repository import GoalSessionRepository
from casefile.application.workflow.goal_session import GOAL_CHECKPOINTED_PUBLIC_MESSAGE
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import (
    AgentGoalDelivery,
    AgentGoalObservation,
    AgentGoalSession,
    AgentGoalTaskRun,
    AgentMessage,
    TaskAttempt,
    TaskRun,
)
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres

SOURCE = "先分析时间线，再审计矛盾。"


def _understanding() -> GoalUnderstandingOutput:
    return GoalUnderstandingOutput.model_validate(
        {
            "goal": "分析并审计",
            "confidence": 1.0,
            "obligations": [
                {
                    "kind": "analysis",
                    "target_state": "baseline",
                    "source_excerpt": "分析时间线",
                },
                {
                    "kind": "audit",
                    "target_state": "baseline",
                    "source_excerpt": "审计矛盾",
                    "depends_on": [1],
                },
            ],
        }
    )


def _decision(capability: str, obligation_id: str) -> GoalDecisionOutput:
    return GoalDecisionOutput.model_validate(
        {
            "plan_items": [
                {"obligation_id": "obl_1", "status": "pending"},
                {"obligation_id": "obl_2", "status": "pending"},
            ],
            "action": {
                "action": "invoke_capability",
                "capability": capability,
                "obligation_ids": [obligation_id],
                "target_state": "baseline",
            },
        }
    )


def _finish() -> GoalDecisionOutput:
    return GoalDecisionOutput.model_validate(
        {
            "plan_items": [
                {"obligation_id": "obl_1", "status": "completed"},
                {"obligation_id": "obl_2", "status": "completed"},
            ],
            "action": {"action": "finish"},
        }
    )


def _amendment() -> GoalAmendmentOutput:
    return GoalAmendmentOutput.model_validate(
        {
            "amendment_kind": "refine",
            "goal": "分析并审计",
            "obligations": [
                {
                    "obligation_ref": "obl_1",
                    "kind": "analysis",
                    "target_state": "baseline",
                    "source_excerpt": "分析时间线",
                },
                {
                    "obligation_ref": "obl_2",
                    "kind": "audit",
                    "target_state": "baseline",
                    "source_excerpt": "审计矛盾",
                    "depends_on": ["obl_1"],
                },
            ],
        }
    )


class _CountingGoalProvider(FakeProvider):
    def __init__(self, counter: dict[str, int], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._counter = counter

    def finalize_goal(self, request):  # type: ignore[no-untyped-def]
        self._counter["finalizer"] = self._counter.get("finalizer", 0) + 1
        return super().finalize_goal(request)


class _SteeringGoalProvider(_CountingGoalProvider):
    def __init__(self, counter: dict[str, int], steer: Any) -> None:
        super().__init__(
            counter,
            goal_understanding=_understanding(),
            goal_amendment=_amendment(),
            goal_decisions=(_decision("analyze", "obl_1"),),
        )
        self._steer = steer

    def collect_chat_evidence(self, request):  # type: ignore[no-untyped-def]
        self._steer()
        return super().collect_chat_evidence(request)


def test_goal_checkpoint_continuation_is_atomic_and_never_creates_two_active_runs(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    frozen = freeze_goal(_understanding(), SOURCE)
    counter: dict[str, int] = {}

    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT": "active",
            "CASEFILE_CHAT_GOAL_ROLLOUT": "active",
        },
    ):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="m38-03-generation"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title="M3.8 checkpoint",
            )
            thread_id = int(thread["thread_id"])
            created = workflow.send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content=SOURCE,
                delivery_mode="new_goal",
            )
            goal_id = int(created["goal"].goal_id)
            first_run_id = int(created["task"]["task_run_id"])

        with factory() as session:
            WorkflowService(session).initialize_agent_goal_task(first_run_id, frozen)
        with factory() as session:
            steered = WorkflowService(session).send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="先按原目标继续，但优先完成时间线分析。",
                delivery_mode="steer",
                expected_goal_id=goal_id,
                expected_goal_revision=1,
            )
            delivery_id = int(steered["delivery"].delivery_id)

        steer_state: dict[str, int] = {}

        def queue_steer_during_capability() -> None:
            if "delivery_id" in steer_state:
                return
            with factory() as session:
                steered_during_capability = WorkflowService(session).send_agent_message(
                    actor_id,
                    project_id,
                    thread_id,
                    expected_draft_id=draft_id,
                    expected_draft_revision=2,
                    content="保留分析结果，接下来优先完成矛盾审计。",
                    delivery_mode="steer",
                    expected_goal_id=goal_id,
                    expected_goal_revision=1,
                )
                steer_state["delivery_id"] = int(
                    steered_during_capability["delivery"].delivery_id
                )

        def provider_for_task(task: TaskRun) -> FakeProvider:
            if isinstance(task.input_jsonb.get("goal_checkpoint"), dict):
                return _CountingGoalProvider(
                    counter,
                    goal_decisions=(
                        _decision("audit", "obl_2"),
                        _finish(),
                    ),
                )
            return _SteeringGoalProvider(counter, queue_steer_during_capability)

        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="m38-03-worker"),
            provider_factory=provider_for_task,
        )
        claimed = worker._claim_next()
        assert isinstance(claimed, tuple)
        claimed_run_id, attempt_id = claimed
        assert claimed_run_id == first_run_id
        with factory() as session:
            claimed_control = WorkflowService(session).claim_agent_goal_control(
                first_run_id,
                attempt_id,
            )
        assert claimed_control is not None
        assert int(claimed_control["delivery_id"]) == delivery_id

        checkpoint = GoalExecutionCheckpoint(obligations_hash=frozen.obligations_hash)
        original_bind = GoalSessionRepository.bind_task_run

        def fail_continuation_bind(self, row, **kwargs):  # type: ignore[no-untyped-def]
            if int(kwargs["task_run_id"]) != first_run_id:
                raise RuntimeError("injected continuation failure")
            return original_bind(self, row, **kwargs)

        with patch.object(GoalSessionRepository, "bind_task_run", fail_continuation_bind):
            with factory() as session:
                with pytest.raises(RuntimeError, match="injected continuation failure"):
                    WorkflowService(session).checkpoint_agent_goal_task(
                        first_run_id,
                        attempt_id,
                        frozen_goal=frozen,
                        checkpoint=checkpoint,
                        safe_point="before_controller",
                        usage={},
                        tools={},
                        delivery_id=delivery_id,
                        amended_goal=frozen,
                        amendment_kind="refine",
                    )

        with factory() as session:
            first_run = session.get(TaskRun, first_run_id)
            first_attempt = session.get(TaskAttempt, attempt_id)
            delivery = session.get(AgentGoalDelivery, delivery_id)
            goal = session.get(AgentGoalSession, goal_id)
            bindings = list(
                session.scalars(
                    select(AgentGoalTaskRun)
                    .where(AgentGoalTaskRun.goal_session_id == goal_id)
                    .order_by(AgentGoalTaskRun.slice_no)
                )
            )
            assert first_run is not None and first_run.status == "running"
            assert first_attempt is not None and first_attempt.status == "running"
            assert delivery is not None and delivery.status == "claimed"
            assert goal is not None
            counters = (
                goal.revision_count,
                goal.task_run_slice_count,
                goal.consumed_control_count,
            )
            assert counters == (
                1,
                1,
                0,
            )
            assert [(row.slice_no, row.status) for row in bindings] == [(1, "active")]

        with factory() as session, session.begin():
            delivery = session.get(AgentGoalDelivery, delivery_id)
            assert delivery is not None
            delivery.status = "cancelled"
            delivery.cancelled_at = datetime.now(UTC)
            delivery.reason_code = "test_replaced_before_capability"
            response = session.get(AgentMessage, delivery.response_message_id)
            assert response is not None
            response.status = "cancelled"
            response.content_text = None

        worker._execute(first_run_id, attempt_id)
        delivery_id = steer_state["delivery_id"]

        with factory() as session:
            goal = session.get(AgentGoalSession, goal_id)
            delivery = session.get(AgentGoalDelivery, delivery_id)
            first_run = session.get(TaskRun, first_run_id)
            bindings = list(
                session.scalars(
                    select(AgentGoalTaskRun)
                    .where(AgentGoalTaskRun.goal_session_id == goal_id)
                    .order_by(AgentGoalTaskRun.slice_no)
                )
            )
            active_thread_runs = int(
                session.scalar(
                    select(func.count(TaskRun.id)).where(
                        TaskRun.agent_thread_id == thread_id,
                        TaskRun.status.in_(("queued", "running", "cancelling")),
                    )
                )
                or 0
            )
            assert goal is not None
            assert first_run is not None
            assert first_run.status == "succeeded", (
                first_run.error_code,
                first_run.error_details_jsonb,
            )
            assert goal.status == "running"
            counters = (
                goal.revision_count,
                goal.task_run_slice_count,
                goal.consumed_control_count,
            )
            assert counters == (
                2,
                2,
                1,
            )
            assert delivery is not None and delivery.status == "consumed"
            assert first_run is not None
            assert first_run.status == "succeeded"
            assert first_run.stage == "checkpointed"
            assert first_run.result_jsonb is not None
            assert first_run.result_jsonb["answer"] == GOAL_CHECKPOINTED_PUBLIC_MESSAGE
            assert "goal_checkpoint" not in first_run.result_jsonb
            assert [(row.slice_no, row.status) for row in bindings] == [
                (1, "checkpointed"),
                (2, "active"),
            ]
            assert bindings[0].checkpoint_hash is not None
            continuation_run_id = bindings[1].task_run_id
            continuation = session.get(TaskRun, continuation_run_id)
            observation_count = int(
                session.scalar(
                    select(func.count(AgentGoalObservation.id)).where(
                        AgentGoalObservation.goal_session_id == goal_id
                    )
                )
                or 0
            )
            assert continuation is not None
            assert continuation.status == "queued"
            checkpoint_observations = continuation.input_jsonb["goal_checkpoint"][
                "observations"
            ]
            assert [row["obligation_ids"] for row in checkpoint_observations] == [["obl_1"]]
            assert continuation.input_jsonb["goal_session"]["goal_revision"] == 2
            assert observation_count == 2
            reused_count = int(
                session.scalar(
                    select(func.count(AgentGoalObservation.id)).where(
                        AgentGoalObservation.goal_session_id == goal_id,
                        AgentGoalObservation.status == "reused",
                    )
                )
                or 0
            )
            assert reused_count == 1
            assert active_thread_runs == 1

        assert worker.run_once()

        with factory() as session:
            goal = session.get(AgentGoalSession, goal_id)
            continuation = session.get(TaskRun, continuation_run_id)
            bindings = list(
                session.scalars(
                    select(AgentGoalTaskRun)
                    .where(AgentGoalTaskRun.goal_session_id == goal_id)
                    .order_by(AgentGoalTaskRun.slice_no)
                )
            )
            active_thread_runs = int(
                session.scalar(
                    select(func.count(TaskRun.id)).where(
                        TaskRun.agent_thread_id == thread_id,
                        TaskRun.status.in_(("queued", "running", "cancelling")),
                    )
                )
                or 0
            )
            response_message = session.get(AgentMessage, continuation.output_message_id)
            assert goal is not None and goal.status == "completed"
            assert continuation is not None and continuation.status == "succeeded"
            assert response_message is not None and response_message.status == "completed"
            assert [(row.slice_no, row.status) for row in bindings] == [
                (1, "checkpointed"),
                (2, "completed"),
            ]
            assert active_thread_runs == 0
            assert counter == {"finalizer": 1}


def test_expired_delivery_claim_is_recovered_by_the_new_task_attempt(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    frozen = freeze_goal(_understanding(), SOURCE)

    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT": "active",
            "CASEFILE_CHAT_GOAL_ROLLOUT": "active",
        },
    ):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="m38-06-generation"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])
        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title="M3.8 delivery recovery",
            )
            thread_id = int(thread["thread_id"])
            created = workflow.send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content=SOURCE,
                delivery_mode="new_goal",
            )
            goal_id = int(created["goal"].goal_id)
            task_id = int(created["task"]["task_run_id"])
        with factory() as session:
            WorkflowService(session).initialize_agent_goal_task(task_id, frozen)
        delivery_ids: list[int] = []
        with factory() as session:
            workflow = WorkflowService(session)
            for content in (
                "优先完成时间线分析。",
                "然后继续审计矛盾。",
            ):
                queued = workflow.send_agent_message(
                    actor_id,
                    project_id,
                    thread_id,
                    expected_draft_id=draft_id,
                    expected_draft_revision=2,
                    content=content,
                    delivery_mode="steer",
                    expected_goal_id=goal_id,
                    expected_goal_revision=1,
                )
                delivery_ids.append(int(queued["delivery"].delivery_id))

        first_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="m38-06-crashed", lease_seconds=1),
            provider_factory=lambda _task: FakeProvider(
                goal_understanding=_understanding(),
                goal_amendment=_amendment(),
            ),
        )
        first_claim = first_worker._claim_next()
        assert isinstance(first_claim, tuple)
        first_task_id, first_attempt_id = first_claim
        assert first_task_id == task_id
        with factory() as session:
            control = WorkflowService(session).claim_agent_goal_control(
                task_id,
                first_attempt_id,
            )
        assert control is not None
        assert int(control["delivery_id"]) == delivery_ids[0]
        with factory() as session:
            first_delivery = session.get(AgentGoalDelivery, delivery_ids[0])
            second_delivery = session.get(AgentGoalDelivery, delivery_ids[1])
            assert first_delivery is not None and first_delivery.status == "claimed"
            assert second_delivery is not None and second_delivery.status == "queued"
            first_owner = first_delivery.claimed_by
            assert first_owner == f"task:{task_id}:attempt:{first_attempt_id}"
        with factory() as session:
            assert WorkflowService(session).has_pending_agent_goal_control(task_id)
        with factory() as session:
            repeated_control = WorkflowService(session).claim_agent_goal_control(
                task_id,
                first_attempt_id,
            )
            assert repeated_control is not None
            assert int(repeated_control["delivery_id"]) == delivery_ids[0]

        expired = datetime.now(UTC) - timedelta(seconds=1)
        with factory() as session, session.begin():
            task = session.get(TaskRun, task_id)
            delivery = session.get(AgentGoalDelivery, delivery_ids[0])
            assert task is not None and delivery is not None
            task.lease_expires_at = expired
            delivery.lease_expires_at = expired

        recovered_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="m38-06-recovered", lease_seconds=30),
            provider_factory=lambda _task: FakeProvider(
                goal_understanding=_understanding(),
                goal_amendment=_amendment(),
            ),
        )
        recovered_claim = recovered_worker._claim_next()
        assert isinstance(recovered_claim, tuple)
        recovered_task_id, recovered_attempt_id = recovered_claim
        assert recovered_task_id == task_id
        with factory() as session:
            recovered_control = WorkflowService(session).claim_agent_goal_control(
                task_id,
                recovered_attempt_id,
            )
        assert recovered_control is not None
        assert int(recovered_control["delivery_id"]) == delivery_ids[0]
        with factory() as session:
            delivery = session.get(AgentGoalDelivery, delivery_ids[0])
            assert delivery is not None and delivery.status == "claimed"
            assert delivery.claimed_by == f"task:{task_id}:attempt:{recovered_attempt_id}"
            assert delivery.claimed_by != first_owner
            assert delivery.reason_code == "delivery_claim_recovered"
        with factory() as session:
            assert WorkflowService(session).has_pending_agent_goal_control(task_id)

        with factory() as session:
            with pytest.raises(RuntimeError, match="no longer owns a running TaskRun"):
                WorkflowService(session).checkpoint_agent_goal_task(
                    task_id,
                    first_attempt_id,
                    frozen_goal=frozen,
                    checkpoint=GoalExecutionCheckpoint(
                        obligations_hash=frozen.obligations_hash
                    ),
                    safe_point="before_controller",
                    usage={},
                    tools={},
                    delivery_id=delivery_ids[0],
                    amended_goal=frozen,
                    amendment_kind="refine",
                )

        recovered_worker._execute(task_id, recovered_attempt_id)
        with factory() as session:
            goal = session.get(AgentGoalSession, goal_id)
            task = session.get(TaskRun, task_id)
            first_delivery = session.get(AgentGoalDelivery, delivery_ids[0])
            second_delivery = session.get(AgentGoalDelivery, delivery_ids[1])
            bindings = list(
                session.scalars(
                    select(AgentGoalTaskRun)
                    .where(AgentGoalTaskRun.goal_session_id == goal_id)
                    .order_by(AgentGoalTaskRun.slice_no)
                )
            )
            assert goal is not None and goal.status == "running"
            assert task is not None and task.status == "succeeded", (
                None if task is None else task.error_code,
                None if task is None else task.error_details_jsonb,
            )
            assert first_delivery is not None and first_delivery.status == "consumed"
            assert second_delivery is not None and second_delivery.status == "queued"
            assert [(row.slice_no, row.status) for row in bindings] == [
                (1, "checkpointed"),
                (2, "active"),
            ]
            assert goal.consumed_control_count == 1
