"""Strict internal contracts for the M3.7 bounded Goal Controller."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from casefile.agent_runtime.models import StrictAgentOutput

GoalObligationKind = Literal["analysis", "audit", "mutation_proposal"]
GoalTargetState = Literal["baseline", "candidate"]
GoalCapability = Literal["analyze", "audit", "propose_mutation"]


class GoalObligationDraft(StrictAgentOutput):
    """Model-authored obligation before the server assigns its identity."""

    kind: GoalObligationKind
    target_state: GoalTargetState
    source_excerpt: str = Field(min_length=1, max_length=2_000)
    depends_on: list[int] = Field(default_factory=list, max_length=6)


class GoalUnderstandingOutput(StrictAgentOutput):
    goal: str = Field(min_length=1, max_length=4_000)
    obligations: list[GoalObligationDraft] = Field(min_length=1, max_length=6)
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguous: bool = False
    missing_info: list[str] = Field(default_factory=list, max_length=20)


class GoalObligation(StrictAgentOutput):
    obligation_id: str = Field(pattern=r"^obl_[1-9][0-9]*$")
    kind: GoalObligationKind
    target_state: GoalTargetState
    source_excerpt: str = Field(min_length=1, max_length=2_000)
    depends_on: list[str] = Field(default_factory=list, max_length=6)


class FrozenGoal(StrictAgentOutput):
    goal: str = Field(min_length=1, max_length=4_000)
    obligations: list[GoalObligation] = Field(min_length=1, max_length=6)
    source_message_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    obligations_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GoalPlanItem(StrictAgentOutput):
    obligation_id: str = Field(pattern=r"^obl_[1-9][0-9]*$")
    status: Literal["pending", "in_progress", "completed"]


class InvokeCapabilityAction(StrictAgentOutput):
    action: Literal["invoke_capability"] = "invoke_capability"
    capability: GoalCapability
    obligation_ids: list[str] = Field(min_length=1, max_length=6)
    target_state: GoalTargetState


class FinishGoalAction(StrictAgentOutput):
    action: Literal["finish"] = "finish"


class GoalDecisionOutput(StrictAgentOutput):
    plan_items: list[GoalPlanItem] = Field(min_length=1, max_length=6)
    action: InvokeCapabilityAction | FinishGoalAction = Field(discriminator="action")

    @model_validator(mode="after")
    def unique_plan_items(self) -> GoalDecisionOutput:
        ids = [item.obligation_id for item in self.plan_items]
        if len(ids) != len(set(ids)):
            raise ValueError("goal plan obligation ids must be unique")
        return self


class GoalObservation(StrictAgentOutput):
    observation_id: str = Field(pattern=r"^obs_[1-9][0-9]*$")
    capability: GoalCapability
    obligation_ids: list[str] = Field(min_length=1, max_length=6)
    target_state: GoalTargetState
    status: Literal["completed", "blocked"]
    summary: str = Field(min_length=1, max_length=4_000)
    object_refs: list[str] = Field(default_factory=list, max_length=50)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)
    action_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    route_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    ledger_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    candidate_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    mutation_proof_ref: str | None = Field(default=None, max_length=128)
    verification_proof_refs: list[str] = Field(default_factory=list, max_length=20)
    tool_calls: int = Field(default=0, ge=0, le=48)
    provider_operations: int = Field(default=1, ge=0, le=14)


class GoalCompletionDecision(StrictAgentOutput):
    allowed: bool
    missing_obligation_ids: list[str] = Field(default_factory=list, max_length=6)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GoalExecutionCheckpoint(StrictAgentOutput):
    """Hashable, persistence-neutral state at one cooperative safe point."""

    version: Literal["goal-execution-checkpoint.v1"] = "goal-execution-checkpoint.v1"
    obligations_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    observations: list[GoalObservation] = Field(default_factory=list, max_length=4)
    completion: GoalCompletionDecision | None = None
    mutation_proof: dict[str, Any] | None = None


class GoalInterpreterInputV1(StrictAgentOutput):
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    author_message: str = Field(min_length=1, max_length=100_000)


class GoalControllerInputV1(StrictAgentOutput):
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    goal: FrozenGoal
    observations: list[GoalObservation] = Field(default_factory=list, max_length=4)
    budget: dict[str, int]
    completion_feedback: GoalCompletionDecision | None = None


class GoalFinalizerInputV1(StrictAgentOutput):
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    casefile: dict[str, Any]
    thread_history: list[dict[str, Any]] = Field(default_factory=list)
    author_message: str = Field(min_length=1, max_length=100_000)
    focus: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    goal: FrozenGoal
    observations: list[GoalObservation] = Field(min_length=2, max_length=4)
    completion: GoalCompletionDecision
    mutation_proof: dict[str, Any] | None = None


def jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


__all__ = [
    "FinishGoalAction",
    "FrozenGoal",
    "GoalCapability",
    "GoalCompletionDecision",
    "GoalExecutionCheckpoint",
    "GoalControllerInputV1",
    "GoalDecisionOutput",
    "GoalObservation",
    "GoalObligation",
    "GoalObligationDraft",
    "GoalPlanItem",
    "GoalTargetState",
    "GoalFinalizerInputV1",
    "GoalInterpreterInputV1",
    "GoalUnderstandingOutput",
    "InvokeCapabilityAction",
]
