"""Deterministic unit contracts for the M3.8 GoalSession state engine."""

from __future__ import annotations

import pytest
from casefile.application.goal_session_state import (
    TERMINAL_GOAL_STATUSES,
    GoalSessionStateError,
    require_budget_available,
    require_expected_revision,
    require_transition,
)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("interpreting", "running"),
        ("running", "running"),
        ("running", "waiting_patch_review"),
        ("waiting_patch_review", "waiting_clarification"),
        ("stale", "interpreting"),
    ],
)
def test_frozen_goal_transitions_are_accepted(current: str, target: str) -> None:
    require_transition(current, target)


@pytest.mark.parametrize("terminal", sorted(TERMINAL_GOAL_STATUSES))
def test_terminal_goal_sessions_cannot_resume(terminal: str) -> None:
    with pytest.raises(GoalSessionStateError) as raised:
        require_transition(terminal, "running")
    assert raised.value.code == "agent_goal_transition_invalid"


def test_unlisted_transition_fails_closed() -> None:
    with pytest.raises(GoalSessionStateError) as raised:
        require_transition("waiting_patch_review", "completed")
    assert raised.value.code == "agent_goal_transition_invalid"


def test_stale_expected_revision_is_rejected() -> None:
    with pytest.raises(GoalSessionStateError) as raised:
        require_expected_revision(3, 2)
    assert raised.value.code == "agent_goal_revision_conflict"


@pytest.mark.parametrize(
    ("counts", "increments"),
    [
        ((8, 0, 0), (1, 0, 0)),
        ((0, 12, 0), (0, 1, 0)),
        ((0, 0, 6), (0, 0, 1)),
    ],
)
def test_session_budget_ceiling_is_enforced(
    counts: tuple[int, int, int], increments: tuple[int, int, int]
) -> None:
    with pytest.raises(GoalSessionStateError) as raised:
        require_budget_available(
            revision_count=counts[0],
            task_run_slice_count=counts[1],
            consumed_control_count=counts[2],
            add_revisions=increments[0],
            add_task_run_slices=increments[1],
            add_consumed_controls=increments[2],
        )
    assert raised.value.code == "agent_goal_budget_exhausted"


def test_values_at_budget_ceiling_remain_valid_without_new_work() -> None:
    require_budget_available(
        revision_count=8,
        task_run_slice_count=12,
        consumed_control_count=6,
    )
