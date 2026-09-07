"""Native provider output is saved before SDK schema interpretation."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from casefile.agent_runtime.provider_adapters.compiler_responses import CompilerResponsesModel


def test_native_invalid_json_is_journaled_before_returning_to_sdk():
    output = SimpleNamespace(model_dump=lambda **_: {"type": "message", "text": "{broken"})
    response = SimpleNamespace(output=[output], usage=SimpleNamespace(
        requests=1, input_tokens=2, output_tokens=3, total_tokens=5,
    ))
    model = CompilerResponsesModel(model="test", openai_client=SimpleNamespace())
    saved = []
    model.on_response = lambda *args: saved.append(args)
    with patch("agents.OpenAIResponsesModel.get_response", AsyncMock(return_value=response)):
        assert asyncio.run(model.get_response()) is response
    assert "{broken" in saved[0][0]
    assert saved[0][1]["total_tokens"] == 5


def test_journal_failure_does_not_allow_model_response_to_advance():
    response = SimpleNamespace(output=[], usage=SimpleNamespace(
        requests=1, input_tokens=2, output_tokens=0, total_tokens=2,
    ))
    model = CompilerResponsesModel(model="test", openai_client=SimpleNamespace())

    def unavailable(*_):
        raise RuntimeError("journal unavailable")

    model.on_response = unavailable
    with patch("agents.OpenAIResponsesModel.get_response", AsyncMock(return_value=response)):
        with pytest.raises(RuntimeError, match="journal unavailable"):
            asyncio.run(model.get_response())
