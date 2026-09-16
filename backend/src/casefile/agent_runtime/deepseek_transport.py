"""Enforce Flash at the actual DeepSeek HTTP boundary, including legacy runners."""

from __future__ import annotations

import json
from typing import Any

import httpx
from openai import AsyncOpenAI, DefaultAsyncHttpxClient, OpenAI

from casefile.agent_runtime.model_call_audit import AuditedAsyncHttpClient, AuditedHttpClient
from casefile.agent_runtime.model_policy import DEEPSEEK_MODEL_ID


def require_flash_request(request: httpx.Request) -> None:
    if request.url.host != "api.deepseek.com" or request.method != "POST":
        return
    payload = json.loads(request.content)
    if payload.get("model") != DEEPSEEK_MODEL_ID:
        raise ValueError("deepseek_flash_required_create_new_run")


async def _require_flash_request(request: httpx.Request) -> None:
    require_flash_request(request)


def model_checked_client(**kwargs: Any) -> OpenAI:
    return OpenAI(
        http_client=AuditedHttpClient(event_hooks={"request": [require_flash_request]}), **kwargs
    )


def model_checked_async_client(**kwargs: Any) -> AsyncOpenAI:
    return AsyncOpenAI(
        http_client=flash_async_http_client(),
        **kwargs,
    )


def flash_async_http_client() -> DefaultAsyncHttpxClient:
    return AuditedAsyncHttpClient(event_hooks={"request": [_require_flash_request]})
