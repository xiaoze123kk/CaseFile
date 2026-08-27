"""Deterministic rule intent resolution for CaseFile chat.

R1 deliberately contains no LLM call: presets and the issue-action UI entry are
resolved from the frozen `routing_hint`. Free-text messages that carry a hint
receive the R1 fallback route; messages without a hint keep the legacy path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import combinations
from typing import Any

from casefile.agent_runtime.models import (
    CaseFileChatRequest,
    CaseFileChatResult,
    ChatTaskUnderstanding,
    ChatTaskUnderstandingOutput,
    RouteDecision,
    agent_state_to_jsonable,
)
from casefile.agent_runtime.public_language import (
    is_protected_internal_disclosure_request,
)

INTENT_ROUTER_VERSION = "casefile-chat-router-v2"

_EDIT_FIELD_ALIASES = {
    "/description": ("描述", "description", "简介"),
    "/aliases": ("别名", "aliases"),
    "/name": ("名称", "名字", "name"),
    "/title": ("标题", "title"),
    "/summary": ("摘要", "summary"),
    "/status": ("状态", "status"),
}

_DESTRUCTIVE_ACTION_MARKERS = (
    "删除",
    "删掉",
    "移除",
    "delete",
    "remove",
)

_CREATE_ACTION_MARKERS = (
    "创建",
    "新建",
    "新增",
    "create",
    "add a new",
)
_AMBIGUOUS_TARGET_MARKERS = (
    "没有说明具体对象",
    "没有说明目标",
    "目标未说明",
    "对象未说明",
    "unspecified target",
)
_CLARIFICATION_REQUEST_MARKERS = (
    "先澄清",
    "先问清",
    "请先确认目标",
    "clarify first",
)
_UNBOUND_TARGET_MARKERS = ("它", "这个对象", "该对象", "this object", " it ")
_ALTERNATIVE_TARGET_MARKERS = ("或", "或者", " or ")
_VAGUE_VALUE_MARKERS = ("改一下", "修改一下", "调整一下", "改改", "change it")
_PROTECTED_COLLECTIONS = ("resolution_specs", "constraints", "structure_locks")

_DRAFT_TARGET_MARKERS = ("draft", "工作稿")
_REVIEW_BYPASS_MARKERS = (
    "直接修改",
    "直接改",
    "直接写入",
    "绕过审阅",
    "跳过审阅",
    "不经审阅",
    "directly modify",
    "directly edit",
    "write directly",
    "bypass review",
    "skip review",
)


@dataclass(frozen=True, slots=True)
class EditTarget:
    object_id: str
    path: str

    def as_dict(self) -> dict[str, str]:
        return {"object_id": self.object_id, "path": self.path}


@dataclass(frozen=True, slots=True)
class EditTargetManifest:
    targets: tuple[EditTarget, ...] = ()
    ambiguous: bool = False

    def as_list(self) -> list[dict[str, str]]:
        return [target.as_dict() for target in self.targets]


def general_mutation_abstention_reason(request: CaseFileChatRequest) -> str | None:
    """Return a deterministic reason when an edit request is not uniquely bound."""

    message = f" {request.message.casefold()} "
    manifest = build_edit_target_manifest(request)
    explicit_object_ids = _explicit_object_ids(request, message)
    destructive = any(marker in message for marker in _DESTRUCTIVE_ACTION_MARKERS)
    creating = any(marker in message for marker in _CREATE_ACTION_MARKERS)
    if destructive and len(explicit_object_ids) != 1:
        return "general_mutation_delete_target_ambiguous"
    if destructive:
        return None
    if any(marker in message for marker in _ALTERNATIVE_TARGET_MARKERS) and len(
        _alternative_candidate_ids(request, message)
    ) > 1:
        return "general_mutation_target_ambiguous"
    if (
        any(marker in message for marker in _UNBOUND_TARGET_MARKERS)
        and not manifest.targets
        and not explicit_object_ids
        and not request.focus.get("object_ids")
        and not request.focus.get("event_ids")
    ):
        return "general_mutation_target_ambiguous"

    vague_value = any(marker in message for marker in _VAGUE_VALUE_MARKERS)
    if explicit_object_ids and not manifest.targets and not destructive and vague_value:
        return "general_mutation_field_ambiguous"
    if not creating and manifest.ambiguous and len(explicit_object_ids) > 1:
        return "general_mutation_target_ambiguous"
    if manifest.targets and vague_value:
        return "general_mutation_value_missing"
    return None


def _explicit_object_ids(request: CaseFileChatRequest, message: str) -> set[str]:
    object_ids: set[str] = set()
    for values in request.casefile.values():
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            labels = (item["id"], item.get("name"), item.get("title"))
            if any(
                isinstance(label, str) and label.strip() and label.casefold() in message
                for label in labels
            ):
                object_ids.add(str(item["id"]))
    return object_ids


def _alternative_candidate_ids(
    request: CaseFileChatRequest,
    message: str,
) -> set[str]:
    """Resolve abbreviated labels only for an explicit either/or choice."""

    object_ids: set[str] = set()
    for values in request.casefile.values():
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            labels = (item.get("name"), item.get("title"), *(item.get("aliases") or ()))
            if any(
                isinstance(label, str)
                and len(label.strip()) >= 3
                and _contains_ordered_label_fragment(message, label.casefold())
                for label in labels
            ):
                object_ids.add(str(item["id"]))
    return object_ids


def _contains_ordered_label_fragment(message: str, label: str) -> bool:
    candidate = "".join(character for character in message if character.isalnum())
    label = "".join(character for character in label if character.isalnum())
    fragments = {"".join(fragment) for fragment in combinations(label, 3)}
    return any(
        fragment in clause
        for clause in candidate.replace("或者", "或").split("或")
        for fragment in fragments
    )


def build_edit_target_manifest(request: CaseFileChatRequest) -> EditTargetManifest:
    """Freeze explicit object/field pairs that resolve uniquely."""

    message = request.message.casefold()
    matches: dict[str, list[str]] = {}
    collection_by_id: dict[str, str] = {}
    for collection, values in request.casefile.items():
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            object_id = str(item["id"])
            collection_by_id[object_id] = collection
            if object_id.casefold() in message:
                matches.setdefault(object_id.casefold(), []).append(object_id)
            for field in ("name", "title"):
                label = item.get(field)
                if isinstance(label, str) and label.strip() and label.casefold() in message:
                    matches.setdefault(label.casefold(), []).append(object_id)
    focused_ids = {
        object_id
        for slot in ("object_ids", "event_ids")
        for object_id in request.focus.get(slot, ())
        if isinstance(object_id, str)
    }
    resolved_labels: dict[str, str] = {}
    unresolved_label = False
    for label, ids in matches.items():
        unique_ids = tuple(dict.fromkeys(ids))
        if any(label != other and label in other for other in matches):
            unresolved_label = True
            continue
        if len(unique_ids) == 1:
            resolved_labels[label] = unique_ids[0]
            continue
        focused_matches = tuple(
            object_id for object_id in unique_ids if object_id in focused_ids
        )
        if len(focused_matches) == 1:
            resolved_labels[label] = focused_matches[0]
        else:
            unresolved_label = True
    label_occurrences = [
        (message.index(label), label)
        for label in matches
        if label in message
    ]
    paths_by_id: dict[str, set[str]] = {}
    field_aliases = dict(_EDIT_FIELD_ALIASES)
    for editable_fields in request.editable_fields_by_collection.values():
        for raw_path in editable_fields:
            path = raw_path if raw_path.startswith("/") else f"/{raw_path}"
            field_name = path.removeprefix("/")
            field_aliases.setdefault(path, (field_name,))
    for path, aliases in field_aliases.items():
        for alias in aliases:
            alias_folded = alias.casefold()
            start = 0
            while (alias_position := message.find(alias_folded, start)) >= 0:
                if label_occurrences:
                    preceding_labels = [
                        occurrence
                        for occurrence in label_occurrences
                        if occurrence[0] <= alias_position
                    ]
                    if preceding_labels:
                        _, nearest_label = max(preceding_labels)
                    else:
                        _, nearest_label = min(
                            label_occurrences,
                            key=lambda occurrence: abs(
                                occurrence[0] - alias_position
                            ),
                        )
                    resolved_object_id = resolved_labels.get(nearest_label)
                    if resolved_object_id is not None:
                        paths_by_id.setdefault(resolved_object_id, set()).add(path)
                elif len(focused_ids) == 1:
                    focused_object_id = next(iter(focused_ids))
                    if focused_object_id in collection_by_id:
                        paths_by_id.setdefault(focused_object_id, set()).add(path)
                start = alias_position + len(alias_folded)
    targets: list[EditTarget] = []
    for object_id in sorted(paths_by_id):
        editable = set(
            request.editable_fields_by_collection.get(collection_by_id[object_id], ())
        )
        for path in sorted(paths_by_id[object_id]):
            if path in editable or path.removeprefix("/") in editable:
                targets.append(EditTarget(object_id=object_id, path=path))
    return EditTargetManifest(
        targets=tuple(targets),
        ambiguous=unresolved_label or (bool(matches) and not bool(targets)),
    )
ALLOWED_PRESET_IDS = frozenset({"inspect", "evidence", "compare", "gate", "audit"})
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
    "audit": {
        "primary_intent": "logic_audit",
        "profile": "logic_audit.full_review",
        "reason_code": "rule_preset:audit",
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


def resolve_rule_route(
    request: CaseFileChatRequest,
    *,
    allow_general_mutation_create: bool = False,
    allow_general_mutation_delete: bool = False,
    allow_general_mutation_update: bool = False,
) -> RuleRoute | None:
    """Resolve preset and issue-action entrypoints; no hint means legacy path."""

    normalized_message = request.message.casefold()
    if is_protected_internal_disclosure_request(request.message):
        return RuleRoute(
            route_source="rule_safety",
            primary_intent="unsupported_action",
            profile="unsupported_action.scope",
            reason_code="rule_safety:protected_internal_disclosure_request",
        )
    protected_ids = {
        str(item["id"]).casefold()
        for collection in _PROTECTED_COLLECTIONS
        for item in request.casefile.get(collection, [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if any(object_id in normalized_message for object_id in protected_ids):
        return RuleRoute(
            route_source="rule_safety",
            primary_intent="unsupported_action",
            profile="unsupported_action.scope",
            reason_code="rule_safety:protected_collection_target",
        )
    destructive_requested = any(
        marker in normalized_message for marker in _DESTRUCTIVE_ACTION_MARKERS
    )
    abstention_reason = general_mutation_abstention_reason(request)
    if destructive_requested and allow_general_mutation_delete and abstention_reason is not None:
        return RuleRoute(
            route_source="rule_safety",
            primary_intent="clarify",
            profile="clarify.question",
            reason_code=f"rule_safety:{abstention_reason}",
        )
    clarification_required = any(
        marker in normalized_message for marker in _AMBIGUOUS_TARGET_MARKERS
    ) and any(marker in normalized_message for marker in _CLARIFICATION_REQUEST_MARKERS)
    if destructive_requested and clarification_required:
        return RuleRoute(
            route_source="rule_safety",
            primary_intent="clarify",
            profile="clarify.question",
            reason_code="rule_safety:ambiguous_destructive_target",
        )
    if destructive_requested:
        if allow_general_mutation_delete:
            return RuleRoute(
                route_source="rule_capability",
                primary_intent="edit_request",
                profile="edit_request.edit",
                reason_code="rule_capability:general_mutation_delete",
            )
        return RuleRoute(
            route_source="rule_safety",
            primary_intent="unsupported_action",
            profile="unsupported_action.scope",
            reason_code="rule_safety:destructive_action",
        )
    if allow_general_mutation_create and any(
        marker in normalized_message for marker in _CREATE_ACTION_MARKERS
    ):
        return RuleRoute(
            route_source="rule_capability",
            primary_intent="edit_request",
            profile="edit_request.edit",
            reason_code="rule_capability:general_mutation_create",
        )
    if allow_general_mutation_update:
        if abstention_reason is not None:
            return RuleRoute(
                route_source="rule_safety",
                primary_intent="clarify",
                profile="clarify.question",
                reason_code=f"rule_safety:{abstention_reason}",
            )
        manifest = build_edit_target_manifest(request)
        if manifest.targets and not manifest.ambiguous:
            return RuleRoute(
                route_source="rule_capability",
                primary_intent="edit_request",
                profile="edit_request.edit",
                reason_code="rule_capability:general_mutation_update",
            )
    if any(marker in normalized_message for marker in _DRAFT_TARGET_MARKERS) and any(
        marker in normalized_message for marker in _REVIEW_BYPASS_MARKERS
    ):
        return RuleRoute(
            route_source="rule_safety",
            primary_intent="unsupported_action",
            profile="unsupported_action.scope",
            reason_code="rule_safety:direct_draft_bypass",
        )
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
    elif rule.primary_intent == "logic_audit":
        capabilities.update(
            {
                "needs_casefile_retrieval": True,
                "needs_relations": True,
                "needs_validation_snapshot": True,
                "needs_suggestion_generation": True,
                "needs_reasoning": True,
            }
        )
        risk_level = "medium"
    elif rule.primary_intent == "edit_request":
        capabilities.update(
            {
                "needs_casefile_retrieval": True,
                "needs_relations": True,
                "needs_validation_snapshot": True,
                "needs_suggestion_generation": True,
            }
        )
        risk_level = "high"
    elif rule.primary_intent == "clarify":
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




def apply_route_suggestion_policy(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
) -> CaseFileChatResult:
    """Return the post-permission candidate shared by Worker and M2.

    Validate the model candidate before this call so an edit-target repair can
    still reason about the original proposal. A denied route must nevertheless
    never expose that proposal to persistence or the outcome grader.
    """

    route = request.route
    suggestions = result.candidate.suggestions
    if route is None or route_allows_suggestions(route) or not suggestions:
        return result
    request.emit(
        "route.suggestions_suppressed",
        "routing",
        {
            **route_public_payload(route),
            "suggestion_policy": route_suggestion_policy(route),
            "suppressed_count": len(suggestions),
            "source": "shared_execution_runner",
        },
    )
    return replace(
        result,
        candidate=result.candidate.model_copy(update={"suggestions": []}),
    )


def suppress_general_mutation_finalizer_suggestions(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
) -> CaseFileChatResult:
    """Discard legacy field suggestions when General Mutation owns the PatchSet."""

    route = request.route
    suggestions = result.candidate.suggestions
    if (
        route is None
        or not suggestions
        or not any(
            code.startswith("rule_capability:general_mutation_")
            or (
                code.startswith("rule_safety:")
                and route_suggestion_policy(route) == "deny"
            )
            for code in route.reason_codes
        )
    ):
        return result
    request.emit(
        "model.general_mutation_suggestions_suppressed",
        "validating",
        {
            "reason_code": "general_mutation_is_authoritative",
            "suppressed_count": len(suggestions),
        },
    )
    return replace(
        result,
        candidate=result.candidate.model_copy(update={"suggestions": []}),
    )

__all__ = [
    "apply_route_suggestion_policy",
    "ALLOWED_PRESET_IDS",
    "EditTarget",
    "EditTargetManifest",
    "INTENT_ROUTER_VERSION",
    "PRESET_ROUTE_TABLE",
    "RuleRoute",
    "confidence_gate_decision",
    "build_edit_target_manifest",
    "general_mutation_abstention_reason",
    "normalize_routing_hint",
    "resolve_intent_mentions",
    "resolve_rule_route",
    "route_allows_suggestions",
    "route_public_payload",
    "route_result_summary",
    "route_suggestion_policy",
    "suppress_general_mutation_finalizer_suggestions",
    "task_understanding_for_rule",
    "task_understanding_from_output",
]
