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
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from pydantic import ValidationError

import casefile.agent_runtime.providers as providers_module
from casefile.agent_runtime import DeepSeekAgentsProvider, FakeProvider, OpenAIAgentsProvider
from casefile.agent_runtime.models import (
    BriefAnchorExtractRequest,
    BriefIntakeSynthesizeRequest,
    BriefPolishCandidate,
    BriefPolishRequest,
    CandidateStrategy,
    CaseFileChatRequest,
    GenerationRequest,
)
from casefile.agent_runtime.prompt import casefile_chat_input
from casefile.agent_runtime.providers import (
    ProviderProtocolError,
    _json_schema_instruction,
    _pydantic_validation_issues,
    _validate_generated_descriptions,
)
from casefile.agent_runtime.tools import GenerationToolContext, validate_casefile_candidate
from casefile.application.casefile_v1 import generation_candidate_summary
from casefile.application.v1_editing import editable_fields_by_collection
from casefile.contracts import ContractValidationError
from casefile.data_postgres.models import TaskRun
from casefile.worker.runtime import _error_code, _safe_error_message, provider_for_task
from casefile_contracts import CaseFile


def _request(api_key: str | None = "sk-deepseek-test") -> GenerationRequest:
    return GenerationRequest(
        task_run_id=1,
        prompt_version="brief-to-draft-v3",
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


def test_openai_provider_loads_the_prompt_version_frozen_on_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded: list[tuple[str, str]] = []

    def fake_system_prompt_for_task(agent_id: str, version: str) -> str:
        loaded.append((agent_id, version))
        return "Role: frozen test prompt.\n"

    async def fake_run_auxiliary(
        *_args: object,
        **kwargs: object,
    ) -> tuple[dict[str, object], dict[str, object]]:
        assert kwargs["instructions"] == "Role: frozen test prompt.\n"
        assert '"polish_mode":"rewrite"' in str(kwargs["input_text"])
        return (
            {
                "polished_text": "原稿",
                "preserved_intent_summary": "保留原意。",
                "ambiguities": [],
                "introduced_details": [],
            },
            {},
        )

    monkeypatch.setattr(
        providers_module,
        "system_prompt_for_task",
        fake_system_prompt_for_task,
    )
    monkeypatch.setattr(
        OpenAIAgentsProvider,
        "_run_auxiliary",
        fake_run_auxiliary,
    )

    result = OpenAIAgentsProvider().polish(
        BriefPolishRequest(
            task_run_id=1,
            prompt_version="brief-polish-v1",
            source_text="原稿",
            polish_mode="rewrite",
            input_hash="a" * 64,
            model_id="gpt-5.6-sol",
            api_key="sk-test",
            max_turns=2,
            emit=lambda _event_type, _stage, _payload: None,
        )
    )

    assert result.candidate.polished_text == "原稿"
    assert loaded == [("brief_polish", "brief-polish-v1")]


def test_polish_rejects_introduced_details_outside_narrative_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_auxiliary(
        *_args: object,
        **_kwargs: object,
    ) -> tuple[dict[str, object], dict[str, object]]:
        return (
            {
                "polished_text": "父亲推开门，看见弟弟偷吃蛋糕。",
                "preserved_intent_summary": "保留发现偷吃蛋糕的核心事实。",
                "ambiguities": [],
                "introduced_details": ["新增父亲推开门的动作。"],
            },
            {},
        )

    monkeypatch.setattr(OpenAIAgentsProvider, "_run_auxiliary", fake_run_auxiliary)

    with pytest.raises(ProviderProtocolError, match="introduced new details"):
        OpenAIAgentsProvider().polish(
            BriefPolishRequest(
                task_run_id=1,
                prompt_version="brief-polish-v3",
                source_text="父亲发现弟弟偷吃蛋糕。",
                polish_mode="rewrite",
                input_hash="a" * 64,
                model_id="gpt-5.6-sol",
                api_key="sk-test",
                max_turns=2,
                emit=lambda _event_type, _stage, _payload: None,
            )
        )


def test_unstructured_provider_receives_exact_auxiliary_schema() -> None:
    instruction = _json_schema_instruction(BriefPolishCandidate)

    assert '"polished_text"' in instruction
    assert '"preserved_intent_summary"' in instruction
    assert '"ambiguities"' in instruction
    assert '"introduced_details"' in instruction
    assert '"input_hash"' not in instruction


def test_fake_provider_keeps_polish_and_extraction_as_reviewable_candidates() -> None:
    provider = FakeProvider()
    polish = provider.polish(
        BriefPolishRequest(
            task_run_id=1,
            prompt_version="brief-polish-v2",
            source_text="  原稿事实保持不变。  ",
            polish_mode="proofread",
            input_hash="a" * 64,
            model_id="fake",
            api_key=None,
            max_turns=2,
            emit=lambda _event_type, _stage, _payload: None,
        )
    )
    assert polish.candidate.polished_text == "原稿事实保持不变。"
    assert polish.polish_mode == "proofread"
    assert polish.candidate.introduced_details == []
    extract = provider.extract_anchors(
        BriefAnchorExtractRequest(
            task_run_id=2,
            prompt_version="brief-anchor-extract-v2",
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


def test_fake_intake_synthesis_emits_named_outline_stages() -> None:
    result = FakeProvider().synthesize_intake(
        BriefIntakeSynthesizeRequest(
            task_run_id=3,
            prompt_version="brief-intake-synthesize-v2",
            input_data={
                "source": {"content_text": "一名档案员发现一段不存在的时间。"},
                "questions": [],
            },
            input_hash="c" * 64,
            model_id="fake",
            api_key=None,
            max_turns=2,
            emit=lambda _event_type, _stage, _payload: None,
        )
    )

    outline = [item.root for item in result.candidate.content_outline]
    assert len(outline) == 3
    assert all("：" in item for item in outline)
    assert all(not item.startswith("阶段名称") for item in outline)


def test_fake_provider_chat_reads_full_casefile_without_mutating_it() -> None:
    emitted: list[tuple[str, str]] = []
    result = FakeProvider().chat(
        CaseFileChatRequest(
            task_run_id=3,
            prompt_version="casefile-chat-v1",
            casefile={
                "entities": [{"id": "ent_1", "name": "Lucy"}],
                "events": [{"id": "evt_1", "title": "蛋糕被偷吃"}],
            },
            history=({"role": "user", "content": "先看完整卷宗。"},),
            message="请讨论 evt_1，但先不要改稿。",
            editable_fields_by_collection={
                "entities": ("description", "name", "tags"),
                "events": ("description", "participant_refs", "tags", "title"),
            },
            input_hash="c" * 64,
            model_id="fake",
            api_key=None,
            max_turns=2,
            emit=lambda event_type, stage, _payload: emitted.append(
                (event_type, stage)
            ),
        )
    )

    assert result.candidate.referenced_object_ids == ["evt_1"]
    assert result.candidate.suggestions == []
    assert "没有自动修改" in result.candidate.answer
    assert emitted == [
        ("model.started", "responding"),
        ("model.completed", "responding"),
    ]


def test_casefile_chat_input_receives_the_exact_editable_field_capabilities() -> None:
    capabilities = editable_fields_by_collection()
    request = CaseFileChatRequest(
        task_run_id=4,
        prompt_version="casefile-chat-v1",
        casefile={"entities": [{"id": "ent_1", "name": "Lucy"}]},
        history=(),
        message="请给 Lucy 增加标签。",
        editable_fields_by_collection=capabilities,
        input_hash="d" * 64,
        model_id="fake",
        api_key=None,
        max_turns=2,
        emit=lambda _event_type, _stage, _payload: None,
    )

    introduction, payload_text = casefile_chat_input(request).split("\n", 1)
    payload = json.loads(payload_text)

    assert introduction.startswith("请根据以下冻结数据回复作者")
    assert payload["editable_fields_by_collection"] == {
        collection: list(fields) for collection, fields in capabilities.items()
    }
    assert "tags" in payload["editable_fields_by_collection"]["entities"]
    assert "revision" not in payload["editable_fields_by_collection"]["entities"]


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


@pytest.mark.parametrize("description", [None, "", "   "])
def test_generation_quality_gate_rejects_missing_object_descriptions(
    description: str | None,
) -> None:
    candidate = {
        "resolution_specs": [{"description": "核心问题说明。"}],
        "entities": [{"description": description}],
        "relationships": [],
        "locations": [],
        "events": [{"description": "事件的经过与因果作用。"}],
        "information_units": [],
        "claims": [],
        "hypotheses": [],
        "reasoning_paths": [],
        "constraints": [],
        "structure_locks": [],
    }

    with pytest.raises(ContractValidationError) as caught:
        _validate_generated_descriptions(candidate)

    assert caught.value.errors == [
        {
            "code": "generated_description_missing",
            "path": "/entities/0/description",
            "message": "Agent-generated objects require a non-empty description",
        }
    ]


def test_fake_generation_populates_descriptions_with_the_draft() -> None:
    request = replace(
        _request(api_key=None),
        prompt_version="brief-to-draft-v4",
        model_id="fake",
        brief={
            "creative_intent": "家庭日常中的小悬念",
            "reasoning_proposition": "弟弟为何偷吃蛋糕？",
            "resolution_mode": "open",
            "conclusion_mode": "open_interpretation",
            "author_answer": None,
            "author_anchors": [{"statement": "弟弟偷吃了蛋糕。"}],
            "creative_constraints": [],
            "source_record_ids": [],
        },
    )

    candidate = FakeProvider().generate(request).candidate
    generated_objects = [
        item
        for collection in (
            "resolution_specs",
            "entities",
            "relationships",
            "locations",
            "events",
            "information_units",
            "claims",
            "hypotheses",
            "reasoning_paths",
            "constraints",
            "structure_locks",
        )
        for item in candidate[collection]
    ]

    assert generated_objects
    assert all(item["description"].strip() for item in generated_objects)


def test_fake_generation_keeps_strategy_candidates_distinct() -> None:
    request = replace(
        _request(api_key=None),
        prompt_version="brief-to-draft-v5",
        model_id="fake",
        brief={
            "creative_intent": "围绕一段失真的时间记录建立推理卷宗",
            "reasoning_proposition": "三份可靠记录为何共同指向不存在的时间？",
            "resolution_mode": "open",
            "author_answer": None,
            "author_anchors": [],
            "creative_constraints": [],
            "source_record_ids": [],
        },
    )

    results = [
        FakeProvider().generate(
            replace(request, candidate_strategy=strategy),
        )
        for strategy in (
            CandidateStrategy.STRUCTURE_FIRST,
            CandidateStrategy.ATMOSPHERE_FIRST,
            CandidateStrategy.REASONING_FIRST,
        )
    ]

    assert len({result.candidate["title"] for result in results}) == 3
    assert len(
        {
            generation_candidate["content_hash"]
            for generation_candidate in (
                generation_candidate_summary(result.candidate)
                for result in results
            )
        }
    ) == 3


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
