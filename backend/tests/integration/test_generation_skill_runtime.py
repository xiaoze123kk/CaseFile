"""v17 Worker recovery, private execution metadata, and candidate boundaries."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from application_services_test_support import _draft_revision_and_content, _prepare_task
from fastapi.testclient import TestClient
from openai import APIConnectionError
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker
from test_brief_to_draft_v8_live_acceptance import _successful_task_violations

import casefile.agent_runtime.provider_adapters.fake as fake
from casefile.agent_runtime.providers import FakeProvider
from casefile.api.app import create_app
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import AgentStepRun, TaskEvent, TaskRun
from casefile.worker.runtime import Worker, WorkerConfig

pytestmark = pytest.mark.postgres


def test_v17_worker_recovery_keeps_private_skill_trace_and_draft(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    original = fake._fake_v8_output
    fail = True

    def output(output_type: Any) -> Any:
        if fail and output_type.__name__ == "ResolutionGovernanceIRV2":
            raise APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
        return original(output_type)

    with (
        patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}),
        patch(
            "casefile.application.workflow.tasks.prompt_version_for_task",
            return_value="brief-to-draft-v17",
        ),
        patch.object(fake, "_fake_v8_output", side_effect=output),
    ):
        project_id, task_id = _prepare_task(engine, actor_id)
        before = _draft_revision_and_content(engine, actor_id, project_id)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="skill-recovery"),
            provider_factory=lambda _: FakeProvider(),
        )
        assert worker.run_once(task_run_id=task_id)
        with factory() as session:
            task = session.get(TaskRun, task_id)
            assert task is not None and task.status == "failed"
        fail = False
        with factory() as session:
            WorkflowService(session).resume_generation_task(
                actor_id,
                project_id,
                task_id,
                expected_draft_id=task.draft_id,
                expected_draft_revision=task.input_draft_revision,
                expected_brief_revision=task.input_brief_revision,
            )
        assert worker.run_once(task_run_id=task_id)
        with factory() as session:
            result = WorkflowService(session).get_task(actor_id, project_id, task_id)
            steps = list(
                session.scalars(select(AgentStepRun).where(AgentStepRun.task_run_id == task_id))
            )
            events = list(
                session.scalars(select(TaskEvent).where(TaskEvent.task_run_id == task_id))
            )
        assert result["status"] == "succeeded"
        assert result["result_snapshot_id"] is None
        assert _draft_revision_and_content(engine, actor_id, project_id) == before
        assert any(
            step.status == "reused" and step.component_id == "case_blueprint_planner"
            for step in steps
        )
        model_steps = [step for step in steps if step.component_id == "case_blueprint_planner"]
        assert all(
            step.diagnostic_jsonb["execution"]["skill"]["release"] == "v17" for step in model_steps
        )
        assert any(step.component_id.startswith("hook_") for step in steps)
        failed_governance = [
            step
            for step in steps
            if step.component_id == "resolution_governance" and step.status == "failed"
        ]
        assert failed_governance
        assert all(step.diagnostic_jsonb["execution"]["resources"] for step in failed_governance)
        assert all(event.event_type != "agent.hooks.executed" for event in events)
        assert "_execution" not in str(result)
        assert not any(
            step["component_id"].startswith("hook_") for step in result["component_steps"]
        )
        with TestClient(create_app(engine.url.render_as_string(hide_password=False))) as client:
            assert (
                _successful_task_violations(
                    client,
                    factory,
                    headers={"X-CaseFile-User-Id": str(actor_id)},
                    project_id=project_id,
                    task=result,
                )
                == []
            )
