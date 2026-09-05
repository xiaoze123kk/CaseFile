"""Real PostgreSQL recovery and long-call lease regression tests."""

from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from application_services_test_support import _prepare_task
from casefile.agent_runtime import FakeProvider
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import TaskAttempt, TaskEvent, TaskRun
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres


def test_same_worker_id_recovery_fences_old_attempt(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, _key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    _project_id, run_id = _prepare_task(engine, actor_id)
    first = Worker(factory, config=WorkerConfig(worker_id="reused-id"))
    claimed = first._claim_next()
    assert isinstance(claimed, tuple)
    _, old_id = claimed
    snapshot = first._load_task_snapshot(run_id)
    with factory() as session, session.begin():
        task = session.get(TaskRun, run_id)
        assert task is not None
        task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(RuntimeError, match="lease was lost"):
        first._emit(snapshot, "agent.step.started", "planning", {})
    assert not first._finalizer._renew_lease(run_id, old_id)

    second = Worker(
        factory, config=WorkerConfig(worker_id="reused-id"),
        provider_factory=lambda _task: FakeProvider(),
    )
    reclaimed = second._claim_next()
    assert isinstance(reclaimed, tuple)
    assert reclaimed[0] == run_id and reclaimed[1] != old_id
    with factory() as session, session.begin():
        with pytest.raises(RuntimeError, match="lease was lost"):
            first._completion._locked_completion_rows(
                session, run_id, old_id, expected_task_type="brief_to_draft"
            )
    with pytest.raises(RuntimeError, match="lease was lost"):
        first._emit(snapshot, "agent.step.started", "planning", {})
    first._fail(
        run_id, old_id, RuntimeError("late failure"), candidate=None,
        usage={}, validation_errors=[], sensitive_values=(),
    )
    assert not first._cancel(run_id, old_id, usage={}, validation_errors=[])
    second._execute(*reclaimed)
    with pytest.raises(RuntimeError, match="completion is no longer owned"):
        first._emit_after_completion(snapshot, "context.compaction_failed", "context", {})
    with factory() as session:
        task = session.get(TaskRun, run_id)
        assert task is not None and task.status == "succeeded", (
            None if task is None else task.error_details_jsonb
        )
        attempts = list(session.scalars(
            select(TaskAttempt).where(TaskAttempt.task_run_id == run_id)
            .order_by(TaskAttempt.attempt_no)
        ))
        assert [attempt.status for attempt in attempts] == ["failed", "succeeded"]
        assert attempts[0].error_code == "worker_lease_expired"
        events = list(session.scalars(
            select(TaskEvent.event_type).where(TaskEvent.task_run_id == run_id)
        ))
        assert events.count("task.succeeded") == 1
        assert "context.compaction_failed" not in events


def test_heartbeat_keeps_long_call_owned_without_consuming_cancellation(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, _key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    project_id, run_id = _prepare_task(engine, actor_id)
    worker = Worker(factory, config=WorkerConfig(worker_id="long-call", lease_seconds=2))
    claimed = worker._claim_next()
    assert isinstance(claimed, tuple)
    _, attempt_id = claimed
    before = worker._load_task_snapshot(run_id)
    assert before.lease_expires_at is not None
    with worker._finalizer.heartbeat(run_id, attempt_id):
        # Simulate a silent Provider call exceeding the original lease.
        Event().wait(2.5)
        with factory() as session:
            task = session.get(TaskRun, run_id)
            assert task is not None and task.lease_expires_at is not None
            assert task.lease_expires_at > datetime.now(UTC) > before.lease_expires_at
            assert task.stage == before.stage
        other = Worker(factory, config=WorkerConfig(worker_id="other"))
        assert other._claim_next() is None
        with factory() as session:
            WorkflowService(session).cancel_task(actor_id, project_id, run_id)
        assert worker._finalizer._renew_lease(run_id, attempt_id)
        with factory() as session:
            task = session.get(TaskRun, run_id)
            attempt = session.get(TaskAttempt, attempt_id)
            assert task is not None and task.status == "cancelling"
            assert attempt is not None and attempt.status == "running"
    assert worker._cancel(run_id, attempt_id, usage={}, validation_errors=[])
