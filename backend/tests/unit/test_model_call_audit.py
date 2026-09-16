"""Real SDK retries over a fake HTTP transport; no network or paid calls."""

import asyncio
import gzip
import json

import httpx
import pytest
from openai import OpenAI

from casefile.agent_runtime.model_call_audit import (
    AuditedAsyncHttpClient,
    AuditedHttpClient,
    observe_model_calls,
)
from casefile.agent_runtime.usage import normalize_usage


def test_usage_protocols_and_zero() -> None:
    for raw in (
        {"input_tokens": 10, "output_tokens": 2, "input_tokens_details": {"cached_tokens": 0}},
        {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    ):
        result = normalize_usage(raw)
        assert result["raw"] == raw
        assert result["tokens"] == {
            "input": 10,
            "output": 2,
            "cached_input": 0,
            "uncached_input": 10,
        }
    assert normalize_usage(None)["tokens"]["cached_input"] is None
    assert normalize_usage({"input_tokens": True})["errors"] == ["input"]


def test_sdk_retries_are_separate_physical_attempts() -> None:
    records = []
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("offline", request=request)
        body = {
            "id": "test",
            "object": "chat.completion",
            "created": 0,
            "model": "model",
            "choices": [],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "prompt_cache_hit_tokens": 8},
        }
        return httpx.Response(200, stream=httpx.ByteStream(json.dumps(body).encode()))

    with observe_model_calls(records.append, binding={"agent": "test"}):
        with OpenAI(
            api_key="secret",
            max_retries=1,
            http_client=AuditedHttpClient(transport=httpx.MockTransport(handler)),
        ) as client:
            client.chat.completions.create(
                model="model",
                messages=[
                    {"role": "user", "content": "private"},
                ],
            )
    finished = [row for row in records if row["event"] == "finished"]
    assert len(finished) == 2
    assert finished[0]["outcome"] == "transport_failed"
    assert finished[0]["usage"]["tokens"]["input"] is None
    assert finished[1]["usage"]["tokens"]["cached_input"] == 8
    assert finished[0]["attempt_id"] != finished[1]["attempt_id"]
    assert [row["transport_retry_index"] for row in finished] == [0, 1]
    assert "secret" not in json.dumps(records) and "private" not in json.dumps(records)


def test_budget_rejection_precedes_network() -> None:
    def reject(record: dict) -> None:
        raise ValueError("budget_exhausted")

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Network reached after rejection")

    with observe_model_calls(reject):
        with AuditedHttpClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="budget_exhausted"):
                client.post("https://example.test/chat", json={"model": "x"})


def test_async_stream_usage_and_early_close() -> None:
    async def run() -> None:
        records = []

        async def handler(request: httpx.Request) -> httpx.Response:
            payload = b'data: {"response":{"model":"x","usage":{"input_tokens":7,'
            payload += b'"output_tokens":2,"input_tokens_details":{"cached_tokens":4}}}}\n\n'
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(payload)
            )

        with observe_model_calls(records.append):
            async with AuditedAsyncHttpClient(transport=httpx.MockTransport(handler)) as client:
                async with client.stream(
                    "POST", "https://example.test/responses", json={"model": "x"}
                ) as response:
                    await response.aread()
                async with client.stream(
                    "POST", "https://example.test/responses", json={"model": "x"}
                ):
                    pass
        rows = [r for r in records if r["event"] == "finished"]
        assert len(rows) == 2
        assert rows[0]["usage"]["tokens"]["cached_input"] == 4
        assert rows[1]["outcome"] == "closed_early"
        assert rows[1]["usage"]["tokens"]["input"] is None

    asyncio.run(run())


def test_gzip_response_usage_is_decoded_without_changing_response() -> None:
    records = []
    body = {"usage": {"prompt_tokens": 12, "completion_tokens": 3}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-encoding": "gzip"},
            stream=httpx.ByteStream(gzip.compress(json.dumps(body).encode())),
        )

    with observe_model_calls(records.append):
        with AuditedHttpClient(transport=httpx.MockTransport(handler)) as client:
            response = client.post("https://example.test/chat", json={"model": "x"})
            assert response.json() == body
    assert records[-1]["usage"]["tokens"]["input"] == 12
