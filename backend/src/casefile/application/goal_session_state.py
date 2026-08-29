"""Authoritative, deterministic M3.8 GoalSession transition and budget rules."""

from __future__ import annotations

from dataclasses import dataclass

GOAL_STATUSES = frozenset(
    {
        "interpreting",
        "running",
        "waiting_clarification",
        "waiting_patch_review",
        "stale",
        "completed",
        "cancelled",
        "superseded",
        "failed",
    }
)
TERMINAL_GOAL_STATUSES = frozenset({"completed", "cancelled", "superseded", "failed"})

_TRANSITIONS = {
    "interpreting": frozenset(
        {"running", "waiting_clarification", "completed", "cancelled", "failed"}
    ),
    "running": frozenset(
        {
            "running",
            "waiting_clarification",
            "waiting_patch_review",
            "stale",
            "completed",
            "cancelled",
            "superseded",
            "failed",
        }
    ),
    "waiting_clarification": frozenset(
        {"interpreting", "running", "stale", "cancelled", "superseded"}
    ),
    "waiting_patch_review": frozenset(
        {"running", "waiting_clarification", "stale", "cancelled", "superseded"}
    ),
    "stale": frozenset({"interpreting", "running", "cancelled", "superseded"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
    "superseded": frozenset(),
    "failed": frozenset(),
}


class GoalSessionStateError(ValueError):
    """Stable internal state-machine failure mapped by a later API phase."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class GoalSessionBudget:
    """Frozen session-wide ceilings layered over per-TaskRun budgets."""

    max_goal_revisions: int = 8
    max_task_run_slices: int = 12
    max_consumed_controls: int = 6


DEFAULT_GOAL_SESSION_BUDGET = GoalSessionBudget()


def require_transition(current_status: str, target_status: str) -> None:
    """Fail closed unless the transition appears in the M3.8 frozen matrix."""

    if current_status not in GOAL_STATUSES or target_status not in GOAL_STATUSES:
        raise GoalSessionStateError("agent_goal_transition_invalid", "unknown GoalSession status")
    if target_status not in _TRANSITIONS[current_status]:
        raise GoalSessionStateError(
            "agent_goal_transition_invalid",
            f"GoalSession cannot transition from {current_status} to {target_status}",
        )


def require_expected_revision(actual_revision: int, expected_revision: int) -> None:
    """Reject stale delivery writers before any durable mutation."""

    if actual_revision != expected_revision:
        raise GoalSessionStateError("agent_goal_revision_conflict", "GoalSession revision is stale")


def require_budget_available(
    *,
    revision_count: int,
    task_run_slice_count: int,
    consumed_control_count: int,
    add_revisions: int = 0,
    add_task_run_slices: int = 0,
    add_consumed_controls: int = 0,
    budget: GoalSessionBudget = DEFAULT_GOAL_SESSION_BUDGET,
) -> None:
    """Fail before a transaction would exceed any session-wide ceiling."""

    requested = (
        revision_count + add_revisions,
        task_run_slice_count + add_task_run_slices,
        consumed_control_count + add_consumed_controls,
    )
    ceilings = (
        budget.max_goal_revisions,
        budget.max_task_run_slices,
        budget.max_consumed_controls,
    )
    if any(value > ceiling for value, ceiling in zip(requested, ceilings, strict=True)):
        raise GoalSessionStateError(
            "agent_goal_budget_exhausted", "GoalSession budget is exhausted"
        )


__all__ = [
    "DEFAULT_GOAL_SESSION_BUDGET",
    "GOAL_STATUSES",
    "TERMINAL_GOAL_STATUSES",
    "GoalSessionBudget",
    "GoalSessionStateError",
    "require_budget_available",
    "require_expected_revision",
    "require_transition",
]
