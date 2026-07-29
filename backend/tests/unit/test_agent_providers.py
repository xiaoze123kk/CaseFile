"""Provider routing and OpenAI-compatible client configuration tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from agents.tool_context import ToolContext
from casefile.agent_runtime import DeepSeekAgentsProvider, FakeProvider, OpenAIAgentsProvider
from casefile.agent_runtime.models import (
    BriefAnchorExtractRequest,
    BriefPolishCandidate,
    BriefPolishRequest,
    GenerationRequest,
)
from casefile.agent_runtime.providers import (
    ProviderProtocolError,
    _json_schema_instruction,
    _pydantic_validation_issues,
)
from casefile.agent_runtime.tools import GenerationToolContext, validate_casefile_candidate
from casefile.data_postgres.models import TaskRun
from casefile.worker.runtime import _error_code, _safe_error_message, provider_for_task
from casefile_contracts import CaseFile
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from pydantic import ValidationError


def _request(api_key: str | None = "sk-deepseek-test") -> GenerationRequest:
    return GenerationRequest(
        task_run_id=1,
        brief={},
        casefile_id="case_1",
        brief_id="brief_1",
        brief_version=1,
        version_id="draft_1",
        version_no=1,
        parent_version_id=None,
        model_id="deepseek-v4-flash",
        api_key=api_key,
        max_turns=2,
        emit=lambda _event_type, _stage, _payload: None,
    )


def test_deepseek_client_uses_official_chat_completions_endpoint() -> None:
    provider = DeepSeekAgentsProvider()
    model = provider.create_model(replace(_request(), network_retries=4))
    client = model._client

    assert model.model == "deepseek-v4-flash"
    assert str(client.base_url).rstrip("/") == "https://api.deepseek.com"
    assert client.max_retries == 4


def test_deepseek_provider_requires_a_key_before_network_access() -> None:
    with pytest.raises(ProviderProtocolError, match="DeepSeek API key is required"):
        DeepSeekAgentsProvider().generate(_request(api_key=None))


def test_unstructured_provider_receives_exact_auxiliary_schema() -> None:
    instruction = _json_schema_instruction(BriefPolishCandidate)

    assert '"polished_text"' in instruction
    assert '"preserved_intent_summary"' in instruction
    assert '"ambiguities"' in instruction
    assert '"input_hash"' not in instruction


def test_fake_provider_keeps_polish_and_extraction_as_reviewable_candidates() -> None:
    provider = FakeProvider()
    polish = provider.polish(
        BriefPolishRequest(
            task_run_id=1,
            source_text="  原稿事实保持不变。  ",
            input_hash="a" * 64,
            model_id="fake",
            api_key=None,
            max_turns=2,
            emit=lambda _event_type, _stage, _payload: None,
        )
    )
    assert polish.candidate.polished_text == "原稿事实保持不变。"
    extract = provider.extract_anchors(
        BriefAnchorExtractRequest(
            task_run_id=2,
            brief={
                "author_answer": "甲修改记录。乙触发保护。",
                "boundary_text": "必须保持唯一答案；可以保留次要歧义。",
            },
            input_hash="b" * 64,
            model_id="fake",
            api_key=None,
            max_turns=2,
            emit=lambda _event_type, _stage, _payload: None,
        )
    )
    assert [item.statement for item in extract.candidate.author_anchors] == [
        "甲修改记录。",
        "乙触发保护。",
    ]
    assert extract.candidate.creative_constraints[0].suggested_strength == "hard"


@pytest.mark.parametrize(
    ("provider_name", "provider_type"),
    [
        ("openai", OpenAIAgentsProvider),
        ("deepseek", DeepSeekAgentsProvider),
    ],
)
def test_worker_routes_frozen_provider(
    provider_name: str,
    provider_type: type[OpenAIAgentsProvider] | type[DeepSeekAgentsProvider],
) -> None:
    task = cast(TaskRun, SimpleNamespace(provider=provider_name))
    assert isinstance(provider_for_task(task), provider_type)


def test_worker_rejects_unknown_frozen_provider() -> None:
    task = cast(TaskRun, SimpleNamespace(provider="unknown"))
    with pytest.raises(RuntimeError, match="Unsupported provider"):
        provider_for_task(task)


def test_worker_redacts_provider_credentials_from_persisted_errors() -> None:
    secret = "sk-deepseek-super-secret-123456"
    message = _safe_error_message(
        RuntimeError(f"Authorization: Bearer {secret}; api_key={secret}"),
        (secret,),
    )

    assert secret not in message
    assert message.count("[REDACTED]") == 2


def test_validation_tool_returns_actionable_issues_and_public_event() -> None:
    emitted: list[tuple[str, str, dict[str, object]]] = []
    request = replace(
        _request(),
        emit=lambda event_type, stage, payload: emitted.append(
            (event_type, stage, payload)
        ),
    )
    context = ToolContext(
        GenerationToolContext(request),
        tool_name="validate_casefile_candidate",
        tool_call_id="call_validation",
        tool_arguments=json.dumps({"candidate_json": "{}"}),
    )

    raw_result = asyncio.run(
        validate_casefile_candidate.on_invoke_tool(
            context,
            json.dumps({"candidate_json": "{}"}),
        )
    )
    result = json.loads(raw_result)

    assert result["valid"] is False
    assert result["issues"][0] == {
        "code": "schema_invalid",
        "path": "",
        "message": "'schema_version' is a required property",
    }
    assert emitted[0][0:2] == ("tool.completed", "validating")
    assert emitted[0][2]["valid"] is False
    assert emitted[0][2]["issues"][0]["message"] == "缺少必填字段 schema_version"


def test_pydantic_candidate_errors_exclude_invalid_input_values() -> None:
    secret = "author-secret-schema-value"
    with pytest.raises(ValidationError) as caught:
        CaseFile.model_validate_json(json.dumps({"schema_version": secret}))

    issues = _pydantic_validation_issues(caught.value)

    assert issues
    assert secret not in repr(issues)
    assert all({"code", "path", "message"} == set(issue) for issue in issues)


def test_provider_transport_errors_have_stable_failure_codes() -> None:
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response_401 = httpx.Response(401, request=request)
    response_429 = httpx.Response(429, request=request)

    assert (
        _error_code(APIConnectionError(request=request))
        == "provider_connection_failed"
    )
    assert _error_code(APITimeoutError(request=request)) == "provider_timeout"
    assert (
        _error_code(
            AuthenticationError(
                "invalid credential",
                response=response_401,
                body=None,
            )
        )
        == "provider_authentication_failed"
    )
    assert (
        _error_code(
            RateLimitError(
                "rate limited",
                response=response_429,
                body=None,
            )
        )
        == "provider_rate_limited"
    )
