"""Provider routing and OpenAI-compatible client configuration tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
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
)
from casefile.data_postgres.models import TaskRun
from casefile.worker.runtime import _safe_error_message, provider_for_task


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
    model = provider.create_model(_request())
    client = model._client

    assert model.model == "deepseek-v4-flash"
    assert str(client.base_url).rstrip("/") == "https://api.deepseek.com"


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
