"""Fake, OpenAI, and DeepSeek adapters for durable CaseFile Agent tasks."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any, Protocol

from agents import Agent, ModelSettings, RunConfig, Runner
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI
from openai.types.shared import Reasoning
from pydantic import BaseModel, ValidationError

from casefile.agent_runtime.models import (
    BriefAnchorExtractCandidate,
    BriefAnchorExtractRequest,
    BriefAnchorExtractResult,
    BriefIntakeQuestionsRequest,
    BriefIntakeQuestionsResult,
    BriefIntakeSynthesizeRequest,
    BriefIntakeSynthesizeResult,
    BriefPolishCandidate,
    BriefPolishRequest,
    BriefPolishResult,
    CaseFileChatCandidate,
    CaseFileChatRequest,
    CaseFileChatResult,
    GenerationRequest,
    GenerationResult,
    ToolMetrics,
)
from casefile.agent_runtime.prompt import (
    anchor_extract_input,
    brief_intake_questions_input,
    brief_intake_synthesize_input,
    casefile_chat_input,
    generation_input,
    polish_input,
)
from casefile.agent_runtime.prompt_repository import system_prompt_for_task
from casefile.agent_runtime.tools import GENERATION_TOOLS, GenerationToolContext
from casefile.contracts import ContractValidationError, validate_casefile
from casefile_contracts import (
    BriefIntakeCandidate as BriefIntakeCandidateContract,
)
from casefile_contracts import (
    BriefIntakeQuestionSet as BriefIntakeQuestionSetContract,
)
from casefile_contracts import (
    CaseFile,
)


class GenerationProvider(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResult: ...


class AgentProvider(GenerationProvider, Protocol):
    def polish(self, request: BriefPolishRequest) -> BriefPolishResult: ...

    def extract_anchors(
        self, request: BriefAnchorExtractRequest
    ) -> BriefAnchorExtractResult: ...

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult: ...

    def intake_questions(
        self, request: BriefIntakeQuestionsRequest
    ) -> BriefIntakeQuestionsResult: ...

    def synthesize_intake(
        self, request: BriefIntakeSynthesizeRequest
    ) -> BriefIntakeSynthesizeResult: ...


class ProviderProtocolError(RuntimeError):
    """The provider returned a structurally unusable result or skipped a required tool."""


class FakeProvider:
    """Zero-cost deterministic provider for tests and local acceptance runs."""

    def polish(self, request: BriefPolishRequest) -> BriefPolishResult:
        system_prompt_for_task("brief_polish", request.prompt_version)
        request.emit("model.started", "polishing", {"model_id": request.model_id})
        candidate = BriefPolishCandidate(
            polished_text=request.source_text.strip(),
            preserved_intent_summary="保留原稿事实、语气与未决含义，未补写新的创作设定。",
            ambiguities=[],
            introduced_details=[],
        )
        usage = _zero_usage()
        request.emit("model.completed", "polishing", {"usage": usage})
        return BriefPolishResult(
            candidate=candidate,
            usage=usage,
            polish_mode=request.polish_mode,
        )

    def extract_anchors(
        self, request: BriefAnchorExtractRequest
    ) -> BriefAnchorExtractResult:
        system_prompt_for_task("brief_anchor_extract", request.prompt_version)
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

    def intake_questions(
        self, request: BriefIntakeQuestionsRequest
    ) -> BriefIntakeQuestionsResult:
        system_prompt_for_task("brief_intake_questions", request.prompt_version)
        request.emit("model.started", "questioning", {"model_id": request.model_id})
        candidate = BriefIntakeQuestionSetContract.model_validate(
            {
                "questions": [
                    {
                        "question_key": "question_resolution_direction",
                        "ordinal": 1,
                        "prompt": "你希望真相由你预先确定，还是由 Agent 提出候选？",
                        "impact": "这会决定结论模式，以及是否需要作者底牌。",
                        "required": True,
                        "suggestions": ["由 Agent 提出候选", "保持开放，不预设唯一结论"],
                    },
                    {
                        "question_key": "question_scope",
                        "ordinal": 2,
                        "prompt": "你预计采用多大规模？",
                        "impact": "这会影响内容骨架和角色数量，但不改变核心解答。",
                        "required": False,
                        "suggestions": ["中篇，4 名核心角色"],
                    },
                ]
            }
        )
        usage = _zero_usage()
        request.emit("model.completed", "questioning", {"usage": usage})
        return BriefIntakeQuestionsResult(candidate=candidate, usage=usage)

    def synthesize_intake(
        self, request: BriefIntakeSynthesizeRequest
    ) -> BriefIntakeSynthesizeResult:
        system_prompt_for_task("brief_intake_synthesize", request.prompt_version)
        request.emit("model.started", "synthesizing", {"model_id": request.model_id})
        source = request.input_data.get("source")
        source_text = (
            str(source.get("content_text", "")).strip()
            if isinstance(source, dict)
            else ""
        )
        concept = source_text.splitlines()[0][:1000].strip() or "尚待作者补充的创作概念"
        raw_questions = request.input_data.get("questions")
        questions = raw_questions if isinstance(raw_questions, list) else []
        pending = [
            {
                "decision_key": str(item.get("question_key", "question_pending")).replace(
                    "question_", "decision_", 1
                ),
                "prompt": str(item.get("prompt", "待决定事项")),
                "impact": str(item.get("impact", "影响后续内容组织")),
                "source": "unresolved",
            }
            for item in questions
            if isinstance(item, dict)
            and not bool(item.get("required"))
            and item.get("answer_status") in {"unanswered", "pending"}
        ]
        candidate = BriefIntakeCandidateContract.model_validate(
            {
                "concept": concept,
                "core_selling_points": ["围绕原始设想建立可验证的推理链。"],
                "content_outline": ["建立核心谜面", "逐步验证线索", "审阅候选结论"],
                "reasoning_goal": "解释核心异常如何发生，并形成可由作者审阅的结论。",
                "resolution_mode": "agent_proposed",
                "author_answer": None,
                "constraints": [],
                "pending_decisions": pending,
                "scope_estimate": "中篇，4 名核心角色，6 至 8 个主要场景。",
                "risk_notes": ["需要在正式审阅中确认结论模式与硬约束。"],
                "field_sources": {
                    "concept": "user_original",
                    "core_selling_points": "agent_suggestion",
                    "content_outline": "agent_suggestion",
                    "reasoning_goal": "agent_suggestion",
                    "resolution_mode": "agent_suggestion",
                    "author_answer": "unresolved",
                    "constraints": "unresolved",
                    "scope_estimate": "agent_suggestion",
                    "risk_notes": "agent_suggestion",
                },
            }
        )
        usage = _zero_usage()
        request.emit("model.completed", "synthesizing", {"usage": usage})
        return BriefIntakeSynthesizeResult(candidate=candidate, usage=usage)

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        system_prompt_for_task("casefile_chat", request.prompt_version)
        request.emit("model.started", "responding", {"model_id": request.model_id})
        referenced = [
            object_id
            for object_id in _casefile_object_ids(request.casefile)
            if object_id in request.message
        ]
        candidate = CaseFileChatCandidate(
            answer=(
                "我已结合当前完整卷宗阅读了这条消息。"
                "本次没有自动修改工作稿；如需改动，我会先给出可逐项审阅的字段建议。"
            ),
            referenced_object_ids=referenced,
            suggestions=[],
        )
        usage = _zero_usage()
        request.emit("model.completed", "responding", {"usage": usage})
        return CaseFileChatResult(candidate=candidate, usage=usage)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        system_prompt_for_task("brief_to_draft", request.prompt_version)
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
                    "description": "说明本案需要回答的核心问题，以及最终解答应覆盖的因果范围。",
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
                    "description": f"生成与后续编辑均需遵守的创作边界：{item['statement']}",
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
        _validate_generated_descriptions(candidate)
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
                instructions=system_prompt_for_task(
                    "brief_polish",
                    request.prompt_version,
                ),
                input_text=polish_input(
                    request.source_text,
                    request.input_hash,
                    request.polish_mode,
                ),
                output_type=BriefPolishCandidate,
                stage="polishing",
            )
        )
        polish_candidate = BriefPolishCandidate.model_validate(candidate)
        _validate_polish_candidate(polish_candidate, request.polish_mode)
        return BriefPolishResult(
            candidate=polish_candidate,
            usage=usage,
            polish_mode=request.polish_mode,
        )

    def extract_anchors(
        self, request: BriefAnchorExtractRequest
    ) -> BriefAnchorExtractResult:
        if not request.api_key:
            raise ProviderProtocolError("OpenAI API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "brief_anchor_extract",
                    request.prompt_version,
                ),
                input_text=anchor_extract_input(request.brief, request.input_hash),
                output_type=BriefAnchorExtractCandidate,
                stage="extracting",
            )
        )
        return BriefAnchorExtractResult(
            candidate=BriefAnchorExtractCandidate.model_validate(candidate),
            usage=usage,
        )

    def intake_questions(
        self, request: BriefIntakeQuestionsRequest
    ) -> BriefIntakeQuestionsResult:
        if not request.api_key:
            raise ProviderProtocolError("OpenAI API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "brief_intake_questions", request.prompt_version
                ),
                input_text=brief_intake_questions_input(
                    request.source_text, request.input_hash
                ),
                output_type=BriefIntakeQuestionSetContract,
                stage="questioning",
            )
        )
        return BriefIntakeQuestionsResult(
            candidate=BriefIntakeQuestionSetContract.model_validate(candidate),
            usage=usage,
        )

    def synthesize_intake(
        self, request: BriefIntakeSynthesizeRequest
    ) -> BriefIntakeSynthesizeResult:
        if not request.api_key:
            raise ProviderProtocolError("OpenAI API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "brief_intake_synthesize", request.prompt_version
                ),
                input_text=brief_intake_synthesize_input(
                    request.input_data, request.input_hash
                ),
                output_type=BriefIntakeCandidateContract,
                stage="synthesizing",
            )
        )
        return BriefIntakeSynthesizeResult(
            candidate=BriefIntakeCandidateContract.model_validate(candidate),
            usage=usage,
        )

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        if not request.api_key:
            raise ProviderProtocolError("OpenAI API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "casefile_chat",
                    request.prompt_version,
                ),
                input_text=casefile_chat_input(request),
                output_type=CaseFileChatCandidate,
                stage="responding",
            )
        )
        return CaseFileChatResult(
            candidate=CaseFileChatCandidate.model_validate(candidate),
            usage=usage,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not request.api_key:
            raise ProviderProtocolError("OpenAI API key is required")
        return asyncio.run(self._generate(request))

    async def _generate(self, request: GenerationRequest) -> GenerationResult:
        client = AsyncOpenAI(
            api_key=request.api_key,
            max_retries=request.network_retries,
        )
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
        request: (
            BriefPolishRequest
            | BriefAnchorExtractRequest
            | BriefIntakeQuestionsRequest
            | BriefIntakeSynthesizeRequest
            | CaseFileChatRequest
        ),
        *,
        instructions: str,
        input_text: str,
        output_type: type[BaseModel],
        stage: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        client = AsyncOpenAI(
            api_key=request.api_key,
            max_retries=request.network_retries,
        )
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
                instructions=system_prompt_for_task(
                    "brief_polish",
                    request.prompt_version,
                ),
                input_text=polish_input(
                    request.source_text,
                    request.input_hash,
                    request.polish_mode,
                ),
                output_type=BriefPolishCandidate,
                stage="polishing",
            )
        )
        polish_candidate = BriefPolishCandidate.model_validate(candidate)
        _validate_polish_candidate(polish_candidate, request.polish_mode)
        return BriefPolishResult(
            candidate=polish_candidate,
            usage=usage,
            polish_mode=request.polish_mode,
        )

    def extract_anchors(
        self, request: BriefAnchorExtractRequest
    ) -> BriefAnchorExtractResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "brief_anchor_extract",
                    request.prompt_version,
                ),
                input_text=anchor_extract_input(request.brief, request.input_hash),
                output_type=BriefAnchorExtractCandidate,
                stage="extracting",
            )
        )
        return BriefAnchorExtractResult(
            candidate=BriefAnchorExtractCandidate.model_validate(candidate),
            usage=usage,
        )

    def intake_questions(
        self, request: BriefIntakeQuestionsRequest
    ) -> BriefIntakeQuestionsResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "brief_intake_questions", request.prompt_version
                ),
                input_text=brief_intake_questions_input(
                    request.source_text, request.input_hash
                ),
                output_type=BriefIntakeQuestionSetContract,
                stage="questioning",
            )
        )
        return BriefIntakeQuestionsResult(
            candidate=BriefIntakeQuestionSetContract.model_validate(candidate),
            usage=usage,
        )

    def synthesize_intake(
        self, request: BriefIntakeSynthesizeRequest
    ) -> BriefIntakeSynthesizeResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "brief_intake_synthesize", request.prompt_version
                ),
                input_text=brief_intake_synthesize_input(
                    request.input_data, request.input_hash
                ),
                output_type=BriefIntakeCandidateContract,
                stage="synthesizing",
            )
        )
        return BriefIntakeSynthesizeResult(
            candidate=BriefIntakeCandidateContract.model_validate(candidate),
            usage=usage,
        )

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "casefile_chat",
                    request.prompt_version,
                ),
                input_text=casefile_chat_input(request),
                output_type=CaseFileChatCandidate,
                stage="responding",
            )
        )
        return CaseFileChatResult(
            candidate=CaseFileChatCandidate.model_validate(candidate),
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
        request: (
            BriefPolishRequest
            | BriefAnchorExtractRequest
            | BriefIntakeQuestionsRequest
            | BriefIntakeSynthesizeRequest
            | CaseFileChatRequest
        ),
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
        request: (
            GenerationRequest
            | BriefPolishRequest
            | BriefAnchorExtractRequest
            | BriefIntakeQuestionsRequest
            | BriefIntakeSynthesizeRequest
            | CaseFileChatRequest
        ),
    ) -> OpenAIChatCompletionsModel:
        client = AsyncOpenAI(
            api_key=request.api_key,
            base_url=self.base_url,
            max_retries=request.network_retries,
        )
        return OpenAIChatCompletionsModel(
            model=request.model_id,
            openai_client=client,
        )


async def _run_auxiliary_agent(
    request: (
        BriefPolishRequest
        | BriefAnchorExtractRequest
        | BriefIntakeQuestionsRequest
        | BriefIntakeSynthesizeRequest
        | CaseFileChatRequest
    ),
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
        name="CaseFile Editorial Assistant",
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
    instructions = system_prompt_for_task("brief_to_draft", request.prompt_version)
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
        except ValidationError as error:
            raise ContractValidationError(_pydantic_validation_issues(error)) from error
    candidate = _remove_absent_descriptions(output.model_dump(mode="json"))
    validate_casefile(candidate)
    _validate_generated_descriptions(candidate)
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


def _casefile_object_ids(casefile: dict[str, Any]) -> list[str]:
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
    return [
        str(item["id"])
        for key in keys
        for item in casefile.get(key, [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]


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


def _validate_generated_descriptions(candidate: dict[str, Any]) -> None:
    issues: list[dict[str, str]] = []
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
    ):
        for index, item in enumerate(candidate.get(collection, [])):
            description = item.get("description") if isinstance(item, dict) else None
            if not isinstance(description, str) or not description.strip():
                issues.append(
                    {
                        "code": "generated_description_missing",
                        "path": f"/{collection}/{index}/description",
                        "message": "Agent-generated objects require a non-empty description",
                    }
                )
    if issues:
        raise ContractValidationError(issues)


def _pydantic_validation_issues(error: ValidationError) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for item in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        issue_type = str(item.get("type", "schema_invalid"))
        code = "candidate_json_invalid" if issue_type == "json_invalid" else issue_type
        path = _json_pointer(item.get("loc", ()))
        issues.append(
            {
                "code": code,
                "path": path,
                "message": str(item.get("msg", "CaseFile schema validation failed")),
            }
        )
    return issues or [
        {
            "code": "schema_invalid",
            "path": "",
            "message": "CaseFile schema validation failed",
        }
    ]


def _json_pointer(parts: Any) -> str:
    escaped = [
        str(part).replace("~", "~0").replace("/", "~1")
        for part in parts
    ]
    return "" if not escaped else "/" + "/".join(escaped)


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
        "\n最终响应必须是严格匹配以下 JSON Schema 的一个 JSON 对象。"
        "必须使用准确的属性名；不得把来源追踪输入字段复制到输出；"
        "不得使用 Markdown 包装，也不得添加任何额外说明。\n"
        + schema
    )


def _validate_polish_candidate(
    candidate: BriefPolishCandidate,
    polish_mode: str,
) -> None:
    if polish_mode != "narrative_enhance" and candidate.introduced_details:
        raise ProviderProtocolError(
            "Polish candidate introduced new details outside narrative enhancement mode"
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
