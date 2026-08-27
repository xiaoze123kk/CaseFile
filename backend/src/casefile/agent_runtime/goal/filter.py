"""Conservative, providerless candidate filter that preserves legacy routing."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ACTION_GROUPS = {
    "analysis": ("分析", "梳理", "比较", "解释", "评估"),
    "audit": ("审计", "核查", "检查", "复查", "验证"),
    "mutation": (
        "新增",
        "新建",
        "创建",
        "修改",
        "更新",
        "改成",
        "改得",
        "删除",
        "移除",
    ),
}
_SEQUENCERS = ("先", "再", "然后", "之后", "后", "并且", "并", "同时")
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
) -> GoalCandidateDecision:
    text = message.strip()
    if routing_entrypoint != "free_text":
        return GoalCandidateDecision(False, "explicit_single_task")
    if any(token in text for token in _UNSAFE):
        return GoalCandidateDecision(False, "goal_hard_safety")
    if _AMBIGUOUS_MUTATION.search(text):
        return GoalCandidateDecision(False, "goal_ambiguous_mutation")
    matched_actions = sum(
        sum(text.count(token) for token in tokens) for tokens in _ACTION_GROUPS.values()
    )
    matched_groups = sum(
        any(token in text for token in tokens) for tokens in _ACTION_GROUPS.values()
    )
    if matched_actions < 2 or matched_groups < 2 or not any(token in text for token in _SEQUENCERS):
        return GoalCandidateDecision(False, "goal_not_multi_obligation")
    return GoalCandidateDecision(True, "goal_candidate")


__all__ = ["GoalCandidateDecision", "goal_candidate_filter"]
