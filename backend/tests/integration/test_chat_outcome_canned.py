"""M1 DB Canned Outcome integration test for the CaseFile chat Agent.

Every T1 Task is sent through the real production path once per trial in a
fresh project, completed by CannedChatOutcomeProvider, and the persisted
AgentMessage/PatchSet/Draft outcome is graded with the deterministic Grader.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from application_services_test_support import (
    RichFixtureProvider,
    _adopt_candidate,
    _prepare_task,
)
from casefile.application.services import CaseFileService
from casefile.application.workflow_service import WorkflowService
from casefile.benchmark.chat_outcome_canned import (
    CannedChatOutcomeProvider,
    grade_persisted_canned_trial,
    persisted_candidate_from_result,
)
from casefile.benchmark.chat_outcome_eval import (
    ChatOutcomeTask,
    ChatOutcomeTrialVerdict,
    build_outcome_tasks,
)
from casefile.data_postgres.models import TaskRun
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres


def _run_canned_trial(
    engine: Engine,
    actor_id: int,
    master_key: str,
    task: ChatOutcomeTask,
) -> ChatOutcomeTrialVerdict:
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

        generation_worker = Worker(
            factory,
            config=WorkerConfig(worker_id=f"m1-gen-{task.task_id}"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            draft_before = CaseFileService(session).get_draft(actor_id, project_id)
            revision_before = int(draft_before["revision"])
            content_before = draft_before["content"]
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=revision_before,
                title=None,
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread["thread_id"],
                expected_draft_id=draft_id,
                expected_draft_revision=revision_before,
                content=task.message,
                focus=None,
                routing_hint=task.hint,
            )
        chat_task_id = int(queued["task"]["task_run_id"])

        provider = CannedChatOutcomeProvider()
        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id=f"m1-chat-{task.task_id}"),
            provider_factory=lambda _task: provider,
        )
        assert chat_worker.run_once() is True
        assert len(provider.requests) == 1

        with factory() as session:
            workflow = WorkflowService(session)
            messages = workflow.list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )
            assistant = next(message for message in messages if message["role"] == "assistant")
            assert assistant["status"] == "completed"
        with factory() as session:
            draft_after = CaseFileService(session).get_draft(actor_id, project_id)
        with factory() as session:
            task_row = session.get(TaskRun, chat_task_id)
            assert task_row is not None
            assert task_row.status == "succeeded"
            frozen_input = task_row.input_jsonb
            result_jsonb = task_row.result_jsonb
        assert isinstance(frozen_input, dict)
        assert isinstance(result_jsonb, dict)

        patch_operations = (
            assistant["patch_set"]["operations"] if assistant["patch_set"] is not None else []
        )
        candidate = persisted_candidate_from_result(result_jsonb, patch_operations)
        validation_issues = tuple(
            issue
            for issue in frozen_input.get("validation", {}).get("issues", [])
            if isinstance(issue, dict)
        )
        routing = result_jsonb.get("routing")
        routing = routing if isinstance(routing, dict) else {}
        draft_unchanged = (
            int(draft_after["revision"]) == revision_before
            and draft_after["content"] == content_before
        )
        return grade_persisted_canned_trial(
            task,
            casefile=frozen_input["casefile"],
            validation_issues=validation_issues,
            candidate=candidate,
            routing=routing,
            draft_unchanged=draft_unchanged,
        )


def test_m1_canned_outcome_suite_passes_through_production_path(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    tasks = build_outcome_tasks()
    verdicts: list[ChatOutcomeTrialVerdict] = []
    for task in tasks:
        verdict = _run_canned_trial(engine, actor_id, master_key, task)
        verdicts.append(verdict)
        assert verdict.passed, (task.task_id, verdict.failures)
    assert len(verdicts) == 30
    assert all(verdict.draft_unchanged for verdict in verdicts)


def test_m1_canned_denied_route_suppresses_suggestions(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    task = next(task for task in build_outcome_tasks() if task.task_id == "golden-entity-question")
    verdict = _run_canned_trial(engine, actor_id, master_key, task)
    assert verdict.passed
    assert verdict.allow_suggestions is False
    assert verdict.unnecessary_suggestions is False
