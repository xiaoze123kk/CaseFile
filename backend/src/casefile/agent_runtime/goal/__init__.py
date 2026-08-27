"""Bounded, provider-neutral Goal Controller primitives."""

from casefile.agent_runtime.goal.contracts import (
    FrozenGoal,
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
    "GoalCompletionDecision",
    "GoalDecisionOutput",
    "GoalObservation",
    "GoalObligation",
    "GoalPolicyError",
    "GoalQualification",
    "GoalUnderstandingOutput",
    "InvokeCapabilityAction",
    "complete_goal",
    "freeze_goal",
    "qualify_goal",
]
