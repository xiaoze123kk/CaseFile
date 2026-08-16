"""Provider routing and OpenAI-compatible client configuration tests."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import casefile.agent_runtime.providers as providers_module
import httpx
import pytest
from agents.tool_context import ToolContext
from casefile.agent_runtime import DeepSeekAgentsProvider, FakeProvider, OpenAIAgentsProvider
from casefile.agent_runtime.brief_to_draft_v8 import workflow as v8_workflow
from casefile.agent_runtime.models import (
    BriefAnchorExtractRequest,
    BriefIntakeSynthesizeRequest,
    BriefPolishCandidate,
    BriefPolishRequest,
    BriefStrategyOptionsCandidate,
    BriefStrategyOptionsRequest,
    CandidateStrategy,
    CaseFileChatRequest,
    GenerationPlan,
    GenerationRequest,
    IdeaGenerationRequest,
    QueryRewriteResult,
    RouteDecision,
)
from casefile.agent_runtime.prompt import casefile_chat_input, idea_generation_input
from casefile.agent_runtime.prompt_repository import PromptRepositoryError
from casefile.agent_runtime.providers import (
    ProviderProtocolError,
    _allocate_plan_ids,
    _deepseek_v8_output_protocol,
    _json_schema_instruction,
    _partition_issues,
    _prune_invalid_reference_list_items,
    _remove_absent_optional_fields,
    _retain_planned_objects,
    _validate_generated_descriptions,
    _validate_partitioned_candidate,
)
from casefile.agent_runtime.structured_output import (
    pydantic_validation_issues as _pydantic_validation_issues,
)
from casefile.agent_runtime.structured_output import (
    validate_model_json as _validate_auxiliary_output,
)
from casefile.agent_runtime.tools import GenerationToolContext, validate_casefile_candidate
from casefile.application.casefile_v1 import generation_candidate_summary
from casefile.application.v1_editing import editable_fields_by_collection
from casefile.contracts import ContractValidationError
from casefile.data_postgres.models import TaskRun
from casefile.worker.runtime import _error_code, _safe_error_message, provider_for_task
from casefile_contracts import CaseFile, ObjectRef
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError


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


def test_deepseek_v8_protocol_uses_json_mode_for_known_flash_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CASEFILE_DEEPSEEK_V8_OUTPUT_PROTOCOL", raising=False)

    assert _deepseek_v8_output_protocol("deepseek-v4-flash") == "json_object"
    assert _deepseek_v8_output_protocol("deepseek-chat") == "json_object"
    assert _deepseek_v8_output_protocol("deepseek-v4-pro") == "strict_tool"

    monkeypatch.setenv("CASEFILE_DEEPSEEK_V8_OUTPUT_PROTOCOL", "strict_tool")
    assert _deepseek_v8_output_protocol("deepseek-v4-flash") == "strict_tool"

    monkeypatch.setenv("CASEFILE_DEEPSEEK_V8_OUTPUT_PROTOCOL", "not-a-protocol")
    with pytest.raises(ProviderProtocolError, match="CASEFILE_DEEPSEEK_V8_OUTPUT_PROTOCOL"):
        _deepseek_v8_output_protocol("deepseek-v4-flash")


def test_deepseek_provider_requires_a_key_before_network_access() -> None:
    with pytest.raises(ProviderProtocolError, match="DeepSeek API key is required"):
        DeepSeekAgentsProvider().generate(_request(api_key=None))


def test_v8_validates_the_frozen_bundle_before_any_step_can_be_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded: list[tuple[str, str]] = []

    def reject_bundle(agent_id: str, version: str) -> object:
        loaded.append((agent_id, version))
        raise PromptRepositoryError("frozen bundle is unavailable")

    async def unexpected_model_call(
        _instructions: str,
        _input_text: str,
        _output_type: type[BaseModel],
        _stage: str,
        _component_id: str,
        _schema_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        raise AssertionError("model steps must not start before Bundle validation")

    monkeypatch.setattr(v8_workflow, "load_prompt", reject_bundle)
    request = replace(
        _request(api_key=None),
        prompt_version="brief-to-draft-v8",
        reusable_steps={"case_blueprint_planner": {}},
    )

    with pytest.raises(PromptRepositoryError, match="frozen bundle is unavailable"):
        asyncio.run(v8_workflow.run_v8_generation(request, call_component=unexpected_model_call))

    assert loaded == [("brief_to_draft", "brief-to-draft-v8")]


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


def test_unstructured_output_preserves_schema_issues_for_bounded_repair() -> None:
    with pytest.raises(ContractValidationError) as caught:
        _validate_auxiliary_output(
            BriefPolishCandidate,
            json.dumps(
                {
                    "polished_text": "修订稿",
                    "ambiguities": [],
                    "introduced_details": [],
                },
                ensure_ascii=False,
            ),
        )

    assert caught.value.errors == [
        {
            "code": "missing",
            "path": "/preserved_intent_summary",
            "message": "缺少必填字段。",
        }
    ]


def test_unstructured_output_reports_invalid_json_as_a_validation_issue() -> None:
    with pytest.raises(ContractValidationError) as caught:
        _validate_auxiliary_output(BriefPolishCandidate, '{"polished_text":')

    assert caught.value.errors[0]["code"] == "candidate_json_invalid"
    assert caught.value.errors[0]["path"] == ""


def test_unstructured_output_discards_only_forbidden_extra_fields() -> None:
    discarded_paths: list[str] = []

    candidate = _validate_auxiliary_output(
        BriefPolishCandidate,
        json.dumps(
            {
                "polished_text": "修订稿",
                "preserved_intent_summary": "保留原意",
                "ambiguities": [],
                "introduced_details": [],
                "confirmation_status_note": "模型自行补充的说明",
            },
            ensure_ascii=False,
        ),
        discarded_paths=discarded_paths,
    )

    assert candidate.polished_text == "修订稿"
    assert discarded_paths == ["/confirmation_status_note"]


def test_unstructured_output_keeps_non_extra_validation_failures_after_cleanup() -> None:
    with pytest.raises(ContractValidationError) as caught:
        _validate_auxiliary_output(
            BriefPolishCandidate,
            json.dumps(
                {
                    "polished_text": "修订稿",
                    "ambiguities": [],
                    "introduced_details": [],
                    "confirmation_status_note": "模型自行补充的说明",
                },
                ensure_ascii=False,
            ),
        )

    assert caught.value.errors == [
        {
            "code": "missing",
            "path": "/preserved_intent_summary",
            "message": "缺少必填字段。",
        }
    ]


def test_unstructured_output_normalizes_refs_from_authoritative_planned_ids() -> None:
    normalized_ref_paths: list[str] = []
    reference = _validate_auxiliary_output(
        ObjectRef,
        json.dumps(
            {
                "object_type": "constraints",
                "object_id": "con_t96_01",
            }
        ),
        planned_object_types={"con_t96_01": "constraint"},
        normalized_ref_paths=normalized_ref_paths,
    )

    assert reference.object_type.value == "constraint"
    assert normalized_ref_paths == ["/object_type"]


def test_unstructured_output_does_not_guess_unknown_reference_types() -> None:
    with pytest.raises(ContractValidationError) as caught:
        _validate_auxiliary_output(
            ObjectRef,
            json.dumps(
                {
                    "object_type": "constraints",
                    "object_id": "con_unknown",
                }
            ),
            planned_object_types={"con_t96_01": "constraint"},
        )

    assert caught.value.errors[0]["path"] == "/object_type"


def test_generation_omits_absent_optional_spatial_positions() -> None:
    normalized = _remove_absent_optional_fields(
        {
            "locations": [
                {
                    "id": "loc_t1_01",
                    "description": "档案馆主楼。",
                    "spatial_position": None,
                    "parent_ref": None,
                }
            ],
            "entities": [{"id": "ent_t1_01", "description": None}],
        }
    )

    assert "spatial_position" not in normalized["locations"][0]
    assert normalized["locations"][0]["parent_ref"] is None
    assert "description" not in normalized["entities"][0]


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
    suggestion = provider.extract_anchors(
        BriefAnchorExtractRequest(
            task_run_id=3,
            prompt_version="brief-anchor-extract-v3",
            brief={
                "creative_intent": "失真的时间档案",
                "reasoning_proposition": "三份记录为何指向不存在的时间？",
                "author_answer": None,
                "boundary_text": None,
            },
            input_hash="c" * 64,
            model_id="fake",
            api_key=None,
            max_turns=2,
            emit=lambda _event_type, _stage, _payload: None,
            mode="suggest_author_answer",
        )
    )
    assert suggestion.candidate.suggested_author_answer is not None
    assert "不存在的时间" in suggestion.candidate.suggested_author_answer


def test_strategy_options_are_tailored_complete_and_not_auto_selected() -> None:
    result = FakeProvider().strategy_options(
        BriefStrategyOptionsRequest(
            task_run_id=8,
            prompt_version="brief-strategy-options-v1",
            brief={
                "creative_intent": "失真的时间档案",
                "reasoning_proposition": "三份记录为何指向不存在的时间？",
            },
            input_hash="e" * 64,
            model_id="fake",
            api_key=None,
            max_turns=2,
            emit=lambda _event_type, _stage, _payload: None,
        )
    )

    assert [option.strategy for option in result.candidate.options] == [
        "structure_first",
        "atmosphere_first",
        "reasoning_first",
    ]
    assert result.candidate.recommended_strategy == "reasoning_first"
    assert "不存在的时间" in result.candidate.options[2].direction


def test_strategy_options_reject_duplicate_or_missing_direction() -> None:
    option = {
        "strategy": "structure_first",
        "direction": "先建立结构。",
        "focus": "结构",
        "strengths": ["稳定", "可审阅"],
        "tradeoffs": ["氛围稍后深化"],
        "brief_fit": "适配当前 Brief。",
    }
    with pytest.raises(ValidationError, match="each selectable strategy exactly once"):
        BriefStrategyOptionsCandidate.model_validate(
            {
                "options": [option, option, option],
                "recommended_strategy": "structure_first",
                "recommendation_reason": "结构最适配。",
            }
        )


def test_generation_plan_rejects_unknown_keys_and_allocates_stable_ids() -> None:
    with pytest.raises(ValidationError, match="unknown keys"):
        GenerationPlan.model_validate(
            {
                "title": "非法计划",
                "objects": [
                    {
                        "local_key": "answer",
                        "collection": "resolution_specs",
                        "title": "解答",
                        "purpose": "固定核心答案。",
                        "referenced_keys": ["missing"],
                    }
                ],
            }
        )

    plan = GenerationPlan.model_validate(
        {
            "title": "稳定计划",
            "objects": [
                {
                    "local_key": "answer",
                    "collection": "resolution_specs",
                    "title": "解答",
                    "purpose": "固定核心答案。",
                },
                {
                    "local_key": "lead",
                    "collection": "entities",
                    "title": "主角",
                    "purpose": "承担调查行动。",
                    "referenced_keys": ["answer"],
                },
            ],
        }
    )
    assert _allocate_plan_ids(42, plan) == {
        "answer": "res_t42_01",
        "lead": "ent_t42_01",
    }


def test_cross_partition_validation_routes_only_affected_partitions() -> None:
    grouped = _partition_issues(
        [
            {"code": "reference_missing", "path": "/claims/0/basis_refs/0"},
            {"code": "description_missing", "path": "/events/0/description"},
            {"code": "metadata_invalid", "path": "/version/version_no"},
        ]
    )

    assert set(grouped) == {"reasoning", "story"}
    assert grouped["reasoning"][0]["path"] == "/claims/0/basis_refs/0"
    assert grouped["story"][0]["path"] == "/events/0/description"


def test_partition_validation_reports_contract_and_planned_id_issues_together() -> None:
    request = replace(
        _request(api_key=None),
        prompt_version="brief-to-draft-v4",
        model_id="fake",
        brief={
            "creative_intent": "家庭日常中的小悬念",
            "reasoning_proposition": "弟弟为什么偷吃蛋糕？",
            "resolution_mode": "open",
            "conclusion_mode": "open_interpretation",
            "author_answer": None,
            "author_anchors": [{"statement": "弟弟偷吃了蛋糕。"}],
            "creative_constraints": [],
            "source_record_ids": [],
        },
    )
    candidate = FakeProvider().generate(request).candidate
    candidate["title"] = ""
    candidate["resolution_specs"][0]["id"] = "res_t1_unplanned"

    with pytest.raises(ContractValidationError) as caught:
        _validate_partitioned_candidate(
            candidate,
            {"answer": "res_t1_01", "constraint": "con_t1_01"},
        )

    assert any(issue["path"] == "/title" for issue in caught.value.errors)
    id_issue = next(
        issue
        for issue in caught.value.errors
        if issue["code"] == "planned_object_ids_mismatch"
    )
    assert id_issue["path"] == "/resolution_specs"
    assert "res_t1_01" in id_issue["message"]
    assert "res_t1_unplanned" in id_issue["message"]


def test_partition_discards_unplanned_objects_but_keeps_missing_ids_visible() -> None:
    partition, discarded = _retain_planned_objects(
        {
            "entities": [
                {"id": "ent_t1_01", "name": "保留"},
                {"id": "ent_unplanned", "name": "丢弃"},
            ],
            "relationships": [{"id": "rel_unplanned", "label": "丢弃"}],
        },
        {"ent_t1_01", "ent_t1_02"},
    )

    assert partition["entities"] == [{"id": "ent_t1_01", "name": "保留"}]
    assert partition["relationships"] == []
    assert discarded == ["ent_unplanned", "rel_unplanned"]


def test_bounded_repair_prunes_only_validator_identified_reference_list_items() -> None:
    candidate = {
        "entities": [
            {
                "knowledge_states": [
                    {
                        "false_belief_refs": [
                            {"object_type": "claim", "object_id": "claim_keep"},
                            {"object_type": "information_unit", "object_id": "info_drop"},
                        ]
                    }
                ]
            }
        ],
        "events": [{"location_ref": {"object_type": "entity", "object_id": "ent_bad"}}],
    }

    pruned = _prune_invalid_reference_list_items(
        candidate,
        [
            {
                "code": "reference_type_mismatch",
                "path": "/entities/0/knowledge_states/0/false_belief_refs/1",
            },
            {
                "code": "reference_type_mismatch",
                "path": "/events/0/location_ref",
            },
        ],
    )

    assert pruned == ["/entities/0/knowledge_states/0/false_belief_refs/1"]
    assert candidate["entities"][0]["knowledge_states"][0]["false_belief_refs"] == [
        {"object_type": "claim", "object_id": "claim_keep"}
    ]
    assert candidate["events"][0]["location_ref"] == {
        "object_type": "entity",
        "object_id": "ent_bad",
    }


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
            emit=lambda event_type, stage, _payload: emitted.append((event_type, stage)),
        )
    )

    assert result.candidate.referenced_object_ids == ["evt_1"]
    assert result.candidate.suggestions == []
    assert "没有自动修改" in result.candidate.answer
    assert emitted == [
        ("model.started", "responding"),
        ("model.completed", "responding"),
    ]


def test_fake_provider_chat_consumes_retrieval_queries_under_route_budget() -> None:
    emitted: list[tuple[str, str, dict[str, Any]]] = []
    result = FakeProvider().chat(
        CaseFileChatRequest(
            task_run_id=30,
            prompt_version="casefile-chat-v2",
            casefile={
                "entities": [{"id": "ent_1", "name": "张三"}],
                "events": [{"id": "evt_1", "title": "三号库区失火"}],
            },
            history=(),
            message="当时谁在库区",
            editable_fields_by_collection={"entities": ("name",)},
            input_hash="a" * 64,
            model_id="fake",
            api_key=None,
            max_turns=6,
            emit=lambda event_type, stage, payload: emitted.append(
                (event_type, stage, payload)
            ),
            route=RouteDecision(
                execution_profile={
                    "toolset": ["search_casefile"],
                    "max_tool_calls": 3,
                    "max_turns": 4,
                }
            ),
            rewrite=QueryRewriteResult(
                original_query="当时谁在库区",
                normalized_query="当时谁在库区",
                canonical_query="当时谁在库区",
                retrieval_queries=("张三", "三号库区失火"),
                rewrite_decision="MULTI_QUERY",
            ),
        )
    )

    assert result.tools.calls == 2
    assert result.tools.successful_calls == 2
    assert result.tools.adopted_results == 2
    assert set(result.tools.retrieved_object_ids) == {"ent_1", "evt_1"}
    assert result.usage["tool_metrics"]["retrieved_object_ids"] == ["ent_1", "evt_1"]
    assert [entry[0] for entry in emitted if entry[0] == "tool.completed"] == [
        "tool.completed",
        "tool.completed",
    ]


def test_fake_provider_chat_stops_retrieval_when_budget_is_exhausted() -> None:
    result = FakeProvider().chat(
        CaseFileChatRequest(
            task_run_id=31,
            prompt_version="casefile-chat-v2",
            casefile={"entities": [{"id": "ent_1", "name": "张三"}]},
            history=(),
            message="检索",
            editable_fields_by_collection={},
            input_hash="b" * 64,
            model_id="fake",
            api_key=None,
            max_turns=4,
            emit=lambda _event_type, _stage, _payload: None,
            route=RouteDecision(
                execution_profile={
                    "toolset": ["search_casefile"],
                    "max_tool_calls": 1,
                    "max_turns": 4,
                }
            ),
            rewrite=QueryRewriteResult(
                original_query="检索",
                normalized_query="检索",
                canonical_query="检索",
                retrieval_queries=("张三", "张三 卷宗"),
                rewrite_decision="MULTI_QUERY",
            ),
        )
    )

    assert result.tools.calls == 2
    assert result.tools.successful_calls == 1
    assert result.tools.budget_exhausted == 1


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
        validation={
            "status": "passed",
            "validator": "casefile.contracts.validate_casefile",
            "schema_version": "casefile-v1",
            "issue_count": 0,
            "issues": [],
            "reason": None,
        },
        focus={"object_ids": ["ent_1"]},
    )

    introduction, payload_text = casefile_chat_input(request).split("\n", 1)
    payload = json.loads(payload_text)

    assert introduction.startswith("请根据以下冻结数据回复作者")
    assert payload["editable_fields_by_collection"] == {
        collection: list(fields) for collection, fields in capabilities.items()
    }
    assert "tags" in payload["editable_fields_by_collection"]["entities"]
    assert "revision" not in payload["editable_fields_by_collection"]["entities"]
    assert payload["validation"]["status"] == "passed"
    assert payload["validation"]["issue_count"] == 0
    assert payload["focus"] == {"object_ids": ["ent_1"]}


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
        emit=lambda event_type, stage, payload: emitted.append((event_type, stage, payload)),
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
            "message": "Agent 生成的对象必须填写非空描述。",
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
            "conclusion_mode": "open_interpretation",
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
    assert (
        len(
            {
                generation_candidate["content_hash"]
                for generation_candidate in (
                    generation_candidate_summary(result.candidate) for result in results
                )
            }
        )
        == 3
    )


def test_provider_transport_errors_have_stable_failure_codes() -> None:
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response_401 = httpx.Response(401, request=request)
    response_429 = httpx.Response(429, request=request)

    assert _error_code(APIConnectionError(request=request)) == "provider_connection_failed"
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


def test_idea_generation_input_passes_preferences() -> None:
    introduction, payload_text = idea_generation_input(
        "a" * 64,
        preferences={
            "eras": ["中世纪"],
            "settings": ["太空"],
            "atmospheres": ["恐怖"],
            "keywords": ["时间循环"],
        },
    ).split("\n", 1)
    payload = json.loads(payload_text)

    assert introduction.startswith("请根据 preferences 中提供的时代、场景、氛围与关键词偏好")
    assert payload["preferences"] == {
        "eras": ["中世纪"],
        "settings": ["太空"],
        "atmospheres": ["恐怖"],
        "keywords": ["时间循环"],
    }


def test_fake_idea_generation_reflects_preferences() -> None:
    result = FakeProvider().generate_ideas(
        IdeaGenerationRequest(
            task_run_id=0,
            prompt_version="idea-generation-v3",
            regenerate=False,
            existing_concepts=(),
            input_hash="a" * 64,
            model_id="fake",
            api_key=None,
            max_turns=8,
            emit=lambda _event_type, _stage, _payload: None,
            preferences={
                "eras": ["中世纪"],
                "settings": ["太空"],
                "atmospheres": ["恐怖"],
                "keywords": ["时间循环"],
            },
        )
    )

    candidates = result.candidate.candidates
    assert len(candidates) == 3
    medieval_professions = {"骑士", "炼金术士", "修道院抄写员", "行会商人", "巡夜人"}
    for candidate in candidates:
        assert "中世纪背景下" in candidate.concept
        assert "太空" in candidate.concept
        assert "恐怖氛围的" in candidate.concept
        assert "时间循环" in candidate.concept
        assert "令人不安的异象" in candidate.core_suspense
        assert "窒息感" in candidate.target_experience
        assert any(prof in candidate.concept for prof in medieval_professions)
