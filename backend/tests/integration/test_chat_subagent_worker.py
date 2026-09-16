"""PostgreSQL Worker acceptance for the experimental chat subagent toolset."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from application_services_test_support import (
    RichFixtureProvider,
    _adopt_candidate,
    _prepare_task,
)
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from casefile.agent_runtime import FakeProvider
from casefile.agent_runtime.chat_tools import CHAT_TOOLSET_V8_VERSION, CHAT_TOOLSET_V9_VERSION
from casefile.agent_runtime.models import CaseFileChatRequest
from casefile.application.services import CaseFileService
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import TaskRun
from casefile.worker.runtime import Worker, WorkerConfig

pytestmark = pytest.mark.postgres


class _CapturingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.chat_requests: list[CaseFileChatRequest] = []

    def chat(self, request: CaseFileChatRequest):
        self.chat_requests.append(request)
        return super().chat(request)


@pytest.mark.parametrize(
    ("rollout", "toolset", "policy"),
    [
        ("experimental", CHAT_TOOLSET_V8_VERSION, "casefile-chat-subagents-v3"),
        ("v4", CHAT_TOOLSET_V9_VERSION, "casefile-chat-subagents-v4"),
    ],
)
def test_experimental_subagent_toolset_survives_full_worker_path(
    workflow_database: tuple[Engine, int, str],
    rollout: str,
    toolset: str,
    policy: str,
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    provider = _CapturingProvider()

    with patch.dict(
        "os.environ",
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_CHAT_GOAL_ROLLOUT": "active",
            "CASEFILE_CHAT_SUBAGENT_ROLLOUT": rollout,
        },
    ):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        generation_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="subagent-generation"),
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
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                int(thread["thread_id"]),
                expected_draft_id=draft_id,
                expected_draft_revision=revision,
                content="核对当前结论的证据链，并明确仍未知的部分。",
                provider="openai",
                routing_hint=None,
            )
            task_id = int(queued["task"]["task_run_id"])
            task = session.get(TaskRun, task_id)
            assert task is not None
            assert task.prompt_version == "casefile-chat-v27"
            assert task.toolset_version == toolset
            runtime = task.input_jsonb.get("subagent_runtime")
            assert isinstance(runtime, dict)
            assert runtime["policy_version"] == policy
            assert runtime["max_tasks"] == 2
            if rollout == "v4":
                assert len(runtime["skill_sha256"]) == 64
                assert runtime["max_gather_turns"] + runtime["max_finalize_turns"] == 6
                assert runtime["max_tool_calls_per_task"] == 10

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id="subagent-chat"),
            provider_factory=lambda _task: provider,
        )
        assert chat_worker.run_once() is True

        with factory() as session:
            completed = session.get(TaskRun, task_id)
            assert completed is not None
            assert completed.status == "succeeded", (
                completed.status,
                completed.error_code,
                completed.error_details_jsonb,
            )
            assert completed.toolset_version == toolset
            assert completed.result_jsonb is not None

    assert len(provider.chat_requests) == 1
    request = provider.chat_requests[0]
    assert request.toolset_version == toolset
    assert request.input_hash
