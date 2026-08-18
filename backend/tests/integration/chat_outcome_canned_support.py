"""Shared M1 canned-outcome trial runner for chat acceptance tests.

Keeps the production-path setup (prepare/adopt → send_agent_message → Worker →
persisted outcome grading) in one place so context-acceptance tests can also
inspect the frozen provider request and TaskEvents without duplicating the
30-task harness.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

from application_services_test_support import (
    RichFixtureProvider,
    _adopt_candidate,
    _prepare_task,
)
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from casefile.agent_runtime.models import (
    CaseFileChatCandidate,
    CaseFileChatCandidateV2,
    CaseFileChatRequest,
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
)
from casefile.data_postgres.models import TaskRun
from casefile.worker.runtime import Worker, WorkerConfig


@dataclass(frozen=True, slots=True)
class CannedTrialOutcome:
    """One persisted M1 trial plus the artifacts context acceptance inspects."""

    verdict: ChatOutcomeTrialVerdict
    chat_task_id: int
    frozen_input: dict[str, Any]
    result_jsonb: dict[str, Any]
    provider_request: CaseFileChatRequest | None
    candidate: CaseFileChatCandidate | CaseFileChatCandidateV2
    draft_unchanged: bool
    patch_set: dict[str, Any] | None


def run_chat_trial(
    engine: Engine,
    actor_id: int,
    master_key: str,
    task: ChatOutcomeTask,
    provider: Any,
    *,
    task_provider: str = "openai",
    message_builder: Callable[[dict[str, Any]], str] | None = None,
) -> CannedTrialOutcome:
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
            if not isinstance(content_before, dict):
                raise AssertionError((task.task_id, "adopted draft content is not a casefile"))
            message = (
                message_builder(content_before)
                if message_builder is not None
                else task.message
            )
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
                content=message,
                provider=task_provider,
                focus=None,
                routing_hint=task.hint,
            )
        chat_task_id = int(queued["task"]["task_run_id"])

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id=f"m1-chat-{task.task_id}"),
            provider_factory=lambda _task: provider,
        )
        assert chat_worker.run_once() is True
        provider_request: CaseFileChatRequest | None = None
        if isinstance(provider, CannedChatOutcomeProvider):
            assert len(provider.requests) == 1
            provider_request = provider.requests[0]

        with factory() as session:
            workflow = WorkflowService(session)
            messages = workflow.list_agent_messages(
                actor_id,
                project_id,
                thread["thread_id"],
            )
            assistant = next(message for message in messages if message["role"] == "assistant")
            if assistant["status"] != "completed":
                with factory() as session:
                    task_row = session.get(TaskRun, chat_task_id)
                    failed_details = (
                        None
                        if task_row is None
                        else (task_row.status, task_row.error_code, task_row.error_details_jsonb)
                    )
                raise AssertionError(
                    (
                        task.task_id,
                        assistant["status"],
                        assistant.get("error_code"),
                        assistant.get("error_details"),
                        failed_details,
                    )
                )
        with factory() as session:
            draft_after = CaseFileService(session).get_draft(actor_id, project_id)
        with factory() as session:
            task_row = session.get(TaskRun, chat_task_id)
            assert task_row is not None
            assert task_row.status == "succeeded", (
                task.task_id,
                task_row.status,
                task_row.error_code,
                task_row.error_details_jsonb,
            )
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
        verdict = grade_persisted_canned_trial(
            task,
            casefile=frozen_input["casefile"],
            validation_issues=validation_issues,
            candidate=candidate,
            routing=routing,
            draft_unchanged=draft_unchanged,
        )
        return CannedTrialOutcome(
            verdict=verdict,
            chat_task_id=chat_task_id,
            frozen_input=frozen_input,
            result_jsonb=result_jsonb,
            provider_request=provider_request,
            candidate=candidate,
            draft_unchanged=draft_unchanged,
            patch_set=assistant["patch_set"],
        )


def run_canned_trial(
    engine: Engine,
    actor_id: int,
    master_key: str,
    task: ChatOutcomeTask,
) -> CannedTrialOutcome:
    """Run one trial with the deterministic canned provider."""

    return run_chat_trial(
        engine,
        actor_id,
        master_key,
        task,
        CannedChatOutcomeProvider(),
    )
