"""Deterministic history window selector with constraint pinning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from casefile.agent_runtime.context.estimators import estimate_jsonable_tokens
from casefile.agent_runtime.context.models import (
    ContextBlock,
    ContextDecision,
    StageResult,
)
from casefile.agent_runtime.context.protocols import ContextRun

DEFAULT_WINDOW_MESSAGES = 6
_WINDOW_BY_PROFILE = {
    "question": 4,
    "analysis": 6,
    "edit": 6,
    "issue": 6,
    "gate": 6,
    "clarify": 4,
    "scope": 4,
}
# Reuses the query_rewrite negation-term thinking plus hard constraints. The
# bare "不" is intentionally excluded: it would pin almost every message.
_PIN_TERMS = ("不能", "不得", "不要", "别", "禁止", "无需", "必须", "务必", "保留", "忽略")


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def select_history_window(
    history: list[dict[str, str]],
    *,
    max_messages: int = DEFAULT_WINDOW_MESSAGES,
    pin_terms: tuple[str, ...] = _PIN_TERMS,
) -> dict[str, Any]:
    """Keep the recent window plus pinned user messages, preserving order.

    Pinned messages are the first user message (thread anchor) and every user
    message carrying a negation or hard-constraint term. The current request is
    carried separately as ``author_message`` and is never part of ``history``.
    """

    clean = [
        item
        for item in history
        if isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
        and item["content"].strip()
    ]
    anchor_index: int | None = next(
        (index for index, item in enumerate(clean) if item["role"] == "user"),
        None,
    )
    pinned_indices: set[int] = set()
    if anchor_index is not None:
        pinned_indices.add(anchor_index)
    for index, item in enumerate(clean):
        if item["role"] == "user" and _contains_any(str(item["content"]), pin_terms):
            pinned_indices.add(index)
    recent_start = max(0, len(clean) - max(1, max_messages))
    selected_indices = sorted(pinned_indices | set(range(recent_start, len(clean))))
    selected = [clean[index] for index in selected_indices]
    dropped_count = len(clean) - len(selected)
    pinned_messages = [
        {
            "index": index,
            "role": clean[index]["role"],
            "reason": "thread_anchor" if index == anchor_index else "constraint_terms",
        }
        for index in selected_indices
        if index in pinned_indices
    ]
    return {
        "thread_history": selected,
        "pinned_messages": pinned_messages,
        "dropped_count": dropped_count,
        "first_index": selected_indices[0] if selected_indices else 0,
        "last_index": selected_indices[-1] if selected_indices else 0,
        "history_turns": len(clean),
    }


def _route_profile(run: ContextRun) -> str:
    routing = run.routing or {}
    route = routing.get("route")
    profile: dict[str, Any] = {}
    if isinstance(route, dict):
        raw_profile = route.get("execution_profile")
        profile = raw_profile if isinstance(raw_profile, dict) else {}
    profile_name = profile.get("profile")
    if isinstance(profile_name, str) and profile_name:
        return profile_name
    return "question"


@dataclass(slots=True)
class HistoryWindowStage:
    """Policy stage producing the ``thread_history`` block."""

    name: str = "history_window_v1"
    version: str = "history-window-v1"
    capabilities: frozenset[str] = frozenset({"selector", "chat", "deterministic"})

    def can_run(self, run: ContextRun) -> bool:
        return isinstance(run.frozen_input.get("history"), list)

    def run(self, run: ContextRun) -> StageResult:
        raw_history = run.frozen_input.get("history")
        if not isinstance(raw_history, list):
            return StageResult()
        history = [item for item in raw_history if isinstance(item, dict)]
        config = run.policy_stage_config("history_window")
        windows = config.get("windows")
        if not isinstance(windows, dict):
            windows = _WINDOW_BY_PROFILE
        profile = _route_profile(run)
        max_messages = DEFAULT_WINDOW_MESSAGES
        value = windows.get(profile, windows.get("default"))
        if isinstance(value, bool):
            value = None
        max_messages = int(value) if isinstance(value, int) else DEFAULT_WINDOW_MESSAGES
        selected = select_history_window(history, max_messages=max_messages)
        decisions: list[ContextDecision] = []
        if selected["dropped_count"]:
            decisions.append(
                ContextDecision(
                    stage="history_window",
                    code="history_window_trimmed",
                    detail=(
                        f"dropped {selected['dropped_count']} earlier messages; "
                        f"{len(selected['pinned_messages'])} pinned messages retained"
                    ),
                )
            )
        payload: list[dict[str, str]] = selected["thread_history"]
        history_turns = int(selected["history_turns"])
        return StageResult(
            added=(
                ContextBlock(
                    id="thread_history",
                    kind="history_window",
                    payload=payload,
                    tokens=estimate_jsonable_tokens(payload, run.estimator),
                    metadata={
                        "profile": profile,
                        "max_messages": max_messages,
                        "dropped_count": selected["dropped_count"],
                        "pinned_message_count": len(selected["pinned_messages"]),
                        "first_index": selected["first_index"],
                        "last_index": selected["last_index"],
                    },
                    age_turns=max(0, history_turns - int(selected["last_index"])),
                    last_access_turn=history_turns,
                ),
            ),
            decisions=tuple(decisions),
            metrics={
                "message_count": len(payload),
                "dropped_count": selected["dropped_count"],
            },
        )


__all__ = [
    "DEFAULT_WINDOW_MESSAGES",
    "HistoryWindowStage",
    "_WINDOW_BY_PROFILE",
    "select_history_window",
]
