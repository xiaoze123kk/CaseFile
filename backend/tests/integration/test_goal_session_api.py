"""M3.8-02 public GoalSession delivery and lifecycle boundary."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from application_services_test_support import (
    RichFixtureProvider,
    _adopt_candidate,
    _prepare_task,
)
from casefile.api.app import create_app
from casefile.application.errors import ApplicationError
from casefile.application.goal_session_repository import GoalSessionRepository
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import (
    AgentGoalDelivery,
    AgentGoalSession,
    AgentMessage,
    TaskRun,
)
from casefile.worker.runtime import Worker, WorkerConfig
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres


def test_goal_session_public_delivery_events_cancellation_and_legacy_default(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT": "off",
        },
    ):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="m38-02-generation"),
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
                title="M3.8 Goal delivery",
            )
            thread_id = int(thread["thread_id"])
            legacy = workflow.send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="旧客户端消息仍按单次运行处理。",
            )
            legacy_run_id = int(legacy["task"]["task_run_id"])

        with factory() as session:
            legacy_task = session.get(TaskRun, legacy_run_id)
            goal_count = session.scalar(select(func.count(AgentGoalSession.id)))
            assert legacy_task is not None
            assert "goal_session" not in legacy_task.input_jsonb
            assert goal_count == 0

        with factory() as session:
            WorkflowService(session).cancel_agent_run(actor_id, project_id, legacy_run_id)
        with factory() as session:
            with pytest.raises(ApplicationError) as disabled:
                WorkflowService(session).send_agent_message(
                    actor_id,
                    project_id,
                    thread_id,
                    expected_draft_id=draft_id,
                    expected_draft_revision=2,
                    content="rollout off 不应创建 Goal。",
                    delivery_mode="new_goal",
                )
            assert getattr(disabled.value, "code", None) == "agent_goal_state_conflict"

        os.environ["CASEFILE_CHAT_GOAL_SESSION_ROLLOUT"] = "active"
        os.environ["CASEFILE_CHAT_GOAL_ROLLOUT"] = "active"
        with factory() as session:
            created = WorkflowService(session).send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="建立一个可持续协作的目标。",
                delivery_mode="new_goal",
            )
            goal_id = int(created["goal"].goal_id)
            goal_run_id = int(created["task"]["task_run_id"])
            assert created["delivery"] is None

        with factory() as session, session.begin():
            repository = GoalSessionRepository(session)
            goal = repository.get_for_update(
                project_id=project_id,
                goal_session_id=goal_id,
            )
            repository.append_revision(
                goal,
                source_message_id=goal.source_message_id,
                amendment_kind="initial",
                goal_text="建立一个可持续协作的目标。",
                source_excerpt="可持续协作",
                obligations_hash="b" * 64,
                state_hash="c" * 64,
                baseline_draft_revision=goal.baseline_draft_revision,
                baseline_hash=goal.baseline_hash,
            )

        app = create_app(os.environ["CASEFILE_TEST_DATABASE_URL"], verify_database=False)
        headers = {"X-CaseFile-User-Id": str(actor_id)}
        message_url = f"/api/v1/projects/{project_id}/agent/threads/{thread_id}/messages"
        goal_url = f"/api/v1/projects/{project_id}/agent/goals/{goal_id}"
        with TestClient(app) as client:
            public_goal = client.get(goal_url, headers=headers)
            assert public_goal.status_code == 200
            assert public_goal.json()["revision"] == 1
            assert public_goal.json()["active_run_id"] == goal_run_id
            assert public_goal.json()["can_steer"] is True

            os.environ["CASEFILE_CHAT_GOAL_SESSION_ROLLOUT"] = "shadow"
            shadow_control = client.post(
                message_url,
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "expected_draft_revision": 2,
                    "content": "shadow 不消费控制消息。",
                    "delivery_mode": "steer",
                    "expected_goal_id": goal_id,
                    "expected_goal_revision": 1,
                },
            )
            assert shadow_control.status_code == 409
            assert shadow_control.json()["code"] == "agent_thread_busy"

            os.environ["CASEFILE_CHAT_GOAL_SESSION_ROLLOUT"] = "active"
            os.environ["CASEFILE_CHAT_GOAL_ROLLOUT"] = "off"
            invalid_rollout = client.post(
                message_url,
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "expected_draft_revision": 2,
                    "content": "无效 rollout 组合。",
                    "delivery_mode": "steer",
                    "expected_goal_id": goal_id,
                    "expected_goal_revision": 1,
                },
            )
            assert invalid_rollout.status_code == 409
            assert invalid_rollout.json()["code"] == "agent_goal_state_conflict"
            os.environ["CASEFILE_CHAT_GOAL_ROLLOUT"] = "active"

            conflict = client.post(
                message_url,
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "expected_draft_revision": 2,
                    "content": "基于旧 revision 的控制消息。",
                    "delivery_mode": "steer",
                    "expected_goal_id": goal_id,
                    "expected_goal_revision": 2,
                },
            )
            assert conflict.status_code == 409

            steered = client.post(
                message_url,
                headers=headers,
                json={
                    "expected_draft_id": draft_id,
                    "expected_draft_revision": 2,
                    "content": "优先审计时间线矛盾。",
                    "delivery_mode": "steer",
                    "expected_goal_id": goal_id,
                    "expected_goal_revision": 1,
                },
            )
            assert steered.status_code == 202
            delivery = steered.json()["delivery"]
            assert delivery["goal_id"] == goal_id
            assert delivery["successor_goal_id"] is None
            assert delivery["mode"] == "steer"
            assert delivery["status"] == "queued"
            response_message_id = int(delivery["response_message_id"])

            cancelled = client.post(f"{goal_url}/cancel", headers=headers)
            assert cancelled.status_code == 202
            assert cancelled.json()["status"] == "cancelled"
            assert cancelled.json()["cancellable"] is False

            events = client.get(f"{goal_url}/events", headers=headers)
            assert events.status_code == 200
            assert [row["status"] for row in events.json()] == [
                "interpreting",
                "cancelled",
            ]
            resumed = client.get(
                f"{goal_url}/events?after_sequence=1",
                headers=headers,
            )
            assert [row["sequence"] for row in resumed.json()] == [2]

            stream = client.get(
                f"{goal_url}/stream",
                headers={**headers, "Last-Event-ID": "1"},
            )
            assert stream.status_code == 200
            assert "id: 2" in stream.text
            assert "event: goal.transition" in stream.text

            with engine.begin() as connection:
                other_actor_id = int(
                    connection.execute(
                        text(
                            "INSERT INTO users (display_name) "
                            "VALUES ('Other Goal Reader') RETURNING id"
                        )
                    ).scalar_one()
                )
            hidden = client.get(
                goal_url,
                headers={"X-CaseFile-User-Id": str(other_actor_id)},
            )
            assert hidden.status_code == 404

        with factory() as session:
            goal_task = session.get(TaskRun, goal_run_id)
            queued_delivery = session.scalar(
                select(AgentGoalDelivery).where(AgentGoalDelivery.goal_session_id == goal_id)
            )
            response_message = session.get(AgentMessage, response_message_id)
            thread_task_count = session.scalar(
                select(func.count(TaskRun.id)).where(TaskRun.agent_thread_id == thread_id)
            )
            assert goal_task is not None
            assert goal_task.input_jsonb["goal_session"] == {
                "goal_id": goal_id,
                "goal_revision": 0,
                "runtime_version": "goal-session-runtime.v1",
                "policy_version": "goal-session-policy.v1",
                "capability_registry_version": "casefile-chat-goal-capabilities.v1",
            }
            assert goal_task.status == "cancelled"
            assert queued_delivery is not None
            assert queued_delivery.status == "cancelled"
            assert response_message is not None
            assert response_message.status == "cancelled"
            assert thread_task_count == 2
