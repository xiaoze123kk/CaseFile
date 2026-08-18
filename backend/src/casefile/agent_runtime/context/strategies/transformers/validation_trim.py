"""Validation snapshot transformer: compact non-focus issues, keep gate snapshots full."""

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

_MAX_MESSAGE_CHARS = 200
_GATE_PROFILE = "validate_request"
_COMPACT_FIELDS = ("issue_id", "rule_id", "severity", "title", "message", "object_refs")


def _compact_issue(issue: dict[str, Any], *, max_message_chars: int) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key in _COMPACT_FIELDS:
        value = issue.get(key)
        if value is None:
            continue
        if key == "message" and isinstance(value, str):
            compacted[key] = value[: max(1, max_message_chars)]
        else:
            compacted[key] = value
    return compacted


def trim_validation_issues(
    issues: list[Any],
    *,
    focus_issue_ids: list[str],
    author_message: str,
    gate: bool = False,
    max_message_chars: int = _MAX_MESSAGE_CHARS,
) -> dict[str, Any]:
    """Return full issues for focus/mention/gate, compact summaries otherwise."""

    if gate:
        return {
            "issues": list(issues),
            "mode": "full",
            "reason": "validate_request_gate",
            "compacted_count": 0,
        }
    kept_ids = set(focus_issue_ids)
    trimmed: list[dict[str, Any]] = []
    compacted_count = 0
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        issue_id = issue.get("issue_id")
        issue_key = str(issue_id) if issue_id is not None else ""
        mentioned = bool(issue_key) and issue_key in author_message
        if issue_key in kept_ids or mentioned:
            trimmed.append(dict(issue))
            continue
        trimmed.append(_compact_issue(issue, max_message_chars=max_message_chars))
        compacted_count += 1
    return {
        "issues": trimmed,
        "mode": "mixed",
        "reason": "focus_or_mention_only_full",
        "compacted_count": compacted_count,
    }


def _route_profile(run: ContextRun) -> str:
    routing = run.routing or {}
    route = routing.get("route")
    profile: dict[str, Any] = {}
    if isinstance(route, dict):
        raw_profile = route.get("execution_profile")
        profile = raw_profile if isinstance(raw_profile, dict) else {}
    value = profile.get("profile")
    return str(value) if isinstance(value, str) and value else ""


@dataclass(slots=True)
class ValidationTrimStage:
    """Policy stage producing the ``validation_issues`` block."""

    name: str = "validation_trim_v1"
    version: str = "validation-trim-v1"
    capabilities: frozenset[str] = frozenset({"transformer", "chat", "deterministic"})

    def can_run(self, run: ContextRun) -> bool:
        return isinstance(run.frozen_input.get("validation"), dict)

    def run(self, run: ContextRun) -> StageResult:
        validation = run.frozen_input.get("validation")
        if not isinstance(validation, dict):
            return StageResult()
        issues = [
            item for item in validation.get("issues") or [] if isinstance(item, dict)
        ]
        focus = run.frozen_input.get("focus")
        focus_issue_ids: list[str] = []
        if isinstance(focus, dict):
            raw_ids = focus.get("validation_issue_ids")
            if isinstance(raw_ids, list):
                focus_issue_ids = [
                    str(value) for value in raw_ids if isinstance(value, str) and value
                ]
        message = run.frozen_input.get("message")
        author_message = str(message) if isinstance(message, str) else ""
        config = run.policy_stage_config("validation_issues")
        max_chars = int(config.get("max_message_chars", _MAX_MESSAGE_CHARS))
        gate_profile = str(config.get("gate_profile", _GATE_PROFILE))
        gate = _route_profile(run) == gate_profile
        trimmed = trim_validation_issues(
            issues,
            focus_issue_ids=focus_issue_ids,
            author_message=author_message,
            gate=gate,
            max_message_chars=max(1, max_chars),
        )
        decisions: list[ContextDecision] = []
        if trimmed["compacted_count"]:
            decisions.append(
                ContextDecision(
                    stage="validation_trim",
                    code="validation_issues_compacted",
                    detail=(
                        f"{trimmed['compacted_count']} non-focus issues compacted; "
                        "full snapshot remains available through get_validation_issues"
                    ),
                )
            )
        elif gate:
            decisions.append(
                ContextDecision(
                    stage="validation_trim",
                    code="validation_full_for_gate",
                    detail="validate_request keeps the full frozen validation snapshot",
                )
            )
        payload: list[dict[str, Any]] = trimmed["issues"]
        return StageResult(
            added=(
                ContextBlock(
                    id="validation_issues",
                    kind="validation_trim",
                    payload=payload,
                    tokens=estimate_jsonable_tokens(payload, run.estimator),
                    metadata={
                        "mode": trimmed["mode"],
                        "compacted_count": trimmed["compacted_count"],
                    },
                ),
            ),
            decisions=tuple(decisions),
            metrics={
                "issue_count": len(payload),
                "compacted_count": trimmed["compacted_count"],
                "gate": gate,
            },
        )


__all__ = [
    "ValidationTrimStage",
    "trim_validation_issues",
]
