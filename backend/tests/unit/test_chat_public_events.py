from __future__ import annotations

import json

from casefile.api.workflow import _public_sse
from casefile.application.chat_public_events import public_agent_event_view


def _run(*, status: str = "running", failure: dict | None = None) -> dict:
    return {
        "task_run_id": 21,
        "status": status,
        "stage": "context",
        "failure": failure,
        "provider": "deepseek-canary",
        "prompt_version": "internal-prompt-canary",
        "usage": {"total_tokens": 999},
        "result": {"internal": "result-canary"},
        "component_steps": [{"component_id": "component-canary"}],
    }


def _event(sequence: int, event_type: str, payload: dict | None = None) -> dict:
    return {
        "event_id": sequence,
        "task_run_id": 21,
        "sequence_no": sequence,
        "event_type": event_type,
        "stage": "internal-stage-canary",
        "payload": payload or {},
        "payload_jsonb": {"secret": "nested-canary"},
        "occurred_at": "2026-08-26T10:00:00+00:00",
    }


def test_event_projector_preserves_source_sequence_and_drops_unknown_events() -> None:
    internal = [
        _event(1, "task.queued", {"model_id": "internal-model-canary"}),
        _event(2, "provider.internal.telemetry", {"secret": "nested-canary"}),
        _event(5, "context.compacted", {"token_count": 12000}),
    ]

    projected = [public_agent_event_view(event, _run()) for event in internal]
    public = [event.model_dump(mode="json") for event in projected if event is not None]

    assert [event["sequence"] for event in public] == [1, 5]
    assert [event["event"] for event in public] == ["run.accepted", "run.context"]
    assert public[-1]["context_state"] == "compacted"
    serialized = json.dumps(public)
    for forbidden in (
        "provider",
        "prompt_version",
        "usage",
        "result",
        "component_steps",
        "payload",
        "payload_jsonb",
        "token_count",
        "nested-canary",
        "internal-model-canary",
    ):
        assert forbidden not in serialized


def test_event_projector_maps_failure_and_cancellation_to_fixed_public_shapes() -> None:
    failed = public_agent_event_view(
        _event(8, "task.failed", {"error_code": "internal-code"}),
        _run(
            status="failed",
            failure={
                "code": "provider_timeout",
                "message": "模型服务暂时不可用。",
                "retryable": True,
                "issues": [{"component_id": "internal-canary"}],
            },
        ),
    )
    cancelled = public_agent_event_view(
        _event(9, "task.cancelled", {"reason_code": "internal-code"}),
        _run(status="cancelled"),
    )

    assert failed is not None
    assert failed.model_dump(mode="json") == {
        "sequence": 8,
        "event": "run.failed",
        "failure": {
            "category": "temporarily_unavailable",
            "message": "模型服务暂时不可用。",
            "retryable": True,
        },
    }
    assert cancelled is not None
    assert cancelled.model_dump(mode="json") == {
        "sequence": 9,
        "event": "run.cancelled",
        "message": "任务已安全停止。",
    }


def test_public_sse_uses_original_sequence_as_resume_cursor() -> None:
    event = {
        "sequence": 5,
        "event": "run.context",
        "context_state": "near_limit",
    }

    encoded = _public_sse(event)

    assert encoded.startswith("id: 5\nevent: run.context\ndata: ")
    data_line = next(line for line in encoded.splitlines() if line.startswith("data: "))
    assert json.loads(data_line.removeprefix("data: ")) == event
