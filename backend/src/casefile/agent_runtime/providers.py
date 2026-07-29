"""Fake, OpenAI, and DeepSeek adapters for the three durable Brief tasks."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any, Protocol

from agents import Agent, ModelSettings, RunConfig, Runner
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from casefile_contracts import CaseFile
from openai import AsyncOpenAI
from openai.types.shared import Reasoning
from pydantic import BaseModel

from casefile.agent_runtime.models import (
    BriefAnchorExtractCandidate,
    BriefAnchorExtractRequest,
    BriefAnchorExtractResult,
    BriefPolishCandidate,
    BriefPolishRequest,
    BriefPolishResult,
    GenerationRequest,
    GenerationResult,
    ToolMetrics,
)
from casefile.agent_runtime.prompt import (
    ANCHOR_EXTRACT_INSTRUCTIONS,
    INSTRUCTIONS,
    POLISH_INSTRUCTIONS,
    anchor_extract_input,
    generation_input,
    polish_input,
)
from casefile.agent_runtime.tools import GENERATION_TOOLS, GenerationToolContext
from casefile.contracts import validate_casefile


class GenerationProvider(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResult: ...


class AgentProvider(GenerationProvider, Protocol):
    def polish(self, request: BriefPolishRequest) -> BriefPolishResult: ...

    def extract_anchors(
        self, request: BriefAnchorExtractRequest
    ) -> BriefAnchorExtractResult: ...


class ProviderProtocolError(RuntimeError):
    """The provider returned a structurally unusable result or skipped a required tool."""


class FakeProvider:
    """Zero-cost deterministic provider for tests and local acceptance runs."""

    def polish(self, request: BriefPolishRequest) -> BriefPolishResult:
        request.emit("model.started", "polishing", {"model_id": request.model_id})
        candidate = BriefPolishCandidate(
            polished_text=request.source_text.strip(),
            preserved_intent_summary="保留原稿事实、语气与未决含义，未补写新的创作设定。",
            ambiguities=[],
        )
        usage = _zero_usage()
        request.emit("model.completed", "polishing", {"usage": usage})
        return BriefPolishResult(candidate=candidate, usage=usage)

    def extract_anchors(
        self, request: BriefAnchorExtractRequest
    ) -> BriefAnchorExtractResult:
        request.emit("model.started", "extracting", {"model_id": request.model_id})
        answer = request.brief.get("author_answer")
        boundary = request.brief.get("boundary_text")
        anchors = [{"statement": value} for value in _atomic_statements(answer)]
        constraints = [
            {
                "statement": value,
                "suggested_strength": _suggested_strength(value),
            }
            for value in _atomic_statements(boundary)
        ]
        warnings: list[str] = []
        if answer and not anchors:
            warnings.append("作者底牌存在，但未能形成非空原子陈述。")
        if boundary and not constraints:
            warnings.append("创作边界存在，但未能形成非空原子约束。")
        candidate = BriefAnchorExtractCandidate.model_validate(
            {
                "author_anchors": anchors,
                "creative_constraints": constraints,
                "warnings": warnings,
            }
        )
        usage = _zero_usage()
        request.emit("model.completed", "extracting", {"usage": usage})
        return BriefAnchorExtractResult(candidate=candidate, usage=usage)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        request.emit("tool.started", "planning", {"tool": "plan_object_ids"})
        resolution_id = f"res_t{request.task_run_id}_01"
        constraints = _brief_constraints(request)
        constraint_ids = [
            f"con_t{request.task_run_id}_{index:02d}"
            for index in range(1, len(constraints) + 1)
        ]
        planned_ids = {resolution_id, *constraint_ids}
        metrics = ToolMetrics(
            calls=1,
            valid_calls=1,
            successful_calls=1,
            adopted_results=1,
            planned_object_ids=planned_ids,
        )
        request.emit(
            "tool.completed",
            "planning",
            {
                "tool": "plan_object_ids",
                "object_count": len(planned_ids),
                "collection_counts": {
                    "resolution_specs": 1,
                    "constraints": len(constraints),
                },
            },
        )
        now = datetime.now(UTC).isoformat()
        resolution_mode = request.brief["resolution_mode"]
        author_answer = request.brief["author_answer"]
        candidate: dict[str, Any] = {
            "schema_version": "1.0",
            "casefile_id": request.casefile_id,
            "title": request.brief["creative_intent"],
            "status": "draft",
            "version": {
                "version_id": request.version_id,
                "version_no": request.version_no,
                "parent_version_id": request.parent_version_id,
            },
            "brief_ref": {
                "brief_id": request.brief_id,
                "version": request.brief_version,
            },
            "resolution_specs": [
                {
                    "id": resolution_id,
                    "title": "核心推理命题",
                    "question_type": "causal_explanation",
                    "reasoning_question": request.brief["reasoning_proposition"],
                    "conclusion_mode": _conclusion_mode(resolution_mode),
                    "required_slots": [
                        {
                            "slot_id": "slot_core_answer",
                            "value_type": "text",
                            "required": True,
                        }
                    ],
                    "accepted_answers": (
                        [author_answer]
                        if resolution_mode == "author_anchored" and author_answer
                        else []
                    ),
                    "required_claim_refs": [],
                    **_metadata(now),
                }
            ],
            "entities": [],
            "relationships": [],
            "locations": [],
            "events": [],
            "information_units": [],
            "claims": [],
            "hypotheses": [],
            "reasoning_paths": [],
            "constraints": [
                {
                    "id": constraint_id,
                    "title": f"Brief 约束 {index}",
                    "level": item["level"],
                    "scope_refs": [
                        {
                            "object_type": "casefile",
                            "object_id": request.casefile_id,
                        }
                    ],
                    "statement": item["statement"],
                    "rule_expression": None,
                    "conflict_refs": [],
                    **_metadata(now),
                }
                for index, (constraint_id, item) in enumerate(
                    zip(constraint_ids, constraints, strict=True),
                    start=1,
                )
            ],
            "structure_locks": [],
            "content_notices": [],
            "extensions": {
                "casefile.brief": {
                    "resolution_mode": resolution_mode,
                    "source_record_ids": request.brief["source_record_ids"],
                }
            },
        }
        validate_casefile(candidate)
        return GenerationResult(
            candidate=candidate,
            usage=_zero_usage(),
            tools=metrics,
        )


class OpenAIAgentsProvider:
    """OpenAI Responses implementation with structured outputs."""

    def polish(self, request: BriefPolishRequest) -> BriefPolishResult:
        if not request.api_key:
            raise ProviderProtocolError("OpenAI API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=POLISH_INSTRUCTIONS,
                input_text=polish_input(request.source_text, request.input_hash),
                output_type=BriefPolishCandidate,
                stage="polishing",
            )
        )
        return BriefPolishResult(
            candidate=BriefPolishCandidate.model_validate(candidate),
            usage=usage,
        )

    def extract_anchors(
        self, request: BriefAnchorExtractRequest
    ) -> BriefAnchorExtractResult:
        if not request.api_key:
            raise ProviderProtocolError("OpenAI API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=ANCHOR_EXTRACT_INSTRUCTIONS,
                input_text=anchor_extract_input(request.brief, request.input_hash),
                output_type=BriefAnchorExtractCandidate,
                stage="extracting",
            )
        )
        return BriefAnchorExtractResult(
            candidate=BriefAnchorExtractCandidate.model_validate(candidate),
            usage=usage,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not request.api_key:
            raise ProviderProtocolError("OpenAI API key is required")
        return asyncio.run(self._generate(request))

    async def _generate(self, request: GenerationRequest) -> GenerationResult:
        client = AsyncOpenAI(api_key=request.api_key, max_retries=2)
        model = OpenAIResponsesModel(model=request.model_id, openai_client=client)
        return await _run_agent(
            request,
            model=model,
            model_settings=ModelSettings(
                reasoning=Reasoning(effort="medium"),
                verbosity="low",
                include_usage=True,
                parallel_tool_calls=False,
            ),
            structured_output=True,
            tracing_disabled=False,
        )

    async def _run_auxiliary(
        self,
        request: BriefPolishRequest | BriefAnchorExtractRequest,
        *,
        instructions: str,
        input_text: str,
        output_type: type[BaseModel],
        stage: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        client = AsyncOpenAI(api_key=request.api_key, max_retries=2)
        model = OpenAIResponsesModel(model=request.model_id, openai_client=client)
        return await _run_auxiliary_agent(
            request,
            model=model,
            model_settings=ModelSettings(
                reasoning=Reasoning(effort="low"),
                verbosity="low",
                include_usage=True,
                parallel_tool_calls=False,
            ),
            instructions=instructions,
            input_text=input_text,
            output_type=output_type,
            stage=stage,
            structured_output=True,
            tracing_disabled=False,
        )


class DeepSeekAgentsProvider:
    """DeepSeek OpenAI-compatible Chat Completions implementation."""

    base_url = "https://api.deepseek.com"

    def polish(self, request: BriefPolishRequest) -> BriefPolishResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=POLISH_INSTRUCTIONS,
                input_text=polish_input(request.source_text, request.input_hash),
                output_type=BriefPolishCandidate,
                stage="polishing",
            )
        )
        return BriefPolishResult(
            candidate=BriefPolishCandidate.model_validate(candidate),
            usage=usage,
        )

    def extract_anchors(
        self, request: BriefAnchorExtractRequest
    ) -> BriefAnchorExtractResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=ANCHOR_EXTRACT_INSTRUCTIONS,
                input_text=anchor_extract_input(request.brief, request.input_hash),
                output_type=BriefAnchorExtractCandidate,
                stage="extracting",
            )
        )
        return BriefAnchorExtractResult(
            candidate=BriefAnchorExtractCandidate.model_validate(candidate),
            usage=usage,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        return asyncio.run(self._generate(request))

    async def _generate(self, request: GenerationRequest) -> GenerationResult:
        model = self.create_model(request)
        return await _run_agent(
            request,
            model=model,
            model_settings=_deepseek_model_settings(),
            structured_output=False,
            tracing_disabled=True,
        )

    async def _run_auxiliary(
        self,
        request: BriefPolishRequest | BriefAnchorExtractRequest,
        *,
        instructions: str,
        input_text: str,
        output_type: type[BaseModel],
        stage: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        model = self.create_model(request)
        return await _run_auxiliary_agent(
            request,
            model=model,
            model_settings=_deepseek_model_settings(),
            instructions=instructions,
            input_text=input_text,
            output_type=output_type,
            stage=stage,
            structured_output=False,
            tracing_disabled=True,
        )

    def create_model(
        self,
        request: GenerationRequest | BriefPolishRequest | BriefAnchorExtractRequest,
    ) -> OpenAIChatCompletionsModel:
        client = AsyncOpenAI(
            api_key=request.api_key,
            base_url=self.base_url,
            max_retries=2,
        )
        return OpenAIChatCompletionsModel(
            model=request.model_id,
            openai_client=client,
        )


async def _run_auxiliary_agent(
    request: BriefPolishRequest | BriefAnchorExtractRequest,
    *,
    model: OpenAIResponsesModel | OpenAIChatCompletionsModel,
    model_settings: ModelSettings,
    instructions: str,
    input_text: str,
    output_type: type[BaseModel],
    stage: str,
    structured_output: bool,
    tracing_disabled: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    resolved_instructions = instructions
    agent_output_type: Any = output_type
    if not structured_output:
        resolved_instructions += _json_schema_instruction(output_type)
        agent_output_type = str
    agent: Agent[Any] = Agent(
        name="CaseFile Brief Assistant",
        instructions=resolved_instructions,
        model=model,
        model_settings=model_settings,
        tools=[],
        output_type=agent_output_type,
    )
    request.emit("model.started", stage, {"model_id": request.model_id})
    result = await Runner.run(
        agent,
        input_text,
        max_turns=request.max_turns,
        run_config=RunConfig(
            workflow_name=f"CaseFile {stage}",
            tracing_disabled=tracing_disabled,
            trace_include_sensitive_data=False,
        ),
    )
    if structured_output:
        output = result.final_output_as(output_type, raise_if_incorrect_type=True)
    else:
        if not isinstance(result.final_output, str):
            raise ProviderProtocolError("DeepSeek auxiliary output must be a JSON string")
        try:
            output = output_type.model_validate_json(result.final_output)
        except ValueError as error:
            raise ProviderProtocolError("DeepSeek returned invalid auxiliary JSON") from error
    usage = _usage_json(result.context_wrapper.usage)
    request.emit("model.completed", stage, {"usage": usage})
    return output.model_dump(mode="json"), usage


async def _run_agent(
    request: GenerationRequest,
    *,
    model: OpenAIResponsesModel | OpenAIChatCompletionsModel,
    model_settings: ModelSettings,
    structured_output: bool,
    tracing_disabled: bool,
) -> GenerationResult:
    context = GenerationToolContext(request=request)
    output_type: Any = CaseFile if structured_output else str
    instructions = INSTRUCTIONS
    if not structured_output:
        instructions += _json_schema_instruction(CaseFile)
    agent = Agent[GenerationToolContext](
        name="CaseFile Architect",
        instructions=instructions,
        model=model,
        model_settings=model_settings,
        tools=GENERATION_TOOLS,
        output_type=output_type,
    )
    request.emit("model.started", "generating", {"model_id": request.model_id})
    result = await Runner.run(
        agent,
        generation_input(request),
        context=context,
        max_turns=request.max_turns,
        run_config=RunConfig(
            workflow_name="CaseFile brief_to_draft",
            tracing_disabled=tracing_disabled,
            trace_include_sensitive_data=False,
        ),
    )
    if context.plan_calls != 1:
        raise ProviderProtocolError("plan_object_ids must be called exactly once")
    if structured_output:
        output = result.final_output_as(CaseFile, raise_if_incorrect_type=True)
    else:
        if not isinstance(result.final_output, str):
            raise ProviderProtocolError("DeepSeek final output must be a JSON string")
        try:
            output = CaseFile.model_validate_json(result.final_output)
        except ValueError as error:
            raise ProviderProtocolError("DeepSeek returned invalid CaseFile JSON") from error
    candidate = _remove_absent_descriptions(output.model_dump(mode="json"))
    validate_casefile(candidate)
    candidate_ids = _candidate_object_ids(candidate)
    if context.metrics.planned_object_ids.issubset(candidate_ids):
        context.metrics.adopted_results += 1
    if context.validation_calls and context.metrics.successful_calls > 1:
        context.metrics.adopted_results += 1
    usage_json = _usage_json(result.context_wrapper.usage)
    request.emit("model.completed", "generating", {"usage": usage_json})
    return GenerationResult(candidate=candidate, usage=usage_json, tools=context.metrics)


def _metadata(updated_at: str) -> dict[str, Any]:
    return {
        "tags": [],
        "source_refs": [],
        "confidence": None,
        "confirmation_status": "ai_inferred",
        "created_by": {"actor_type": "agent", "actor_id": "agent_casefile_generator"},
        "updated_at": updated_at,
        "revision": 1,
    }


def _candidate_object_ids(candidate: dict[str, Any]) -> set[str]:
    keys = (
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
    return {item["id"] for key in keys for item in candidate[key]}


def _remove_absent_descriptions(value: Any) -> Any:
    if isinstance(value, list):
        return [_remove_absent_descriptions(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _remove_absent_descriptions(item)
            for key, item in value.items()
            if not (key == "description" and item is None)
        }
    return value


def _deepseek_model_settings() -> ModelSettings:
    return ModelSettings(
        include_usage=True,
        parallel_tool_calls=False,
        extra_args={"response_format": {"type": "json_object"}},
        extra_body={"thinking": {"type": "disabled"}},
    )


def _json_schema_instruction(output_type: type[BaseModel]) -> str:
    schema = json.dumps(
        output_type.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "\nThe final response must be exactly one JSON object matching the following JSON Schema. "
        "Use the exact property names; do not copy provenance input fields into the output; "
        "do not wrap the object in Markdown or add commentary.\n"
        + schema
    )


def _usage_json(usage: Any) -> dict[str, Any]:
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return {
        "requests": int(getattr(usage, "requests", 0) or 0),
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        "cached_tokens": int(getattr(input_details, "cached_tokens", 0) or 0),
        "reasoning_tokens": int(getattr(output_details, "reasoning_tokens", 0) or 0),
    }


def _zero_usage() -> dict[str, int]:
    return {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
    }


def _atomic_statements(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    normalized = value.strip()
    if not normalized:
        return []
    parts = re.split(r"(?:\r?\n)+|(?<=[。！？；;])\s*", normalized)
    result: list[str] = []
    for part in parts:
        statement = part.strip()
        if statement and statement not in result:
            result.append(statement)
    return result


def _suggested_strength(statement: str) -> str:
    hard_markers = ("必须", "不得", "不能", "禁止", "务必", "must", "never", "shall")
    lowered = statement.lower()
    return "hard" if any(marker in lowered for marker in hard_markers) else "soft"


def _brief_constraints(request: GenerationRequest) -> list[dict[str, str]]:
    result = [
        {"statement": item["statement"], "level": "hard"}
        for item in request.brief["author_anchors"]
    ]
    result.extend(
        {
            "statement": item["statement"],
            "level": item["strength"],
        }
        for item in request.brief["creative_constraints"]
    )
    return result


def _conclusion_mode(resolution_mode: str) -> str:
    if resolution_mode == "author_anchored":
        return "unique"
    if resolution_mode == "open":
        return "open_interpretation"
    return "undetermined"


__all__ = [
    "AgentProvider",
    "DeepSeekAgentsProvider",
    "FakeProvider",
    "GenerationProvider",
    "OpenAIAgentsProvider",
]
