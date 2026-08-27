"""PostgreSQL proof that the Backend Release executor injects real faults."""

from __future__ import annotations

import os
from typing import Any, cast
from unittest.mock import patch

import pytest
from application_services_test_support import (
    RichFixtureProvider,
    _adopt_candidate,
    _prepare_task,
)
from casefile.application.services import CaseFileService
from casefile.application.workflow_service import WorkflowService
from casefile.benchmark.closure_repair_backend_executor import (
    PostgresBackendReleaseExecutor,
    _TrialProvider,
)
from casefile.benchmark.closure_repair_backend_release import FAULT_MATRIX
from casefile.data_postgres.models import AgentPatchOperation, AgentPatchSet
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres


def test_backend_executor_fault_matrix_uses_real_postgres_seams(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    database_url = engine.url.render_as_string(hide_password=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        assert Worker(
            factory,
            config=WorkerConfig(worker_id="backend-fault-generation"),
            provider_factory=lambda _task: RichFixtureProvider(),  # type: ignore[arg-type, return-value]
        ).run_once()
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = cast(int, cast(dict[str, Any], adopted)["draft_id"])
        with factory() as session:
            workflow = WorkflowService(session)
            workflow.save_provider_setting(
                actor_id,
                provider="deepseek",
                api_key="backend-fault-dummy-key",
                model_id="deepseek-v4-pro",
                model_is_custom=False,
            )
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                title="Backend fault matrix",
            )
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                int(thread["thread_id"]),
                expected_draft_id=draft_id,
                expected_draft_revision=2,
                content="Modify the first object description.",
                provider="deepseek",
            )
            task_run_id = int(queued["task"]["task_run_id"])
        with factory() as session:
            document = CaseFileService(session).get_draft(actor_id, project_id)["content"]
        primary = {
            "operation_type": "update_field",
            "object_id": document["entities"][0]["id"],
            "field_path": "/name",
            "new_value": "Backend fault matrix target",
        }

        assert Worker(
            factory,
            config=WorkerConfig(
                worker_id="backend-fault-chat",
                closure_repair_mode="off",
            ),
            provider_factory=lambda _task: _TrialProvider(document, primary),
        ).run_once()
        with factory() as session:
            patch_set = session.scalar(
                select(AgentPatchSet).where(AgentPatchSet.task_run_id == task_run_id)
            )
            assert patch_set is not None
            operation_ids = list(
                session.scalars(
                    select(AgentPatchOperation.id)
                    .where(AgentPatchOperation.patch_set_id == patch_set.id)
                    .order_by(AgentPatchOperation.ordinal)
                )
            )
        with factory() as session:
            WorkflowService(session).apply_agent_patch_set(
                actor_id,
                project_id,
                patch_set.id,
                expected_draft_id=draft_id,
                expected_revision=2,
                operation_ids=operation_ids,
            )
        with factory() as session:
            WorkflowService(session).undo_agent_patch_set(
                actor_id,
                project_id,
                patch_set.id,
                expected_draft_id=draft_id,
                expected_revision=3,
            )
        with factory() as session:
            WorkflowService(session).redo_agent_patch_set(
                actor_id,
                project_id,
                patch_set.id,
                expected_draft_id=draft_id,
                expected_revision=4,
            )

        executor = PostgresBackendReleaseExecutor(
            database_url=database_url,
            repair_api_key="backend-fault-dummy-key",
        )
        try:
            trial_context = {
                "actor_id": actor_id,
                "project_id": project_id,
                "task_run_id": task_run_id,
                "patch_set_id": int(patch_set.id),
                "draft_id": draft_id,
            }
            executor._last_trial = trial_context
            executor._last_agent_trial = {
                **trial_context,
                "operation_ids": operation_ids,
                "current_revision": 5,
            }
            results = {fault_id: executor.execute_fault(fault_id) for fault_id in FAULT_MATRIX}
        finally:
            executor.close()

    assert set(results) == set(FAULT_MATRIX)
    failed = {
        fault_id: result
        for fault_id, result in results.items()
        if result["passed"] is not True
    }
    assert failed == {}
    assert all(result["production_database"] is True for result in results.values())
