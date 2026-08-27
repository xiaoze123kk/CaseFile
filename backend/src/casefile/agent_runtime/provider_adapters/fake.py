"""Deterministic FakeProvider adapter for tests and evaluations."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal, cast

from casefile_contracts import (
    BriefIntakeCandidate as BriefIntakeCandidateContract,
)
from casefile_contracts import (
    BriefIntakeQuestionSet as BriefIntakeQuestionSetContract,
)
from casefile_contracts import Status as ClaimStatus
from pydantic import BaseModel

from casefile.agent_runtime.brief_to_draft_runtime import resolve_pipeline_spec
from casefile.agent_runtime.chat_tools import (
    ChatToolContext,
    ChatToolMetrics,
    chat_tool_manifest,
    freeze_chat_tool_ledger,
    search_casefile_records,
)
from casefile.agent_runtime.closure_repair import (
    ClaimDependenciesRepairOutputV2,
    ClaimStatusRepairOutputV2,
    ClosureRepairOperationOutputV1,
    ClosureRepairOutputV1,
    ClosureRepairOutputV2,
    ClosureRepairOutputV3,
    ClosureRepairProviderResult,
    ClosureRepairRequest,
)
from casefile.agent_runtime.closure_repair_prompt import render_closure_repair_prompt
from casefile.agent_runtime.constraint_first_story_planner import (
    SemanticFillRequest,
    SemanticFillResult,
    SkeletonProposalRequest,
    SkeletonProposalResult,
)
from casefile.agent_runtime.context.thread_memory import (
    ThreadCompactionRequest,
    ThreadCompactionResult,
    ThreadMemoryDelta,
)
from casefile.agent_runtime.general_mutation import (
    GeneralMutationPlannerRequest,
    GeneralMutationPlannerResult,
)
from casefile.agent_runtime.general_mutation_prompt import (
    general_mutation_output_type,
    render_general_mutation_prompt,
)
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
    CaseFileChatCandidateV2,
    CaseFileChatRequest,
    CaseFileChatResult,
    CaseFileChatTargetLockedRepairOutput,
    ChatTaskUnderstandingOutput,
    GenerationRequest,
    GenerationResult,
    IdeaCandidateModel,
    IdeaCandidateSet,
    IdeaGenerationRequest,
    IdeaGenerationResult,
    IntentConstraintsOutput,
    IntentEntitiesOutput,
    IntentEntityMention,
    IntentUnderstandingResult,
    QueryRewriteOutput,
    ReverseParseCandidate,
    ReverseParseItem,
    ReverseParseRequest,
    ReverseParseResult,
    RouteSpecificRewriteRequest,
    RouteSpecificRewriteResult,
    ToolMetrics,
)
from casefile.agent_runtime.prompt import (
    COMPONENT_GENERATION_PROMPT_VERSIONS,
    chat_finalizer_component,
    chat_finalizer_output_type,
    render_chat_executor_prompt,
    render_chat_finalizer_prompt,
)
from casefile.agent_runtime.prompt_repository import (
    system_prompt_for_task,
)
from casefile.agent_runtime.provider_adapters.generation import _brief_to_draft_runner
from casefile.agent_runtime.provider_adapters.protocols import ProviderProtocolError
from casefile.agent_runtime.provider_adapters.shared import (
    _bind_safe_patch_registry,
    _validate_generated_descriptions,
)
from casefile.agent_runtime.story_planner import (
    StoryPlannerPatchProviderResult,
    StoryPlannerPatchRequest,
    StoryPlannerProviderResult,
    StoryPlannerRequest,
)
from casefile.contracts import validate_casefile


def _fake_intent_understanding(message: str) -> ChatTaskUnderstandingOutput:
    """Deterministic LLM-shaped intent output for FakeProvider tests and Eval."""

    text = message.strip()
    if any(token in text for token in ("删除", "清空", "覆盖")):
        return ChatTaskUnderstandingOutput(
            original_query=text,
            normalized_query=text,
            primary_intent="unsupported_action",
            sub_intents=["delete_request"],
            entities=_fake_intent_entities(),
            constraints=_fake_intent_constraints(preserved_actions=[text]),
            capabilities={
                "needs_casefile_retrieval": True,
                "needs_suggestion_generation": False,
            },
            risk_level="high",
            confidence=0.93,
            reason_codes=["explicit_destructive_verb"],
            canonical_query=text,
        )
    if "别动时间线" in text and "改" in text:
        return ChatTaskUnderstandingOutput(
            original_query=text,
            normalized_query=text,
            primary_intent="edit_request",
            sub_intents=["modify_description"],
            entities=_fake_intent_entities(
                object_mentions=[IntentEntityMention(text="它")],
                temporal_mentions=["时间线"],
            ),
            constraints=_fake_intent_constraints(
                preserved_negations=["别动时间线"],
                preserved_actions=["改"],
                output_format="patch_proposal",
            ),
            capabilities={
                "needs_casefile_retrieval": True,
                "needs_suggestion_generation": True,
            },
            risk_level="medium",
            confidence=0.91,
            reason_codes=["explicit_edit_verb", "focus_resolved_anaphora"],
            canonical_query="把焦点对象的描述改得更克制，但别动时间线。",
        )
    if any(token in text for token in ("导出前检查", "门禁")):
        return ChatTaskUnderstandingOutput(
            original_query=text,
            normalized_query=text,
            primary_intent="validate_request",
            sub_intents=["gate_check"],
            entities=_fake_intent_entities(),
            constraints=_fake_intent_constraints(preserved_actions=["检查"]),
            capabilities={"needs_validation_snapshot": True},
            confidence=0.96,
            reason_codes=["explicit_gate_request"],
            canonical_query=text,
        )
    audit_terms = ("逻辑漏洞", "矛盾", "断链", "时序", "动机缺口")
    audit_scope_terms = ("全案", "全卷", "整个卷宗", "复查", "检查")
    if any(token in text for token in audit_terms) and any(
        token in text for token in audit_scope_terms
    ):
        uncertain = any(token in text for token in ("随便", "低置信度"))
        return ChatTaskUnderstandingOutput(
            original_query=text,
            normalized_query=text,
            primary_intent="logic_audit",
            sub_intents=["full_case_audit"],
            entities=_fake_intent_entities(),
            constraints=_fake_intent_constraints(output_format="mixed"),
            capabilities={
                "needs_casefile_retrieval": True,
                "needs_relations": True,
                "needs_validation_snapshot": True,
                "needs_suggestion_generation": True,
                "needs_reasoning": True,
            },
            risk_level="medium",
            confidence=0.61 if uncertain else 0.91,
            reason_codes=["uncertain_audit"] if uncertain else ["audit_request"],
            canonical_query=text,
        )
    if any(token in text for token in ("与卷宗无关", "别的项目", "量子")):
        return ChatTaskUnderstandingOutput(
            original_query=text,
            normalized_query=text,
            primary_intent="out_of_scope",
            sub_intents=[],
            entities=_fake_intent_entities(),
            constraints=_fake_intent_constraints(),
            confidence=0.92,
            reason_codes=["out_of_scope_markers"],
            canonical_query=text,
        )
    if any(token in text for token in ("下一步怎么做", "该怎么做", "拿不准")):
        return ChatTaskUnderstandingOutput(
            original_query=text,
            normalized_query=text,
            primary_intent="clarify",
            sub_intents=["request_guidance"],
            entities=_fake_intent_entities(),
            constraints=_fake_intent_constraints(),
            confidence=0.8,
            ambiguous=True,
            missing_info=["intent_guidance"],
            reason_codes=["ambiguous_guidance_request"],
            canonical_query=text,
        )
    if "低置信度" in text:
        return ChatTaskUnderstandingOutput(
            original_query=text,
            normalized_query=text,
            primary_intent="edit_request",
            sub_intents=["modify_description"],
            entities=_fake_intent_entities(),
            constraints=_fake_intent_constraints(output_format="patch_proposal"),
            capabilities={"needs_suggestion_generation": True},
            confidence=0.61,
            reason_codes=["uncertain_edit"],
            canonical_query=text,
        )
    if any(token in text for token in ("改", "修改", "更新", "调整")):
        return ChatTaskUnderstandingOutput(
            original_query=text,
            normalized_query=text,
            primary_intent="edit_request",
            sub_intents=["modify_fields"],
            entities=_fake_intent_entities(
                object_mentions=[IntentEntityMention(text="它")] if "它" in text else None
            ),
            constraints=_fake_intent_constraints(
                preserved_actions=[
                    token for token in ("改", "修改", "更新", "调整") if token in text
                ]
            ),
            capabilities={
                "needs_casefile_retrieval": True,
                "needs_suggestion_generation": True,
            },
            risk_level="medium",
            confidence=0.91,
            reason_codes=["explicit_edit_verb"],
            canonical_query=text,
        )
    if "验证问题" in text or "规则失败" in text:
        return ChatTaskUnderstandingOutput(
            original_query=text,
            normalized_query=text,
            primary_intent="explain_issue",
            sub_intents=["explain_failure", "propose_fix"],
            entities=_fake_intent_entities(),
            constraints=_fake_intent_constraints(output_format="mixed"),
            capabilities={
                "needs_casefile_retrieval": True,
                "needs_validation_snapshot": True,
                "needs_suggestion_generation": True,
            },
            confidence=0.9,
            reason_codes=["validation_issue_focus"],
            canonical_query=text,
        )
    if any(token in text for token in ("体检", "证据链", "候选解释", "对比")):
        return ChatTaskUnderstandingOutput(
            original_query=text,
            normalized_query=text,
            primary_intent="analysis",
            sub_intents=["read_only_analysis", "compare_candidates"],
            entities=_fake_intent_entities(),
            constraints=_fake_intent_constraints(),
            capabilities={
                "needs_casefile_retrieval": True,
                "needs_relations": True,
                "needs_validation_snapshot": True,
            },
            confidence=0.92,
            reason_codes=["analysis_markers"],
            canonical_query=text,
        )
    return ChatTaskUnderstandingOutput(
        original_query=text,
        normalized_query=text,
        primary_intent="question",
        sub_intents=["factual_question"],
        entities=_fake_intent_entities(),
        constraints=_fake_intent_constraints(),
        capabilities={"needs_casefile_retrieval": True},
        confidence=0.9,
        reason_codes=["question_markers"],
        canonical_query=text,
    )


def _fake_intent_entities(
    *,
    object_mentions: list[IntentEntityMention] | None = None,
    event_mentions: list[IntentEntityMention] | None = None,
    issue_mentions: list[IntentEntityMention] | None = None,
    temporal_mentions: list[str] | None = None,
) -> IntentEntitiesOutput:
    return IntentEntitiesOutput(
        object_mentions=object_mentions or [],
        event_mentions=event_mentions or [],
        issue_mentions=issue_mentions or [],
        temporal_mentions=temporal_mentions or [],
    )


def _fake_intent_constraints(
    *,
    preserved_negations: list[str] | None = None,
    preserved_actions: list[str] | None = None,
    output_format: Literal["answer", "patch_proposal", "mixed"] = "answer",
) -> IntentConstraintsOutput:
    return IntentConstraintsOutput(
        preserved_negations=preserved_negations or [],
        preserved_actions=preserved_actions or [],
        output_format=output_format,
    )


def _fake_chat_tool_metrics(request: CaseFileChatRequest) -> ChatToolMetrics:
    """FakeProvider consumes route-scoped retrieval queries deterministically."""

    metrics = ChatToolMetrics()
    route = request.route
    if route is None:
        return metrics
    available_tools = {
        tool.name
        for tool in chat_tool_manifest(
            route,
            toolset_version=request.toolset_version,
        )
    }
    if "search_casefile" not in available_tools:
        return metrics
    max_calls = route.execution_profile.get("max_tool_calls")
    max_calls = max_calls if isinstance(max_calls, int) else 0
    queries = (
        list(request.rewrite.retrieval_queries)
        if request.rewrite is not None and request.rewrite.retrieval_queries
        else [request.rewrite.canonical_query if request.rewrite is not None else request.message]
    )
    for query in queries:
        metrics.calls += 1
        if metrics.calls > max_calls:
            metrics.budget_exhausted += 1
            request.emit(
                "tool.completed",
                "responding",
                {
                    "tool": "search_casefile",
                    "toolset_version": request.toolset_version,
                    "valid": False,
                    "reason_code": "tool_budget_exhausted",
                    "calls": metrics.calls,
                    "valid_calls": metrics.valid_calls,
                    "successful_calls": metrics.successful_calls,
                    "max_tool_calls": max_calls,
                },
            )
            break
        results = search_casefile_records(request.casefile, query)
        for result in results:
            object_id = str(result["id"])
            if object_id not in metrics.retrieved_object_ids:
                metrics.retrieved_object_ids.append(object_id)
        metrics.valid_calls += 1
        metrics.successful_calls += 1
        metrics.adopted_results += min(1, len(results))
        request.emit(
            "tool.completed",
            "responding",
            {
                "tool": "search_casefile",
                "toolset_version": request.toolset_version,
                "valid": True,
                "query": query,
                "result_count": len(results),
                "object_ids": [str(result["id"]) for result in results],
            },
        )
    return metrics


class FakeProvider:
    """Zero-cost deterministic provider for tests and local acceptance runs."""

    def propose_skeleton(
        self, request: SkeletonProposalRequest
    ) -> SkeletonProposalResult:
        basis = request.planning_problem["object_refs"][0]
        proposal = {
            "schema_id": "compiler.skeleton-proposal.v1",
            "scenes": [
                {
                    **slot,
                    "purpose": (
                        "resolution"
                        if slot["discourse_order"]
                        == len(request.planning_problem["scene_slots"])
                        else "investigation"
                    ),
                    "presentation_mode": request.planning_problem["hard_constraints"][
                        "structure"
                    ]["allowed_presentation_modes"][0],
                    "story_time_refs": [],
                    "participant_refs": [],
                    "basis_refs": [basis],
                    "exposure": [],
                    "resolutions": [],
                    "prerequisite_scene_ids": (
                        []
                        if slot["discourse_order"] == 1
                        else [f"scene_{slot['discourse_order'] - 1:03d}"]
                    ),
                }
                for slot in request.planning_problem["scene_slots"]
            ],
        }
        return SkeletonProposalResult(
            proposal=proposal,
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )

    def fill_semantics(self, request: SemanticFillRequest) -> SemanticFillResult:
        catalog = request.model_view["object_catalog"]
        entity = _first_ref(catalog.get("entities", []))
        location = _first_ref(catalog.get("locations", []))
        event = _first_ref(catalog.get("events", []))
        fill = {
            "schema_id": "compiler.semantic-fill.v1",
            "chapters": [
                {
                    "chapter_id": slot["chapter_id"],
                    "title": f"第{slot['ordinal']}章",
                }
                for slot in request.skeleton["chapter_slots"]
            ],
            "scenes": [
                {
                    "scene_id": scene["scene_id"],
                    "intent": f"推进第 {scene['discourse_order']} 个结构节点。",
                    "pov_ref": entity,
                    "location_ref": location,
                    "event_refs": [] if event is None else [event],
                }
                for scene in request.skeleton["scenes"]
            ],
        }
        return SemanticFillResult(
            fill=fill,
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )

    def plan_story(self, request: StoryPlannerRequest) -> StoryPlannerProviderResult:
        candidate = _fake_story_plan_candidate(request.planner_input)
        return StoryPlannerProviderResult(
            candidate=candidate,
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            raw_output=None,
        )

    def patch_story(
        self, request: StoryPlannerPatchRequest
    ) -> StoryPlannerPatchProviderResult:
        return StoryPlannerPatchProviderResult(
            patch={
                "schema_id": "compiler.story-plan-structural-patch.v1",
                "patches": [
                    {
                        "op": "replace_scene_purpose",
                        "scene_id": scene_id,
                        "purpose": "investigation",
                    }
                    for scene_id in request.expected_scene_ids
                ],
            },
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            raw_output=None,
        )
    def plan_general_mutation(
        self,
        request: GeneralMutationPlannerRequest,
    ) -> GeneralMutationPlannerResult:
        rendered = render_general_mutation_prompt(request)
        output_type = general_mutation_output_type(rendered)
        candidate = output_type.model_validate(
            {
                "operations": [
                    {
                        "operation_type": "create_object",
                        "operation_key": "create_agent_entity",
                        "local_ref": "new_entity",
                        "collection": "entities",
                        "fields": {"entity_type": "person", "name": "新人物"},
                        "depends_on_operation_keys": [],
                        "reason": "FakeProvider 通用修改候选。",
                    }
                ],
            }
        )
        usage = _zero_usage()
        request.emit(
            "model.completed",
            "general_mutation",
            {
                "usage": usage,
                "schema_id": rendered.output_schema_id,
            },
        )
        return GeneralMutationPlannerResult(candidate, usage)

    def repair_closure(
        self,
        request: ClosureRepairRequest,
    ) -> ClosureRepairProviderResult:
        rendered = render_closure_repair_prompt(request)
        schema_id = rendered.output_schema_id
        request.emit(
            "model.started",
            "closure_repair",
            {
                "model_id": request.model_id,
                "attempt_no": 1,
                "protocol": "fake_strict_schema",
                "component_id": request.component_id,
                "schema_id": schema_id,
            },
        )
        request.emit(
            "agent.model_call.started",
            "closure_repair",
            {
                "component_id": request.component_id,
                "schema_id": schema_id,
                "attempt_no": 1,
                "protocol": "fake_strict_schema",
                "model_id": request.model_id,
                "prompt_sha256": rendered.prompt_sha256,
            },
        )
        grouped: dict[tuple[str, str], list[str]] = {}
        for obligation in request.context["obligations"]:
            subject_id = str(obligation["subject_object_ids"][0])
            paths = {
                path
                for item in obligation["allowed_paths"]
                if item["object_id"] == subject_id
                for path in item["field_paths"]
            }
            field_path = "/status" if "/status" in paths else sorted(paths)[0]
            grouped.setdefault((subject_id, field_path), []).append(
                str(obligation["obligation_key"])
            )
        if schema_id == "closure-repair-output-v1":
            candidate: ClosureRepairOutputV1 | ClosureRepairOutputV2 | ClosureRepairOutputV3 = (
                ClosureRepairOutputV1(
                    operations=[
                        ClosureRepairOperationOutputV1(
                            obligation_keys=obligation_keys,
                            object_id=object_id,
                            field_path=field_path,
                            value_json=('"unresolved"' if field_path == "/status" else "[]"),
                            reason="将主张调整为与当前证据和依赖相容的最小状态。",
                        )
                        for (object_id, field_path), obligation_keys in grouped.items()
                    ]
                )
            )
        elif schema_id == "closure-repair-output-v2":
            candidate = ClosureRepairOutputV2(
                operations=[
                    (
                        ClaimStatusRepairOutputV2(
                            operation_type="claim_status",
                            obligation_keys=obligation_keys,
                            object_id=object_id,
                            field_path="/status",
                            value=ClaimStatus.unresolved,
                            reason="将主张调整为与当前证据和依赖相容的最小状态。",
                        )
                        if field_path == "/status"
                        else ClaimDependenciesRepairOutputV2(
                            operation_type="claim_dependencies",
                            obligation_keys=obligation_keys,
                            object_id=object_id,
                            field_path="/dependency_claim_refs",
                            value=[],
                            reason="将主张调整为与当前证据和依赖相容的最小状态。",
                        )
                    )
                    for (object_id, field_path), obligation_keys in grouped.items()
                ]
            )
        else:
            alternatives = request.context.get("repair_alternatives", [])
            if not alternatives:
                raise ValueError("closure_repair_fake_alternatives_missing")
            selected = min(
                alternatives,
                key=lambda item: (
                    item.get("outcome") != "repaired",
                    str(item.get("kind")),
                    str(item.get("alternative_id")),
                ),
            )
            candidate = ClosureRepairOutputV3(
                selected_alternative_id=str(selected["alternative_id"]),
                reason="选择服务器已经证明可进展的最小修复。",
            )
        usage = _zero_usage()
        raw_output = candidate.model_dump_json()
        request.emit("model.completed", "closure_repair", {"usage": usage})
        request.emit(
            "agent.model_call.completed",
            "closure_repair",
            {
                "component_id": request.component_id,
                "schema_id": schema_id,
                "attempt_no": 1,
                "protocol": "fake_strict_schema",
                "output_hash": sha256(raw_output.encode("utf-8")).hexdigest(),
                "output_size_bytes": len(raw_output.encode("utf-8")),
                "usage": usage,
                "_raw_output": raw_output,
                "raw_output_truncated": False,
            },
        )
        request.emit(
            "model.output_validated",
            "closure_repair",
            {
                "protocol": "fake_strict_schema",
                "attempt_count": 1,
                "repaired": False,
            },
        )
        return ClosureRepairProviderResult(candidate=candidate, usage=usage)

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

    def reverse_parse(self, request: ReverseParseRequest) -> ReverseParseResult:
        system_prompt_for_task("reverse_parse", request.prompt_version)
        request.emit("model.started", "parsing", {"model_id": request.model_id})
        items = [
            ReverseParseItem(
                item_type="entity_alias",
                content={"name": "林晚", "aliases": ["档案修复师"], "description": "主角"},
                grading="explicit",
                source_block_refs=[1],
                source_quote="档案修复师林晚",
            ),
            ReverseParseItem(
                item_type="event",
                content={
                    "title": "发现异常记录",
                    "order_index": 1,
                    "description": "林晚发现三份记录指向不存在的时间",
                },
                grading="explicit",
                source_block_refs=[1],
                source_quote="三份记录都指向一段不存在的时间",
            ),
            ReverseParseItem(
                item_type="candidate_question",
                content={"question": "是谁改写了记录中的时间？"},
                grading="needs_confirmation",
                source_block_refs=[1],
                source_quote="一段不存在的时间",
            ),
            ReverseParseItem(
                item_type="candidate_conclusion",
                content={"conclusion": "有人在封存前系统性地篡改了档案", "mode": "unique"},
                grading="inferred",
                source_block_refs=[1],
                source_quote="三份记录都指向一段不存在的时间",
            ),
        ]
        candidate = ReverseParseCandidate(items=items)
        usage = _zero_usage()
        request.emit("model.completed", "parsing", {"usage": usage})
        return ReverseParseResult(candidate=candidate, usage=usage)

    def generate_ideas(self, request: IdeaGenerationRequest) -> IdeaGenerationResult:
        system_prompt_for_task("idea_generation", request.prompt_version)
        request.emit("model.started", "generating_ideas", {"model_id": request.model_id})
        import random

        PROFESSIONS = [
            "法医",
            "退休警官",
            "调查记者",
            "档案管理员",
            "心理治疗师",
            "黑客",
            "保险理赔员",
            "古董估价师",
            "人类学家",
            "图书管理员",
            "前情报人员",
            "AI工程师",
            "天文台研究员",
            "海事调查员",
            "语言学教授",
            "游戏设计师",
            "刑辩律师",
            "气象学家",
            "策展人",
            "遗传学家",
        ]
        SETTINGS = [
            "小镇档案馆",
            "废弃的地下实验室",
            "远洋科考船",
            "百年图书馆",
            "私人侦探事务所",
            "无人机物流中心",
            "极地研究站",
            "古城遗址",
            "直播公司后台",
            "老式胶片放映厅",
            "虚拟现实服务器机房",
        ]
        PREMISES = [
            "发现一组无法解释的加密数据与未破悬案吻合",
            "收到已故之人的信件声称知道真相",
            "无意间目睹本应不存在的监控录像片段",
            "在一次例行检查中发现的异常引发了连锁追问",
            "多份看似无关的官方记录在时间线上形成闭合冲突",
            "一段被遗忘的往事因偶然事件重新浮出水面",
            "两个完全互斥的目击证词同时具有铁证支持",
            "一份遗物中的密码笔记指向跨越数十年的秘密",
        ]
        REASONING_TYPES: list[str] = ["deductive", "inductive", "abductive", "hybrid"]
        CONCLUSION_MODES: list[str] = ["author_anchored", "agent_proposed", "open"]
        EXPERIENCES = [
            "在碎片信息中逐一拼合真相的沉浸感",
            "时间压力下步步紧逼的紧迫感",
            "细碎线索汇聚成清晰逻辑链的解谜快感",
            "在看似无关的细节中发现隐秘联系的顿悟体验",
            "场景氛围与心理张力叠加的深度沉浸",
        ]
        RISKS = [
            "多条线索之间需要精确的时间与逻辑衔接",
            "关键信息揭露的时机直接影响叙事张力",
            "需要在专业准确性和叙事可读性之间取得平衡",
            "多视角叙述需要保持各信息源的可信度和差异性",
        ]
        SCALES = ["短篇（2-4 小时）", "中篇（5-8 小时）", "中篇（6-10 小时）", "长篇（15-25 小时）"]

        prefs = request.preferences or {}
        pref_settings = [str(s) for s in (prefs.get("settings") or [])]
        pref_eras = [str(e) for e in (prefs.get("eras") or [])]
        pref_atmospheres = [str(a) for a in (prefs.get("atmospheres") or [])]
        pref_keywords = [str(k) for k in (prefs.get("keywords") or [])]

        ERA_PROFESSIONS = {
            "古代": ["游侠", "宫廷画师", "驿站驿丞", "江湖郎中", "仵作"],
            "中世纪": ["骑士", "炼金术士", "修道院抄写员", "行会商人", "巡夜人"],
            "近代": ["私家侦探", "报社记者", "医生", "钟表匠", "海员"],
            "现代": ["法医", "黑客", "调查记者", "档案管理员", "心理治疗师"],
            "近未来": ["AI工程师", "无人机调度员", "记忆修复师", "基因顾问", "数据审计员"],
            "远未来": ["太空站研究员", "星际商贩", "量子考古学家", "殖民舰队领航员", "轨道清洁工"],
        }
        ATMOSPHERE_HINTS = {
            "温馨": ("温暖而细腻的异常", "在温情中逐步靠近真相的安心感"),
            "恐怖": ("令人不安的异象", "在压抑与恐惧中步步逼近真相的窒息感"),
            "悬疑": ("环环相扣的谜团", "在层层反转中拼合真相的解谜快感"),
            "轻松": ("轻松有趣的小谜团", "在幽默与轻松中推进调查的愉悦感"),
            "科幻": ("超越常识的技术谜团", "在硬核设定中探索未来的沉浸感"),
            "暗黑": ("深埋的黑暗真相", "在灰暗基调中直面人性的压抑感"),
            "浪漫": ("与情感交织的谜题", "在情感与推理交织中的沉浸感"),
            "热血": ("充满张力的对抗", "在高燃节奏中追逐真相的刺激感"),
        }

        def pick_profession(era: str) -> str:
            if era:
                pool = ERA_PROFESSIONS.get(era)
                if pool:
                    return random.choice(pool)
            return random.choice(PROFESSIONS)

        chosen = []
        for i in range(3):
            era = random.choice(pref_eras) if pref_eras else ""
            setting = random.choice(pref_settings) if pref_settings else random.choice(SETTINGS)
            atmosphere = random.choice(pref_atmospheres) if pref_atmospheres else ""
            prof = pick_profession(era)
            prem = random.choice(PREMISES)

            era_text = f"{era}背景下，" if era else ""
            atm_text = f"{atmosphere}氛围的" if atmosphere else ""
            concept = f"{era_text}{atm_text}{prof}在{setting}——{prem}"
            if pref_keywords:
                concept += f"，围绕「{'、'.join(pref_keywords)}」展开"
            concept += "。"

            hint = ATMOSPHERE_HINTS.get(atmosphere) if atmosphere else None
            suspense_core = hint[0] if hint else "看似平常的迹象中隐藏的模式"
            experience = hint[1] if hint else random.choice(EXPERIENCES)

            chosen.append(
                {
                    "concept": concept,
                    "core_suspense": (
                        f"主角必须从{suspense_core}中锁定真相，同时应对来自"
                        f"{random.choice(['同行质疑', '权力掩盖', '公众误解', '时间毁灭'])}"
                        "的外部压力。"
                    ),
                    "reasoning_type": REASONING_TYPES[i % len(REASONING_TYPES)],
                    "conclusion_mode": CONCLUSION_MODES[i % len(CONCLUSION_MODES)],
                    "target_experience": experience,
                    "design_risk": random.choice(RISKS),
                    "scale_estimate": random.choice(SCALES),
                }
            )

        if request.regenerate and request.existing_concepts:
            for c in chosen:
                while c["concept"] in request.existing_concepts:
                    c["concept"] = (
                        f"{random.choice(PROFESSIONS)}在{random.choice(SETTINGS)}"
                        f"——{random.choice(PREMISES)}（新方向）。"
                    )

        candidate = IdeaCandidateSet(
            candidates=[IdeaCandidateModel.model_validate(c) for c in chosen]
        )
        usage = _zero_usage()
        request.emit("model.completed", "generating_ideas", {"usage": usage})
        return IdeaGenerationResult(candidate=candidate, usage=usage)

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        if request.prompt_version in {
            "casefile-chat-v14",
            "casefile-chat-v15",
            "casefile-chat-v16",
        }:
            return self._chat_v14(request)
        render_chat_executor_prompt(request)
        request.emit("model.started", "responding", {"model_id": request.model_id})
        referenced = [
            object_id
            for object_id in _casefile_object_ids(request.casefile)
            if object_id in request.message
        ]
        metrics = _fake_chat_tool_metrics(request)
        candidate = CaseFileChatCandidate(
            answer=(
                "我已结合当前完整卷宗阅读了这条消息。"
                "本次没有自动修改工作稿；如需改动，我会先给出可逐项审阅的字段建议。"
            ),
            referenced_object_ids=referenced,
            suggestions=[],
        )
        usage: dict[str, Any] = _zero_usage()
        usage["tool_metrics"] = metrics.as_dict()
        request.emit("model.completed", "responding", {"usage": usage})
        return CaseFileChatResult(candidate=candidate, usage=usage, tools=metrics)

    def _chat_v14(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        render_chat_executor_prompt(request)
        metrics = _fake_chat_tool_metrics(request)
        ledger_payload = request.frozen_tool_ledger
        if ledger_payload is None:
            request.emit(
                "model.tool_agent.started",
                "gathering_evidence",
                {"model_id": request.model_id},
            )
            if request.route is not None:
                context = ChatToolContext(request=request, route=request.route, metrics=metrics)
                ledger_payload = freeze_chat_tool_ledger(
                    context,
                    evidence_summary="FakeProvider 已核对冻结输入，未发现需要补充的外部证据。",
                ).as_dict()
            request.emit(
                "model.tool_agent.completed",
                "gathering_evidence",
                {"usage": _zero_usage(), "tool_calls": metrics.calls},
            )
            request.emit(
                "model.tool_ledger.frozen",
                "finalizing",
                {
                    "ledger_hash": None
                    if ledger_payload is None
                    else ledger_payload.get("ledger_hash"),
                    "entry_count": 0
                    if ledger_payload is None
                    else len(ledger_payload.get("entries", [])),
                },
            )
        request, safe_patch_registry = _bind_safe_patch_registry(request, ledger_payload)
        finalizer_instructions, _finalizer_input = render_chat_finalizer_prompt(
            request,
            tool_ledger=ledger_payload,
            evidence_summary=""
            if ledger_payload is None
            else str(ledger_payload.get("evidence_summary") or ""),
            previous_candidate=request.previous_candidate,
            repair_plan=request.repair_plan,
        )
        output_type = chat_finalizer_output_type(request)
        finalizer_component_id, finalizer_schema_id = chat_finalizer_component(request)
        request.emit(
            "model.finalizer.started",
            "finalizing",
            {
                "model_id": request.model_id,
                "schema_id": finalizer_schema_id,
                "repair": request.repair_plan is not None,
            },
        )
        request.emit(
            "agent.model_call.started",
            "finalizing",
            {
                "component_id": finalizer_component_id,
                "schema_id": finalizer_schema_id,
                "attempt_no": 1,
                "protocol": "fake_strict",
                "model_id": request.model_id,
                "prompt_sha256": sha256(finalizer_instructions.encode("utf-8")).hexdigest(),
            },
        )
        referenced = [
            object_id
            for object_id in _casefile_object_ids(request.casefile)
            if object_id in request.message
        ]
        candidate = output_type.model_validate(
            {
                "value_json": '"已根据冻结证据补充说明。"',
                "reason": "补丁仅修正服务器锁定的审计目标。",
            }
            if output_type is CaseFileChatTargetLockedRepairOutput
            else {
                "answer": "我已核对冻结卷宗；本次没有自动修改工作稿。",
                "referenced_object_ids": referenced,
                "suggestions": [],
                **({"audit_findings": []} if output_type is CaseFileChatCandidateV2 else {}),
            }
        )
        usage = _zero_usage()
        serialized_output = candidate.model_dump_json().encode("utf-8")
        request.emit(
            "agent.model_call.completed",
            "finalizing",
            {
                "component_id": finalizer_component_id,
                "schema_id": finalizer_schema_id,
                "attempt_no": 1,
                "protocol": "fake_strict",
                "output_hash": sha256(serialized_output).hexdigest(),
                "output_size_bytes": len(serialized_output),
                "usage": usage,
            },
        )
        request.emit(
            "model.finalizer.completed",
            "finalizing",
            {"usage": usage, "repair": request.repair_plan is not None},
        )
        return CaseFileChatResult(
            candidate=cast(
                CaseFileChatCandidate | CaseFileChatCandidateV2,
                candidate,
            ),
            usage=usage,
            tools=metrics,
            tool_ledger=ledger_payload,
            safe_patch_registry=safe_patch_registry,
        )

    def compact_thread_memory(
        self,
        request: ThreadCompactionRequest,
    ) -> ThreadCompactionResult:
        """Deterministic fake compactor: old state is carried by the merger."""

        old_state = request.input_data.get("old_state")
        topics = list(old_state.get("topics") or []) if isinstance(old_state, dict) else []
        usage: dict[str, Any] = _zero_usage()
        request.emit("model.completed", "compacting", {"usage": usage})
        return ThreadCompactionResult(
            candidate=ThreadMemoryDelta(topics=topics),
            usage=usage,
        )

    def understand_intent(self, request: CaseFileChatRequest) -> IntentUnderstandingResult:
        request.emit("model.started", "understanding", {"model_id": request.model_id})
        request.emit(
            "agent.model_call.started",
            "understanding",
            {
                "component_id": "intent_router",
                "schema_id": "chat-task-understanding-v1",
                "attempt_no": 1,
                "protocol": "fake_strict",
                "model_id": request.model_id,
            },
        )
        candidate = _fake_intent_understanding(request.message)
        request.emit(
            "agent.model_call.completed",
            "understanding",
            {
                "component_id": "intent_router",
                "schema_id": "chat-task-understanding-v1",
                "attempt_no": 1,
                "protocol": "fake_strict",
                "output_hash": sha256(
                    json.dumps(
                        candidate.model_dump(mode="json"),
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest(),
                "usage": _zero_usage(),
            },
        )
        usage = _zero_usage()
        request.emit("model.completed", "understanding", {"usage": usage})
        return IntentUnderstandingResult(candidate=candidate, usage=usage)

    def rewrite_for_route(
        self,
        request: RouteSpecificRewriteRequest,
    ) -> RouteSpecificRewriteResult:
        request.emit("model.started", "rewriting", {"model_id": request.model_id})
        request.emit(
            "agent.model_call.started",
            "rewriting",
            {
                "component_id": "query_rewriter",
                "schema_id": "query-rewrite-v1",
                "attempt_no": 1,
                "protocol": "fake_strict",
                "model_id": request.model_id,
            },
        )
        if request.rewrite_strategy == "MULTI_QUERY":
            retrieval_queries = [
                request.conservative_canonical_query,
                f"{request.conservative_canonical_query} 时间线",
                f"{request.conservative_canonical_query} 引用关系",
            ]
        else:
            retrieval_queries = []
        candidate = QueryRewriteOutput(
            canonical_query=request.conservative_canonical_query,
            retrieval_queries=retrieval_queries,
            rewrite_decision=cast(Any, request.rewrite_strategy),
            preservation_checks={},
        )
        request.emit(
            "agent.model_call.completed",
            "rewriting",
            {
                "component_id": "query_rewriter",
                "schema_id": "query-rewrite-v1",
                "attempt_no": 1,
                "protocol": "fake_strict",
                "output_hash": sha256(
                    json.dumps(
                        candidate.model_dump(mode="json"),
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest(),
                "usage": _zero_usage(),
            },
        )
        usage = _zero_usage()
        request.emit("model.completed", "rewriting", {"usage": usage})
        return RouteSpecificRewriteResult(candidate=candidate, usage=usage)

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if request.prompt_version in COMPONENT_GENERATION_PROMPT_VERSIONS:

            async def call_component(
                _instructions: str,
                input_text: str,
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
                if output_type.__name__ == "MatrixEvaluationOutputV1":
                    output = _fake_matrix_evaluation_output(json.loads(input_text))
                else:
                    output = _fake_v8_output(output_type)
                    if resolve_pipeline_spec(request.prompt_version).features.competition_matrix:
                        _add_fake_v10_matrix_plan(output_type, output)
                    if output_type.__name__ in {
                        "ResolutionGovernanceIRV1",
                        "ResolutionGovernanceIRV2",
                    }:
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

            runner = _brief_to_draft_runner(request.prompt_version)
            return cast(
                GenerationResult,
                asyncio.run(runner(request, call_component=call_component)),
            )
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
            "schema_version": request.schema_version,
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
            "reasoning_paths": [node("path", "验证路径", ["record", "claim", "hypothesis"])],
            "constraints": [node("constraint", "作者边界", ["resolution"])],
            "structure_locks": [node("lock", "事件标题锁", ["discovery"])],
        }
    if output_type.__name__ in {"StoryWorldIRV1", "StoryWorldIRV2", "StoryWorldIRV3"}:
        output: dict[str, Any] = {
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
                    "participant_keys": ["author"],
                    "location_key": "archive",
                    "cause_keys": [],
                    "effect_keys": [],
                    "observed_by_keys": ["author"],
                }
            ],
        }
        if output_type.__name__ == "StoryWorldIRV3":
            output["schema_id"] = "story-world-ir-v3"
        elif output_type.__name__ == "StoryWorldIRV2":
            output["schema_id"] = "story-world-ir-v2"
        if output_type.__name__ == "StoryWorldIRV2":
            output["events"][0]["time"] = {
                "kind": "exact",
                "value": "2026-08-08T08:00",
                "precision": "minute",
            }
        elif output_type.__name__ == "StoryWorldIRV1":
            output["events"][0]["time"] = {
                "start": "2026-08-08T08:00:00Z",
                "end": None,
                "precision": "minute",
            }
        return output
    if output_type.__name__ == "TemporalPlanV1":
        return {
            "assignments": [
                {
                    "event_key": "discovery",
                    "time": {
                        "kind": "exact",
                        "value": "2026-08-08T08:00",
                        "precision": "minute",
                    },
                    "basis": "design_anchor",
                    "basis_refs": [],
                }
            ]
        }
    if output_type.__name__ in {"EvidenceLogicIRV1", "EvidenceLogicIRV2"}:
        output = {
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
        if output_type.__name__ == "EvidenceLogicIRV2":
            output["schema_id"] = "evidence-logic-ir-v2"
            output["hypotheses"] = [
                {
                    "local_key": "hypothesis",
                    **common,
                    "title": "记录解释",
                    "proposition": "记录内容对应真实发生的事件。",
                    "target_resolution_key": "resolution",
                    "required_claim_keys": ["claim"],
                    "falsifier_keys": [],
                    "competing_hypothesis_keys": ["alternative_hypothesis"],
                    "evidence_assessments": [
                        {
                            "information_key": "record",
                            "effect": "supports",
                            "strength": "strong",
                            "rationale": "关键记录与已知发现时间一致。",
                        }
                    ],
                    "status": "supported",
                    "score": 0.9,
                },
                {
                    "local_key": "alternative_hypothesis",
                    **common,
                    "title": "记录误导",
                    "proposition": "记录内容经过事后篡改，不能直接说明事件真实经过。",
                    "target_resolution_key": "resolution",
                    "required_claim_keys": ["claim"],
                    "falsifier_keys": [],
                    "competing_hypothesis_keys": ["hypothesis"],
                    "evidence_assessments": [
                        {
                            "information_key": "record",
                            "effect": "contradicts",
                            "strength": "moderate",
                            "rationale": "记录来源尚未独立验证，存在篡改可能。",
                        }
                    ],
                    "status": "undetermined",
                    "score": 0.4,
                },
            ]
            output["reasoning_paths"] = [
                {
                    "local_key": "path",
                    **common,
                    "title": "记录支持路径",
                    "path_type": "proof",
                    "target_key": "hypothesis",
                    "steps": [
                        {
                            "step_key": "verify_record",
                            "input_keys": ["record"],
                            "operation": "infer",
                            "output_key": "claim",
                        }
                    ],
                    "required_for_resolution": True,
                    "alternative_path_keys": ["alternative_path"],
                },
                {
                    "local_key": "alternative_path",
                    **common,
                    "title": "记录冲突路径",
                    "path_type": "proof",
                    "target_key": "alternative_hypothesis",
                    "steps": [
                        {
                            "step_key": "challenge_record",
                            "input_keys": ["record"],
                            "operation": "compare",
                            "output_key": "claim",
                        }
                    ],
                    "required_for_resolution": True,
                    "alternative_path_keys": ["path"],
                },
            ]
        return output
    if output_type.__name__ in {"ResolutionGovernanceIRV1", "ResolutionGovernanceIRV2"}:
        output = {
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
        if output_type.__name__ == "ResolutionGovernanceIRV2":
            output["resolution_specs"][0]["conclusion"] = {
                "outcome": "answer",
                "summary": "关键记录可信，可以作为当前核心问题的答案。",
                "values": [],
                "selected_hypothesis_keys": ["hypothesis"],
                "supporting_reasoning_path_keys": ["path"],
                "rationale": "记录内容与支持性信息一致，竞争解释仍缺少直接依据。",
                "unresolved_gaps": [],
            }
            output["schema_id"] = "resolution-governance-ir-v2"
        return output
    raise ProviderProtocolError(f"Fake v8 component is unsupported: {output_type.__name__}")


def _fake_matrix_evaluation_output(payload: dict[str, Any]) -> dict[str, Any]:
    """Return one deterministic fake judgment per requested matrix cell."""

    effects = ("supports", "contradicts", "neutral")
    raw_cells = payload.get("cells")
    cells = raw_cells if isinstance(raw_cells, list) else []
    return {
        "assessments": [
            {
                "hypothesis_key": cell["hypothesis_key"],
                "information_key": cell["information_key"],
                "effect": effects[index % 3],
                "strength": "moderate",
                "rationale": (
                    f"信息 {cell['information_key']} 与假设 {cell['hypothesis_key']} "
                    "命题的一致性支持当前判定。"
                ),
            }
            for index, cell in enumerate(cells)
            if isinstance(cell, dict)
            and isinstance(cell.get("hypothesis_key"), str)
            and isinstance(cell.get("information_key"), str)
        ]
    }


def _add_fake_v10_matrix_plan(output_type: type[BaseModel], output: dict[str, Any]) -> None:
    """Keep the FakeProvider plan aligned with v10's two explicit competitors."""

    if output_type.__name__ != "CaseBlueprintV1":
        return
    output["hypotheses"].append(
        {
            "local_key": "alternative_hypothesis",
            "title": "记录误导",
            "purpose": "与记录解释竞争的可检验替代假设。",
            "dependency_keys": ["resolution", "claim"],
        }
    )
    if output["reasoning_paths"]:
        output["reasoning_paths"][0]["target_key"] = "hypothesis"
        output["reasoning_paths"][0]["required_information_keys"] = ["record"]
    output["reasoning_paths"].append(
        {
            "local_key": "alternative_path",
            "title": "记录冲突路径",
            "purpose": "验证记录是否可能被事后篡改。",
            "dependency_keys": ["record", "claim", "alternative_hypothesis"],
            "target_key": "alternative_hypothesis",
            "required_information_keys": ["record"],
        }
    )


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


def _fake_story_plan_candidate(planner_input: dict[str, Any]) -> dict[str, Any]:
    constraints = planner_input["planning_constraints"]
    narrative = planner_input["narrative_ir"]
    objects = narrative["objects"]
    all_refs = [
        envelope["object_ref"] for collection in objects.values() for envelope in collection
    ]
    basis = all_refs[:1] or [narrative["source"]["casefile_ref"]]
    entities = [ref for ref in all_refs if ref["object_type"] == "entity"]
    locations = [ref for ref in all_refs if ref["object_type"] == "location"]
    events = [ref for ref in all_refs if ref["object_type"] == "event"]
    resolutions = [ref for ref in all_refs if ref["object_type"] == "resolution_spec"]
    exposure = planner_input.get("exposure_plan")
    entries = [] if exposure is None else exposure["frozen_payload"].get("entries", [])
    chapter_count = constraints["target_chapters"]
    scene_count = constraints["target_scenes"]
    chapters = [
        {
            "chapter_id": f"chapter_{index}",
            "ordinal": index,
            "act_ordinal": min(3, ((index - 1) * 3 // chapter_count) + 1),
            "title": f"第{index}章",
        }
        for index in range(1, chapter_count + 1)
    ]
    scenes = []
    for index in range(1, scene_count + 1):
        chapter_index = min(
            chapter_count,
            ((index - 1) * chapter_count // scene_count) + 1,
        )
        scene_exposure = (
            [{"entry_key": item["entry_key"], "action": "introduce"} for item in entries]
            if index == 1
            else []
        )
        scene_resolutions = (
            [{"resolution_ref": ref, "action": "resolve"} for ref in resolutions]
            if index == scene_count
            else []
        )
        scenes.append(
            {
                "scene_id": f"scene_{index}",
                "chapter_id": f"chapter_{chapter_index}",
                "discourse_order": index,
                "purpose": "resolution" if index == scene_count else "investigation",
                "intent": f"推进冻结事实所支持的第 {index} 个叙事节点。",
                "presentation_mode": constraints["allowed_presentation_modes"][0],
                "pov_ref": entities[0] if entities else None,
                "participant_refs": entities[:1],
                "location_ref": locations[0] if locations else None,
                "event_refs": events[:1],
                "story_time_refs": events[:1],
                "basis_refs": basis,
                "exposure": scene_exposure,
                "resolutions": scene_resolutions,
                "prerequisite_scene_ids": [] if index == 1 else [f"scene_{index - 1}"],
            }
        )
    return {
        "schema_id": "compiler.novel-plan-candidate.v1",
        "chapters": chapters,
        "scenes": scenes,
    }


def _first_ref(values: list[dict[str, Any]]) -> dict[str, str] | None:
    if not values:
        return None
    return cast(dict[str, str], values[0]["object_ref"])


__all__ = ["FakeProvider"]
