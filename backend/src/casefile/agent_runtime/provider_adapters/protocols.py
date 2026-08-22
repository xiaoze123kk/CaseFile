"""Stable Provider protocols and errors."""

from __future__ import annotations

from typing import Protocol

from casefile.agent_runtime.context.thread_memory import (
    ThreadCompactionRequest,
    ThreadCompactionResult,
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


class GenerationProvider(Protocol):
    def generate(self, request: GenerationRequest) -> GenerationResult: ...


class AgentProvider(GenerationProvider, Protocol):
    def polish(self, request: BriefPolishRequest) -> BriefPolishResult: ...

    def extract_anchors(self, request: BriefAnchorExtractRequest) -> BriefAnchorExtractResult: ...

    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult: ...

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
