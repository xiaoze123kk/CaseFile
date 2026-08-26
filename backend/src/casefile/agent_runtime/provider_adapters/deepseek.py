"""DeepSeek Agent provider adapter."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal, cast

from agents import Tool
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from casefile_contracts import (
    BriefIntakeCandidate as BriefIntakeCandidateContract,
)
from casefile_contracts import (
    BriefIntakeQuestionSet as BriefIntakeQuestionSetContract,
)
from casefile_contracts import (
    NovelPlanCandidate,
    SemanticFillProposal,
    SkeletonProposal,
    StoryPlanStructuralPatch,
)
from openai import AsyncOpenAI
from pydantic import BaseModel

from casefile.agent_runtime.chat_tools import (
    ChatToolContext,
    ChatToolLedger,
)
from casefile.agent_runtime.closure_repair import (
    ClosureRepairOutputV1,
    ClosureRepairOutputV2,
    ClosureRepairOutputV3,
    ClosureRepairProviderResult,
    ClosureRepairRequest,
)
from casefile.agent_runtime.closure_repair_prompt import (
    closure_repair_output_type,
    render_closure_repair_prompt,
)
from casefile.agent_runtime.constraint_first_story_planner import (
    SemanticFillRequest,
    SemanticFillResult,
    SkeletonProposalRequest,
    SkeletonProposalResult,
)
from casefile.agent_runtime.constraint_first_story_planner_prompt import (
    render_semantic_fill_prompt,
    render_skeleton_proposal_prompt,
)
from casefile.agent_runtime.context.thread_memory import (
    ThreadCompactionRequest,
    ThreadCompactionResult,
    ThreadMemoryDelta,
)
from casefile.agent_runtime.general_mutation import (
    GENERAL_MUTATION_COMPONENT_ID,
    GeneralMutationPlannerRequest,
    GeneralMutationPlannerResult,
)
from casefile.agent_runtime.general_mutation_prompt import (
    general_mutation_output_type,
    render_general_mutation_prompt,
)
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
    BriefStrategyOptionsCandidate,
    BriefStrategyOptionsRequest,
    BriefStrategyOptionsResult,
    CaseFileChatCandidate,
    CaseFileChatCandidateV2,
    CaseFileChatRequest,
    CaseFileChatResult,
    ChatTaskUnderstandingOutput,
    GenerationRequest,
    GenerationResult,
    IdeaCandidateSet,
    IdeaGenerationRequest,
    IdeaGenerationResult,
    IntentUnderstandingResult,
    QueryRewriteOutput,
    ReverseParseCandidate,
    ReverseParseRequest,
    ReverseParseResult,
    RouteSpecificRewriteRequest,
    RouteSpecificRewriteResult,
    ToolMetrics,
)
from casefile.agent_runtime.prompt import (
    COMPONENT_GENERATION_PROMPT_VERSIONS,
    anchor_extract_input,
    brief_intake_questions_input,
    brief_intake_synthesize_input,
    brief_strategy_options_input,
    chat_executor_output_type,
    chat_finalizer_output_type,
    idea_generation_input,
    polish_input,
    render_chat_executor_prompt,
    render_chat_finalizer_prompt,
    render_chat_rewrite_prompt,
    render_chat_router_prompt,
    reverse_parse_input,
    thread_compaction_input,
)
from casefile.agent_runtime.prompt_repository import (
    component_prompt_for_task,
    system_prompt_for_task,
)
from casefile.agent_runtime.provider_adapters.generation import (
    _brief_to_draft_runner,
    _run_partitioned_generation,
)
from casefile.agent_runtime.provider_adapters.protocols import ProviderProtocolError
from casefile.agent_runtime.provider_adapters.shared import (
    _bind_safe_patch_registry,
    _chat_live_temperature,
    _chat_tool_runtime,
    _deepseek_model_settings,
    _deepseek_v8_output_protocol,
    _frozen_evidence_summary,
    _model_client,
    _run_agent,
    _run_auxiliary_agent,
    _run_chat_tool_agent,
    _validate_polish_candidate,
)
from casefile.agent_runtime.story_planner import (
    StoryPlannerPatchProviderResult,
    StoryPlannerPatchRequest,
    StoryPlannerProviderResult,
    StoryPlannerRequest,
)
from casefile.agent_runtime.story_planner_prompt import (
    render_story_planner_patch_prompt,
    render_story_planner_prompt,
)
from casefile.agent_runtime.structured_output import (
    merge_usage as _merge_structured_usage,
)


class DeepSeekAgentsProvider:
    """DeepSeek OpenAI-compatible Chat Completions implementation."""

    base_url = "https://api.deepseek.com"

    def propose_skeleton(
        self, request: SkeletonProposalRequest
    ) -> SkeletonProposalResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        instructions, input_text, _prompt_hash = render_skeleton_proposal_prompt(request)
        proposal, usage, raw_output = asyncio.run(
            self._story_planner_json_object(
                request,
                instructions=instructions,
                input_text=input_text,
                output_type=SkeletonProposal,
                schema_id="compiler.skeleton-proposal.v1",
                stage="skeleton_proposal",
            )
        )
        return SkeletonProposalResult(proposal, usage, raw_output)

    def fill_semantics(self, request: SemanticFillRequest) -> SemanticFillResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        instructions, input_text, _prompt_hash = render_semantic_fill_prompt(request)
        fill, usage, raw_output = asyncio.run(
            self._story_planner_json_object(
                request,
                instructions=instructions,
                input_text=input_text,
                output_type=SemanticFillProposal,
                schema_id="compiler.semantic-fill.v1",
                stage="semantic_fill",
            )
        )
        return SemanticFillResult(fill, usage, raw_output)

    def plan_story(self, request: StoryPlannerRequest) -> StoryPlannerProviderResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        instructions, input_text, _prompt_hash = render_story_planner_prompt(request)
        candidate, usage, raw_output = asyncio.run(
            self._story_planner_json_object(
                request,
                instructions=instructions,
                input_text=input_text,
                output_type=NovelPlanCandidate,
                schema_id="compiler.novel-plan-candidate.v1",
                stage="story_planner",
            )
        )
        return StoryPlannerProviderResult(
            candidate=candidate,
            usage=usage,
            raw_output=raw_output,
        )

    def patch_story(
        self, request: StoryPlannerPatchRequest
    ) -> StoryPlannerPatchProviderResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        instructions, input_text, _prompt_hash = render_story_planner_patch_prompt(request)
        patch, usage, raw_output = asyncio.run(
            self._story_planner_json_object(
                request,
                instructions=instructions,
                input_text=input_text,
                output_type=StoryPlanStructuralPatch,
                schema_id="compiler.story-plan-structural-patch.v1",
                stage="story_planner_structural_patch",
            )
        )
        return StoryPlannerPatchProviderResult(
            patch=patch,
            usage=usage,
            raw_output=raw_output,
        )

    async def _story_planner_json_object(
        self,
        request: (
            StoryPlannerRequest
            | StoryPlannerPatchRequest
            | SkeletonProposalRequest
            | SemanticFillRequest
        ),
        *,
        instructions: str,
        input_text: str,
        output_type: type[BaseModel],
        schema_id: str,
        stage: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        """Return the raw candidate so the outer bounded repair loop owns validation."""

        client = AsyncOpenAI(
            api_key=request.api_key,
            base_url=self.base_url,
            max_retries=request.network_retries,
        )
        schema_text = json.dumps(
            output_type.model_json_schema(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        request.emit(
            "agent.model_call.started",
            stage,
            {
                "component_id": "story_planner",
                "schema_id": schema_id,
                "protocol": "json_object",
                "model_id": request.model_id,
            },
        )
        try:
            response = await client.chat.completions.create(
                model=request.model_id,
                messages=[
                    {
                        "role": "system",
                        "content": instructions
                        + "\n\n必须严格遵守以下 JSON Schema：\n"
                        + schema_text,
                    },
                    {"role": "user", "content": input_text},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                extra_body={"thinking": {"type": "disabled"}},
            )
        finally:
            await client.close()
        if len(response.choices) != 1:
            raise ProviderProtocolError("DeepSeek Story Planner returned an invalid choice count")
        raw_output = response.choices[0].message.content
        if not raw_output:
            raise ProviderProtocolError("DeepSeek Story Planner returned no content")
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError:
            parsed = {}
        candidate = parsed if isinstance(parsed, dict) else {}
        response_usage = response.usage
        usage = {
            "requests": 1,
            "input_tokens": int(getattr(response_usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(response_usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(response_usage, "total_tokens", 0) or 0),
            "cached_tokens": int(getattr(response_usage, "prompt_cache_hit_tokens", 0) or 0),
            "reasoning_tokens": 0,
        }
        request.emit(
            "agent.model_call.completed",
            stage,
            {
                "component_id": "story_planner",
                "schema_id": schema_id,
                "protocol": "json_object",
                "model_id": request.model_id,
                "usage": usage,
            },
        )
        return candidate, usage, raw_output
    def plan_general_mutation(
        self,
        request: GeneralMutationPlannerRequest,
    ) -> GeneralMutationPlannerResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        rendered = render_general_mutation_prompt(request)
        output_type = general_mutation_output_type(rendered)
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=rendered.instructions,
                input_text=rendered.input_text,
                output_type=output_type,
                stage="general_mutation",
                component_id=GENERAL_MUTATION_COMPONENT_ID,
                schema_id=rendered.output_schema_id,
                deepseek_output_protocol="json_object",
                deepseek_output_protocol_is_primary=True,
                strict_validation=True,
            )
        )
        return GeneralMutationPlannerResult(output_type.model_validate(candidate), usage)

    def repair_closure(
        self,
        request: ClosureRepairRequest,
    ) -> ClosureRepairProviderResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        rendered = render_closure_repair_prompt(request)
        output_type = closure_repair_output_type(rendered)
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=rendered.instructions,
                input_text=rendered.input_text,
                output_type=output_type,
                stage="closure_repair",
                component_id=request.component_id,
                schema_id=rendered.output_schema_id,
                deepseek_output_protocol="strict_tool",
                strict_validation=True,
            )
        )
        return ClosureRepairProviderResult(
            candidate=cast(
                ClosureRepairOutputV1 | ClosureRepairOutputV2 | ClosureRepairOutputV3,
                output_type.model_validate(candidate),
            ),
            usage=usage,
        )

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

    def generate_ideas(self, request: IdeaGenerationRequest) -> IdeaGenerationResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task("idea_generation", request.prompt_version),
                input_text=idea_generation_input(
                    request.input_hash,
                    regenerate=request.regenerate,
                    existing_concepts=request.existing_concepts,
                    preferences=request.preferences,
                ),
                output_type=IdeaCandidateSet,
                stage="generating_ideas",
            )
        )
        return IdeaGenerationResult(
            candidate=IdeaCandidateSet.model_validate(candidate),
            usage=usage,
        )

    def reverse_parse(self, request: ReverseParseRequest) -> ReverseParseResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=system_prompt_for_task("reverse_parse", request.prompt_version),
                input_text=reverse_parse_input(request.blocks, request.input_hash),
                output_type=ReverseParseCandidate,
                stage="parsing",
            )
        )
        return ReverseParseResult(
            candidate=ReverseParseCandidate.model_validate(candidate),
            usage=usage,
        )

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        if request.prompt_version in {"casefile-chat-v14", "casefile-chat-v15"}:
            return self._chat_v14(request)
        instructions, input_text = render_chat_executor_prompt(request)
        tools, context, max_turns = _chat_tool_runtime(request)
        output_type = chat_executor_output_type(request)
        output_protocol = "json_object" if tools else _deepseek_v8_output_protocol(request.model_id)
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=instructions,
                input_text=input_text,
                output_type=output_type,
                stage="responding",
                tools=tools,
                context=context,
                max_turns=max_turns,
                deepseek_output_protocol=output_protocol,
                deepseek_output_protocol_is_primary=bool(tools),
                temperature=_chat_live_temperature(),
            )
        )
        return CaseFileChatResult(
            candidate=cast(
                CaseFileChatCandidate | CaseFileChatCandidateV2,
                output_type.model_validate(candidate),
            ),
            usage=usage,
            tools=context.metrics if context is not None else ToolMetrics(),
        )

    def _chat_v14(self, request: CaseFileChatRequest) -> CaseFileChatResult:
        instructions, input_text = render_chat_executor_prompt(request)
        tools, context, max_turns = _chat_tool_runtime(request)
        usage_records: list[dict[str, Any]] = []
        ledger_payload = request.frozen_tool_ledger
        evidence_summary = ""
        metrics: ToolMetrics = ToolMetrics()
        if ledger_payload is not None:
            evidence_summary = str(ledger_payload.get("evidence_summary") or "")
        elif tools and context is not None:
            try:
                ledger, tool_usage = asyncio.run(
                    self._gather_chat_evidence(
                        request,
                        instructions=instructions,
                        input_text=input_text,
                        tools=tools,
                        context=context,
                        max_turns=max_turns,
                    )
                )
            except Exception as error:
                if request.prompt_version != "casefile-chat-v15":
                    raise
                request.emit(
                    "model.tool_agent.failed",
                    "gathering_evidence",
                    {
                        "reason_code": "evidence_agent_failed",
                        "error_class": type(error).__name__,
                        "fallback": "frozen_bundle",
                        "tool_calls": context.metrics.calls,
                        "successful_calls": context.metrics.successful_calls,
                    },
                )
                ledger = None
                tool_usage = {}
                evidence_summary = _frozen_evidence_summary(context)
            if ledger is not None:
                ledger_payload = ledger.as_dict()
                evidence_summary = ledger.evidence_summary
                usage_records.append(tool_usage)
            metrics = context.metrics
        request, safe_patch_registry = _bind_safe_patch_registry(request, ledger_payload)
        finalizer_instructions, finalizer_input = render_chat_finalizer_prompt(
            request,
            tool_ledger=ledger_payload,
            evidence_summary=evidence_summary,
            previous_candidate=request.previous_candidate,
            repair_plan=request.repair_plan,
        )
        output_type = chat_finalizer_output_type(request)
        request.emit(
            "model.finalizer.started",
            "finalizing",
            {
                "model_id": request.model_id,
                "schema_id": output_type.__name__,
                "ledger_hash": (
                    None if ledger_payload is None else ledger_payload.get("ledger_hash")
                ),
                "repair": request.repair_plan is not None,
            },
        )
        candidate, finalizer_usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=finalizer_instructions,
                input_text=finalizer_input,
                output_type=output_type,
                stage="finalizing",
                deepseek_output_protocol=_deepseek_v8_output_protocol(request.model_id),
                temperature=_chat_live_temperature(),
            )
        )
        usage_records.append(finalizer_usage)
        request.emit(
            "model.finalizer.completed",
            "finalizing",
            {"usage": finalizer_usage, "repair": request.repair_plan is not None},
        )
        return CaseFileChatResult(
            candidate=cast(
                CaseFileChatCandidate | CaseFileChatCandidateV2,
                output_type.model_validate(candidate),
            ),
            usage=_merge_structured_usage(usage_records),
            tools=metrics,
            tool_ledger=ledger_payload,
            safe_patch_registry=safe_patch_registry,
        )

    async def _gather_chat_evidence(
        self,
        request: CaseFileChatRequest,
        *,
        instructions: str,
        input_text: str,
        tools: list[Tool],
        context: ChatToolContext,
        max_turns: int | None,
    ) -> tuple[ChatToolLedger, dict[str, Any]]:
        model = self.create_model(request)
        client = _model_client(model)
        try:
            return await _run_chat_tool_agent(
                request,
                model=model,
                model_settings=_deepseek_model_settings(temperature=_chat_live_temperature()),
                instructions=instructions,
                input_text=input_text,
                tools=tools,
                context=context,
                max_turns=max_turns,
                tracing_disabled=True,
            )
        finally:
            if client is not None:
                await client.close()

    def compact_thread_memory(
        self,
        request: ThreadCompactionRequest,
    ) -> ThreadCompactionResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=component_prompt_for_task(
                    "casefile_chat_context_compactor",
                    request.prompt_version,
                    "compact",
                ),
                input_text=thread_compaction_input(request),
                output_type=ThreadMemoryDelta,
                stage="compacting",
                component_id="context_compactor",
                schema_id="casefile-chat-thread-memory-delta-v1",
                deepseek_output_protocol=_deepseek_v8_output_protocol(
                    request.model_id,
                ),
            )
        )
        return ThreadCompactionResult(
            candidate=ThreadMemoryDelta.model_validate(candidate),
            usage=usage,
        )

    def understand_intent(self, request: CaseFileChatRequest) -> IntentUnderstandingResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        instructions, input_text = render_chat_router_prompt(request)
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=instructions,
                input_text=input_text,
                output_type=ChatTaskUnderstandingOutput,
                stage="understanding",
                component_id="intent_router",
                schema_id="chat-task-understanding-v1",
                temperature=_chat_live_temperature(),
            )
        )
        return IntentUnderstandingResult(
            candidate=ChatTaskUnderstandingOutput.model_validate(candidate),
            usage=usage,
        )

    def rewrite_for_route(
        self,
        request: RouteSpecificRewriteRequest,
    ) -> RouteSpecificRewriteResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        instructions, input_text = render_chat_rewrite_prompt(request)
        candidate, usage = asyncio.run(
            self._run_auxiliary(
                request,
                instructions=instructions,
                input_text=input_text,
                output_type=QueryRewriteOutput,
                stage="rewriting",
                component_id="query_rewriter",
                schema_id="query-rewrite-v1",
                temperature=_chat_live_temperature(),
            )
        )
        return RouteSpecificRewriteResult(
            candidate=QueryRewriteOutput.model_validate(candidate),
            usage=usage,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if not request.api_key:
            raise ProviderProtocolError("DeepSeek API key is required")
        return asyncio.run(self._generate(request))

    async def _generate(self, request: GenerationRequest) -> GenerationResult:
        model = self.create_model(request)
        client = _model_client(model)
        try:
            if request.prompt_version in COMPONENT_GENERATION_PROMPT_VERSIONS:

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

                runner = _brief_to_draft_runner(request.prompt_version)
                return cast(
                    GenerationResult,
                    await runner(request, call_component=call_component),
                )
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
        finally:
            if client is not None:
                await client.close()

    async def _run_auxiliary(
        self,
        request: (
            BriefPolishRequest
            | BriefAnchorExtractRequest
            | BriefIntakeQuestionsRequest
            | BriefIntakeSynthesizeRequest
            | BriefStrategyOptionsRequest
            | CaseFileChatRequest
            | RouteSpecificRewriteRequest
            | ReverseParseRequest
            | IdeaGenerationRequest
            | ThreadCompactionRequest
            | ClosureRepairRequest
            | StoryPlannerRequest
            | GeneralMutationPlannerRequest
        ),
        *,
        instructions: str,
        input_text: str,
        output_type: type[BaseModel],
        stage: str,
        component_id: str | None = None,
        schema_id: str | None = None,
        tools: list[Tool] | None = None,
        context: ChatToolContext | None = None,
        max_turns: int | None = None,
        deepseek_output_protocol: Literal["strict_tool", "json_object"] | None = None,
        deepseek_output_protocol_is_primary: bool = False,
        temperature: float | None = None,
        strict_validation: bool = False,
        max_protocol_attempts: int = 3,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        model = self.create_model(request)
        client = _model_client(model)
        try:
            return await _run_auxiliary_agent(
                request,
                model=model,
                model_settings=_deepseek_model_settings(temperature=temperature),
                instructions=instructions,
                input_text=input_text,
                output_type=output_type,
                stage=stage,
                structured_output=False,
                tracing_disabled=True,
                component_id=component_id,
                schema_id=schema_id,
                deepseek_output_protocol=(
                    deepseek_output_protocol or _deepseek_v8_output_protocol(request.model_id)
                ),
                deepseek_output_protocol_is_primary=deepseek_output_protocol_is_primary,
                strict_validation=strict_validation,
                max_protocol_attempts=max_protocol_attempts,
                tools=tools,
                context=context,
                max_turns=max_turns,
            )
        finally:
            if client is not None:
                await client.close()

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
            | RouteSpecificRewriteRequest
            | ReverseParseRequest
            | IdeaGenerationRequest
            | ThreadCompactionRequest
            | ClosureRepairRequest
            | StoryPlannerRequest
            | GeneralMutationPlannerRequest
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


__all__ = ["DeepSeekAgentsProvider"]
