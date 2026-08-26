from __future__ import annotations

from dataclasses import dataclass

from casefile.benchmark.chat_public_language_executor import (
    PUBLIC_SENSITIVE_CANARY,
    _EphemeralCredentialProvider,
)


@dataclass(frozen=True)
class _Request:
    api_key: str


class _RecordingLiveProvider:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def chat(self, request: _Request) -> str:
        self.keys.append(request.api_key)
        return "ok"


def test_real_credential_is_injected_only_for_the_provider_call() -> None:
    secret = "ephemeral-real-secret"
    live = _RecordingLiveProvider()
    provider = _EphemeralCredentialProvider({}, secret, live)
    request = _Request(api_key=PUBLIC_SENSITIVE_CANARY)

    assert provider.chat(request) == "ok"

    assert live.keys == [secret]
    assert request.api_key == PUBLIC_SENSITIVE_CANARY
    assert secret not in repr(provider)
