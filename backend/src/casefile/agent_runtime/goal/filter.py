"""Conservative, providerless candidate filter that preserves legacy routing."""

from __future__ import annotations

import re
from dataclasses import dataclass

from casefile.agent_runtime.chat_request_signals import (
    affirmative_request_contains,
    request_signals,
)

_SEQUENCERS = ("先", "再", "然后", "之后", "后", "并且", "并", "同时", "then", "and")
_UNSAFE = (
    "自动应用",
    "直接应用",
    "不要确认",
    "无需确认",
    "自行修好",
    "你认为有问题的地方",
)
_AMBIGUOUS_MUTATION = re.compile(r"(?:删掉|删除|修改|更新)(?:那个|这个|它|不需要的东西)")


@dataclass(frozen=True, slots=True)
class GoalCandidateDecision:
    candidate: bool
    reason_code: str


def goal_candidate_filter(
    message: str,
    *,
    routing_entrypoint: str = "free_text",
    has_issue_focus: bool = False,
) -> GoalCandidateDecision:
    text = message.strip()
    if routing_entrypoint != "free_text" and not (
        routing_entrypoint == "issue_action" and has_issue_focus
    ):
        return GoalCandidateDecision(False, "explicit_single_task")
    if affirmative_request_contains(text, _UNSAFE):
        return GoalCandidateDecision(False, "goal_hard_safety")
    if _AMBIGUOUS_MUTATION.search(text):
        return GoalCandidateDecision(False, "goal_ambiguous_mutation")
    signals = request_signals(text)
    if len(signals.action_groups) < 2 or not any(token in text.casefold() for token in _SEQUENCERS):
        return GoalCandidateDecision(False, "goal_not_multi_obligation")
    return GoalCandidateDecision(True, "goal_candidate")


__all__ = ["GoalCandidateDecision", "goal_candidate_filter"]
