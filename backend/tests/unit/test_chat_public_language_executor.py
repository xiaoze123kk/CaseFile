from dataclasses import dataclass
from types import SimpleNamespace

from casefile.agent_runtime.goal.provider import GoalUnderstandingRequest
from casefile.agent_runtime.models import CaseFileChatRequest
from casefile.benchmark.chat_public_language_executor import (
    PUBLIC_SENSITIVE_CANARY,
    _EphemeralCredentialProvider,
    _infrastructure_failure,
)


class _RequestEchoProvider:
    def understand_goal(self, request: GoalUnderstandingRequest) -> GoalUnderstandingRequest:
        return request


@dataclass(frozen=True)
class _DirectRequest:
    api_key: str


class _RecordingLiveProvider:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def chat(self, request: _DirectRequest) -> str:
        self.keys.append(request.api_key)
        return "ok"


def _chat_request() -> CaseFileChatRequest:
    return CaseFileChatRequest(
        task_run_id=1,
        prompt_version="casefile-chat-v17",
        casefile={},
        history=(),
        message="先分析，再审计。",
        editable_fields_by_collection={},
        input_hash="a" * 64,
        model_id="deepseek-v4-pro",
        api_key=PUBLIC_SENSITIVE_CANARY,
        max_turns=4,
        emit=lambda *_args, **_kwargs: None,
    )


def test_ephemeral_provider_injects_key_into_goal_wrapped_chat_request() -> None:
    original = GoalUnderstandingRequest(chat=_chat_request())
    provider = _EphemeralCredentialProvider(
        document={},
        api_key="ephemeral-test-secret",
        live=_RequestEchoProvider(),
    )

    injected = provider.understand_goal(original)

    assert injected.chat.api_key == "ephemeral-test-secret"
    assert original.chat.api_key == PUBLIC_SENSITIVE_CANARY


def test_ephemeral_provider_still_injects_direct_request_without_leaking_secret() -> None:
    secret = "ephemeral-real-secret"
    live = _RecordingLiveProvider()
    provider = _EphemeralCredentialProvider({}, secret, live)
    request = _DirectRequest(api_key=PUBLIC_SENSITIVE_CANARY)

    assert provider.chat(request) == "ok"
    assert live.keys == [secret]
    assert request.api_key == PUBLIC_SENSITIVE_CANARY
    assert secret not in repr(provider)


def test_terminal_model_transport_failure_is_infrastructure_even_after_fallback() -> None:
    succeeded_task = SimpleNamespace(
        status="succeeded",
        error_details_jsonb=None,
        error_code=None,
    )
    failed_event = SimpleNamespace(
        event_type="agent.model_call.failed",
        payload_jsonb={
            "failure_layer": "transport",
            "transport_error_class": "provider_4xx",
            "retry_exhausted": True,
        },
    )

    assert _infrastructure_failure(succeeded_task, [failed_event]) == (
        "provider_transport:provider_4xx"
    )


def test_recoverable_protocol_fallback_is_not_infrastructure() -> None:
    succeeded_task = SimpleNamespace(
        status="succeeded",
        error_details_jsonb=None,
        error_code=None,
    )
    recoverable_event = SimpleNamespace(
        event_type="agent.model_call.failed",
        payload_jsonb={
            "failure_layer": "transport",
            "transport_error_class": "protocol_unsupported",
            "retry_exhausted": False,
        },
    )

    assert _infrastructure_failure(succeeded_task, [recoverable_event]) is None
