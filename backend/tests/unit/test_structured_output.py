"""Structured-output schema, protocol, fallback, and retry tests."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from agents import ModelSettings
from agents.exceptions import ModelBehaviorError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import casefile.agent_runtime.providers as providers_module
import casefile.agent_runtime.structured_output as structured_module
from casefile.agent_runtime.brief_to_draft_v12.contracts import TemporalPlanV1
from casefile.agent_runtime.models import (
    BriefPolishCandidate,
    BriefPolishRequest,
    GenerationPlan,
)
from casefile.agent_runtime.providers import DeepSeekAgentsProvider, _run_auxiliary_agent
from casefile.agent_runtime.structured_output import (
    STRICT_OUTPUT_TOOL_NAME,
    StrictOutputProtocolError,
    StrictSchemaIneligible,
    StructuredCallResult,
    call_deepseek_strict_tool,
    compile_deepseek_strict_schema,
    pydantic_validation_issues,
    strict_fallback_reason,
    validate_model_json,
)
from casefile.contracts import ContractValidationError


class _StrictSchemaFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"]
    note: str | None = None
    label: str = Field(min_length=2, max_length=20)


class _OpenMapFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: dict[str, str]


def _request(events: list[tuple[str, str, dict[str, Any]]]) -> BriefPolishRequest:
    return BriefPolishRequest(
        task_run_id=101,
        prompt_version="brief-polish-v3",
        source_text="原稿",
        polish_mode="rewrite",
        input_hash="a" * 64,
        model_id="deepseek-v4-flash",
        api_key="sk-test",
        max_turns=2,
        emit=lambda event_type, stage, payload: events.append((event_type, stage, payload)),
    )


def _valid_polish_json() -> str:
    return json.dumps(
        {
            "polished_text": "修订稿",
            "preserved_intent_summary": "保留原意",
            "ambiguities": [],
            "introduced_details": [],
        },
        ensure_ascii=False,
    )


def _usage() -> SimpleNamespace:
    return SimpleNamespace(
        requests=1,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
        input_tokens_details=SimpleNamespace(cached_tokens=2),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )


def test_compile_strict_schema_closes_objects_and_preserves_nullable_shape() -> None:
    schema = compile_deepseek_strict_schema(_StrictSchemaFixture)
    properties = schema["properties"]

    assert schema["required"] == ["status", "note", "label"]
    assert schema["additionalProperties"] is False
    assert properties["status"]["enum"] == ["ok"]
    assert properties["note"]["anyOf"][1] == {"enum": [None]}
    assert "minLength" not in properties["label"]
    assert "maxLength" not in properties["label"]


def test_compile_strict_schema_rejects_open_maps_without_weakening_them() -> None:
    with pytest.raises(StrictSchemaIneligible, match="Open object"):
        compile_deepseek_strict_schema(_OpenMapFixture)


def test_temporal_plan_normalizes_only_zero_precision_suffixes() -> None:
    normalized_paths: list[str] = []
    result = validate_model_json(
        TemporalPlanV1,
        json.dumps(
            {
                "assignments": [
                    {
                        "event_key": "discovery",
                        "time": {
                            "kind": "exact",
                            "value": "2031-10-14T22:30:00",
                            "precision": "minute",
                        },
                        "basis": "design_anchor",
                        "basis_refs": [],
                    },
                    {
                        "event_key": "follow_up",
                        "time": {
                            "kind": "approximate",
                            "value": "2031-10-14T23:00",
                            "precision": "hour",
                        },
                        "basis": "design_anchor",
                        "basis_refs": [],
                    },
                ]
            }
        ),
        normalized_time_paths=normalized_paths,
    )

    assert result.assignments[0].time.value == "2031-10-14T22:30"
    assert result.assignments[1].time.value == "2031-10-14T23"
    assert normalized_paths == [
        "/assignments/0/time/value",
        "/assignments/1/time/value",
    ]


def test_temporal_plan_does_not_normalize_nonzero_or_timezone_suffixes() -> None:
    for value in ("2031-10-14T22:30:15", "2031-10-14T22:30+08:00"):
        with pytest.raises(ContractValidationError):
            validate_model_json(
                TemporalPlanV1,
                json.dumps(
                    {
                        "assignments": [
                            {
                                "event_key": "discovery",
                                "time": {
                                    "kind": "exact",
                                    "value": value,
                                    "precision": "minute",
                                },
                                "basis": "design_anchor",
                                "basis_refs": [],
                            }
                        ]
                    }
                ),
            )


def test_temporal_normalization_emits_an_observable_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, dict[str, Any]]] = []

    async def fake_runner_run(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            final_output=json.dumps(
                {
                    "assignments": [
                        {
                            "event_key": "discovery",
                            "time": {
                                "kind": "exact",
                                "value": "2031-10-14T22:30:00",
                                "precision": "minute",
                            },
                            "basis": "design_anchor",
                            "basis_refs": [],
                        }
                    ]
                }
            ),
            context_wrapper=SimpleNamespace(usage=_usage()),
        )

    monkeypatch.setattr(providers_module.Runner, "run", fake_runner_run)
    request = _request(events)
    output, _usage_result = asyncio.run(
        _run_auxiliary_agent(
            request,
            model=DeepSeekAgentsProvider().create_model(request),
            model_settings=ModelSettings(),
            instructions="Return JSON.",
            input_text="input",
            output_type=TemporalPlanV1,
            stage="temporal_planning",
            structured_output=False,
            tracing_disabled=True,
            component_id="temporal_structure_planner",
            schema_id="temporal-plan-v1",
            deepseek_output_protocol="json_object",
        )
    )

    assert output["assignments"][0]["time"]["value"] == "2031-10-14T22:30"
    normalized = next(
        event for event in events if event[0] == "validation.wall_clock_times_normalized"
    )
    assert normalized[2] == {
        "paths": ["/assignments/0/time/value"],
        "field_count": 1,
    }


def test_literal_validation_feedback_includes_allowed_enum_without_input() -> None:
    secret_invalid_value = "secret-invalid-collection"
    with pytest.raises(ValidationError) as caught:
        GenerationPlan.model_validate(
            {
                "title": "plan",
                "objects": [
                    {
                        "local_key": "item",
                        "collection": secret_invalid_value,
                        "title": "item",
                        "purpose": "test",
                        "referenced_keys": [],
                    }
                ],
            }
        )

    issues = pydantic_validation_issues(caught.value)

    assert "resolution_specs" in issues[0]["message"]
    assert secret_invalid_value not in repr(issues)


def test_plan_graph_validation_feedback_is_actionable_and_bounded() -> None:
    with pytest.raises(ValidationError) as caught:
        GenerationPlan.model_validate(
            {
                "title": "plan",
                "objects": [
                    {
                        "local_key": "entity_a",
                        "collection": "entities",
                        "title": "entity",
                        "purpose": "test",
                        "referenced_keys": ["missing_key"],
                    }
                ],
            }
        )

    issues = pydantic_validation_issues(caught.value)

    assert issues == [
        {
            "code": "value_error",
            "path": "",
            "message": "referenced_keys 只能引用同一计划中已声明的 local_key。",
        }
    ]


def test_strict_fallback_classifier_rejects_unknown_operational_errors() -> None:
    assert strict_fallback_reason(RuntimeError("network unavailable")) is None
    assert (
        strict_fallback_reason(
            StrictOutputProtocolError("strict_tool_missing", "missing required tool call")
        )
        == "strict_tool_missing"
    )


def test_deepseek_strict_call_uses_beta_forced_tool_and_validates_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeCompletions:
        async def create(self, **kwargs: Any) -> SimpleNamespace:
            captured["request"] = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            tool_calls=[
                                SimpleNamespace(
                                    type="function",
                                    function=SimpleNamespace(
                                        name=STRICT_OUTPUT_TOOL_NAME,
                                        arguments=_valid_polish_json(),
                                    ),
                                )
                            ]
                        )
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    prompt_cache_hit_tokens=2,
                ),
            )

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(structured_module, "AsyncOpenAI", FakeClient)
    result = asyncio.run(
        call_deepseek_strict_tool(
            api_key="sk-test",
            model_id="deepseek-v4-flash",
            network_retries=2,
            instructions="Return the result through the required tool.",
            input_text="input",
            output_type=BriefPolishCandidate,
        )
    )

    assert captured["client"]["base_url"] == "https://api.deepseek.com/beta"
    request = captured["request"]
    assert request["tools"][0]["function"]["strict"] is True
    assert request["tool_choice"]["function"]["name"] == STRICT_OUTPUT_TOOL_NAME
    assert request["parallel_tool_calls"] is False
    assert json.loads(result.raw_output)["polished_text"] == "修订稿"
    assert result.usage["requests"] == 1


def test_deepseek_protocol_fallback_then_repair_is_bounded_to_three_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, dict[str, Any]]] = []
    stable_outputs = iter(
        [
            json.dumps(
                {
                    "polished_text": "修订稿",
                    "ambiguities": [],
                    "introduced_details": [],
                }
            ),
            _valid_polish_json(),
        ]
    )
    stable_calls = 0

    async def fake_strict_call(**_kwargs: Any) -> StructuredCallResult:
        raise StrictOutputProtocolError("strict_tool_missing", "tool call missing")

    async def fake_runner_run(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        nonlocal stable_calls
        stable_calls += 1
        return SimpleNamespace(
            final_output=next(stable_outputs),
            context_wrapper=SimpleNamespace(usage=_usage()),
        )

    monkeypatch.setattr(providers_module, "call_deepseek_strict_tool", fake_strict_call)
    monkeypatch.setattr(providers_module.Runner, "run", fake_runner_run)
    request = _request(events)
    output, usage = asyncio.run(
        _run_auxiliary_agent(
            request,
            model=DeepSeekAgentsProvider().create_model(request),
            model_settings=ModelSettings(),
            instructions="Return JSON.",
            input_text="input",
            output_type=BriefPolishCandidate,
            stage="polishing",
            structured_output=False,
            tracing_disabled=True,
        )
    )

    assert output["preserved_intent_summary"] == "保留原意"
    assert stable_calls == 2
    assert usage["requests"] == 2
    assert any(event[0] == "model.output_protocol_fallback" for event in events)
    repair_event = next(event for event in events if event[0] == "model.output_repair_started")
    assert repair_event[2]["attempt_no"] == 3
    validated = next(event for event in events if event[0] == "model.output_validated")
    assert validated[2] == {
        "protocol": "json_object",
        "attempt_count": 3,
        "repaired": True,
    }


def test_openai_native_structured_output_retries_only_model_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, dict[str, Any]]] = []
    calls = 0

    class FakeResult:
        def __init__(self, valid: bool) -> None:
            self.valid = valid
            self.context_wrapper = SimpleNamespace(usage=_usage())

        def final_output_as(
            self,
            _output_type: type[BaseModel],
            *,
            raise_if_incorrect_type: bool,
        ) -> BriefPolishCandidate:
            assert raise_if_incorrect_type is True
            if not self.valid:
                raise ModelBehaviorError("invalid structured response")
            return BriefPolishCandidate.model_validate_json(_valid_polish_json())

    async def fake_runner_run(*_args: Any, **_kwargs: Any) -> FakeResult:
        nonlocal calls
        calls += 1
        return FakeResult(valid=calls == 2)

    monkeypatch.setattr(providers_module.Runner, "run", fake_runner_run)
    request = _request(events)
    output, usage = asyncio.run(
        _run_auxiliary_agent(
            request,
            model=DeepSeekAgentsProvider().create_model(request),
            model_settings=ModelSettings(),
            instructions="Return structured output.",
            input_text="input",
            output_type=BriefPolishCandidate,
            stage="polishing",
            structured_output=True,
            tracing_disabled=True,
        )
    )

    assert output["polished_text"] == "修订稿"
    assert calls == 2
    assert usage["requests"] == 2
    assert any(event[0] == "model.output_repair_started" for event in events)


def test_strict_schema_violation_preserves_stable_repair_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, dict[str, Any]]] = []
    strict_calls = 0
    stable_calls = 0
    stable_outputs = iter(
        [
            json.dumps(
                {
                    "polished_text": "修订稿",
                    "ambiguities": [],
                    "introduced_details": [],
                }
            ),
            _valid_polish_json(),
        ]
    )

    async def fake_strict_call(**_kwargs: Any) -> StructuredCallResult:
        nonlocal strict_calls
        strict_calls += 1
        return StructuredCallResult(
            raw_output=json.dumps(
                {
                    "polished_text": "修订稿",
                    "ambiguities": [],
                    "introduced_details": [],
                }
            ),
            usage={"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )

    async def fake_runner_run(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        nonlocal stable_calls
        stable_calls += 1
        return SimpleNamespace(
            final_output=next(stable_outputs),
            context_wrapper=SimpleNamespace(usage=_usage()),
        )

    monkeypatch.setattr(providers_module, "call_deepseek_strict_tool", fake_strict_call)
    monkeypatch.setattr(providers_module.Runner, "run", fake_runner_run)
    request = _request(events)
    output, usage = asyncio.run(
        _run_auxiliary_agent(
            request,
            model=DeepSeekAgentsProvider().create_model(request),
            model_settings=ModelSettings(),
            instructions="Return JSON.",
            input_text="input",
            output_type=BriefPolishCandidate,
            stage="polishing",
            structured_output=False,
            tracing_disabled=True,
            component_id="story_world",
            schema_id="story-world-ir-v1",
        )
    )

    assert output["preserved_intent_summary"] == "保留原意"
    assert strict_calls == 1
    assert stable_calls == 2
    assert usage["requests"] == 3
    fallback = next(event for event in events if event[0] == "model.output_protocol_fallback")
    assert fallback[2]["reason_code"] == "strict_schema_violation"
    failed_call = next(event for event in events if event[0] == "agent.model_call.failed")
    assert failed_call[2]["error_code"] == "structured_output_validation_failed"


def test_unknown_strict_error_does_not_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, str, dict[str, Any]]] = []

    async def fake_strict_call(**_kwargs: Any) -> StructuredCallResult:
        raise RuntimeError("authentication or transport failure")

    monkeypatch.setattr(providers_module, "call_deepseek_strict_tool", fake_strict_call)
    with pytest.raises(RuntimeError, match="transport failure"):
        request = _request(events)
        asyncio.run(
            _run_auxiliary_agent(
                request,
                model=DeepSeekAgentsProvider().create_model(request),
                model_settings=ModelSettings(),
                instructions="Return JSON.",
                input_text="input",
                output_type=BriefPolishCandidate,
                stage="polishing",
                structured_output=False,
                tracing_disabled=True,
            )
        )

    assert not any(event[0] == "model.output_protocol_fallback" for event in events)
