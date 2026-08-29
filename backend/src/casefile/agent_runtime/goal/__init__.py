"""Bounded, provider-neutral Goal Controller primitives."""

from casefile.agent_runtime.goal.contracts import (
    FrozenGoal,
    GoalAmendmentOutput,
    GoalCompletionDecision,
    GoalDecisionOutput,
    GoalObligation,
    GoalObservation,
    GoalUnderstandingOutput,
    InvokeCapabilityAction,
)
from casefile.agent_runtime.goal.policy import (
    GOAL_CAPABILITY_REGISTRY_VERSION,
    GOAL_POLICY_VERSION,
    GOAL_RUNTIME_VERSION,
    GoalBudget,
    GoalPolicyError,
    GoalQualification,
    apply_goal_amendment,
    complete_goal,
    freeze_goal,
    qualify_goal,
)

__all__ = [
    "FrozenGoal",
    "GOAL_CAPABILITY_REGISTRY_VERSION",
    "GOAL_POLICY_VERSION",
    "GOAL_RUNTIME_VERSION",
    "GoalBudget",
    "GoalAmendmentOutput",
    "GoalCompletionDecision",
    "GoalDecisionOutput",
    "GoalObservation",
    "GoalObligation",
    "GoalPolicyError",
    "GoalQualification",
    "GoalUnderstandingOutput",
    "InvokeCapabilityAction",
    "apply_goal_amendment",
    "complete_goal",
    "freeze_goal",
    "qualify_goal",
]
