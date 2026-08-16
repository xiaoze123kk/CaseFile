"""L3 feedback metrics integration tests over the real PatchSet lifecycle."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from application_services_test_support import (
    ChatSuggestionProvider,
    RichFixtureProvider,
    _adopt_candidate,
    _prepare_task,
)
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from casefile.application.services import CaseFileService
from casefile.application.workflow_service import WorkflowService
from casefile.benchmark.chat_feedback_metrics import run_chat_feedback_metrics
from casefile.worker.runtime import Worker, WorkerConfig

pytestmark = pytest.mark.postgres


def test_chat_feedback_metrics_empty_baseline(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, _actor_id, _master_key = workflow_database
    report = run_chat_feedback_metrics(engine)
    assert report.patch_set_total == 0
    assert report.pending == 0
    assert report.apply_rate == 0.0
    assert report.reject_rate == 0.0
    assert report.undo_rate == 0.0
    assert report.stale_rate == 0.0
    assert report.post_apply_rewrite_rate == 0.0


def _pending_patch_set(
    engine: Engine,
    actor_id: int,
    master_key: str,
) -> tuple[int, int, int, dict[str, object]]:
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

        generation_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="feedback-fixture-gen"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            draft = CaseFileService(session).get_draft(actor_id, project_id)
            revision = int(draft["revision"])
        with factory() as session:
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=revision,
                title=None,
            )
            workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=revision,
                content="请通读整个卷宗并给出可以审阅的修改建议。",
            )
        provider = ChatSuggestionProvider()
        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="feedback-fixture-chat"),
            provider_factory=lambda _task: provider,
        )
        assert chat_worker.run_once() is True

        with factory() as session:
            workflow = WorkflowService(session)
            messages = workflow.list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )
        assistant = next(message for message in messages if message["role"] == "assistant")
        patch_set = assistant["patch_set"]
        assert isinstance(patch_set, dict)
        return project_id, draft_id, revision, patch_set


def test_chat_feedback_metrics_pending_apply_undo_lifecycle(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    project_id, draft_id, revision, patch_set = _pending_patch_set(
        engine,
        actor_id,
        master_key,
    )
    patch_set_id = int(patch_set["patch_set_id"])

    pending = run_chat_feedback_metrics(engine, project_id=project_id)
    assert pending.patch_set_total == 1
    assert pending.pending == 1
    assert pending.applied == 0
    assert pending.apply_rate == 0.0

    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        applied = WorkflowService(session).apply_agent_patch_set(
            actor_id,
            project_id,
            patch_set_id,
            expected_draft_id=draft_id,
            expected_revision=revision,
            operation_ids=None,
        )
    after_apply = run_chat_feedback_metrics(engine, project_id=project_id)
    assert after_apply.applied == 1
    assert after_apply.apply_rate == 1.0
    assert after_apply.undo_rate == 0.0
    assert after_apply.post_apply_rewrite_rate == 0.0

    with factory() as session:
        undone = WorkflowService(session).undo_agent_patch_set(
            actor_id,
            project_id,
            patch_set_id,
            expected_draft_id=draft_id,
            expected_revision=int(applied["draft_revision"]),
        )
    after_undo = run_chat_feedback_metrics(engine, project_id=project_id)
    assert undone["status"] == "undone"
    assert after_undo.undone == 1
    assert after_undo.applied == 0
    assert after_undo.apply_rate == 1.0
    assert after_undo.undo_rate == 1.0
    assert after_undo.reject_rate == 0.0
    assert after_undo.stale_rate == 0.0
