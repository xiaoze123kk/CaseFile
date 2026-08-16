"""Focused TaskRun cancellation state and HTTP contract regressions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from casefile.api.app import create_app
from casefile.api.dependencies import get_actor_user_id, get_session
from casefile.application.task_cancellation import (
    CANCELLED_CHAT_MESSAGE,
    finalize_task_cancellation,
)
from casefile.application.workflow_service import WorkflowService
from casefile.data_postgres.models import AgentMessage


class _MessageSession:
    def __init__(self, output_message: SimpleNamespace) -> None:
        self.output_message = output_message
        self.lookups: list[tuple[type[Any], int]] = []

    def get(self, model: type[Any], identity: int) -> SimpleNamespace:
        self.lookups.append((model, identity))
        return self.output_message


def test_finalize_task_cancellation_closes_attempt_and_pending_chat_message() -> None:
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    output_message = SimpleNamespace(status="pending", content_text=None)
    session = _MessageSession(output_message)
    task = SimpleNamespace(
        status="cancelling",
        stage="generating",
        task_type="casefile_chat",
        output_message_id=91,
        usage_jsonb={},
        cancel_requested_at=now - timedelta(seconds=1),
        completed_at=None,
        leased_by="worker-a",
        lease_expires_at=now + timedelta(seconds=30),
    )
    attempt = SimpleNamespace(
        status="running",
        validation_errors_jsonb=[],
        usage_jsonb={},
        finished_at=None,
    )
    usage = {"requests": 1, "total_tokens": 20}
    validation_errors = [{"code": "cancelled_after_validation"}]

    finalize_task_cancellation(  # type: ignore[arg-type]
        session,
        task,
        now=now,
        attempt=attempt,
        usage=usage,
        validation_errors=validation_errors,
    )

    assert task.status == "cancelled"
    assert task.stage == "cancelled"
    assert task.completed_at == now
    assert task.leased_by is None
    assert task.lease_expires_at is None
    assert task.usage_jsonb == usage
    assert attempt.status == "cancelled"
    assert attempt.finished_at == now
    assert attempt.usage_jsonb == usage
    assert attempt.validation_errors_jsonb == validation_errors
    assert session.lookups == [(AgentMessage, 91)]
    assert output_message.status == "failed"
    assert output_message.content_text == CANCELLED_CHAT_MESSAGE


def test_cancel_task_endpoint_returns_accepted_and_delegates() -> None:
    expected = {"task_run_id": 9, "project_id": 42, "status": "cancelled"}
    app = create_app(verify_database=False)
    app.dependency_overrides[get_actor_user_id] = lambda: 17
    app.dependency_overrides[get_session] = object

    with patch.object(WorkflowService, "cancel_task", return_value=expected) as cancel_task:
        with TestClient(app) as client:
            response = client.post("/api/v1/projects/42/tasks/9/cancel")

    assert response.status_code == 202
    assert response.json() == expected
    cancel_task.assert_called_once_with(17, 42, 9)
