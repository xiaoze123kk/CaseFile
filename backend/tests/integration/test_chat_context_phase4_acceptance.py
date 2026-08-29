"""Phase 4 Context Tools acceptance on the real production path.

v6 (hardened routing + structured logic_audit rollout) is the accepted
default, pairing ``casefile-chat-context-v6`` with the active Goal v17
prompt and v4 toolset. The v5 opt-in test still runs when
``CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v5`` (v9 prompt), the
v4 opt-in test runs when
``CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v4``, and the v3 opt-in
test runs when
``CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v3``. All use the
deterministic FakeProvider and lowered compaction thresholds so one exchange
produces the same rolling compaction behavior as Phase 3.
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
from casefile.agent_runtime.chat_tools import (
    CHAT_TOOLSET_V3_VERSION,
    CHAT_TOOLSET_V4_VERSION,
)
from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_V3_VERSION,
    CHAT_CONTEXT_POLICY_V4_VERSION,
    CHAT_CONTEXT_POLICY_V5_VERSION,
    CHAT_CONTEXT_POLICY_V6_VERSION,
    CHAT_CONTEXT_PROMPT_V4_VERSION,
    CHAT_CONTEXT_PROMPT_V5_VERSION,
    CHAT_CONTEXT_PROMPT_V6_VERSION,
)
from casefile.agent_runtime.models import CaseFileChatRequest
from casefile.application.services import CaseFileService
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import AgentThreadContextState, TaskRun
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres

ROLLOUT = CHAT_CONTEXT_POLICY_V3_VERSION
ROLLOUT_V4 = CHAT_CONTEXT_POLICY_V4_VERSION
ROLLOUT_V5 = CHAT_CONTEXT_POLICY_V5_VERSION
ROLLOUT_V6 = CHAT_CONTEXT_POLICY_V6_VERSION


class CapturingChatProvider(FakeProvider):
    """Deterministic fake chat provider that keeps the bound requests."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[CaseFileChatRequest] = []

    def chat(self, request: CaseFileChatRequest):
        self.requests.append(request)
        return super().chat(request)


def test_phase4_context_tools_rollout_binds_v7_and_v3_toolset(
    workflow_database: tuple[Engine, int, str],
) -> None:
    if os.environ.get("CASEFILE_CHAT_CONTEXT_ROLLOUT") != ROLLOUT:
        pytest.skip("CASEFILE_CHAT_CONTEXT_ROLLOUT is not casefile-chat-context-v3")

    engine, actor_id, master_key = workflow_database
    provider = CapturingChatProvider()
    with (
        patch.dict(
            os.environ,
            {
                "CASEFILE_MASTER_KEY": master_key,
                "CASEFILE_CHAT_GOAL_ROLLOUT": "off",
            },
        ),
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
            config=WorkerConfig(worker_id="phase4-generation"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            revision = int(
                CaseFileService(session).get_draft(actor_id, project_id)["revision"]
            )
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
            config=WorkerConfig(worker_id="phase4-chat"),
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
            assert first_row.prompt_version == CHAT_CONTEXT_PROMPT_V4_VERSION
            assert first_row.toolset_version == CHAT_TOOLSET_V3_VERSION
            assert first_row.input_jsonb.get("context_policy_version") == ROLLOUT
            state_row = session.scalar(
                select(AgentThreadContextState)
                .where(AgentThreadContextState.thread_id == thread_id)
                .order_by(AgentThreadContextState.id.desc())
                .limit(1)
            )
            assert state_row is not None
            state_id = int(state_row.id)

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
            assert second_row.prompt_version == CHAT_CONTEXT_PROMPT_V4_VERSION
            assert second_row.toolset_version == CHAT_TOOLSET_V3_VERSION
            context_state = second_row.input_jsonb.get("context_state")
            assert isinstance(context_state, dict)
            assert context_state["state_id"] == state_id

        assert chat_worker.run_once() is True
        assert len(provider.requests) == 2
        second_request = provider.requests[1]
        assert second_request.prompt_version == CHAT_CONTEXT_PROMPT_V4_VERSION
        assert second_request.toolset_version == CHAT_TOOLSET_V3_VERSION
        assert second_request.context_policy_version == ROLLOUT
        assert second_request.assembled_input is not None
        dashboard = second_request.assembled_input.get("context_dashboard")
        assert isinstance(dashboard, dict)
        assert isinstance(dashboard["recoverable_evidence_ids"], list)
        assert second_request.thread_evidence_resolver is not None
        evidence = second_request.thread_evidence_resolver(
            f"thread://{thread_id}/message/2"
        )
        assert isinstance(evidence, dict)
        assert evidence["content"]


def test_phase4_v4_rollout_binds_v8_v4_toolset_and_full_audit_snapshot(
    workflow_database: tuple[Engine, int, str],
) -> None:
    rollout = os.environ.get("CASEFILE_CHAT_CONTEXT_ROLLOUT")
    if rollout != ROLLOUT_V4:
        pytest.skip("CASEFILE_CHAT_CONTEXT_ROLLOUT is not casefile-chat-context-v4")

    engine, actor_id, master_key = workflow_database
    provider = CapturingChatProvider()
    with (
        patch.dict(
            os.environ,
            {
                "CASEFILE_MASTER_KEY": master_key,
                "CASEFILE_CHAT_GOAL_ROLLOUT": "off",
            },
        ),
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
            config=WorkerConfig(worker_id="phase4-v4-generation"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            revision = int(
                CaseFileService(session).get_draft(actor_id, project_id)["revision"]
            )
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
                content="请把全案逻辑漏洞查一遍，能修的给出补丁。",
                provider="openai",
                routing_hint={"entrypoint": "preset", "preset_id": "audit"},
            )
            first_task_id = int(first["task"]["task_run_id"])

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="phase4-v4-chat"),
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
            assert first_row.prompt_version == CHAT_CONTEXT_PROMPT_V5_VERSION
            assert first_row.toolset_version == CHAT_TOOLSET_V4_VERSION
            assert first_row.input_jsonb.get("context_policy_version") == ROLLOUT_V4

        assert len(provider.requests) == 1
        request = provider.requests[0]
        assert request.prompt_version == CHAT_CONTEXT_PROMPT_V5_VERSION
        assert request.toolset_version == CHAT_TOOLSET_V4_VERSION
        assert request.context_policy_version == ROLLOUT_V4
        execution_profile = request.route.execution_profile
        assert execution_profile["profile"] == "logic_audit.full_review"
        assert execution_profile["prompt_component"] == "audit"
        assert "simulate_patch_application" in execution_profile["toolset"]
        assert request.assembled_input is not None
        full_validation_issues = request.assembled_input["validation_issues"]
        assert isinstance(full_validation_issues, list)
        assert full_validation_issues == list(request.validation_issues)


def test_phase4_v5_rollout_binds_v9_v4_toolset_and_structured_audit(
    workflow_database: tuple[Engine, int, str],
) -> None:
    rollout = os.environ.get("CASEFILE_CHAT_CONTEXT_ROLLOUT")
    if rollout != ROLLOUT_V5:
        pytest.skip("CASEFILE_CHAT_CONTEXT_ROLLOUT is not casefile-chat-context-v5")

    engine, actor_id, master_key = workflow_database
    provider = CapturingChatProvider()
    with (
        patch.dict(
            os.environ,
            {
                "CASEFILE_MASTER_KEY": master_key,
                "CASEFILE_CHAT_GOAL_ROLLOUT": "off",
            },
        ),
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
            config=WorkerConfig(worker_id="phase4-v5-generation"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            revision = int(
                CaseFileService(session).get_draft(actor_id, project_id)["revision"]
            )
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
                content="请把全案逻辑漏洞查一遍，能修的给出补丁。",
                provider="openai",
                routing_hint={"entrypoint": "preset", "preset_id": "audit"},
            )
            first_task_id = int(first["task"]["task_run_id"])

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="phase4-v5-chat"),
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
            assert first_row.prompt_version == CHAT_CONTEXT_PROMPT_V6_VERSION
            assert first_row.toolset_version == CHAT_TOOLSET_V4_VERSION
            assert first_row.input_jsonb.get("context_policy_version") == ROLLOUT_V5

        assert len(provider.requests) == 1
        request = provider.requests[0]
        assert request.prompt_version == CHAT_CONTEXT_PROMPT_V6_VERSION
        assert request.toolset_version == CHAT_TOOLSET_V4_VERSION
        assert request.context_policy_version == ROLLOUT_V5
        execution_profile = request.route.execution_profile
        assert execution_profile["profile"] == "logic_audit.full_review"
        assert execution_profile["prompt_component"] == "audit"
        assert "simulate_patch_application" in execution_profile["toolset"]
        assert request.assembled_input is not None
        full_validation_issues = request.assembled_input["validation_issues"]
        assert isinstance(full_validation_issues, list)
        assert full_validation_issues == list(request.validation_issues)


def test_phase4_v6_rollout_binds_v17_v4_toolset_and_hardened_router(
    workflow_database: tuple[Engine, int, str],
) -> None:
    rollout = os.environ.get("CASEFILE_CHAT_CONTEXT_ROLLOUT")
    if rollout not in (None, ROLLOUT_V6):
        pytest.skip("CASEFILE_CHAT_CONTEXT_ROLLOUT pins an older context policy")

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
            config=WorkerConfig(worker_id="phase4-v6-generation"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        with factory() as session:
            revision = int(
                CaseFileService(session).get_draft(actor_id, project_id)["revision"]
            )
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
                content="请把全案逻辑漏洞查一遍，能修的给出补丁。",
                provider="openai",
                routing_hint={"entrypoint": "preset", "preset_id": "audit"},
            )
            first_task_id = int(first["task"]["task_run_id"])

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="phase4-v6-chat"),
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
            assert first_row.prompt_version == "casefile-chat-v17"
            assert first_row.toolset_version == CHAT_TOOLSET_V4_VERSION
            assert first_row.input_jsonb.get("context_policy_version") == ROLLOUT_V6

        assert len(provider.requests) == 1
        request = provider.requests[0]
        assert request.prompt_version == "casefile-chat-v17"
        assert request.toolset_version == CHAT_TOOLSET_V4_VERSION
        assert request.context_policy_version == ROLLOUT_V6
        execution_profile = request.route.execution_profile
        assert execution_profile["profile"] == "logic_audit.full_review"
        assert execution_profile["prompt_component"] == "audit"
        assert "simulate_patch_application" in execution_profile["toolset"]
        assert request.assembled_input is not None
        full_validation_issues = request.assembled_input["validation_issues"]
        assert isinstance(full_validation_issues, list)
        assert full_validation_issues == list(request.validation_issues)
