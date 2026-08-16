"""Deterministic rule intent resolution for CaseFile chat.

R1 deliberately contains no LLM call: presets and the issue-action UI entry are
resolved from the frozen `routing_hint`. Free-text messages that carry a hint
receive the R1 fallback route; messages without a hint keep the legacy path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from casefile.agent_runtime.models import (
    CaseFileChatRequest,
    ChatTaskUnderstanding,
    ChatTaskUnderstandingOutput,
    RouteDecision,
    agent_state_to_jsonable,
)

INTENT_ROUTER_VERSION = "casefile-chat-router-v2"
ALLOWED_PRESET_IDS = frozenset({"inspect", "evidence", "compare", "gate"})
VALID_ENTRYPOINTS = frozenset({"free_text", "preset", "issue_action"})

# preset_id -> (primary_intent, route profile)
PRESET_ROUTE_TABLE: dict[str, dict[str, str]] = {
    "inspect": {
        "primary_intent": "analysis",
        "profile": "analysis.healthcheck",
        "reason_code": "rule_preset:inspect",
    },
    "evidence": {
        "primary_intent": "analysis",
        "profile": "analysis.evidence_summary",
        "reason_code": "rule_preset:evidence",
    },
    "compare": {
        "primary_intent": "analysis",
        "profile": "analysis.comparison",
        "reason_code": "rule_preset:compare",
    },
    "gate": {
        "primary_intent": "validate_request",
        "profile": "validate_request.gate_check",
        "reason_code": "rule_preset:gate",
    },
}


@dataclass(frozen=True, slots=True)
class RuleRoute:
    """Deterministic rule hit; resolved without any model call."""

    route_source: str
    primary_intent: str
    profile: str
    reason_code: str


def normalize_routing_hint(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Return a valid hint shape; unknown presets degrade to free_text."""

    if not isinstance(raw, dict):
        return {}
    entrypoint = raw.get("entrypoint")
    if entrypoint not in VALID_ENTRYPOINTS:
        entrypoint = "free_text"
    preset_id = raw.get("preset_id")
    if entrypoint == "preset":
        if (
            not isinstance(preset_id, str)
            or not preset_id.strip()
            or preset_id.strip() not in ALLOWED_PRESET_IDS
        ):
            return {"entrypoint": "free_text", "preset_id": None}
        return {"entrypoint": "preset", "preset_id": preset_id.strip()}
    return {"entrypoint": entrypoint, "preset_id": None}


def resolve_rule_route(request: CaseFileChatRequest) -> RuleRoute | None:
    """Resolve preset and issue-action entrypoints; no hint means legacy path."""

    hint = request.routing_hint
    if not hint:
        return None
    entrypoint = hint.get("entrypoint")
    if entrypoint == "preset":
        preset_id = hint.get("preset_id")
        if not isinstance(preset_id, str):
            return None
        preset = PRESET_ROUTE_TABLE.get(preset_id)
        if preset is None:
            return None
        return RuleRoute(
            route_source="rule_preset",
            primary_intent=preset["primary_intent"],
            profile=preset["profile"],
            reason_code=preset["reason_code"],
        )
    if entrypoint == "issue_action":
        issue_ids = request.focus.get("validation_issue_ids")
        if not isinstance(issue_ids, list) or not any(
            isinstance(item, str) and item for item in issue_ids
        ):
            return None
        return RuleRoute(
            route_source="rule_ui",
            primary_intent="explain_issue",
            profile="explain_issue.issue_fix",
            reason_code="rule_ui:issue_action",
        )
    return None


def task_understanding_for_rule(rule: RuleRoute) -> ChatTaskUnderstanding:
    """Build the deterministic Task State for a rule hit (confidence = 1.0)."""

    capabilities: dict[str, Any] = {
        "needs_casefile_retrieval": False,
        "needs_relations": False,
        "needs_validation_snapshot": False,
        "needs_suggestion_generation": False,
        "needs_reasoning": False,
    }
    risk_level = "low"
    if rule.primary_intent == "analysis":
        capabilities.update(
            {
                "needs_casefile_retrieval": True,
                "needs_relations": True,
                "needs_validation_snapshot": True,
                "needs_reasoning": True,
            }
        )
    elif rule.primary_intent == "validate_request":
        capabilities["needs_validation_snapshot"] = True
    elif rule.primary_intent == "explain_issue":
        capabilities.update(
            {
                "needs_casefile_retrieval": True,
                "needs_validation_snapshot": True,
                "needs_suggestion_generation": True,
            }
        )
        risk_level = "medium"
    return ChatTaskUnderstanding(
        primary_intent=rule.primary_intent,
        sub_intents=(rule.profile.removeprefix(f"{rule.primary_intent}."),),
        constraints={},
        capabilities=capabilities,
        complexity="low",
        multi_step=False,
        risk_level=risk_level,
        ambiguous=False,
        missing_info=(),
        confidence=1.0,
        reason_codes=(rule.reason_code,),
    )


def route_suggestion_policy(route: RouteDecision | dict[str, Any]) -> str:
    """Return deny / allow / inherit for the completion hook."""

    profile: Any
    if isinstance(route, RouteDecision):
        profile = route.execution_profile
    else:
        profile = route.get("execution_profile")
    if isinstance(profile, dict):
        policy = profile.get("suggestion_policy")
        if isinstance(policy, str) and policy in {"deny", "allow", "inherit"}:
            return policy
    allow = profile.get("allow_suggestions") if isinstance(profile, dict) else None
    if isinstance(allow, bool):
        return "allow" if allow else "deny"
    return "inherit"


def route_allows_suggestions(route: RouteDecision | dict[str, Any]) -> bool:
    """False only for routes whose completion hook must suppress suggestions."""

    return route_suggestion_policy(route) != "deny"


def task_understanding_from_output(
    candidate: ChatTaskUnderstandingOutput,
) -> ChatTaskUnderstanding:
    """Convert the validated LLM state into the runtime dataclass contract."""

    entities = candidate.entities
    constraints = candidate.constraints
    return ChatTaskUnderstanding(
        primary_intent=candidate.primary_intent,
        sub_intents=tuple(candidate.sub_intents),
        entities={
            "object_mentions": [
                {"text": mention.text, "resolved_ref": None}
                for mention in entities.object_mentions
            ],
            "event_mentions": [
                {"text": mention.text, "resolved_ref": None}
                for mention in entities.event_mentions
            ],
            "issue_mentions": [
                {"text": mention.text, "resolved_ref": None}
                for mention in entities.issue_mentions
            ],
            "temporal_mentions": list(entities.temporal_mentions),
        },
        constraints={
            "preserved_negations": list(constraints.preserved_negations),
            "preserved_actions": list(constraints.preserved_actions),
            "output_format": constraints.output_format,
        },
        capabilities=dict(candidate.capabilities),
        complexity=candidate.complexity,
        multi_step=candidate.multi_step,
        risk_level=candidate.risk_level,
        ambiguous=candidate.ambiguous,
        missing_info=tuple(candidate.missing_info),
        confidence=candidate.confidence,
        reason_codes=tuple(candidate.reason_codes),
    )


def resolve_intent_mentions(
    task_understanding: ChatTaskUnderstanding,
    request: CaseFileChatRequest,
) -> ChatTaskUnderstanding:
    """Deterministically resolve mention text to real IDs.

    The LLM only emits mention text; refs come from focus first, then from
    object labels in the frozen CaseFile. Unknown mentions stay unresolved.
    """

    labels = _casefile_object_labels(request.casefile)
    focus_object_ids = request.focus.get("object_ids")
    focus_event_ids = request.focus.get("event_ids")
    focus_issue_ids = request.focus.get("validation_issue_ids")
    issue_labels = {
        str(item["issue_id"]): [
            str(item["issue_id"]),
            str(item.get("title") or ""),
            str(item.get("message") or ""),
        ]
        for item in request.validation_issues
        if isinstance(item, dict) and item.get("issue_id")
    }
    entities = task_understanding.entities
    object_mentions = _resolve_mention_group(
        entities.get("object_mentions"),
        labels,
        focus_ids=focus_object_ids if isinstance(focus_object_ids, list) else [],
    )
    event_mentions = _resolve_mention_group(
        entities.get("event_mentions"),
        labels,
        focus_ids=focus_event_ids if isinstance(focus_event_ids, list) else [],
    )
    issue_mentions = _resolve_mention_group(
        entities.get("issue_mentions"),
        issue_labels,
        focus_ids=focus_issue_ids if isinstance(focus_issue_ids, list) else [],
    )
    return replace(
        task_understanding,
        entities={
            **entities,
            "object_mentions": object_mentions,
            "event_mentions": event_mentions,
            "issue_mentions": issue_mentions,
        },
    )


def confidence_gate_decision(
    task_understanding: ChatTaskUnderstanding,
    *,
    tau_high: float = 0.85,
) -> bool:
    """R2 gate: high-confidence states pass; sensitive intents never pass at mid."""

    if task_understanding.confidence < tau_high:
        return False
    return True


def _resolve_mention_group(
    mentions: object,
    labels: Mapping[str, Sequence[str]],
    *,
    focus_ids: list[Any],
) -> list[dict[str, Any]]:
    if not isinstance(mentions, list):
        return []
    resolved: list[dict[str, Any]] = []
    for raw in mentions:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        resolved_ref: str | None = None
        if _is_anaphoric_mention(text) and len(focus_ids) == 1:
            resolved_ref = str(focus_ids[0])
        if resolved_ref is None:
            for object_id in focus_ids:
                object_id = str(object_id)
                if _mention_matches(text, object_id):
                    resolved_ref = object_id
                    break
        if resolved_ref is None:
            matches = [
                object_id
                for object_id, candidate_labels in labels.items()
                if _mention_matches(text, *candidate_labels)
            ]
            if len(matches) == 1:
                resolved_ref = matches[0]
        resolved.append({"text": text, "resolved_ref": resolved_ref})
    return resolved


def _is_anaphoric_mention(text: str) -> bool:
    stripped = text.strip()
    if stripped in {"它", "她", "他", "该对象", "这个对象", "焦点对象"}:
        return True
    return stripped in {"这条事件", "该事件", "这个事件", "这个问题", "该问题", "焦点问题"}


def _mention_matches(text: str, *candidates: str) -> bool:
    lowered = text.lower()
    for candidate in candidates:
        candidate = str(candidate or "").strip().lower()
        if not candidate:
            continue
        if candidate == lowered or candidate in lowered or lowered in candidate:
            return True
    return False


def _casefile_object_labels(casefile: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    labels: dict[str, tuple[str, ...]] = {}
    for collection in (
        "entities",
        "relationships",
        "locations",
        "events",
        "information_units",
        "claims",
        "hypotheses",
        "reasoning_paths",
    ):
        for item in casefile.get(collection) or []:
            if not isinstance(item, dict):
                continue
            object_id = item.get("id")
            if not isinstance(object_id, str):
                continue
            candidate_labels = [
                object_id,
                str(item.get("name") or ""),
                str(item.get("title") or ""),
                f"{object_id}:{item.get('name') or item.get('title') or ''}",
            ]
            labels[object_id] = tuple(candidate_labels)
    return labels


def route_public_payload(route: RouteDecision | dict[str, Any]) -> dict[str, Any]:
    """Audit-safe route payload: policy summary only, no casefile or derivation text."""

    payload = agent_state_to_jsonable(route)
    if not isinstance(payload, dict):
        raise TypeError("route must be a RouteDecision or a dict")
    return payload


def route_result_summary(
    route: RouteDecision | dict[str, Any] | None,
    *,
    suggestion_policy: str | None = None,
    suppressed_count: int = 0,
) -> dict[str, Any]:
    """Small routing summary embedded in TaskRun result payloads."""

    if route is None:
        return {
            "router_version": INTENT_ROUTER_VERSION,
            "route_source": "legacy",
            "intent": None,
            "rewrite_strategy": "KEEP",
            "suggestion_policy": suggestion_policy or "inherit",
            "suppressed_count": suppressed_count,
        }
    payload = route_public_payload(route)
    profile = payload.get("execution_profile")
    intent = profile.get("primary_intent") if isinstance(profile, dict) else None
    return {
        "router_version": payload.get("router_version", INTENT_ROUTER_VERSION),
        "route_hash": payload.get("route_hash"),
        "route_source": payload.get("route_source"),
        "intent": intent,
        "rewrite_strategy": payload.get("rewrite_strategy", "KEEP"),
        "suggestion_policy": suggestion_policy or route_suggestion_policy(route),
        "suppressed_count": suppressed_count,
    }


__all__ = [
    "ALLOWED_PRESET_IDS",
    "INTENT_ROUTER_VERSION",
    "PRESET_ROUTE_TABLE",
    "RuleRoute",
    "confidence_gate_decision",
    "normalize_routing_hint",
    "resolve_intent_mentions",
    "resolve_rule_route",
    "route_allows_suggestions",
    "route_public_payload",
    "route_result_summary",
    "route_suggestion_policy",
    "task_understanding_for_rule",
    "task_understanding_from_output",
]
