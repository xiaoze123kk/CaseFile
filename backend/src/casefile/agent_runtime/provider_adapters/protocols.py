"""Stable Provider protocols and errors."""

from __future__ import annotations

from typing import Protocol

from casefile.agent_runtime.closure_repair import (
    ClosureRepairProviderResult,
    ClosureRepairRequest,
)
from casefile.agent_runtime.constraint_first_story_planner import (
    SemanticFillRequest,
    SemanticFillResult,
    SkeletonProposalRequest,
    SkeletonProposalResult,
)
from casefile.agent_runtime.context.thread_memory import (
    ThreadCompactionRequest,
    ThreadCompactionResult,
)
from casefile.agent_runtime.general_mutation import (
    GeneralMutationPlannerRequest,
    GeneralMutationPlannerResult,
)
from casefile.agent_runtime.goal.provider import (
    ChatEvidenceCollection,
    GoalDecisionRequest,
    GoalDecisionResult,
    GoalFinalizerRequest,
    GoalUnderstandingRequest,
    GoalUnderstandingResult,
)
from casefile.agent_runtime.models import (
    BriefAnchorExtractRequest,
    BriefAnchorExtractResult,
    BriefIntakeQuestionsRequest,
    BriefIntakeQuestionsResult,
    BriefIntakeSynthesizeRequest,
    BriefIntakeSynthesizeResult,
    BriefPolishRequest,
    BriefPolishResult,
    BriefStrategyOptionsRequest,
    BriefStrategyOptionsResult,
    CaseFileChatRequest,
    CaseFileChatResult,
    GenerationRequest,
    GenerationResult,
    IdeaGenerationRequest,
    IdeaGenerationResult,
    IntentUnderstandingResult,
    ReverseParseRequest,
    ReverseParseResult,
    RouteSpecificRewriteRequest,
    RouteSpecificRewriteResult,
)
from casefile.agent_runtime.scene_compiler import (
    SceneFillBatchRequest,
    SceneFillBatchResult,
)
from casefile.agent_runtime.story_planner import (
    StoryPlannerProviderResult,
    StoryPlannerRequest,
)


class GenerationProvider(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResult: ...


class AgentProvider(GenerationProvider, Protocol):
    def fill_scene_batch(self, request: SceneFillBatchRequest) -> SceneFillBatchResult: ...

    def propose_skeleton(
        self, request: SkeletonProposalRequest
    ) -> SkeletonProposalResult: ...

    def fill_semantics(self, request: SemanticFillRequest) -> SemanticFillResult: ...

    def plan_story(self, request: StoryPlannerRequest) -> StoryPlannerProviderResult: ...

    def plan_general_mutation(
        self,
        request: GeneralMutationPlannerRequest,
    ) -> GeneralMutationPlannerResult: ...

    def repair_closure(
        self,
        request: ClosureRepairRequest,
    ) -> ClosureRepairProviderResult: ...

    def polish(self, request: BriefPolishRequest) -> BriefPolishResult: ...

    def extract_anchors(self, request: BriefAnchorExtractRequest) -> BriefAnchorExtractResult: ...

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult: ...

    def understand_goal(self, request: GoalUnderstandingRequest) -> GoalUnderstandingResult: ...

    def decide_goal(self, request: GoalDecisionRequest) -> GoalDecisionResult: ...

    def collect_chat_evidence(self, request: CaseFileChatRequest) -> ChatEvidenceCollection: ...

    def finalize_goal(self, request: GoalFinalizerRequest) -> CaseFileChatResult: ...

    def compact_thread_memory(
        self,
        request: ThreadCompactionRequest,
    ) -> ThreadCompactionResult: ...

    def understand_intent(self, request: CaseFileChatRequest) -> IntentUnderstandingResult: ...

    def rewrite_for_route(
        self,
        request: RouteSpecificRewriteRequest,
    ) -> RouteSpecificRewriteResult: ...

    def intake_questions(
        self, request: BriefIntakeQuestionsRequest
    ) -> BriefIntakeQuestionsResult: ...

    def synthesize_intake(
        self, request: BriefIntakeSynthesizeRequest
    ) -> BriefIntakeSynthesizeResult: ...

    def strategy_options(
        self, request: BriefStrategyOptionsRequest
    ) -> BriefStrategyOptionsResult: ...

    def generate_ideas(self, request: IdeaGenerationRequest) -> IdeaGenerationResult: ...

    def reverse_parse(self, request: ReverseParseRequest) -> ReverseParseResult: ...


class ProviderProtocolError(RuntimeError):
    """The provider returned a structurally unusable result or skipped a required tool."""


__all__ = ["AgentProvider", "GenerationProvider", "ProviderProtocolError"]
