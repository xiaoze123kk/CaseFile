"""Fake, OpenAI, and DeepSeek adapters for durable CaseFile Agent tasks."""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import UTC, datetime
from hashlib import sha256
from time import perf_counter
from typing import Any, Literal, Protocol, cast

from agents import Agent, ModelSettings, RunConfig, Runner
from agents.exceptions import ModelBehaviorError
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.models.openai_responses import OpenAIResponsesModel
from casefile_contracts import (
    BriefIntakeCandidate as BriefIntakeCandidateContract,
)
from casefile_contracts import (
    BriefIntakeQuestionSet as BriefIntakeQuestionSetContract,
)
from casefile_contracts import (
    CaseFile,
)
from openai import AsyncOpenAI
from openai.types.shared import Reasoning
from pydantic import BaseModel, create_model

from casefile.agent_runtime.brief_to_draft_v8.workflow import run_v8_generation
from casefile.agent_runtime.brief_to_draft_v9.workflow import run_v9_generation
from casefile.agent_runtime.models import (
    CANDIDATE_STRATEGY_VERSION,
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
    BriefStrategyOptionsCandidate,
    BriefStrategyOptionsRequest,
    BriefStrategyOptionsResult,
    CandidateStrategy,
    CaseFileChatCandidate,
    CaseFileChatRequest,
    CaseFileChatResult,
    GenerationPlan,
    GenerationRequest,
    GenerationResult,
    StrictAgentOutput,
    ToolMetrics,
)
from casefile.agent_runtime.prompt import (
    anchor_extract_input,
    brief_intake_questions_input,
    brief_intake_synthesize_input,
    brief_strategy_options_input,
    casefile_chat_input,
    generation_input,
    polish_input,
)
from casefile.agent_runtime.prompt_repository import system_prompt_for_task
from casefile.agent_runtime.structured_output import (
    call_deepseek_strict_tool,
    compile_deepseek_strict_schema,
    repair_input,
    strict_fallback_reason,
)
from casefile.agent_runtime.structured_output import (
    merge_usage as _merge_structured_usage,
)
from casefile.agent_runtime.structured_output import (
    validate_model_json as _validate_auxiliary_output,
)
from casefile.agent_runtime.tools import GENERATION_TOOLS, GenerationToolContext
from casefile.contracts import ContractValidationError, validate_casefile
from casefile.contracts.validation import COLLECTION_OBJECT_TYPES


class GenerationProvider(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResult: ...


class AgentProvider(GenerationProvider, Protocol):
    def polish(self, request: BriefPolishRequest) -> BriefPolishResult: ...

    def extract_anchors(self, request: BriefAnchorExtractRequest) -> BriefAnchorExtractResult: ...

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult: ...

    def intake_questions(
        self, request: BriefIntakeQuestionsRequest
    ) -> BriefIntakeQuestionsResult: ...

    def synthesize_intake(
        self, request: BriefIntakeSynthesizeRequest
    ) -> BriefIntakeSynthesizeResult: ...

    def strategy_options(
        self, request: BriefStrategyOptionsRequest
    ) -> BriefStrategyOptionsResult: ...


class ProviderProtocolError(RuntimeError):
    """The provider returned a structurally unusable result or skipped a required tool."""


_PARTITION_FIELDS: dict[str, tuple[str, ...]] = {
    "story": ("entities", "relationships", "locations", "events"),
    "reasoning": (
        "information_units",
        "claims",
        "hypotheses",
        "reasoning_paths",
    ),
    "governance": (
        "resolution_specs",
        "constraints",
        "structure_locks",
        "content_notices",
        "extensions",
    ),
}

_COLLECTION_PREFIXES = {
    "resolution_specs": "res",
    "entities": "ent",
    "relationships": "rel",
    "locations": "loc",
    "events": "evt",
    "information_units": "info",
    "claims": "claim",
    "hypotheses": "hyp",
    "reasoning_paths": "path",
    "constraints": "con",
    "structure_locks": "lock",
}


def _partition_output_model(partition: str) -> type[BaseModel]:
    fields: dict[str, tuple[Any, Any]] = {}
    for field_name in _PARTITION_FIELDS[partition]:
        model_field = CaseFile.model_fields[field_name]
        fields[field_name] = (model_field.annotation, ...)
    return cast(
        type[BaseModel],
        create_model(  # type: ignore[call-overload]
            f"CaseFile{partition.title()}Partition",
            __base__=StrictAgentOutput,
            **fields,
        ),
    )


_PARTITION_MODELS = {
    partition: _partition_output_model(partition) for partition in _PARTITION_FIELDS
}


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

    def extract_anchors(self, request: BriefAnchorExtractRequest) -> BriefAnchorExtractResult:
        system_prompt_for_task("brief_anchor_extract", request.prompt_version)
        request.emit("model.started", "extracting", {"model_id": request.model_id})
        answer = request.brief.get("author_answer")
        boundary = request.brief.get("boundary_text")
        suggested_author_answer = None
        if request.mode == "suggest_author_answer":
            proposition = str(request.brief.get("reasoning_proposition") or "核心真相").strip()
            intent = str(request.brief.get("creative_intent") or "当前创意").strip()
            suggested_author_answer = (
                f"作者预设“{proposition}”的真相在结局中被明确揭示，"
                f"并由“{intent}”中的关键线索承担验证。"
            )
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
                "suggested_author_answer": suggested_author_answer,
                "author_anchors": anchors,
                "creative_constraints": constraints,
                "warnings": warnings,
            }
        )
        usage = _zero_usage()
        request.emit("model.completed", "extracting", {"usage": usage})
        return BriefAnchorExtractResult(candidate=candidate, usage=usage)

    def strategy_options(self, request: BriefStrategyOptionsRequest) -> BriefStrategyOptionsResult:
        system_prompt_for_task("brief_strategy_options", request.prompt_version)
        request.emit("model.started", "analyzing_strategies", {"model_id": request.model_id})
        intent = str(request.brief.get("creative_intent", "当前创意")).strip()
        proposition = str(request.brief.get("reasoning_proposition", "核心问题")).strip()
        candidate = BriefStrategyOptionsCandidate.model_validate(
            {
                "strategy_version": CANDIDATE_STRATEGY_VERSION,
                "options": [
                    {
                        "strategy": "structure_first",
                        "direction": f"围绕“{intent}”先搭建阶段、对象与因果骨架。",
                        "focus": "让事件顺序、对象关系和推进节点首先清晰可审阅。",
                        "strengths": ["结构稳定，便于继续扩写", "引用与因果关系更容易校验"],
                        "tradeoffs": ["首稿的感官细节会相对克制"],
                        "brief_fit": f"适合先稳定当前推理命题“{proposition}”的承载结构。",
                    },
                    {
                        "strategy": "atmosphere_first",
                        "direction": f"从“{intent}”的场景质感、人物张力和节奏进入。",
                        "focus": "优先形成可感知的场景与情绪线索，同时保留事实边界。",
                        "strengths": ["阅读体验更鲜明", "人物和地点更容易形成记忆点"],
                        "tradeoffs": ["需要后续格外检查推理密度"],
                        "brief_fit": "适合把当前创意中的氛围潜力转化为可追踪场景。",
                    },
                    {
                        "strategy": "reasoning_first",
                        "direction": f"围绕“{proposition}”建立问题、证据、反证与解答链。",
                        "focus": "优先保证每个关键结论都能追溯到信息与推理路径。",
                        "strengths": ["推理链可验证", "假设与证据边界更明确"],
                        "tradeoffs": ["场景铺陈需要在后续编辑中继续深化"],
                        "brief_fit": "当前 Brief 已给出明确推理命题，适合先锁定证据闭环。",
                    },
                ],
                "recommended_strategy": "reasoning_first",
                "recommendation_reason": (
                    "当前 Brief 的推理命题足够明确，先建立证据闭环最能降低后续返工。"
                ),
            }
        )
        usage = _zero_usage()
        request.emit("model.completed", "analyzing_strategies", {"usage": usage})
        return BriefStrategyOptionsResult(candidate=candidate, usage=usage)

    def intake_questions(self, request: BriefIntakeQuestionsRequest) -> BriefIntakeQuestionsResult:
        system_prompt_for_task("brief_intake_questions", request.prompt_version)
        request.emit("model.started", "questioning", {"model_id": request.model_id})
        questions = (
            [
                {
                    "question_key": "question_evidence_density",
                    "ordinal": 1,
                    "prompt": "你希望读者需要交叉核对多少组彼此矛盾的记录？",
                    "impact": "这会影响线索密度与阅读负担，但不会改变已经确认的核心方向。",
                    "required": False,
                    "suggestions": ["保持精炼，只设置两组关键矛盾", "增加到四组，形成层层互证"],
                },
                {
                    "question_key": "question_supporting_cast",
                    "ordinal": 2,
                    "prompt": "次要证人需要各自承担独立线索，还是合并为更少角色？",
                    "impact": "这会影响角色规模和信息分配。",
                    "required": False,
                    "suggestions": ["合并角色，保持紧凑", "保留独立证人，强化多视角"],
                },
            ]
            if request.mode == "additional"
            else [
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
        )
        candidate = BriefIntakeQuestionSetContract.model_validate({"questions": questions})
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
            str(source.get("content_text", "")).strip() if isinstance(source, dict) else ""
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
                "content_outline": [
                    "发现谜面：建立原始异常并明确待追查的核心问题。",
                    "验证线索：沿独立线索逐步核对并排除表面解释。",
                    "审阅结论：整理候选解释，交给作者确认最终方向。",
                ],
                "reasoning_goal": "解释核心异常如何发生，并形成可由作者审阅的结论。",
                "resolution_mode": "agent_proposed",
                "conclusion_mode": "undetermined",
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
                    "conclusion_mode": "agent_suggestion",
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
        if request.prompt_version in {"brief-to-draft-v8", "brief-to-draft-v9"}:

            async def call_component(
                _instructions: str,
                _input_text: str,
                output_type: type[BaseModel],
                stage: str,
                component_id: str,
                schema_id: str,
            ) -> tuple[dict[str, Any], dict[str, Any]]:
                request.emit(
                    "agent.model_call.started",
                    stage,
                    {
                        "component_id": component_id,
                        "schema_id": schema_id,
                        "attempt_no": 1,
                        "protocol": "fake_strict",
                        "model_id": request.model_id,
                        "prompt_sha256": sha256(_instructions.encode("utf-8")).hexdigest(),
                    },
                )
                output = _fake_v8_output(output_type)
                if output_type.__name__ == "ResolutionGovernanceIRV1":
                    output["resolution_specs"][0]["conclusion_mode"] = request.brief[
                        "conclusion_mode"
                    ]
                usage = _zero_usage()
                request.emit(
                    "agent.model_call.completed",
                    stage,
                    {
                        "component_id": component_id,
                        "schema_id": schema_id,
                        "attempt_no": 1,
                        "protocol": "fake_strict",
                        "output_hash": sha256(
                            json.dumps(output, sort_keys=True).encode("utf-8")
                        ).hexdigest(),
                        "output_size_bytes": len(json.dumps(output).encode("utf-8")),
                        "usage": usage,
                    },
                )
                return output, usage

            runner = (
                run_v9_generation
                if request.prompt_version == "brief-to-draft-v9"
                else run_v8_generation
            )
            return asyncio.run(runner(request, call_component=call_component))
        system_prompt_for_task("brief_to_draft", request.prompt_version)
        request.emit("tool.started", "planning", {"tool": "plan_object_ids"})
        resolution_id = f"res_t{request.task_run_id}_01"
        constraints = _brief_constraints(request)
        constraint_ids = [
            f"con_t{request.task_run_id}_{index:02d}" for index in range(1, len(constraints) + 1)
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
        conclusion_mode = request.brief["conclusion_mode"]
        author_answer = request.brief["author_answer"]
        try:
            candidate_strategy = CandidateStrategy(request.candidate_strategy)
        except ValueError:
            candidate_strategy = CandidateStrategy.BALANCED
        strategy_titles = {
            CandidateStrategy.STRUCTURE_FIRST: "结构优先",
            CandidateStrategy.ATMOSPHERE_FIRST: "氛围优先",
            CandidateStrategy.REASONING_FIRST: "推理优先",
        }
        strategy_title = strategy_titles.get(candidate_strategy)
        candidate: dict[str, Any] = {
            "schema_version": "1.0",
            "casefile_id": request.casefile_id,
            "title": (
                request.brief["creative_intent"]
                if strategy_title is None
                else f"{request.brief['creative_intent']}｜{strategy_title}"
            ),
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
                    "conclusion_mode": conclusion_mode,
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

    def extract_anchors(self, request: BriefAnchorExtractRequest) -> BriefAnchorExtractResult:
        if not request.api_key:
            raise ProviderProtocolError("OpenAI API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "brief_anchor_extract",
                    request.prompt_version,
                ),
                input_text=anchor_extract_input(
                    request.brief,
                    request.input_hash,
                    mode=request.mode,
                ),
                output_type=BriefAnchorExtractCandidate,
                stage="extracting",
            )
        )
        return BriefAnchorExtractResult(
            candidate=BriefAnchorExtractCandidate.model_validate(candidate),
            usage=usage,
        )

    def intake_questions(self, request: BriefIntakeQuestionsRequest) -> BriefIntakeQuestionsResult:
        if not request.api_key:
            raise ProviderProtocolError("OpenAI API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "brief_intake_questions", request.prompt_version
                ),
                input_text=brief_intake_questions_input(
                    request.source_text,
                    request.input_hash,
                    existing_questions=request.existing_questions,
                    mode=request.mode,
                ),
                output_type=BriefIntakeQuestionSetContract,
                stage="questioning",
            )
        )
        return BriefIntakeQuestionsResult(
            candidate=BriefIntakeQuestionSetContract.model_validate(candidate),
            usage=usage,
        )

    def strategy_options(self, request: BriefStrategyOptionsRequest) -> BriefStrategyOptionsResult:
        if not request.api_key:
            raise ProviderProtocolError("OpenAI API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "brief_strategy_options", request.prompt_version
                ),
                input_text=brief_strategy_options_input(request),
                output_type=BriefStrategyOptionsCandidate,
                stage="analyzing_strategies",
            )
        )
        return BriefStrategyOptionsResult(
            candidate=BriefStrategyOptionsCandidate.model_validate(candidate),
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
                input_text=brief_intake_synthesize_input(request.input_data, request.input_hash),
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
        if request.prompt_version in {"brief-to-draft-v8", "brief-to-draft-v9"}:

            async def call_component(
                instructions: str,
                input_text: str,
                output_type: type[BaseModel],
                stage: str,
                component_id: str,
                schema_id: str,
            ) -> tuple[dict[str, Any], dict[str, Any]]:
                return await _run_auxiliary_agent(
                    request,
                    model=model,
                    model_settings=ModelSettings(
                        reasoning=Reasoning(effort="medium"),
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
                    component_id=component_id,
                    schema_id=schema_id,
                )

            runner = (
                run_v9_generation
                if request.prompt_version == "brief-to-draft-v9"
                else run_v8_generation
            )
            return await runner(request, call_component=call_component)
        if request.prompt_version == "brief-to-draft-v7":
            return await _run_partitioned_generation(
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
            | BriefStrategyOptionsRequest
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

    def extract_anchors(self, request: BriefAnchorExtractRequest) -> BriefAnchorExtractResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "brief_anchor_extract",
                    request.prompt_version,
                ),
                input_text=anchor_extract_input(
                    request.brief,
                    request.input_hash,
                    mode=request.mode,
                ),
                output_type=BriefAnchorExtractCandidate,
                stage="extracting",
            )
        )
        return BriefAnchorExtractResult(
            candidate=BriefAnchorExtractCandidate.model_validate(candidate),
            usage=usage,
        )

    def intake_questions(self, request: BriefIntakeQuestionsRequest) -> BriefIntakeQuestionsResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "brief_intake_questions", request.prompt_version
                ),
                input_text=brief_intake_questions_input(
                    request.source_text,
                    request.input_hash,
                    existing_questions=request.existing_questions,
                    mode=request.mode,
                ),
                output_type=BriefIntakeQuestionSetContract,
                stage="questioning",
            )
        )
        return BriefIntakeQuestionsResult(
            candidate=BriefIntakeQuestionSetContract.model_validate(candidate),
            usage=usage,
        )

    def strategy_options(self, request: BriefStrategyOptionsRequest) -> BriefStrategyOptionsResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task(
                    "brief_strategy_options", request.prompt_version
                ),
                input_text=brief_strategy_options_input(request),
                output_type=BriefStrategyOptionsCandidate,
                stage="analyzing_strategies",
            )
        )
        return BriefStrategyOptionsResult(
            candidate=BriefStrategyOptionsCandidate.model_validate(candidate),
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
                input_text=brief_intake_synthesize_input(request.input_data, request.input_hash),
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
        if request.prompt_version in {"brief-to-draft-v8", "brief-to-draft-v9"}:

            async def call_component(
                instructions: str,
                input_text: str,
                output_type: type[BaseModel],
                stage: str,
                component_id: str,
                schema_id: str,
            ) -> tuple[dict[str, Any], dict[str, Any]]:
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
                    component_id=component_id,
                    schema_id=schema_id,
                    deepseek_output_protocol=_deepseek_v8_output_protocol(request.model_id),
                )

            runner = (
                run_v9_generation
                if request.prompt_version == "brief-to-draft-v9"
                else run_v8_generation
            )
            return await runner(request, call_component=call_component)
        if request.prompt_version == "brief-to-draft-v7":
            return await _run_partitioned_generation(
                request,
                model=model,
                model_settings=_deepseek_model_settings(),
                structured_output=False,
                tracing_disabled=True,
            )
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
            | BriefStrategyOptionsRequest
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
            | BriefStrategyOptionsRequest
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
        GenerationRequest
        | BriefPolishRequest
        | BriefAnchorExtractRequest
        | BriefIntakeQuestionsRequest
        | BriefIntakeSynthesizeRequest
        | BriefStrategyOptionsRequest
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
    planned_object_types: dict[str, str] | None = None,
    component_id: str | None = None,
    schema_id: str | None = None,
    deepseek_output_protocol: Literal["strict_tool", "json_object"] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = (
        "native_json_schema"
        if structured_output
        else deepseek_output_protocol or "strict_tool"
    )
    usage_records: list[dict[str, Any]] = []
    repaired = False
    selected_protocols: set[str] = set()
    current_input = input_text

    if not structured_output and deepseek_output_protocol == "json_object":
        request.emit(
            "model.output_protocol_fallback",
            stage,
            {
                "from": "strict_tool",
                "to": "json_object",
                "reason_code": "strict_tool_disabled_by_capability_policy",
            },
        )

    if protocol == "strict_tool":
        try:
            compile_deepseek_strict_schema(output_type)
        except Exception as error:
            reason = strict_fallback_reason(error)
            if reason is None:
                raise
            protocol = "json_object"
            request.emit(
                "model.output_protocol_fallback",
                stage,
                {"from": "strict_tool", "to": protocol, "reason_code": reason},
            )

    for attempt_no in range(1, 4):
        raw_output_text: str | None = None
        if protocol not in selected_protocols:
            request.emit(
                "model.output_protocol_selected",
                stage,
                {"protocol": protocol, "attempt_no": attempt_no},
            )
            selected_protocols.add(protocol)
        request.emit(
            "model.started",
            stage,
            {
                "model_id": request.model_id,
                "attempt_no": attempt_no,
                "protocol": protocol,
                **({"component_id": component_id} if component_id else {}),
                **({"schema_id": schema_id} if schema_id else {}),
            },
        )
        if component_id:
            request.emit(
                "agent.model_call.started",
                stage,
                {
                    "component_id": component_id,
                    "schema_id": schema_id,
                    "attempt_no": attempt_no,
                    "protocol": protocol,
                    "model_id": request.model_id,
                    "prompt_sha256": sha256(instructions.encode("utf-8")).hexdigest(),
                },
            )
        try:
            discarded_paths: list[str] = []
            normalized_ref_paths: list[str] = []
            if protocol == "strict_tool":
                if not request.api_key:
                    raise ProviderProtocolError("DeepSeek API key is required")
                strict_result = await call_deepseek_strict_tool(
                    api_key=request.api_key,
                    model_id=request.model_id,
                    network_retries=request.network_retries,
                    instructions=instructions,
                    input_text=current_input,
                    output_type=output_type,
                )
                raw_output_text = strict_result.raw_output
                usage_records.append(strict_result.usage)
                output = _validate_auxiliary_output(
                    output_type,
                    strict_result.raw_output,
                    discarded_paths=discarded_paths,
                    planned_object_types=planned_object_types,
                    normalized_ref_paths=normalized_ref_paths,
                )
            else:
                resolved_instructions = instructions
                agent_output_type: Any = output_type
                if protocol == "json_object":
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
                result = await Runner.run(
                    agent,
                    current_input,
                    max_turns=request.max_turns,
                    run_config=RunConfig(
                        workflow_name=f"CaseFile {stage}",
                        tracing_disabled=tracing_disabled,
                        trace_include_sensitive_data=False,
                    ),
                )
                usage_records.append(_usage_json(result.context_wrapper.usage))
                final_output = getattr(result, "final_output", None)
                if isinstance(final_output, str):
                    raw_output_text = final_output
                if protocol == "native_json_schema":
                    output = result.final_output_as(
                        output_type,
                        raise_if_incorrect_type=True,
                    )
                else:
                    if not isinstance(result.final_output, str):
                        raise ProviderProtocolError(
                            "DeepSeek auxiliary output must be a JSON string"
                        )
                    output = _validate_auxiliary_output(
                        output_type,
                        result.final_output,
                        discarded_paths=discarded_paths,
                        planned_object_types=planned_object_types,
                        normalized_ref_paths=normalized_ref_paths,
                    )
            if discarded_paths:
                request.emit(
                    "validation.extra_fields_discarded",
                    stage,
                    {"paths": discarded_paths, "field_count": len(discarded_paths)},
                )
            if normalized_ref_paths:
                request.emit(
                    "validation.object_ref_types_normalized",
                    stage,
                    {
                        "paths": normalized_ref_paths,
                        "field_count": len(normalized_ref_paths),
                    },
                )
            usage = _merge_structured_usage(usage_records)
            request.emit("model.completed", stage, {"usage": usage})
            if component_id:
                serialized_output = json.dumps(
                    output.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                request.emit(
                    "agent.model_call.completed",
                    stage,
                    {
                        "component_id": component_id,
                        "schema_id": schema_id,
                        "attempt_no": attempt_no,
                        "protocol": protocol,
                        "output_hash": sha256(serialized_output).hexdigest(),
                        "output_size_bytes": len(serialized_output),
                        "usage": usage,
                    },
                )
            request.emit(
                "model.output_validated",
                stage,
                {
                    "protocol": protocol,
                    "attempt_count": attempt_no,
                    "repaired": repaired,
                },
            )
            return output.model_dump(mode="json"), usage
        except ContractValidationError as error:
            if component_id:
                request.emit(
                    "agent.model_call.failed",
                    stage,
                    {
                        "component_id": component_id,
                        "schema_id": schema_id,
                        "attempt_no": attempt_no,
                        "protocol": protocol,
                        "failure_layer": "pydantic",
                        "error_code": "structured_output_validation_failed",
                        "issues": error.errors[:20],
                        **_retained_raw_output(raw_output_text),
                    },
                )
            if protocol == "strict_tool" and attempt_no < 3:
                issues = error.errors[:20]
                request.emit(
                    "model.output_protocol_fallback",
                    stage,
                    {
                        "from": protocol,
                        "to": "json_object",
                        "reason_code": "strict_schema_violation",
                    },
                )
                protocol = "json_object"
                current_input = repair_input(input_text, issues)
                continue
            if repaired or attempt_no == 3:
                raise
            repaired = True
            issues = error.errors[:20]
            request.emit(
                "model.output_repair_started",
                stage,
                {
                    "attempt_no": attempt_no + 1,
                    "protocol": protocol,
                    "validation_layer": "pydantic",
                    "issues": issues,
                },
            )
            current_input = repair_input(input_text, issues)
        except ModelBehaviorError:
            if component_id:
                request.emit(
                    "agent.model_call.failed",
                    stage,
                    {
                        "component_id": component_id,
                        "schema_id": schema_id,
                        "attempt_no": attempt_no,
                        "protocol": protocol,
                        "failure_layer": "pydantic",
                        "error_code": "model_output_invalid",
                        "issues": [
                            {
                                "code": "model_output_invalid",
                                "path": "",
                                "message": "模型输出无法按目标结构解析。",
                            }
                        ],
                        **_retained_raw_output(raw_output_text),
                    },
                )
            if repaired or attempt_no == 3:
                raise
            repaired = True
            issues = [
                {
                    "code": "model_output_invalid",
                    "path": "",
                    "message": "模型输出无法按目标结构解析。",
                }
            ]
            request.emit(
                "model.output_repair_started",
                stage,
                {
                    "attempt_no": attempt_no + 1,
                    "protocol": protocol,
                    "validation_layer": "pydantic",
                    "issues": issues,
                },
            )
            current_input = repair_input(input_text, issues)
        except Exception as error:
            if component_id:
                request.emit(
                    "agent.model_call.failed",
                    stage,
                    {
                        "component_id": component_id,
                        "schema_id": schema_id,
                        "attempt_no": attempt_no,
                        "protocol": protocol,
                        "failure_layer": "transport",
                        "error_code": "provider_call_failed",
                        "issues": [],
                        **_retained_raw_output(raw_output_text),
                    },
                )
            reason = strict_fallback_reason(error) if protocol == "strict_tool" else None
            if reason is None or attempt_no == 3:
                raise
            request.emit(
                "model.output_protocol_fallback",
                stage,
                {"from": protocol, "to": "json_object", "reason_code": reason},
            )
            protocol = "json_object"
    raise ProviderProtocolError("Structured output attempts were exhausted")


def _retained_raw_output(raw_output: str | None) -> dict[str, Any]:
    """Apply the v8 development/production failed-output retention policy."""

    if raw_output is None:
        return {}
    encoded = raw_output.encode("utf-8")
    payload: dict[str, Any] = {
        "output_hash": sha256(encoded).hexdigest(),
        "output_size_bytes": len(encoded),
    }
    environment = os.getenv("CASEFILE_RUNTIME_ENV", "development").strip().lower()
    if environment in {"production", "prod"}:
        return payload
    limit = 1024 * 1024
    payload["_raw_output"] = encoded[:limit].decode("utf-8", errors="ignore")
    payload["raw_output_truncated"] = len(encoded) > limit
    return payload


def _fake_v8_output(output_type: type[BaseModel]) -> dict[str, Any]:
    common = {"description": "用于验证 v8 确定性编译链路的完整语义对象。", "tags": []}
    if output_type.__name__ == "CaseBlueprintV1":

        def node(
            key: str,
            title: str,
            dependencies: list[str] | None = None,
        ) -> dict[str, Any]:
            return {
                "local_key": key,
                "title": title,
                "purpose": f"验证{title}的确定性生成与链接。",
                "dependency_keys": dependencies or [],
            }

        return {
            "title": "v8 可恢复生成样例",
            "resolution_specs": [node("resolution", "核心解答", ["claim"])],
            "entities": [node("author", "记录者")],
            "relationships": [],
            "locations": [node("archive", "档案室")],
            "events": [node("discovery", "发现记录", ["author", "archive"])],
            "information_units": [node("record", "关键记录", ["discovery", "claim"])],
            "claims": [node("claim", "记录可信", ["record"])],
            "hypotheses": [node("hypothesis", "记录解释", ["resolution", "claim"])],
            "reasoning_paths": [node("path", "验证路径", ["record", "claim"])],
            "constraints": [node("constraint", "作者边界", ["resolution"])],
            "structure_locks": [node("lock", "事件标题锁", ["discovery"])],
        }
    if output_type.__name__ == "StoryWorldIRV1":
        return {
            "entities": [
                {
                    "local_key": "author",
                    **common,
                    "entity_type": "person",
                    "name": "记录者",
                    "aliases": [],
                    "traits": ["谨慎"],
                    "goals": ["还原事实"],
                    "secrets": [],
                    "capabilities": ["档案分析"],
                    "knowledge_states": [],
                }
            ],
            "relationships": [],
            "locations": [
                {
                    "local_key": "archive",
                    **common,
                    "name": "档案室",
                    "spatial_position": {"coordinate_system": "schematic", "x": 50, "y": 50},
                    "parent_key": None,
                    "adjacency_keys": [],
                    "access_rules": ["由记录者进入"],
                    "travel_times": [],
                    "visibility_rules": [],
                }
            ],
            "events": [
                {
                    "local_key": "discovery",
                    **common,
                    "title": "发现关键记录",
                    "truth_status": "canon_true",
                    "time": {
                        "start": "2026-08-08T08:00:00Z",
                        "end": None,
                        "precision": "minute",
                    },
                    "participant_keys": ["author"],
                    "location_key": "archive",
                    "cause_keys": [],
                    "effect_keys": [],
                    "observed_by_keys": ["author"],
                }
            ],
        }
    if output_type.__name__ == "EvidenceLogicIRV1":
        return {
            "information_units": [
                {
                    "local_key": "record",
                    **common,
                    "information_type": "document",
                    "title": "关键记录",
                    "content": "记录显示核心主张成立。",
                    "source_event_key": "discovery",
                    "reliability": "high",
                    "truth_status": "canon_true",
                    "supports_claim_keys": ["claim"],
                    "refutes_claim_keys": [],
                    "availability": {
                        "perspective_keys": ["author"],
                        "acquisition_conditions": ["进入档案室"],
                        "alternative_path_keys": [],
                    },
                    "classification": "key",
                }
            ],
            "claims": [
                {
                    "local_key": "claim",
                    **common,
                    "title": "记录可信",
                    "statement": "关键记录能够支持最终解答。",
                    "claim_type": "fact",
                    "support_keys": ["record"],
                    "refute_keys": [],
                    "dependency_claim_keys": [],
                    "status": "supported",
                    "materiality": "critical",
                }
            ],
            "hypotheses": [
                {
                    "local_key": "hypothesis",
                    **common,
                    "title": "记录解释",
                    "proposition": "记录内容对应真实发生的事件。",
                    "target_resolution_key": "resolution",
                    "required_claim_keys": ["claim"],
                    "falsifier_keys": [],
                    "competing_hypothesis_keys": [],
                    "status": "supported",
                    "score": 0.9,
                }
            ],
            "reasoning_paths": [
                {
                    "local_key": "path",
                    **common,
                    "title": "验证路径",
                    "path_type": "proof",
                    "target_key": "resolution",
                    "steps": [
                        {
                            "step_key": "verify_record",
                            "input_keys": ["record"],
                            "operation": "infer",
                            "output_key": "claim",
                        }
                    ],
                    "required_for_resolution": True,
                    "alternative_path_keys": [],
                }
            ],
        }
    if output_type.__name__ == "ResolutionGovernanceIRV1":
        return {
            "resolution_specs": [
                {
                    "local_key": "resolution",
                    **common,
                    "title": "核心解答",
                    "question_type": "fact_reconstruction",
                    "reasoning_question": "关键记录是否可信？",
                    "conclusion_mode": "unique",
                    "required_slots": [],
                    "accepted_answer_texts": ["记录可信。"],
                    "accepted_answer_keys": [],
                    "required_claim_keys": ["claim"],
                }
            ],
            "constraints": [
                {
                    "local_key": "constraint",
                    **common,
                    "title": "作者边界",
                    "level": "hard",
                    "scope_keys": ["resolution"],
                    "statement": "不得绕过关键记录得出结论。",
                    "rule_expression": None,
                    "conflict_keys": [],
                }
            ],
            "structure_locks": [
                {
                    "local_key": "lock",
                    **common,
                    "title": "事件标题锁",
                    "lock_type": "hard",
                    "object_key": "discovery",
                    "field_paths": ["/title"],
                    "reason": "保留核心发现节点。",
                }
            ],
            "content_notices": [],
        }
    raise ProviderProtocolError(f"Fake v8 component is unsupported: {output_type.__name__}")


async def _run_partitioned_generation(
    request: GenerationRequest,
    *,
    model: OpenAIResponsesModel | OpenAIChatCompletionsModel,
    model_settings: ModelSettings,
    structured_output: bool,
    tracing_disabled: bool,
) -> GenerationResult:
    """Generate one complete CaseFile through a shared plan and isolated partitions."""

    instructions = system_prompt_for_task("brief_to_draft", request.prompt_version)
    strategy = (
        request.candidate_strategy.value
        if hasattr(request.candidate_strategy, "value")
        else str(request.candidate_strategy)
    )
    frozen_context = {
        "schema_version": "1.0",
        "casefile_id": request.casefile_id,
        "brief_ref": {"brief_id": request.brief_id, "version": request.brief_version},
        "version": {
            "version_id": request.version_id,
            "version_no": request.version_no,
            "parent_version_id": request.parent_version_id,
        },
        "status": "draft",
        "candidate_strategy": strategy,
        "candidate_strategy_version": request.candidate_strategy_version,
    }
    usage_records: list[dict[str, Any]] = []

    request.emit("generation.plan_started", "planning", {"attempt": 1})
    plan: GenerationPlan | None = None
    for attempt in range(1, 3):
        try:
            plan_json, usage = await _run_auxiliary_agent(
                request,
                model=model,
                model_settings=model_settings,
                instructions=(
                    instructions + "\n当前阶段：只返回紧凑对象计划。每个 local_key 必须唯一，"
                    "referenced_keys 只能引用同一计划中的 local_key。"
                    "\nGenerationPlan constraints: collection MUST be exactly one of "
                    "resolution_specs, entities, relationships, locations, events, "
                    "information_units, claims, hypotheses, reasoning_paths, constraints, "
                    "structure_locks. local_key MUST match ^[a-z][a-z0-9_]*$, MUST be unique, "
                    "and referenced_keys MUST only use local_key values declared in this plan."
                ),
                input_text=json.dumps(
                    {
                        "brief": request.brief,
                        "frozen_context": frozen_context,
                        "repair": attempt == 2,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                output_type=GenerationPlan,
                stage="planning",
                structured_output=structured_output,
                tracing_disabled=tracing_disabled,
            )
            usage_records.append(usage)
            plan = GenerationPlan.model_validate(plan_json)
            break
        except Exception as error:
            if attempt == 1:
                raise
            request.emit(
                "generation.plan_repair_started",
                "repairing",
                {"attempt": 2, "error_type": type(error).__name__},
            )
    if plan is None:
        raise ProviderProtocolError("Generation plan was not produced")

    id_directory = _allocate_plan_ids(request.task_run_id, plan)
    planned_object_types = {
        id_directory[item.local_key]: COLLECTION_OBJECT_TYPES[item.collection]
        for item in plan.objects
    }
    plan_payload = {
        "title": plan.title,
        "objects": [
            {
                **item.model_dump(mode="json"),
                "object_id": id_directory[item.local_key],
                "referenced_object_ids": [id_directory[ref] for ref in item.referenced_keys],
            }
            for item in plan.objects
        ],
    }
    request.emit(
        "generation.plan_completed",
        "planning",
        {
            "object_count": len(plan.objects),
            "collection_counts": _plan_collection_counts(plan),
        },
    )

    updated_at = datetime.now(UTC).isoformat()

    async def generate_partition(
        partition: str,
        *,
        issues: list[dict[str, Any]] | None = None,
        previous: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        output_type = _PARTITION_MODELS[partition]
        last_error: Exception | None = None
        for attempt in range(1, 3):
            started_at = perf_counter()
            request.emit(
                "generation.partition_started",
                f"generating_{partition}",
                {"partition": partition, "attempt": attempt, "targeted_repair": bool(issues)},
            )
            try:
                payload: dict[str, Any] = {
                    "brief": request.brief,
                    "frozen_context": frozen_context,
                    "updated_at": updated_at,
                    "shared_plan": plan_payload,
                    "partition": partition,
                    "required_fields": list(_PARTITION_FIELDS[partition]),
                }
                if issues:
                    payload["repair_feedback"] = issues
                    payload["previous_partition"] = previous
                elif attempt == 2 and isinstance(last_error, ContractValidationError):
                    payload["repair_feedback"] = last_error.errors
                elif attempt == 2:
                    payload["repair_feedback"] = [
                        {
                            "code": "partition_output_invalid",
                            "path": f"/{partition}",
                            "message": "上一响应无法解析或不符合当前分区结构",
                        }
                    ]
                partition_json, usage = await _run_auxiliary_agent(
                    request,
                    model=model,
                    model_settings=model_settings,
                    instructions=(
                        instructions
                        + f"\n当前阶段：只生成 {partition} 分区，且必须完整返回："
                        + ", ".join(_PARTITION_FIELDS[partition])
                        + "。必须恰好使用 shared_plan 中属于这些集合的全部 object_id。"
                    ),
                    input_text=json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    output_type=output_type,
                    stage=f"generating_{partition}",
                    structured_output=structured_output,
                    tracing_disabled=tracing_disabled,
                    planned_object_types=planned_object_types,
                )
                validated = output_type.model_validate(partition_json).model_dump(mode="json")
                validated, discarded_ids = _retain_planned_objects(
                    validated,
                    set(id_directory.values()),
                )
                if discarded_ids:
                    request.emit(
                        "generation.unplanned_objects_discarded",
                        f"generating_{partition}",
                        {
                            "partition": partition,
                            "object_ids": discarded_ids,
                            "object_count": len(discarded_ids),
                        },
                    )
                request.emit(
                    "generation.partition_completed",
                    f"generating_{partition}",
                    {
                        "partition": partition,
                        "attempt": attempt,
                        "targeted_repair": bool(issues),
                        "elapsed_ms": round((perf_counter() - started_at) * 1000),
                    },
                )
                return validated, usage
            except Exception as error:
                last_error = error
                request.emit(
                    "generation.partition_failed",
                    f"generating_{partition}",
                    {
                        "partition": partition,
                        "attempt": attempt,
                        "targeted_repair": bool(issues),
                        "elapsed_ms": round((perf_counter() - started_at) * 1000),
                        "error_type": type(error).__name__,
                    },
                )
                if issues or attempt == 1:
                    raise
                request.emit(
                    "generation.partition_repair_started",
                    "repairing",
                    {"partition": partition, "attempt": 2, "error_type": type(error).__name__},
                )
        raise last_error or ProviderProtocolError("Partition generation failed")

    request.emit(
        "generation.partitions_started",
        "generating",
        {"partitions": list(_PARTITION_FIELDS)},
    )
    partition_pairs = await asyncio.gather(
        *(generate_partition(partition) for partition in _PARTITION_FIELDS)
    )
    partitions = {
        partition: partition_pairs[index][0] for index, partition in enumerate(_PARTITION_FIELDS)
    }
    usage_records.extend(pair[1] for pair in partition_pairs)
    request.emit("generation.assembly_started", "assembling", {})
    candidate = _assemble_partitioned_candidate(
        request,
        plan.title,
        partitions,
    )
    request.emit(
        "generation.assembly_completed",
        "assembling",
        {"partitions": list(_PARTITION_FIELDS), "object_count": len(id_directory)},
    )

    request.emit("generation.validation_started", "validating", {})
    try:
        _validate_partitioned_candidate(candidate, id_directory)
    except ContractValidationError as error:
        issues_by_partition = _partition_issues(error.errors)
        if not issues_by_partition:
            raise
        repaired_pairs = await asyncio.gather(
            *(
                generate_partition(
                    partition,
                    issues=issues,
                    previous=partitions[partition],
                )
                for partition, issues in issues_by_partition.items()
            )
        )
        for index, partition in enumerate(issues_by_partition):
            partitions[partition] = repaired_pairs[index][0]
            usage_records.append(repaired_pairs[index][1])
        candidate = _assemble_partitioned_candidate(request, plan.title, partitions)
        try:
            _validate_partitioned_candidate(candidate, id_directory)
        except ContractValidationError as repaired_error:
            pruned_paths = _prune_invalid_reference_list_items(
                candidate,
                repaired_error.errors,
            )
            if not pruned_paths:
                raise
            request.emit(
                "generation.invalid_references_pruned",
                "repairing",
                {"paths": pruned_paths, "reference_count": len(pruned_paths)},
            )
            _validate_partitioned_candidate(candidate, id_directory)
    request.emit(
        "generation.validation_completed",
        "validating",
        {"object_count": len(id_directory)},
    )

    metrics = ToolMetrics(
        calls=len(usage_records),
        valid_calls=len(usage_records),
        successful_calls=len(usage_records),
        adopted_results=1,
        planned_object_ids=set(id_directory.values()),
    )
    request.emit(
        "generation.assembled",
        "validating",
        {"partitions": list(_PARTITION_FIELDS), "object_count": len(id_directory)},
    )
    return GenerationResult(
        candidate=candidate,
        usage=_merge_usage(usage_records),
        tools=metrics,
    )


def _allocate_plan_ids(task_run_id: int, plan: GenerationPlan) -> dict[str, str]:
    counters = {collection: 0 for collection in _COLLECTION_PREFIXES}
    allocated: dict[str, str] = {}
    for item in plan.objects:
        counters[item.collection] += 1
        allocated[item.local_key] = (
            f"{_COLLECTION_PREFIXES[item.collection]}_t{task_run_id}_"
            f"{counters[item.collection]:02d}"
        )
    return allocated


def _plan_collection_counts(plan: GenerationPlan) -> dict[str, int]:
    counts = {collection: 0 for collection in _COLLECTION_PREFIXES}
    for item in plan.objects:
        counts[item.collection] += 1
    return counts


def _retain_planned_objects(
    partition: dict[str, Any],
    planned_ids: set[str],
) -> tuple[dict[str, Any], list[str]]:
    discarded_ids: list[str] = []
    for collection in _COLLECTION_PREFIXES:
        if collection not in partition:
            continue
        retained: list[Any] = []
        for item in partition[collection]:
            object_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(object_id, str) and object_id in planned_ids:
                retained.append(item)
                continue
            if isinstance(object_id, str):
                discarded_ids.append(object_id)
        partition[collection] = retained
    return partition, sorted(discarded_ids)


def _assemble_partitioned_candidate(
    request: GenerationRequest,
    title: str,
    partitions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "schema_version": "1.0",
        "casefile_id": request.casefile_id,
        "title": title,
        "status": "draft",
        "version": {
            "version_id": request.version_id,
            "version_no": request.version_no,
            "parent_version_id": request.parent_version_id,
        },
        "brief_ref": {"brief_id": request.brief_id, "version": request.brief_version},
    }
    for partition in _PARTITION_FIELDS:
        candidate.update(partitions[partition])
    return cast(dict[str, Any], _remove_absent_optional_fields(candidate))


def _validate_partitioned_candidate(
    candidate: dict[str, Any],
    id_directory: dict[str, str],
) -> None:
    issues: list[dict[str, Any]] = []
    for validator in (validate_casefile, _validate_generated_descriptions):
        try:
            validator(candidate)
        except ContractValidationError as error:
            issues.extend(error.errors)
    issues.extend(_planned_object_id_issues(candidate, id_directory))
    if issues:
        raise ContractValidationError(issues)


def _planned_object_id_issues(
    candidate: dict[str, Any],
    id_directory: dict[str, str],
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    expected_ids = set(id_directory.values())
    for collection, prefix in _COLLECTION_PREFIXES.items():
        expected = {object_id for object_id in expected_ids if object_id.startswith(f"{prefix}_")}
        actual = {
            str(item.get("id"))
            for item in candidate.get(collection, [])
            if isinstance(item, dict) and item.get("id") is not None
        }
        if actual == expected:
            continue
        issues.append(
            {
                "code": "planned_object_ids_mismatch",
                "path": f"/{collection}",
                "message": (
                    "候选对象 ID 与计划不一致；"
                    f"缺少：{sorted(expected - actual)!r}；"
                    f"多出：{sorted(actual - expected)!r}。"
                ),
            }
        )
    return issues


def _partition_issues(
    issues: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    field_to_partition = {
        field: partition for partition, fields in _PARTITION_FIELDS.items() for field in fields
    }
    for issue in issues:
        path = str(issue.get("path", ""))
        first = path.lstrip("/").split("/", 1)[0]
        partition = field_to_partition.get(first)
        if partition is not None:
            grouped.setdefault(partition, []).append(issue)
    return grouped


def _prune_invalid_reference_list_items(
    candidate: dict[str, Any],
    issues: list[dict[str, Any]],
) -> list[str]:
    removable_codes = {
        "missing_reference",
        "reference_type_mismatch",
        "self_reference",
    }
    removals: dict[tuple[str, ...], set[int]] = {}
    for issue in issues:
        if issue.get("code") not in removable_codes:
            continue
        path = str(issue.get("path", ""))
        parts = tuple(
            part.replace("~1", "/").replace("~0", "~")
            for part in path.lstrip("/").split("/")
            if part
        )
        if not parts or not parts[-1].isdigit():
            continue
        removals.setdefault(parts[:-1], set()).add(int(parts[-1]))

    pruned_paths: list[str] = []
    for parent_parts, indexes in removals.items():
        parent: Any = candidate
        try:
            for part in parent_parts:
                parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        except (IndexError, KeyError, TypeError, ValueError):
            continue
        if not isinstance(parent, list):
            continue
        for index in sorted(indexes, reverse=True):
            if index < 0 or index >= len(parent):
                continue
            parent.pop(index)
            pointer = "/" + "/".join(
                part.replace("~", "~0").replace("/", "~1") for part in (*parent_parts, str(index))
            )
            pruned_paths.append(pointer)
    return sorted(pruned_paths)


def _merge_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    for record in records:
        for key in tuple(merged):
            value = record.get(key, 0)
            if isinstance(value, int):
                merged[key] += value
    merged["partition_calls"] = len(records)
    return merged


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
        output = cast(
            CaseFile,
            _validate_auxiliary_output(CaseFile, result.final_output),
        )
    candidate = _remove_absent_optional_fields(output.model_dump(mode="json"))
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


def _remove_absent_optional_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_remove_absent_optional_fields(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _remove_absent_optional_fields(item)
            for key, item in value.items()
            if not (key in {"description", "spatial_position"} and item is None)
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
                        "message": "Agent 生成的对象必须填写非空描述。",
                    }
                )
    if issues:
        raise ContractValidationError(issues)


def _deepseek_model_settings() -> ModelSettings:
    return ModelSettings(
        include_usage=True,
        parallel_tool_calls=False,
        extra_args={"response_format": {"type": "json_object"}},
        extra_body={"thinking": {"type": "disabled"}},
    )


def _deepseek_v8_output_protocol(model_id: str) -> Literal["strict_tool", "json_object"]:
    """Choose the safest DeepSeek output mode for the compound v8 IR.

    DeepSeek's Beta strict function schema is retained and can be forced for
    investigation.  Real v8 acceptance on the compatibility aliases for
    ``deepseek-v4-flash`` showed accepted strict requests returning tool
    arguments with missing required fields.  JSON mode plus local Pydantic
    validation and the same bounded repair contract is therefore the default
    for that capability profile.
    """

    configured = os.getenv("CASEFILE_DEEPSEEK_V8_OUTPUT_PROTOCOL", "auto").strip().lower()
    if configured not in {"auto", "strict_tool", "json_object"}:
        raise ProviderProtocolError(
            "CASEFILE_DEEPSEEK_V8_OUTPUT_PROTOCOL must be auto, strict_tool, or json_object"
        )
    if configured != "auto":
        return cast(Literal["strict_tool", "json_object"], configured)
    if model_id.strip().lower() in {"deepseek-v4-flash", "deepseek-chat"}:
        return "json_object"
    return "strict_tool"


def _json_schema_instruction(output_type: type[BaseModel]) -> str:
    schema = json.dumps(
        output_type.model_json_schema(),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "\n最终响应必须是严格匹配以下 JSON Schema 的一个 JSON 对象。"
        "必须使用准确的属性名；不得把来源追踪输入字段复制到输出；"
        "不得使用 Markdown 包装，也不得添加任何额外说明。\n" + schema
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


__all__ = [
    "AgentProvider",
    "DeepSeekAgentsProvider",
    "FakeProvider",
    "GenerationProvider",
    "OpenAIAgentsProvider",
]
