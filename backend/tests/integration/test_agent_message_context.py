"""Per-message context uses the same transaction and facts as the queued task."""

import os
from unittest.mock import patch

import pytest
from alembic import command
from application_services_test_support import (
    RichFixtureProvider,
    _adopt_candidate,
    _alembic_config,
    _prepare_task,
)
from casefile.application.agent_message_context import message_context_input
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import (
    AgentMessage,
    AgentMessageContext,
    AgentMessageContextRef,
    TaskRun,
)
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres


def test_context_is_atomic_pruned_isolated_and_shared_with_task(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_id = _prepare_task(engine, actor_id)
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="context-test"),
            provider_factory=lambda _task: RichFixtureProvider(),
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_id)
        draft_id = int(adopted["draft_id"])
        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id, project_id, expected_draft_id=draft_id, expected_draft_revision=2
            )
            thread_id = int(thread["thread_id"])
            sent = workflow.send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="只检查当前选择。",
                focus={
                    "object_ids": ["claim_backup_trigger", "missing"],
                    "event_ids": ["missing-event"],
                    "validation_issue_ids": ["missing-issue"],
                    "view": "relations",
                },
            )
            message_id = int(sent["user_message"]["message_id"])
            task_id = int(sent["task"]["task_run_id"])
        with factory() as session:
            saved = message_context_input(session, project_id, message_id)
            snapshot = saved["context_snapshot"]
            assert snapshot == sent["user_message"]["context_snapshot"]
            assert snapshot["object_ids"] == ["claim_backup_trigger"]
            assert snapshot["event_ids"] == []
            assert snapshot["validation_issue_ids"] == []
            assert snapshot["draft_revision"] == 2
            task = session.get(TaskRun, task_id)
            assert task is not None
            assert task.input_jsonb["context_snapshot"] == snapshot
            assert task.input_jsonb["focus"]["object_ids"] == snapshot["object_ids"]
            assert (
                message_context_input(session, project_id + 1000, message_id)["context_snapshot"]
                is None
            )
            assert session.scalar(select(func.count(AgentMessageContextRef.id))) == 1
        with factory() as session:
            workflow = WorkflowService(session)
            workflow.cancel_agent_run(actor_id, project_id, task_id)
        with (
            factory() as session,
            patch(
                "casefile.application.workflow.agent._persist_message_context",
                side_effect=RuntimeError("injected persistence failure"),
            ),
        ):
            with pytest.raises(RuntimeError, match="injected"):
                WorkflowService(session).send_agent_message(
                    actor_id,
                    project_id,
                    thread_id,
                    expected_draft_id=draft_id,
                    expected_draft_revision=2,
                    content="不能部分保存。",
                )
        with factory() as session:
            assert session.scalar(select(func.count(AgentMessageContext.id))) == 1
        with factory() as session:
            messages = WorkflowService(session).list_agent_messages(actor_id, project_id, thread_id)
            assert len(messages) == 2
            assert messages[0]["context_snapshot"] == snapshot
        with factory() as session, session.begin():
            session.add(
                AgentMessage(
                    project_id=project_id,
                    thread_id=thread_id,
                    sequence_no=3,
                    role="user",
                    status="completed",
                    content_text="Legacy without recorded input",
                    created_by_user_id=actor_id,
                )
            )
        # Exercise the additive migration against real prior-version messages/tasks.
        test_url = os.environ["CASEFILE_TEST_DATABASE_URL"]
        with patch.dict(os.environ, {"DATABASE_URL": test_url}):
            config = _alembic_config(test_url)
            command.downgrade(config, "20260903182536")
            command.upgrade(config, "head")
        with factory() as session:
            restored = WorkflowService(session).list_agent_messages(actor_id, project_id, thread_id)
            assert restored[0]["context_snapshot"] == snapshot
            assert restored[-1]["context_snapshot"] is None
