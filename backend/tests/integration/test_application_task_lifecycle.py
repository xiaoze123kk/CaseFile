"""PostgreSQL integration tests for TaskRun cancellation, recovery, and provenance."""

from __future__ import annotations

import os
from unittest.mock import patch

import casefile.agent_runtime.provider_adapters.fake as agent_providers
import pytest
from application_services_test_support import (
    _draft_revision_and_content,
    _prepare_task,
)
from casefile.agent_runtime import FakeProvider
from casefile.agent_runtime.models import GenerationRequest, GenerationResult
from casefile.application.a_path_metrics import APathMetricsService
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentStepRun,
    BriefVersion,
    TaskAttempt,
    TaskEvent,
    TaskRun,
)
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres


def _persist_usage_probe(
    worker: Worker,
    task_run_id: int,
    usage: dict[str, int],
) -> None:
    component_id = "usage_probe"
    schema_id = "usage-probe-v1"
    worker._emit(
        task_run_id,
        "agent.step.started",
        "planning",
        {
            "component_id": component_id,
            "schema_id": schema_id,
            "input_hash": "a" * 64,
            "upstream_hashes": {},
        },
    )
    worker._emit(
        task_run_id,
        "agent.model_call.started",
        "planning",
        {
            "component_id": component_id,
            "schema_id": schema_id,
            "attempt_no": 1,
            "protocol": "native_json_schema",
            "prompt_sha256": "b" * 64,
        },
    )
    worker._emit(
        task_run_id,
        "agent.model_call.completed",
        "planning",
        {
            "component_id": component_id,
            "schema_id": schema_id,
            "attempt_no": 1,
            "protocol": "native_json_schema",
            "output_hash": "c" * 64,
            "output_size_bytes": 128,
            "usage": usage,
        },
    )
    worker._emit(
        task_run_id,
        "agent.step.completed",
        "planning",
        {
            "component_id": component_id,
            "schema_id": schema_id,
            "output_hash": "c" * 64,
            "usage": usage,
        },
    )


def test_generation_task_uses_the_registry_version_without_a_deployment_override(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    with patch.dict(
        os.environ,
        {
            "CASEFILE_MASTER_KEY": master_key,
            "CASEFILE_BRIEF_TO_DRAFT_PROMPT_VERSION": "brief-to-draft-v7",
        },
    ):
        _project_id, task_run_id = _prepare_task(engine, actor_id)

    with factory() as session:
        task = session.get(TaskRun, task_run_id)
        assert task is not None
        assert task.prompt_version == "brief-to-draft-v15"
        assert task.agent_version == "brief-to-draft-pipeline-v15"


def test_worker_can_claim_a_known_task_without_consuming_an_older_queue_item(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        _first_project, first_task_id = _prepare_task(engine, actor_id)
        _second_project, second_task_id = _prepare_task(engine, actor_id)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="specific-claim-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert worker.run_once(task_run_id=second_task_id) is True

    with factory() as session:
        first = session.get(TaskRun, first_task_id)
        second = session.get(TaskRun, second_task_id)
    assert first is not None and first.status == "queued"
    assert first.attempt_count == 0
    assert second is not None and second.status == "succeeded"
    assert second.attempt_count == 1


def test_queued_task_cancels_immediately_and_is_never_claimed(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        draft_revision_before, draft_content_before = _draft_revision_and_content(
            engine,
            actor_id,
            project_id,
        )
        with factory() as session:
            workflow = WorkflowService(session)
            cancelled = workflow.cancel_task(
                actor_id,
                project_id,
                task_run_id,
            )
            repeated = workflow.cancel_task(actor_id, project_id, task_run_id)

        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="queued-cancel-worker"),
            provider_factory=lambda _task: FakeProvider(),
        )
        assert cancelled["status"] == "cancelled"
        assert cancelled["stage"] == "cancelled"
        assert repeated["status"] == "cancelled"
        assert worker.run_once() is False

        with factory() as session:
            workflow = WorkflowService(session)
            task = workflow.get_task(actor_id, project_id, task_run_id)
            events = workflow.list_task_events(actor_id, project_id, task_run_id)
            attempts = list(
                session.scalars(select(TaskAttempt).where(TaskAttempt.task_run_id == task_run_id))
            )

        assert task["status"] == "cancelled"
        assert attempts == []
        assert events[-1]["event_type"] == "task.cancelled"
        assert sum(event["event_type"] == "task.cancelled" for event in events) == 1
        draft_revision_after, draft_content_after = _draft_revision_and_content(
            engine,
            actor_id,
            project_id,
        )
        assert draft_revision_after == draft_revision_before
        assert draft_content_after == draft_content_before


def test_running_task_cooperatively_finishes_its_attempt_as_cancelled(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        draft_revision_before, draft_content_before = _draft_revision_and_content(
            engine,
            actor_id,
            project_id,
        )

        class ProjectAwareCancelProvider(FakeProvider):
            def generate(self, request: GenerationRequest) -> GenerationResult:
                with factory() as cancellation_session:
                    workflow = WorkflowService(cancellation_session)
                    requested = workflow.cancel_task(
                        actor_id,
                        project_id,
                        request.task_run_id,
                    )
                    repeated = workflow.cancel_task(actor_id, project_id, request.task_run_id)
                assert requested["status"] == "cancelling"
                assert repeated["status"] == "cancelling"
                request.emit(
                    "agent.step.started",
                    "generating",
                    {"component_id": "cancellation_probe"},
                )
                raise AssertionError("cancelled execution must stop before provider completion")

        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="running-cancel-worker"),
            provider_factory=lambda _task: ProjectAwareCancelProvider(),
        )
        assert worker.run_once() is True

        with factory() as session:
            workflow = WorkflowService(session)
            task = workflow.get_task(actor_id, project_id, task_run_id)
            terminal = workflow.cancel_task(actor_id, project_id, task_run_id)
            events = workflow.list_task_events(actor_id, project_id, task_run_id)
            attempt = session.scalar(
                select(TaskAttempt).where(TaskAttempt.task_run_id == task_run_id)
            )

        assert task["status"] == "cancelled"
        assert terminal["status"] == "cancelled"
        assert task["result"] is None
        assert attempt is not None
        assert attempt.status == "cancelled"
        assert [event["event_type"] for event in events][-2:] == [
            "task.cancel_requested",
            "task.cancelled",
        ]
        assert sum(event["event_type"] == "task.cancel_requested" for event in events) == 1
        assert sum(event["event_type"] == "task.cancelled" for event in events) == 1
        draft_revision_after, draft_content_after = _draft_revision_and_content(
            engine,
            actor_id,
            project_id,
        )
        assert draft_revision_after == draft_revision_before
        assert draft_content_after == draft_content_before


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
def test_a_path_metrics_keep_partial_component_usage_after_terminal_attempt(
    workflow_database: tuple[Engine, int, str],
    terminal_status: str,
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    usage = {
        "requests": 2,
        "input_tokens": 30,
        "output_tokens": 10,
        "total_tokens": 40,
        "cached_tokens": 4,
        "reasoning_tokens": 3,
    }

    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        worker = Worker(factory, config=WorkerConfig(worker_id=f"usage-{terminal_status}"))
        claimed = worker._claim_next()
        assert isinstance(claimed, tuple)
        claimed_task_run_id, attempt_id = claimed
        assert claimed_task_run_id == task_run_id
        _persist_usage_probe(worker, task_run_id, usage)

        if terminal_status == "failed":
            worker._fail(
                task_run_id,
                attempt_id,
                RuntimeError("expected usage probe failure"),
                candidate=None,
                usage={},
                validation_errors=[],
                sensitive_values=(),
            )
        else:
            with factory() as session:
                requested = WorkflowService(session).cancel_task(
                    actor_id,
                    project_id,
                    task_run_id,
                )
            assert requested["status"] == "cancelling"
            assert worker._cancel(
                task_run_id,
                attempt_id,
                usage={},
                validation_errors=[],
            )

        with factory() as session:
            task = session.get(TaskRun, task_run_id)
            attempt = session.get(TaskAttempt, attempt_id)
            model_calls = list(
                session.scalars(
                    select(AgentModelCall).where(AgentModelCall.task_run_id == task_run_id)
                )
            )
            steps = list(
                session.scalars(select(AgentStepRun).where(AgentStepRun.task_run_id == task_run_id))
            )
        assert task is not None
        assert attempt is not None
        assert task.status == terminal_status
        assert attempt.status == terminal_status
        assert task.usage_jsonb == usage
        assert attempt.usage_jsonb == usage
        assert len(model_calls) == 1
        assert model_calls[0].status == "succeeded"
        assert model_calls[0].usage_jsonb == usage
        assert any(
            step.component_id == "usage_probe" and step.status == "succeeded" for step in steps
        )

        with factory() as session:
            metrics = APathMetricsService(session).project_metrics(actor_id, project_id)
        assert metrics["usage_totals"] == usage
        assert metrics["usage_observations"] == {
            "task_attempts": 1,
            "model_calls": 1,
            "model_call_attempts": 1,
            "model_call_usage_snapshots": 1,
            "task_attempt_fallbacks": 0,
            "task_run_fallbacks": 0,
        }


def test_planner_semantic_failure_persists_gate_and_resumes_without_reuse(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    fail_planner = True
    original_matrix_plan = agent_providers._add_fake_v10_matrix_plan

    def matrix_plan_with_invalid_competition(
        output_type: type[object],
        payload: dict[str, object],
    ) -> None:
        original_matrix_plan(output_type, payload)
        if not fail_planner or output_type.__name__ != "CaseBlueprintV1":
            return
        reasoning_paths = payload["reasoning_paths"]
        assert isinstance(reasoning_paths, list)
        shared_path = reasoning_paths[0]
        assert isinstance(shared_path, dict)
        shared_path["dependency_keys"] = [
            "record",
            "claim",
            "hypothesis",
            "alternative_hypothesis",
        ]
        payload["reasoning_paths"] = [shared_path]

    with (
        patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}),
        patch(
            "casefile.application.workflow.content.prompt_version_for_task",
            return_value="brief-to-draft-v11",
        ),
        patch.object(
            agent_providers,
            "_add_fake_v10_matrix_plan",
            side_effect=matrix_plan_with_invalid_competition,
        ),
    ):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="planner-semantic-gate"),
            provider_factory=lambda _task: FakeProvider(),
        )

        assert worker.run_once() is True

        with factory() as session:
            failed = WorkflowService(session).get_task(actor_id, project_id, task_run_id)
            task_row = session.get(TaskRun, task_run_id)
            attempt_one = session.scalar(
                select(TaskAttempt).where(
                    TaskAttempt.task_run_id == task_run_id,
                    TaskAttempt.attempt_no == 1,
                )
            )
            assert task_row is not None
            assert attempt_one is not None
            expected_quality_steps = int(task_row.budget_jsonb["structural_repair_attempts"]) + 1
            attempt_one_steps = list(
                session.scalars(
                    select(AgentStepRun).where(AgentStepRun.task_attempt_id == attempt_one.id)
                )
            )

        assert failed["status"] == "failed"
        assert failed["failure"]["retryable"] is True
        assert {issue["code"] for issue in failed["failure"]["issues"]} == {
            "competing_hypothesis_path_plan_missing"
        }
        quality_steps = [
            step for step in attempt_one_steps if step.component_id == "quality_repair_gate"
        ]
        assert len(quality_steps) == expected_quality_steps
        assert all(step.status == "failed" for step in quality_steps)
        assert not any(step.component_id == "run_coordinator" for step in attempt_one_steps)

        fail_planner = False
        with factory() as session:
            WorkflowService(session).resume_generation_task(
                actor_id,
                project_id,
                task_run_id,
                expected_draft_id=task_row.draft_id,
                expected_draft_revision=task_row.input_draft_revision,
                expected_brief_revision=task_row.input_brief_revision,
            )

        assert worker.run_once() is True

        with factory() as session:
            recovered = WorkflowService(session).get_task(actor_id, project_id, task_run_id)
        assert recovered["status"] == "succeeded"
        attempt_two_planner = [
            step
            for step in recovered["component_steps"]
            if step["attempt_no"] == 2 and step["component_id"] == "case_blueprint_planner"
        ]
        assert len(attempt_two_planner) == 1
        assert attempt_two_planner[0]["status"] == "succeeded"


def test_expired_lease_creates_a_new_attempt(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        _, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        first = Worker(factory, config=WorkerConfig(worker_id="worker-a", lease_seconds=1))
        assert first._claim_next() is not None
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE task_runs SET lease_expires_at = "
                    "CURRENT_TIMESTAMP - INTERVAL '1 second'"
                )
            )
        second = Worker(factory, config=WorkerConfig(worker_id="worker-b", lease_seconds=60))
        claimed = second._claim_next()
        assert claimed is not None
        with factory() as session:
            attempts = list(
                session.scalars(
                    select(TaskAttempt)
                    .where(TaskAttempt.task_run_id == task_run_id)
                    .order_by(TaskAttempt.attempt_no)
                )
            )
        assert [attempt.status for attempt in attempts] == ["failed", "running"]
    assert attempts[0].error_code == "worker_lease_expired"


def test_expired_cancelling_lease_is_reaped_without_starting_a_new_attempt(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        draft_revision_before, draft_content_before = _draft_revision_and_content(
            engine,
            actor_id,
            project_id,
        )
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        first = Worker(factory, config=WorkerConfig(worker_id="cancel-worker-a", lease_seconds=1))
        claimed = first._claim_next()
        assert isinstance(claimed, tuple)

        with factory() as session:
            requested = WorkflowService(session).cancel_task(
                actor_id,
                project_id,
                task_run_id,
            )
        assert requested["status"] == "cancelling"
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE task_runs SET lease_expires_at = "
                    "CURRENT_TIMESTAMP - INTERVAL '1 second' WHERE id = :task_run_id"
                ),
                {"task_run_id": task_run_id},
            )

        reaper = Worker(factory, config=WorkerConfig(worker_id="cancel-worker-b"))
        assert reaper.run_once() is True
        assert reaper.run_once() is False
        with factory() as session:
            workflow = WorkflowService(session)
            task = workflow.get_task(actor_id, project_id, task_run_id)
            events = workflow.list_task_events(actor_id, project_id, task_run_id)
            attempts = list(
                session.scalars(select(TaskAttempt).where(TaskAttempt.task_run_id == task_run_id))
            )

        assert task["status"] == "cancelled"
        assert task["attempt_count"] == 1
        assert [attempt.status for attempt in attempts] == ["cancelled"]
        assert [event["event_type"] for event in events][-2:] == [
            "task.cancel_requested",
            "task.cancelled",
        ]
        draft_revision_after, draft_content_after = _draft_revision_and_content(
            engine,
            actor_id,
            project_id,
        )
        assert draft_revision_after == draft_revision_before
        assert draft_content_after == draft_content_before


def test_worker_rejects_rotated_provider_configuration(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, task_run_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        with factory() as session:
            rotated = WorkflowService(session).save_provider_setting(
                actor_id,
                api_key="sk-test-rotated-secret",
                model_id="gpt-5.6-sol",
                model_is_custom=False,
            )
        assert rotated["config_version"] == 2

        provider_called = False

        def provider_factory(_task: TaskRun) -> FakeProvider:
            nonlocal provider_called
            provider_called = True
            return FakeProvider()

        worker = Worker(
            factory,
            config=WorkerConfig(worker_id="rotated-provider-test"),
            provider_factory=provider_factory,
        )
        assert worker.run_once() is True
        assert provider_called is False
        with factory() as session:
            task = WorkflowService(session).get_task(actor_id, project_id, task_run_id)
        assert task["status"] == "failed"
        assert task["error_code"] == "agent_component_failed"
        assert task["failure"]["issues"][0]["code"] == "generation_failed"
        assert len(task["component_steps"]) == 1
        coordinator = task["component_steps"][0]
        assert coordinator["component_id"] == "run_coordinator"
        assert coordinator["status"] == "failed"
        assert coordinator["failure_layer"] == "frozen_context"
        assert coordinator["schema_id"] == "task-run-v1"
        assert coordinator["recoverable"] is False
        assert coordinator["issues"][0]["component_id"] == "run_coordinator"
        assert coordinator["issues"][0]["code"] == "generation_failed"


def test_confirmed_brief_and_task_events_are_database_immutable(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, master_key = workflow_database
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        _, task_run_id = _prepare_task(engine, actor_id)
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(text("UPDATE brief_versions SET version_no = 9"))
        with pytest.raises(DBAPIError), engine.begin() as connection:
            connection.execute(
                text("UPDATE task_events SET stage = 'changed' WHERE task_run_id = :task_id"),
                {"task_id": task_run_id},
            )
        with engine.connect() as connection:
            assert connection.execute(select(BriefVersion.version_no)).scalar_one() == 1
            assert connection.execute(select(TaskEvent.stage)).scalar_one() == "queued"
