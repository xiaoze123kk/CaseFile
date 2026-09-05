"""PostgreSQL vertical slice for the M3.7 bounded Goal path."""

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
    GoalDecisionOutput,
    GoalUnderstandingOutput,
)
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentPatchSet,
    AgentStepRun,
    TaskEvent,
    TaskRun,
)
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres

SOURCE = "先分析时间线，再审计其中的因果矛盾。"


def test_active_read_only_goal_persists_lineage_and_one_completed_message(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_GOAL_ROLLOUT": "active",
        },
    ):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="m37-generation"),
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
                title="Goal vertical slice",
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                int(thread["thread_id"]),
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content=SOURCE,
            )
        task_id = int(queued["task"]["task_run_id"])
        provider = FakeProvider(
            goal_understanding=_understanding(),
            goal_decisions=(
                _decision("analyze", "obl_1"),
                _decision("audit", "obl_2"),
                _finish(),
            ),
        )
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="m37-goal"),
            provider_factory=lambda _task: provider,
        ).run_once()

    with factory() as session:
        task = session.get(TaskRun, task_id)
        steps = list(
            session.scalars(
                select(AgentStepRun)
                .where(AgentStepRun.task_run_id == task_id)
                .order_by(AgentStepRun.id)
            )
        )
        calls = list(
            session.scalars(select(AgentModelCall).where(AgentModelCall.task_run_id == task_id))
        )
        events = list(session.scalars(select(TaskEvent).where(TaskEvent.task_run_id == task_id)))
        patch_sets = list(
            session.scalars(select(AgentPatchSet).where(AgentPatchSet.task_run_id == task_id))
        )
    with factory() as session:
        messages = WorkflowService(session).list_agent_messages(
            actor_id, project_id, int(thread["thread_id"])
        )

    assert task is not None
    assert task.prompt_version == "casefile-chat-v22"
    assert task.input_jsonb["goal_runtime"]["mode"] == "active"
    assert task.status == "succeeded"
    assert task.result_jsonb["routing"]["intent"] == "logic_audit"
    assert patch_sets == []
    assert messages[-1]["status"] == "completed"
    assert messages[-1]["patch_set"] is None
    component_ids = {step.component_id for step in steps}
    assert {"goal_interpreter", "goal_controller", "goal_finalizer"} <= component_ids
    assert {
        step.parent_component_id
        for step in steps
        if step.component_id.startswith("goal_capability_")
    } == {"goal_controller"}
    assert len(calls) >= 4
    assert {event.event_type for event in events} >= {
        "goal.started",
        "goal.capability_completed",
        "goal.completed",
        "task.succeeded",
    }


def _understanding() -> GoalUnderstandingOutput:
    return GoalUnderstandingOutput.model_validate(
        {
            "goal": "分析并审计当前时间线",
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
                    "source_excerpt": "审计其中的因果矛盾",
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
