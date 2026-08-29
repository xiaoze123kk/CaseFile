"""M3.8-04 clarification, Patch review, stale, and cancellation boundaries."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from application_services_test_support import (
    RichFixtureProvider,
    _adopt_candidate,
    _prepare_task,
)
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import sessionmaker

from casefile.agent_runtime.goal.contracts import (
    GoalCompletionDecision,
    GoalExecutionCheckpoint,
    GoalObservation,
    GoalUnderstandingOutput,
)
from casefile.agent_runtime.goal.policy import freeze_goal, stable_hash
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.application.errors import ApplicationError
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import (
    AgentGoalDelivery,
    AgentGoalObservation,
    AgentGoalSession,
    AgentGoalTaskRun,
    AgentPatchOperation,
    AgentPatchSet,
    CaseFileObject,
    Draft,
    TaskRun,
)
from casefile.worker.runtime import Worker, WorkerConfig

pytestmark = pytest.mark.postgres


def _ambiguous_understanding() -> GoalUnderstandingOutput:
    return GoalUnderstandingOutput.model_validate(
        {
            "goal": "分析并修订目标对象",
            "confidence": 0.95,
            "ambiguous": True,
            "missing_info": ["需要修改哪个对象"],
            "obligations": [
                {
                    "kind": "analysis",
                    "target_state": "baseline",
                    "source_excerpt": "分析当前时间线",
                },
                {
                    "kind": "audit",
                    "target_state": "baseline",
                    "source_excerpt": "审计并准备修改",
                    "depends_on": [1],
                },
            ],
        }
    )


def _mutation_understanding(object_key: str) -> GoalUnderstandingOutput:
    del object_key
    return GoalUnderstandingOutput.model_validate(
        {
            "goal": "分析并准备标题修改",
            "confidence": 1.0,
            "obligations": [
                {
                    "kind": "analysis",
                    "target_state": "baseline",
                    "source_excerpt": "分析当前对象",
                },
                {
                    "kind": "mutation_proposal",
                    "target_state": "baseline",
                    "source_excerpt": "准备修改标题",
                    "depends_on": [1],
                },
            ],
        }
    )


def _checkpoint(frozen, object_key: str) -> GoalExecutionCheckpoint:  # type: ignore[no-untyped-def]
    candidate_hash = stable_hash(["candidate", object_key])
    observations = [
        GoalObservation(
            observation_id="obs_1",
            capability="analyze",
            obligation_ids=["obl_1"],
            target_state="baseline",
            status="completed",
            summary="已完成基线分析。",
            object_refs=[object_key],
            action_hash=stable_hash(["action", 1]),
            input_hash=stable_hash(["input", 1]),
            output_hash=stable_hash(["output", 1]),
        ),
        GoalObservation(
            observation_id="obs_2",
            capability="propose_mutation",
            obligation_ids=["obl_2"],
            target_state="baseline",
            status="completed",
            summary="已形成待审阅修改。",
            object_refs=[object_key],
            action_hash=stable_hash(["action", 2]),
            input_hash=stable_hash(["input", 2]),
            output_hash=stable_hash(["output", 2]),
            candidate_hash=candidate_hash,
            mutation_proof_ref="general_mutation:test",
        ),
    ]
    return GoalExecutionCheckpoint(
        obligations_hash=frozen.obligations_hash,
        observations=observations,
        completion=GoalCompletionDecision(
            allowed=True,
            state_hash=stable_hash(["complete", object_key]),
        ),
        mutation_proof={"candidate_hash": candidate_hash},
    )


def _prepare_goal_patch(
    engine: Engine,
    actor_id: int,
    master_key: str,
) -> tuple[sessionmaker, int, int, int, int, int]:  # type: ignore[type-arg]
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    project_id, generation_task_id = _prepare_task(engine, actor_id)
    assert Worker(
        factory,
        config=WorkerConfig(worker_id="m38-04-generation"),
        provider_factory=lambda _task: RichFixtureProvider(),
    ).run_once()
    adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
    draft_id = int(adopted["draft_id"])
    with factory() as session:
        target = session.scalar(
            select(CaseFileObject)
            .where(CaseFileObject.project_id == project_id)
            .order_by(CaseFileObject.id)
        )
        assert target is not None
        object_key = target.object_id
    with factory() as session:
        workflow = WorkflowService(session)
        thread = workflow.create_agent_thread(
            actor_id,
            project_id,
            expected_draft_id=draft_id,
            expected_draft_revision=2,
            title="M3.8 HITL",
        )
        queued = workflow.send_agent_message(
            actor_id,
            project_id,
            int(thread["thread_id"]),
            expected_draft_id=draft_id,
            expected_draft_revision=2,
            content="分析当前对象并准备修改标题。",
            delivery_mode="new_goal",
        )
    task_id = int(queued["task"]["task_run_id"])
    goal_id = int(queued["goal"].goal_id)
    frozen = freeze_goal(_mutation_understanding(object_key), "分析当前对象并准备修改标题。")
    with factory() as session:
        WorkflowService(session).initialize_agent_goal_task(task_id, frozen)
    claimer = Worker(
        factory,
        config=WorkerConfig(worker_id="m38-04-claim"),
        provider_factory=lambda _task: FakeProvider(),
    )
    claimed = claimer._claim_next()
    assert claimed is not None and claimed[0] == task_id
    with factory() as session:
        completion = WorkflowService(session).complete_chat_task(
            task_id,
            claimed[1],
            answer="已准备一条待审阅的标题修改。",
            referenced_object_ids=[object_key],
            referenced_event_ids=[],
            referenced_validation_issue_ids=[],
            suggested_view="timeline",
            suggestions=[
                {
                    "object_id": object_key,
                    "path": "/title",
                    "value": "应用后的复核标题",
                    "reason": "用于验证 Goal Patch 审阅边界。",
                }
            ],
            usage={},
            frozen_goal=frozen,
            goal_checkpoint=_checkpoint(frozen, object_key),
        )
    patch_id = int(completion["message"]["patch_set"]["patch_set_id"])
    operation_id = int(completion["message"]["patch_set"]["operations"][0]["operation_id"])
    return factory, project_id, draft_id, goal_id, patch_id, operation_id


def test_missing_info_waits_then_steer_revises_and_run_cancel_converges(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_GOAL_ROLLOUT": "active",
            "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT": "active",
        },
    ):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="m38-04-clarification-generation"),
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
                title="M3.8 clarification",
            )
            thread_id = int(thread["thread_id"])
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="先分析当前时间线，再审计并准备修改；具体修改对象需要我补充。",
                delivery_mode="new_goal",
            )
            goal_id = int(queued["goal"].goal_id)
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="m38-04-clarification"),
            provider_factory=lambda _task: FakeProvider(
                goal_understanding=_ambiguous_understanding()
            ),
        ).run_once()
        with factory() as session:
            goal = session.get(AgentGoalSession, goal_id)
            assert goal is not None and goal.status == "waiting_clarification"
        with factory() as session:
            resumed = WorkflowService(session).send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="目标是第一个事件对象。",
                delivery_mode="steer",
                expected_goal_id=goal_id,
                expected_goal_revision=1,
            )
            continuation_id = int(resumed["task"]["task_run_id"])
            assert resumed["goal"].revision == 2
        with factory() as session:
            queued_control = WorkflowService(session).send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="同时保留原始标题作为说明。",
                delivery_mode="steer",
                expected_goal_id=goal_id,
                expected_goal_revision=2,
            )
            delivery_id = int(queued_control["delivery"].delivery_id)
        with factory() as session:
            cancelled = WorkflowService(session).cancel_agent_run(
                actor_id, project_id, continuation_id
            )
            assert cancelled.status.value == "cancelled"
        with factory() as session:
            goal = session.get(AgentGoalSession, goal_id)
            delivery = session.get(AgentGoalDelivery, delivery_id)
            assert goal is not None and goal.status == "cancelled"
            assert delivery is not None and delivery.status == "cancelled"


def test_goal_patch_rejects_to_clarification(
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
        factory, project_id, draft_id, goal_id, patch_id, _ = _prepare_goal_patch(
            engine, actor_id, master_key
        )
        with factory() as session:
            goal = session.get(AgentGoalSession, goal_id)
            patch_set = session.get(AgentPatchSet, patch_id)
            observations = int(
                session.scalar(
                    select(func.count(AgentGoalObservation.id)).where(
                        AgentGoalObservation.goal_session_id == goal_id
                    )
                )
                or 0
            )
            assert goal is not None and goal.status == "waiting_patch_review"
            assert patch_set is not None and patch_set.status == "pending"
            assert observations == 2
        with factory() as session:
            rejected = WorkflowService(session).apply_agent_patch_set(
                actor_id,
                project_id,
                patch_id,
                expected_draft_id=draft_id,
                expected_revision=2,
                operation_ids=[],
            )
            assert rejected["status"] == "rejected"
            assert rejected["goal"].status.value == "waiting_clarification"
        with factory() as session:
            goal = session.get(AgentGoalSession, goal_id)
            assert goal is not None and goal.status == "waiting_clarification"


def test_goal_patch_apply_rebinds_baseline_and_queues_one_post_apply_audit(
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
        factory, project_id, draft_id, goal_id, patch_id, operation_id = (
            _prepare_goal_patch(engine, actor_id, master_key)
        )
        with factory() as session:
            applied = WorkflowService(session).apply_agent_patch_set(
                actor_id,
                project_id,
                patch_id,
                expected_draft_id=draft_id,
                expected_revision=2,
                operation_ids=[operation_id],
            )
            continuation_id = int(applied["continuation_run"]["task_run_id"])
            assert applied["draft_revision"] == 3
            assert applied["goal"].status.value == "running"
        with factory() as session:
            goal = session.get(AgentGoalSession, goal_id)
            continuation = session.get(TaskRun, continuation_id)
            post_apply_count = int(
                session.scalar(
                    select(func.count(AgentGoalTaskRun.id)).where(
                        AgentGoalTaskRun.goal_session_id == goal_id,
                        AgentGoalTaskRun.trigger_kind == "post_apply",
                    )
                )
                or 0
            )
            assert goal is not None
            assert goal.status == "running"
            assert goal.baseline_draft_revision == 3
            assert goal.active_patch_set_id is None
            assert continuation is not None and continuation.status == "queued"
            assert continuation.input_jsonb["verification_trigger"] == "post_apply"
            assert post_apply_count == 1


def test_stale_goal_patch_is_persisted_before_conflict_response(
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
        factory, project_id, draft_id, goal_id, patch_id, operation_id = (
            _prepare_goal_patch(engine, actor_id, master_key)
        )
        with factory() as session, session.begin():
            draft = session.get(Draft, draft_id)
            assert draft is not None
            draft.revision = 3
        with factory() as session:
            with pytest.raises(ApplicationError, match="CaseFile 已发生变化") as captured:
                WorkflowService(session).apply_agent_patch_set(
                    actor_id,
                    project_id,
                    patch_id,
                    expected_draft_id=draft_id,
                    expected_revision=2,
                    operation_ids=[operation_id],
                )
            assert captured.value.code == "agent_patch_stale"
        with factory() as session:
            goal = session.get(AgentGoalSession, goal_id)
            patch_set = session.get(AgentPatchSet, patch_id)
            operations = list(
                session.scalars(
                    select(AgentPatchOperation).where(
                        AgentPatchOperation.patch_set_id == patch_id
                    )
                )
            )
            assert goal is not None and goal.status == "stale"
            assert patch_set is not None and patch_set.status == "stale"
            assert all(operation.decision == "pending" for operation in operations)
