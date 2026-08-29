"""Provider-neutral request/result ports for Goal interpretation and control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from casefile.agent_runtime.goal.contracts import (
    FrozenGoal,
    GoalAmendmentOutput,
    GoalCompletionDecision,
    GoalDecisionOutput,
    GoalObservation,
    GoalUnderstandingOutput,
)
from casefile.agent_runtime.goal.policy import GoalBudget
from casefile.agent_runtime.models import (
    CaseFileChatRequest,
    CaseFileChatResult,
    ToolMetrics,
)


@dataclass(frozen=True, slots=True)
class GoalUnderstandingRequest:
    chat: CaseFileChatRequest


@dataclass(frozen=True, slots=True)
class GoalUnderstandingResult:
    candidate: GoalUnderstandingOutput
    usage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GoalAmendmentRequest:
    chat: CaseFileChatRequest
    current_goal: FrozenGoal


@dataclass(frozen=True, slots=True)
class GoalAmendmentResult:
    candidate: GoalAmendmentOutput
    usage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GoalDecisionRequest:
    chat: CaseFileChatRequest
    goal: FrozenGoal
    observations: tuple[GoalObservation, ...]
    budget: GoalBudget
    completion_feedback: GoalCompletionDecision | None = None


@dataclass(frozen=True, slots=True)
class GoalDecisionResult:
    candidate: GoalDecisionOutput
    usage: dict[str, Any]
    reused_from_step_run_id: int | None = None


@dataclass(frozen=True, slots=True)
class GoalFinalizerRequest:
    chat: CaseFileChatRequest
    goal: FrozenGoal
    observations: tuple[GoalObservation, ...]
    completion: GoalCompletionDecision
    mutation_proof: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ChatEvidenceCollection:
    ledger: dict[str, Any] | None
    evidence_summary: str
    usage: dict[str, Any]
    tools: ToolMetrics


class GoalProviderPort:
    """Documentation-only nominal surface; runtime accepts the Provider protocol."""

    def understand_goal(self, request: GoalUnderstandingRequest) -> GoalUnderstandingResult:
        raise NotImplementedError

    def amend_goal(self, request: GoalAmendmentRequest) -> GoalAmendmentResult:
        raise NotImplementedError

    def decide_goal(self, request: GoalDecisionRequest) -> GoalDecisionResult:
        raise NotImplementedError

    def collect_chat_evidence(self, request: CaseFileChatRequest) -> ChatEvidenceCollection:
        raise NotImplementedError

    def finalize_goal(self, request: GoalFinalizerRequest) -> CaseFileChatResult:
        raise NotImplementedError


__all__ = [
    "ChatEvidenceCollection",
    "GoalAmendmentRequest",
    "GoalAmendmentResult",
    "GoalDecisionRequest",
    "GoalDecisionResult",
    "GoalFinalizerRequest",
    "GoalProviderPort",
    "GoalUnderstandingRequest",
    "GoalUnderstandingResult",
]
