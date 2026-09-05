"""Regression protection for ordinary Worker attempt ownership."""

from copy import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from casefile.worker.executors.completion import CompletionExecutor
from casefile.worker.executors.scene_compiler import _lock_active as lock_scene
from casefile.worker.executors.story_planner import _lock_active as lock_story
from casefile.worker.finalization import TaskFinalizer


def rows() -> tuple[SimpleNamespace, SimpleNamespace]:
    task = SimpleNamespace(
        id=7, task_type="brief_polish", status="running", leased_by="worker",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=600),
        attempt_count=2, input_jsonb={},
    )
    attempt = SimpleNamespace(id=12, task_run_id=7, status="running", attempt_no=2)
    return task, attempt


@pytest.mark.parametrize("invalid", ["expired", "missing_lease", "failed", "old", "foreign"])
def test_completion_rejects_lost_attempt(invalid: str) -> None:
    task, attempt = rows()
    if invalid == "expired":
        task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif invalid == "missing_lease":
        task.lease_expires_at = None
    elif invalid == "failed":
        attempt.status = "failed"
    elif invalid == "old":
        attempt.attempt_no = 1
    else:
        attempt.task_run_id = 99
    session = MagicMock()
    session.scalar.side_effect = [task, attempt]
    executor = CompletionExecutor(MagicMock(), worker_id="worker", emit=MagicMock())
    with pytest.raises(RuntimeError):
        executor._locked_completion_rows(session, 7, 12, expected_task_type="brief_polish")


def test_completion_accepts_current_attempt() -> None:
    task, attempt = rows()
    session = MagicMock()
    session.scalar.side_effect = [task, attempt]
    executor = CompletionExecutor(MagicMock(), worker_id="worker", emit=MagicMock())
    assert executor._locked_completion_rows(
        session, 7, 12, expected_task_type="brief_polish"
    ) == (task, attempt)


@pytest.mark.parametrize("terminal", ["fail", "cancel"])
def test_expired_attempt_cannot_finalize(terminal: str) -> None:
    task, attempt = rows()
    task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    if terminal == "cancel":
        task.status = "cancelling"
    factory = MagicMock()
    session = factory.return_value.__enter__.return_value
    session.scalar.side_effect = [task, attempt]
    finalizer = TaskFinalizer(factory, worker_id="worker", lease_seconds=600)
    if terminal == "cancel":
        assert not finalizer._cancel(7, 12, usage={}, validation_errors=[])
    else:
        finalizer._fail(
            7, 12, RuntimeError("late result"), candidate=None,
            usage={}, validation_errors=[], sensitive_values=(),
        )
    assert attempt.status == "running"
    assert task.status == ("cancelling" if terminal == "cancel" else "running")
    session.add.assert_not_called()


@pytest.mark.parametrize("lost", ["expired", "reclaimed"])
def test_late_event_cannot_renew_or_write_into_current_attempt(lost: str) -> None:
    task, attempt = rows()
    snapshot = copy(task)
    if lost == "expired":
        task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    else:
        task.attempt_count = 3
        attempt.status = "failed"
    previous_expiry = task.lease_expires_at
    factory = MagicMock()
    session = factory.return_value.__enter__.return_value
    session.scalar.side_effect = [task, attempt]
    finalizer = TaskFinalizer(factory, worker_id="worker", lease_seconds=600)
    with patch("casefile.worker.finalization.append_task_event") as append:
        with pytest.raises(RuntimeError, match="lease was lost"):
            finalizer._emit(snapshot, "agent.step.started", "planning", {})
        append.assert_not_called()
    assert task.lease_expires_at == previous_expiry


def test_expired_attempt_cannot_be_resurrected_by_heartbeat() -> None:
    task, attempt = rows()
    task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    expiry = task.lease_expires_at
    factory = MagicMock()
    session = factory.return_value.__enter__.return_value
    session.scalar.return_value = task
    session.get.return_value = attempt
    finalizer = TaskFinalizer(factory, worker_id="worker", lease_seconds=600)
    assert not finalizer._renew_lease(7, 12)
    assert task.lease_expires_at == expiry


@pytest.mark.parametrize("component", ["story", "scene"])
@pytest.mark.parametrize("expired", [False, True])
def test_non_shadow_compiler_checks_attempt_expiry(component: str, expired: bool) -> None:
    task, attempt = rows()
    if expired:
        task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session = MagicMock()
    session.scalar.side_effect = [task, attempt]
    lock = lock_story if component == "story" else lock_scene
    if expired:
        with pytest.raises(RuntimeError, match="compiler_worker_lease_lost"):
            lock(session, "worker", 7, 12)
    else:
        assert lock(session, "worker", 7, 12) == (task, attempt)
