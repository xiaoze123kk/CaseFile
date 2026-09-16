"""Physical HTTP-attempt accounting, including retries and consumed SSE streams."""

from __future__ import annotations

import json
import logging
import zlib
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from inspect import iscoroutinefunction, signature
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

import httpx
from openai import DefaultAsyncHttpxClient, DefaultHttpxClient

from casefile.agent_runtime.skill_assembly import request_identity
from casefile.agent_runtime.usage import normalize_usage

_logger = logging.getLogger(__name__)
_observer: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "model_call_observer", default=None
)
_binding: ContextVar[dict[str, Any] | None] = ContextVar("model_call_binding", default=None)


def audited_call[F: Callable[..., Any]](function: F) -> F:
    """Bind existing request identity without changing provider return contracts."""
    parameters = signature(function)

    def binding(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        values = parameters.bind(*args, **kwargs).arguments
        request = values.get("request")
        source = getattr(request, "chat", request)
        prompt_version = str(getattr(source, "prompt_version", "unbound"))
        component = values.get("component_id")
        component_id = str(component) if component is not None else None
        from casefile.agent_runtime.agent_skill_release import binding_for_prompt_version

        return {
            **(_binding.get() or {}),
            "logical_call_id": uuid4().hex,
            "entrypoint": function.__module__ + "." + function.__qualname__,
            "prompt_version": prompt_version,
            **binding_for_prompt_version(prompt_version, component_id),
            "stage": str(values.get("stage", function.__name__)),
        }

    @wraps(function)
    def sync(*args: Any, **kwargs: Any) -> Any:
        token = _binding.set(binding(args, kwargs))
        try:
            return function(*args, **kwargs)
        finally:
            _binding.reset(token)

    @wraps(function)
    async def asynchronous(*args: Any, **kwargs: Any) -> Any:
        token = _binding.set(binding(args, kwargs))
        try:
            return await function(*args, **kwargs)
        finally:
            _binding.reset(token)

    return cast(F, asynchronous if iscoroutinefunction(function) else sync)


@contextmanager
def model_call_binding(binding: dict[str, Any]) -> Iterator[None]:
    token = _binding.set({**(_binding.get() or {}), **binding})
    try:
        yield
    finally:
        _binding.reset(token)


@contextmanager
def observe_model_calls(
    observer: Callable[[dict[str, Any]], None], *, binding: dict[str, Any] | None = None
) -> Iterator[None]:
    """A started observer may reject a request before network I/O (budget reserve).

    No credentials or model input are sent to this observer. Context is isolated per
    asyncio task and propagated by asyncio.to_thread, never installed globally.
    """
    token = _observer.set(observer)
    binding_token = _binding.set(binding)
    try:
        yield
    finally:
        _binding.reset(binding_token)
        _observer.reset(token)


class Attempt:
    def __init__(self, request: httpx.Request) -> None:
        self.started = perf_counter()
        self.finished = False
        self.usage: Any = None
        self.buffer = b""
        self.sse = False
        self.status: int | None = None
        self.response_model: str | None = None
        self.decoder: Any = None
        self.observer = _observer.get()
        body = json.loads(request.content)
        self.record: dict[str, Any] = {
            "attempt_id": uuid4().hex,
            "started_at": datetime.now(UTC).isoformat(),
            "binding": dict(_binding.get() or {}),
            "request_model": body.get("model"),
            "request_bytes": len(request.content),
            "transport_retry_index": int(request.headers.get("x-stainless-retry-count", "0")),
            "max_output_tokens": body.get(
                "max_completion_tokens", body.get("max_output_tokens", body.get("max_tokens"))
            ),
            **request_identity(body),
        }
        self.emit("started")

    def emit(self, event: str) -> None:
        record = {**self.record, "event": event}
        if self.observer is not None:
            self.observer(record)
        else:
            _logger.info("model.physical_call %s", json.dumps(record, ensure_ascii=False))

    def response(self, response: httpx.Response) -> None:
        self.status = response.status_code
        self.sse = "text/event-stream" in response.headers.get("content-type", "")
        encoding = response.headers.get("content-encoding", "").lower()
        if not response.is_closed and encoding in {"gzip", "deflate"}:
            self.decoder = zlib.decompressobj(31 if encoding == "gzip" else 15)

    def value(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        nested = value.get("response")
        if isinstance(nested, dict):
            value = nested
        if value.get("usage") is not None:
            self.usage = value["usage"]
        if isinstance(value.get("model"), str):
            self.response_model = value["model"]

    def feed(self, chunk: bytes) -> None:
        if self.decoder is not None:
            try:
                chunk = self.decoder.decompress(chunk)
            except zlib.error:
                self.record["usage_parse_error"] = "content_decoding_failed"
                return
        self.buffer += chunk
        if self.sse:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if line.startswith(b"data:"):
                    self.decode(line[5:].strip())
        # Usage is normally near the end. Bound memory; never log response text.
        if len(self.buffer) > 16 * 1024 * 1024:
            self.buffer = b""

    def decode(self, data: bytes) -> None:
        try:
            self.value(json.loads(data))
        except (ValueError, UnicodeDecodeError):
            pass

    def finish(self, outcome: str) -> None:
        if self.finished:
            return
        self.finished = True
        if outcome == "completed" and self.status is not None and self.status >= 400:
            outcome = "http_failed"
        if not self.sse:
            self.decode(self.buffer)
        elif self.buffer.startswith(b"data:"):
            self.decode(self.buffer[5:].strip())
        self.buffer = b""
        self.record.update(
            outcome=outcome,
            ended_at=datetime.now(UTC).isoformat(),
            status_code=self.status,
            response_model=self.response_model,
            elapsed_ms=round((perf_counter() - self.started) * 1000, 3),
            usage=normalize_usage(self.usage),
        )
        self.emit("finished")


class AuditStream(httpx.SyncByteStream):
    def __init__(self, inner: httpx.SyncByteStream, attempt: Attempt) -> None:
        self.inner, self.attempt = inner, attempt

    def __iter__(self) -> Iterator[bytes]:
        try:
            for chunk in self.inner:
                self.attempt.feed(chunk)
                yield chunk
        except BaseException:
            self.attempt.finish("stream_failed")
            raise
        self.attempt.finish("completed")

    def close(self) -> None:
        try:
            self.inner.close()
        finally:
            self.attempt.finish("closed_early")


class AsyncAuditStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream, attempt: Attempt) -> None:
        self.inner, self.attempt = inner, attempt

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.inner:
                self.attempt.feed(chunk)
                yield chunk
        except BaseException:
            self.attempt.finish("stream_failed")
            raise
        self.attempt.finish("completed")

    async def aclose(self) -> None:
        try:
            await self.inner.aclose()
        finally:
            self.attempt.finish("closed_early")


class AuditedHttpClient(DefaultHttpxClient):
    """Observe send, preserving SDK proxy, TLS, timeout and pool configuration."""

    def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        if request.method != "POST":
            return super().send(request, **kwargs)
        attempt = Attempt(request)
        stream = kwargs.pop("stream", False)
        try:
            response = super().send(request, stream=True, **kwargs)
            attempt.response(response)
            if response.is_closed:
                attempt.feed(response.content)
                attempt.finish("completed")
                return response
            response.stream = AuditStream(response.stream, attempt)  # type: ignore[arg-type]
            if not stream:
                response.read()
            return response
        except BaseException:
            attempt.finish("transport_failed")
            raise


class AuditedAsyncHttpClient(DefaultAsyncHttpxClient):
    async def send(self, request: httpx.Request, **kwargs: Any) -> httpx.Response:
        if request.method != "POST":
            return await super().send(request, **kwargs)
        attempt = Attempt(request)
        stream = kwargs.pop("stream", False)
        try:
            response = await super().send(request, stream=True, **kwargs)
            attempt.response(response)
            if response.is_closed:
                attempt.feed(response.content)
                attempt.finish("completed")
                return response
            response.stream = AsyncAuditStream(response.stream, attempt)  # type: ignore[arg-type]
            if not stream:
                await response.aread()
            return response
        except BaseException:
            attempt.finish("transport_failed")
            raise
