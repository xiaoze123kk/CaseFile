"""Phase 3 rolling Thread Memory acceptance on the real production path.

Opt-in via ``CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v2``. The
test keeps a deterministic FakeProvider so the whole pipeline can run without
network, and lowers the compaction thresholds so a single user/assistant
exchange triggers the monitor after the first reply.
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
from casefile.agent_runtime import FakeProvider
from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_V2_VERSION,
    CHAT_CONTEXT_PROMPT_V2_VERSION,
)
from casefile.agent_runtime.models import CaseFileChatRequest
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
from casefile.data_postgres.models import (
    AgentThreadContextState,
    TaskEvent,
    TaskRun,
)
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres

ROLLOUT = CHAT_CONTEXT_POLICY_V2_VERSION


class CapturingChatProvider(FakeProvider):
    """Deterministic fake chat provider that keeps the bound requests."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[CaseFileChatRequest] = []

    def chat(self, request: CaseFileChatRequest):
        self.requests.append(request)
        return super().chat(request)


def test_phase3_rolling_compaction_freezes_state_and_binds_v5(
    workflow_database: tuple[Engine, int, str],
) -> None:
    if os.environ.get("CASEFILE_CHAT_CONTEXT_ROLLOUT") != ROLLOUT:
        pytest.skip("CASEFILE_CHAT_CONTEXT_ROLLOUT is not casefile-chat-context-v2")

    engine, actor_id, master_key = workflow_database
    provider = CapturingChatProvider()
    with (
        patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}),
        patch.dict(
            os.environ,
            {
                "CASEFILE_CHAT_COMPACTION_HISTORY_TOKENS": "1",
                "CASEFILE_CHAT_COMPACTION_MIN_MESSAGES": "2",
            },
        ),
    ):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

        generation_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="phase3-generation"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            revision = int(CaseFileService(session).get_draft(actor_id, project_id)["revision"])
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=revision,
                title=None,
            )
            thread_id = int(thread["thread_id"])
            first = workflow.send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=revision,
                content="请通读整个卷宗并核对关键人物。",
                provider="openai",
                routing_hint=None,
            )
        first_task_id = int(first["task"]["task_run_id"])

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="phase3-chat"),
            provider_factory=lambda _task: provider,
        )
        assert chat_worker.run_once() is True

        with factory() as session:
            first_row = session.get(TaskRun, first_task_id)
            assert first_row is not None
            assert first_row.status == "succeeded", (
                first_row.status,
                first_row.error_code,
                first_row.error_details_jsonb,
            )
            assert first_row.input_jsonb.get("context_policy_version") == ROLLOUT
            context_events = list(
                session.scalars(
                    select(TaskEvent)
                    .where(
                        TaskEvent.task_run_id == first_task_id,
                        TaskEvent.event_type.in_(
                            (
                                "context.compacted",
                                "context.compaction_failed",
                                "context.compaction_skipped",
                            )
                        ),
                    )
                    .order_by(TaskEvent.sequence_no)
                )
            )
            state_row = session.scalar(
                select(AgentThreadContextState)
                .where(AgentThreadContextState.thread_id == thread_id)
                .order_by(AgentThreadContextState.id.desc())
                .limit(1)
            )
            assert context_events, [event.payload_jsonb for event in context_events]
            assert state_row is not None
            assert state_row.policy_version == ROLLOUT
            assert state_row.from_message_seq == 1
            assert state_row.to_message_seq == 2
            state_id = int(state_row.id)
            compacted_events = list(
                session.scalars(
                    select(TaskEvent)
                    .where(
                        TaskEvent.task_run_id == first_task_id,
                        TaskEvent.event_type == "context.compacted",
                    )
                    .order_by(TaskEvent.sequence_no)
                )
            )
            assert len(compacted_events) == 1
            assert compacted_events[0].payload_jsonb["state_id"] == state_id

        with factory() as session:
            workflow = WorkflowService(session)
            second = workflow.send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=revision,
                content="请继续核对事件时间线。",
                provider="openai",
                routing_hint=None,
            )
            second_task_id = int(second["task"]["task_run_id"])
            second_row = session.get(TaskRun, second_task_id)
            assert second_row is not None
            assert second_row.prompt_version == CHAT_CONTEXT_PROMPT_V2_VERSION
            context_state = second_row.input_jsonb.get("context_state")
            assert isinstance(context_state, dict)
            assert context_state["state_id"] == state_id
            assert context_state["policy_version"] == ROLLOUT

        assert chat_worker.run_once() is True
        assert len(provider.requests) == 2
        second_request = provider.requests[1]
        assert second_request.prompt_version == CHAT_CONTEXT_PROMPT_V2_VERSION
        assert second_request.context_policy_version == ROLLOUT
        assert second_request.assembled_input is not None
        assert second_request.assembled_input["thread_memory"]["last_compacted_message_seq"] == 2

        with factory() as session:
            built_events = list(
                session.scalars(
                    select(TaskEvent)
                    .where(
                        TaskEvent.task_run_id == second_task_id,
                        TaskEvent.event_type == "context.built",
                    )
                    .order_by(TaskEvent.sequence_no)
                )
            )
            assert len(built_events) == 1
            blocks = {
                block["id"]: block
                for block in built_events[0].payload_jsonb["blocks"]
            }
            assert blocks["thread_memory"]["kind"] == "thread_memory"
            assert blocks["thread_memory"]["tokens"] >= 0


def _run_edit_trial(
    engine: Engine,
    actor_id: int,
    master_key: str,
    task: ChatOutcomeTask,
    *,
    rollout: str,
    warmup: bool,
) -> tuple[ChatOutcomeTrialVerdict, CaseFileChatRequest | None]:
    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_CONTEXT_ROLLOUT": rollout,
        },
    ):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        generation_worker = Worker(
            factory,
            config=WorkerConfig(worker_id=f"phase3-m1-gen-{rollout}"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        provider = CannedChatOutcomeProvider()
        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id=f"phase3-m1-chat-{rollout}"),
            provider_factory=lambda _task: provider,
        )

        with factory() as session:
            revision = int(
                CaseFileService(session).get_draft(actor_id, project_id)["revision"]
            )
            draft_before = CaseFileService(session).get_draft(actor_id, project_id)
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=revision,
                title=None,
            )
            thread_id = int(thread["thread_id"])
            if warmup:
                workflow.send_agent_message(
                    actor_id,
                    project_id,
                    thread_id,
                    expected_draft_id=draft_id,
                    expected_draft_revision=revision,
                    content="请通读整个卷宗并核对关键人物。",
                    provider="openai",
                    routing_hint=None,
                )
        if warmup:
            assert chat_worker.run_once() is True

        with factory() as session:
            workflow = WorkflowService(session)
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=revision,
                content=task.message,
                provider="openai",
                routing_hint=task.hint,
            )
        edit_task_id = int(queued["task"]["task_run_id"])

        assert chat_worker.run_once() is True

        with factory() as session:
            edit_row = session.get(TaskRun, edit_task_id)
            assert edit_row is not None
            assert edit_row.status == "succeeded", (
                edit_row.status,
                edit_row.error_code,
                edit_row.error_details_jsonb,
            )
            frozen_input = edit_row.input_jsonb
            result_jsonb = edit_row.result_jsonb
        with factory() as session:
            workflow = WorkflowService(session)
            messages = workflow.list_agent_messages(actor_id, project_id, thread_id)
            assistants = [
                message for message in messages if message["role"] == "assistant"
            ]
            assert assistants and assistants[-1]["status"] == "completed"
            patch_operations = (
                assistants[-1]["patch_set"]["operations"]
                if assistants[-1]["patch_set"] is not None
                else []
            )
            draft_after = CaseFileService(session).get_draft(actor_id, project_id)
        assert isinstance(frozen_input, dict)
        assert isinstance(result_jsonb, dict)
        routing = result_jsonb.get("routing")
        verdict = grade_persisted_canned_trial(
            task,
            casefile=frozen_input["casefile"],
            validation_issues=tuple(
                issue
                for issue in frozen_input.get("validation", {}).get("issues", [])
                if isinstance(issue, dict)
            ),
            candidate=persisted_candidate_from_result(result_jsonb, patch_operations),
            routing=routing if isinstance(routing, dict) else {},
            draft_unchanged=(
                int(draft_after["revision"]) == revision
                and draft_after["content"] == draft_before["content"]
            ),
        )
        edit_request = provider.requests[-1] if provider.requests else None
        return verdict, edit_request


def test_phase3_compacted_patch_suggestion_legality_does_not_degrade(
    workflow_database: tuple[Engine, int, str],
) -> None:
    if os.environ.get("CASEFILE_CHAT_CONTEXT_ROLLOUT") != ROLLOUT:
        pytest.skip("CASEFILE_CHAT_CONTEXT_ROLLOUT is not casefile-chat-context-v2")

    engine, actor_id, master_key = workflow_database
    task = next(
        item for item in build_outcome_tasks() if item.task_id == "golden-edit-description"
    )
    with patch.dict(
        os.environ,
        {
            "CASEFILE_CHAT_COMPACTION_HISTORY_TOKENS": "1",
            "CASEFILE_CHAT_COMPACTION_MIN_MESSAGES": "2",
        },
    ):
        baseline, _ = _run_edit_trial(
            engine,
            actor_id,
            master_key,
            task,
            rollout="agent-focus-v1",
            warmup=False,
        )
        compacted, compacted_request = _run_edit_trial(
            engine,
            actor_id,
            master_key,
            task,
            rollout=ROLLOUT,
            warmup=True,
        )
    assert baseline.passed is True
    assert compacted.passed is True
    assert compacted.suggestion_valid_count >= baseline.suggestion_valid_count
    assert compacted.suggestion_total_count == baseline.suggestion_total_count
    assert compacted_request is not None
    assert compacted_request.assembled_input is not None
    assert isinstance(compacted_request.assembled_input["thread_memory"], dict)
    assert compacted_request.context_policy_version == ROLLOUT
