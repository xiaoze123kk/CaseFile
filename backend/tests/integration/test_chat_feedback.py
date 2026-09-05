"""Feedback protocol, public replay, and non-interrupting Attempt fencing."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from application_services_test_support import RichFixtureProvider, _adopt_candidate, _prepare_task
from casefile.api.app import create_app
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import TaskEvent, TaskRun
from casefile.worker.chat_feedback import ChatFeedbackWriter
from casefile.worker.runtime import Worker, WorkerConfig
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres


def test_feedback_is_replayable_versioned_fenced_and_non_interrupting(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(
        os.environ, {"CASEFILE_MASTER_KEY": master_key, "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT": "off"}
    ):
        project_id, generation_id = _prepare_task(engine, actor_id)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="feedback-test"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert worker.run_once()
        draft_id = int(_adopt_candidate(engine, actor_id, project_id, generation_id)["draft_id"])
        with factory() as session:
            service = WorkflowService(session)
            thread = service.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title="反馈验证",
            )
            receipt = service.send_agent_message(
                actor_id,
                project_id,
                int(thread["thread_id"]),
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="检查卷宗。",
            )
        run_id = int(receipt["task"]["task_run_id"])
        claimed = worker._claim_next()
        assert isinstance(claimed, tuple) and claimed[0] == run_id
        feedback = ChatFeedbackWriter(factory, *claimed)
        stale = ChatFeedbackWriter(factory, run_id, claimed[1] + 10000)
        stale("message.preview_started", {})
        feedback("message.preview_started", {})
        feedback("message.preview_delta", {"offset": 0, "text": "正在核对。"})
        feedback("message.preview_delta", {"offset": 0, "text": "重复片段"})
        with factory() as session:
            rows = list(
                session.scalars(
                    select(TaskEvent).where(
                        TaskEvent.task_run_id == run_id,
                        TaskEvent.event_type == "message.preview_delta",
                    )
                )
            )
            assert len(rows) == 1
            start_sequence = feedback.preview_sequence
        with TestClient(create_app(engine.url.render_as_string(hide_password=False))) as client:
            headers = {"X-CaseFile-User-Id": str(actor_id)}
            path = f"/api/v1/projects/{project_id}/agent/runs/{run_id}/events"
            old = client.get(path, headers=headers)
            assert old.status_code == 200
            assert not any(event["event"].startswith("message.") for event in old.json())
            enhanced = client.get(path, params={"feedback_version": 2}, headers=headers)
            assert enhanced.status_code == 200
            deltas = [
                event for event in enhanced.json() if event["event"] == "message.preview_delta"
            ]
            assert len(deltas) == 1 and deltas[0]["preview_sequence"] == start_sequence
            assert (
                client.get(path, params={"feedback_version": 3}, headers=headers).status_code == 422
            )
            assert client.get(
                path, headers={"X-CaseFile-User-Id": str(actor_id + 999)}
            ).status_code in {401, 403, 404}
            feedback("message.preview_invalidated", {"discard": True})
            replay = client.get(path, params={"feedback_version": 2}, headers=headers).json()
            assert not any(event["event"] == "message.preview_delta" for event in replay)
            with factory() as session, session.begin():
                task = session.get(TaskRun, run_id)
                assert task is not None
                task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            recovered = worker._claim_next()
            assert isinstance(recovered, tuple) and recovered[1] != claimed[1]
            feedback("message.preview_delta", {"offset": 6, "text": "旧尝试不应写入"})
            with factory() as session:
                WorkflowService(session).cancel_agent_run(actor_id, project_id, run_id)
            feedback("message.preview_delta", {"offset": 6, "text": "不可公开"})
            with factory() as session:
                assert session.get(TaskRun, run_id).status == "cancelling"
                assert (
                    len(
                        list(
                            session.scalars(
                                select(TaskEvent).where(
                                    TaskEvent.task_run_id == run_id,
                                    TaskEvent.event_type == "message.preview_delta",
                                )
                            )
                        )
                    )
                    == 1
                )
