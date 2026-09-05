"""M3.8-05 semantic amendment, replace, follow-up, and reuse integration."""

from __future__ import annotations

import os
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
    GoalUnderstandingOutput,
)
from casefile.agent_runtime.goal.policy import freeze_goal
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.application.errors import ApplicationError
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import (
    AgentGoalDelivery,
    AgentGoalRevision,
    AgentGoalSession,
    AgentModelCall,
    TaskRun,
)
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres

SOURCE = "先分析时间线，再审计矛盾。"


def _understanding(*, ambiguous: bool = False) -> GoalUnderstandingOutput:
    return GoalUnderstandingOutput.model_validate(
        {
            "goal": "分析并审计当前时间线",
            "confidence": 1.0,
            "ambiguous": ambiguous,
            "missing_info": ["需要明确目标事件"] if ambiguous else [],
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


def _amendment() -> GoalAmendmentOutput:
    return GoalAmendmentOutput.model_validate(
        {
            "amendment_kind": "refine",
            "goal": "分析并审计第一个事件对象",
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


def _prepare_draft(engine: Engine, actor_id: int) -> tuple[sessionmaker, int, int]:  # type: ignore[type-arg]
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    project_id, generation_task_id = _prepare_task(engine, actor_id)
    assert Worker(
        factory,
        config=WorkerConfig(worker_id="m38-05-generation"),
        provider_factory=lambda _task: RichFixtureProvider(),
    ).run_once()
    adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
    return factory, project_id, int(adopted["draft_id"])


def _create_goal(
    factory: sessionmaker,  # type: ignore[type-arg]
    actor_id: int,
    project_id: int,
    draft_id: int,
) -> tuple[int, int, int]:
    with factory() as session:
        workflow = WorkflowService(session)
        thread = workflow.create_agent_thread(
            actor_id,
            project_id,
            expected_draft_id=draft_id,
            expected_draft_revision=2,
            title="M3.8 steering",
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
    return thread_id, int(created["goal"].goal_id), int(created["task"]["task_run_id"])


def test_waiting_steer_uses_current_amendment_and_stable_obligation_keys(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_GOAL_ROLLOUT": "active",
            "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT": "active",
        },
    ):
        factory, project_id, draft_id = _prepare_draft(engine, actor_id)
        thread_id, goal_id, _ = _create_goal(factory, actor_id, project_id, draft_id)
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="m38-05-ambiguous"),
            provider_factory=lambda _task: FakeProvider(
                goal_understanding=_understanding(ambiguous=True)
            ),
        ).run_once()
        with factory() as session:
            steered = WorkflowService(session).send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="补充：目标是第一个事件对象。",
                focus={"view": "relations"},
                delivery_mode="steer",
                expected_goal_id=goal_id,
                expected_goal_revision=1,
            )
            delivery_id = int(steered["delivery"].delivery_id)
            continuation_id = int(steered["task"]["task_run_id"])
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="m38-05-amendment"),
            provider_factory=lambda _task: FakeProvider(
                goal_amendment=_amendment(),
                goal_decisions=(
                    _decision("analyze", "obl_1"),
                    _decision("audit", "obl_2"),
                    _finish(),
                ),
            ),
        ).run_once()
        with factory() as session:
            goal = session.get(AgentGoalSession, goal_id)
            delivery = session.get(AgentGoalDelivery, delivery_id)
            continuation = session.get(TaskRun, continuation_id)
            revisions = list(
                session.scalars(
                    select(AgentGoalRevision)
                    .where(AgentGoalRevision.goal_session_id == goal_id)
                    .order_by(AgentGoalRevision.revision_no)
                )
            )
            amendment_calls = int(
                session.scalar(
                    select(func.count(AgentModelCall.id)).where(
                        AgentModelCall.task_run_id == continuation_id,
                        AgentModelCall.prompt_component_id == "goal_amendment",
                    )
                )
                or 0
            )
            assert goal is not None and goal.status == "completed", (
                None if continuation is None else continuation.status,
                None if continuation is None else continuation.error_code,
                None if continuation is None else continuation.error_details_jsonb,
            )
            assert goal.revision_count == 2
            assert delivery is not None and delivery.status == "consumed"
            assert continuation is not None and continuation.prompt_version == "casefile-chat-v20"
            assert continuation.input_jsonb["focus"]["view"] == "relations"
            assert (
                continuation.input_jsonb["context_snapshot"]
                == steered["user_message"]["context_snapshot"]
            )
            assert [row.amendment_kind for row in revisions] == ["initial", "refine"]
            assert revisions[0].obligations_hash == revisions[1].obligations_hash
            assert amendment_calls == 1


def test_running_replace_is_fifo_and_preserves_supersede_lineage(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_GOAL_ROLLOUT": "active",
            "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT": "active",
        },
    ):
        factory, project_id, draft_id = _prepare_draft(engine, actor_id)
        thread_id, goal_id, task_id = _create_goal(factory, actor_id, project_id, draft_id)
        frozen = freeze_goal(_understanding(), SOURCE)
        with factory() as session:
            WorkflowService(session).initialize_agent_goal_task(task_id, frozen)
        with factory() as session:
            queued = WorkflowService(session).send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="替换为新目标：先分析时间线，再审计矛盾。",
                focus={"view": "reasoning"},
                delivery_mode="replace",
                expected_goal_id=goal_id,
                expected_goal_revision=1,
            )
            delivery_id = int(queued["delivery"].delivery_id)
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="m38-05-replace"),
            provider_factory=lambda _task: FakeProvider(goal_understanding=_understanding()),
        ).run_once()
        with factory() as session:
            old_goal = session.get(AgentGoalSession, goal_id)
            successor = session.scalar(
                select(AgentGoalSession).where(
                    AgentGoalSession.predecessor_goal_session_id == goal_id
                )
            )
            delivery = session.get(AgentGoalDelivery, delivery_id)
            old_task = session.get(TaskRun, task_id)
            active_runs = int(
                session.scalar(
                    select(func.count(TaskRun.id)).where(
                        TaskRun.agent_thread_id == thread_id,
                        TaskRun.status.in_(("queued", "running", "cancelling")),
                    )
                )
                or 0
            )
            assert old_goal is not None and old_goal.status == "superseded", (
                None if old_task is None else old_task.status,
                None if old_task is None else old_task.error_code,
                None if old_task is None else old_task.error_details_jsonb,
            )
            assert successor is not None and successor.status == "running"
            assert successor.predecessor_goal_session_id == old_goal.id
            successor_id = successor.id
            assert delivery is not None and delivery.status == "consumed"
            assert active_runs == 1
            continuation = session.scalar(
                select(TaskRun).where(
                    TaskRun.input_message_id == queued["user_message"]["message_id"]
                )
            )
            assert continuation is not None
            assert continuation.input_jsonb["focus"]["view"] == "reasoning"
            assert (
                continuation.input_jsonb["context_snapshot"]
                == queued["user_message"]["context_snapshot"]
            )
        with factory() as session:
            public_deliveries = WorkflowService(session).list_agent_goal_deliveries(
                actor_id, project_id, goal_id
            )
            assert public_deliveries[0].status.value == "consumed"
            assert public_deliveries[0].successor_goal_id == successor_id


def test_follow_up_requires_completed_predecessor_and_creates_successor(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_GOAL_ROLLOUT": "active",
            "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT": "active",
        },
    ):
        factory, project_id, draft_id = _prepare_draft(engine, actor_id)
        thread_id, goal_id, _ = _create_goal(factory, actor_id, project_id, draft_id)
        with factory() as session:
            with pytest.raises(ApplicationError) as blocked:
                WorkflowService(session).send_agent_message(
                    actor_id,
                    project_id,
                    thread_id,
                    expected_draft_id=draft_id,
                    expected_draft_revision=2,
                    content="完成后继续检查证据。",
                    delivery_mode="follow_up",
                    expected_goal_id=goal_id,
                    expected_goal_revision=0,
                )
            assert blocked.value.code == "agent_goal_state_conflict"
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="m38-05-complete"),
            provider_factory=lambda _task: FakeProvider(
                goal_understanding=_understanding(),
                goal_decisions=(
                    _decision("analyze", "obl_1"),
                    _decision("audit", "obl_2"),
                    _finish(),
                ),
            ),
        ).run_once()
        with factory() as session:
            follow_up = WorkflowService(session).send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="完成后继续检查证据。",
                focus={"view": "evidence"},
                delivery_mode="follow_up",
                expected_goal_id=goal_id,
                expected_goal_revision=1,
            )
            successor_id = int(follow_up["goal"].goal_id)
            assert follow_up["task"] is not None
            assert follow_up["delivery"].status.value == "consumed"
            task = session.get(TaskRun, int(follow_up["task"]["task_run_id"]))
            assert task is not None
            assert task.input_jsonb["focus"]["view"] == "evidence"
            assert (
                task.input_jsonb["context_snapshot"]
                == follow_up["user_message"]["context_snapshot"]
            )
        with factory() as session:
            predecessor = session.get(AgentGoalSession, goal_id)
            successor = session.get(AgentGoalSession, successor_id)
            assert predecessor is not None and predecessor.status == "completed"
            assert successor is not None and successor.status == "interpreting"
            assert successor.predecessor_goal_session_id == predecessor.id
