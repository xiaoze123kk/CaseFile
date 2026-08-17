"""Pure routing policy for CaseFile chat.

Intent Understanding only describes what the user wants; this module decides
the cheapest safe execution path from frozen capability, budget, risk and
permission tables. No model output may choose a route.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace
from typing import Any

import rfc8785

from casefile.agent_runtime.models import (
    ChatTaskUnderstanding,
    RouteDecision,
    agent_state_to_jsonable,
)

CONFIDENCE_GATE_HIGH = 0.85
SENSITIVE_INTENTS = frozenset({"edit_request", "unsupported_action"})

# v1 capability table from the routing scheme §6.4. `profile` is the default
# route-specific profile when a preset/UI rule does not supply a tighter one.
EXECUTION_PROFILES: dict[str, dict[str, Any]] = {
    "question": {
        "primary_intent": "question",
        "profile": "question.chat",
        "prompt_component": "chat",
        "allow_suggestions": False,
        "suggestion_policy": "deny",
        "toolset": [
            "list_casefile_records",
            "search_casefile",
            "get_casefile_object",
            "get_related_objects",
        ],
        "context_tools": ["retrieve_thread_evidence"],
        "max_turns": 4,
        "max_tool_calls": 6,
        "context_profile": "focus_first",
    },
    "analysis": {
        "primary_intent": "analysis",
        "profile": "analysis.inspect",
        "prompt_component": "analysis",
        "allow_suggestions": False,
        "suggestion_policy": "deny",
        "toolset": [
            "list_casefile_records",
            "search_casefile",
            "get_casefile_object",
            "get_related_objects",
            "get_validation_issues",
        ],
        "context_tools": [
            "retrieve_thread_evidence",
            "request_thread_compaction",
        ],
        "max_turns": 6,
        "max_tool_calls": 12,
        "context_profile": "focus_first",
    },
    "explain_issue": {
        "primary_intent": "explain_issue",
        "profile": "explain_issue.issue_fix",
        "prompt_component": "issue",
        "allow_suggestions": True,
        "suggestion_policy": "allow",
        "toolset": [
            "list_casefile_records",
            "search_casefile",
            "get_casefile_object",
            "get_related_objects",
            "get_validation_issues",
        ],
        "context_tools": ["retrieve_thread_evidence"],
        "max_turns": 6,
        "max_tool_calls": 10,
        "context_profile": "focus_first",
    },
    "edit_request": {
        "primary_intent": "edit_request",
        "profile": "edit_request.edit",
        "prompt_component": "edit",
        "allow_suggestions": True,
        "suggestion_policy": "allow",
        "toolset": [
            "list_casefile_records",
            "search_casefile",
            "get_casefile_object",
            "get_related_objects",
            "validate_patch_proposal",
        ],
        "context_tools": [
            "retrieve_thread_evidence",
            "request_thread_compaction",
        ],
        "max_turns": 6,
        "max_tool_calls": 12,
        "context_profile": "focus_first",
    },
    "validate_request": {
        "primary_intent": "validate_request",
        "profile": "validate_request.gate_check",
        "prompt_component": "gate",
        "allow_suggestions": False,
        "suggestion_policy": "deny",
        "toolset": [],
        "max_turns": 2,
        "max_tool_calls": 0,
        "context_profile": "focus_first",
    },
    "unsupported_action": {
        "primary_intent": "unsupported_action",
        "profile": "unsupported_action.scope",
        "prompt_component": "scope",
        "allow_suggestions": False,
        "suggestion_policy": "deny",
        "toolset": [],
        "max_turns": 2,
        "max_tool_calls": 0,
        "context_profile": "focus_first",
    },
    "clarify": {
        "primary_intent": "clarify",
        "profile": "clarify.question",
        "prompt_component": "clarify",
        "allow_suggestions": False,
        "suggestion_policy": "deny",
        "toolset": [],
        "max_turns": 2,
        "max_tool_calls": 0,
        "context_profile": "focus_first",
    },
    "out_of_scope": {
        "primary_intent": "out_of_scope",
        "profile": "out_of_scope.scope",
        "prompt_component": "scope",
        "allow_suggestions": False,
        "suggestion_policy": "deny",
        "toolset": [],
        "max_turns": 2,
        "max_tool_calls": 0,
        "context_profile": "focus_first",
    },
}


def routing_policy(
    task_understanding: ChatTaskUnderstanding,
    *,
    budget: dict[str, Any] | None = None,
    profile: str | None = None,
    rewrite_strategy: str = "KEEP",
    route_source: str = "llm",
    suggestion_policy: str | None = None,
) -> RouteDecision:
    """Decide one execution profile; budgets can only be tightened, never widened."""

    primary_intent = task_understanding.primary_intent
    base_profile = EXECUTION_PROFILES.get(primary_intent, EXECUTION_PROFILES["question"])
    execution_profile = deepcopy(base_profile)
    execution_profile["primary_intent"] = primary_intent
    execution_profile["profile"] = profile or str(base_profile["profile"])
    if suggestion_policy in {"deny", "allow", "inherit"}:
        execution_profile["suggestion_policy"] = suggestion_policy
        execution_profile["allow_suggestions"] = suggestion_policy == "allow"

    frozen_budget = budget if isinstance(budget, dict) else {}
    default_turns = int(execution_profile.get("max_turns", 4))
    frozen_turns = frozen_budget.get("max_turns")
    execution_profile["max_turns"] = (
        _tightened(default_turns, frozen_turns)
        if frozen_turns is not None
        else default_turns
    )
    default_calls = int(execution_profile.get("max_tool_calls", 0))
    frozen_calls = frozen_budget.get("max_tool_calls")
    execution_profile["max_tool_calls"] = (
        _tightened(default_calls, frozen_calls, minimum=0)
        if frozen_calls is not None
        else default_calls
    )

    confidence = float(task_understanding.confidence)
    confidence_margin = round(max(0.0, confidence - CONFIDENCE_GATE_HIGH), 6)
    risk_level = task_understanding.risk_level
    candidate_routes = (
        {
            "target": f"casefile_chat:{primary_intent}",
            "score": confidence,
            "cost": "low",
            "risk": risk_level,
        },
    )
    routes = (
        {
            "target_agent_id": "casefile_chat",
            "profile": execution_profile["profile"],
        },
    )
    reason_codes = tuple(task_understanding.reason_codes)
    route = RouteDecision(
        route_source=route_source,
        candidate_routes=candidate_routes,
        routes=routes,
        execution_mode="serial",
        merge_strategy=None,
        rewrite_strategy=rewrite_strategy,
        execution_profile=execution_profile,
        confidence=confidence,
        confidence_margin=confidence_margin,
        reason_codes=reason_codes,
        fallback="question",
        route_hash="",
    )
    return replace(route, route_hash=route_hash(route))


def fallback_route(*, reason_codes: tuple[str, ...] = ()) -> RouteDecision:
    """Safe fallback for LLM failure or confidence-gate rejection."""

    understanding = ChatTaskUnderstanding(
        primary_intent="question",
        sub_intents=(),
        confidence=0.0,
        risk_level="low",
        ambiguous=True,
        missing_info=("intent_router_fallback",),
        reason_codes=reason_codes or ("rule_miss", "intent_router_fallback"),
    )
    return routing_policy(
        understanding,
        budget=None,
        profile="question.chat",
        rewrite_strategy="KEEP",
        route_source="fallback",
        suggestion_policy="deny",
    )


def route_llm_task(
    task_understanding: ChatTaskUnderstanding,
    *,
    budget: dict[str, Any] | None,
    rewrite_strategy: str,
    tau_high: float = CONFIDENCE_GATE_HIGH,
) -> RouteDecision:
    """Confidence Gate + policy for one LLM-understood free-text task."""

    confidence = float(task_understanding.confidence)
    if confidence < tau_high:
        sensitive = task_understanding.primary_intent in SENSITIVE_INTENTS
        reason = (
            "confidence_gate_sensitive"
            if sensitive
            else "confidence_gate_below_threshold"
        )
        return fallback_route(reason_codes=(reason,))
    return routing_policy(
        task_understanding,
        budget=budget,
        rewrite_strategy=rewrite_strategy,
        route_source="llm",
    )


def route_hash(route: RouteDecision) -> str:
    """Stable canonical hash of every routing field except route_hash itself."""

    payload = agent_state_to_jsonable(route)
    if not isinstance(payload, dict):
        raise TypeError("route must be a RouteDecision")
    payload.pop("route_hash", None)
    canonical = rfc8785.dumps(payload)
    return hashlib.sha256(canonical).hexdigest()


def _tightened(default: int, value: Any, *, minimum: int = 1) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(minimum, min(default, value))


__all__ = [
    "CONFIDENCE_GATE_HIGH",
    "EXECUTION_PROFILES",
    "SENSITIVE_INTENTS",
    "fallback_route",
    "route_hash",
    "route_llm_task",
    "routing_policy",
]
