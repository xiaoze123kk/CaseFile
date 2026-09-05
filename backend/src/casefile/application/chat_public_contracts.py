"""Minimal allowlist adapters from legacy Chat views to frozen public DTOs.

Phase 1 keeps this module deliberately coarse: it establishes a strict HTTP
boundary without taking ownership of the richer event projection (Phase 2) or
author-readable field/object formatting (Phase 4).
"""

from __future__ import annotations

from typing import Any

from casefile_contracts import (
    PublicAgentMessage,
    PublicAgentMessageReceipt,
    PublicAgentRun,
    PublicGoalDelivery,
    PublicGoalSession,
    PublicPatchResponse,
    PublicPatchReviewResult,
    PublicPatchSet,
    PublicRoutingFeedbackReceipt,
)

from casefile.application.chat_public_patches import (
    public_patch_review_payload,
    public_patch_set_payload,
)

_ACTIVITY_BY_STAGE = {
    "queued": "understanding",
    "preparing": "understanding",
    "routing": "understanding",
    "rewriting": "understanding",
    "context": "reading",
    "retrieving": "reading",
    "tool_call": "reading",
    "tool_result": "reading",
    "validating": "checking",
    "verification": "checking",
    "repairing": "checking",
    "general_mutation": "preparing_changes",
    "mutation": "preparing_changes",
    "finalizing": "finalizing",
    "cancelling": "finalizing",
}

_PUBLIC_INTERPRETATION_BY_INTENT = {
    "question": "conversation",
    "analysis": "analysis",
    "explain_issue": "analysis",
    "logic_audit": "logic_review",
    "validate_request": "logic_review",
    "edit_request": "change_request",
    "clarify": "clarification",
    "unsupported_action": "clarification",
    "out_of_scope": "clarification",
}

_INTERNAL_INTENT_BY_INTERPRETATION = {
    "conversation": "question",
    "analysis": "analysis",
    "logic_review": "logic_audit",
    "change_request": "edit_request",
    "clarification": "clarify",
}


def public_agent_message_view(value: dict[str, Any]) -> PublicAgentMessage:
    task = value.get("task") if isinstance(value.get("task"), dict) else None
    result = task.get("result") if task is not None and isinstance(task.get("result"), dict) else {}
    patch_value = value.get("patch_set")
    patch = public_patch_set_view(patch_value) if isinstance(patch_value, dict) else None
    findings = _public_findings(result.get("audit_findings"))
    role = "assistant" if value.get("role") == "assistant" else "user"
    status = _message_status(value.get("status"))
    return PublicAgentMessage.model_validate(
        {
            "message_id": int(value["message_id"]),
            "sequence": int(value["sequence_no"]),
            "role": role,
            "status": status,
            "response_kind": _response_kind(
                role=role,
                status=status,
                patch=patch,
                findings=findings,
                result=result,
            ),
            "body": value.get("content") if isinstance(value.get("content"), str) else None,
            "context_snapshot": value.get("context_snapshot"),
            "interpretation": public_routing_interpretation(result.get("routing")),
            "references": _public_references(value),
            "findings": findings,
            "patch": patch,
            "run": None if task is None else public_agent_run_view(task),
            "created_at": value["created_at"],
            "updated_at": value["updated_at"],
        }
    )


def public_agent_message_receipt_view(
    value: dict[str, Any],
) -> PublicAgentMessageReceipt:
    goal_value = value.get("goal")
    delivery_value = value.get("delivery")
    return PublicAgentMessageReceipt(
        user_message=public_agent_message_view(_required_dict(value, "user_message")),
        assistant_message=public_agent_message_view(_required_dict(value, "assistant_message")),
        goal=(
            goal_value
            if isinstance(goal_value, PublicGoalSession)
            else (
                None
                if not isinstance(goal_value, dict)
                else PublicGoalSession.model_validate(goal_value)
            )
        ),
        delivery=(
            delivery_value
            if isinstance(delivery_value, PublicGoalDelivery)
            else (
                None
                if not isinstance(delivery_value, dict)
                else PublicGoalDelivery.model_validate(delivery_value)
            )
        ),
    )


def public_agent_run_view(value: dict[str, Any]) -> PublicAgentRun:
    status = str(value.get("status") or "failed")
    if status not in {"queued", "running", "cancelling", "succeeded", "failed", "cancelled"}:
        status = "failed"
    failure_value = value.get("failure")
    failure = None
    if isinstance(failure_value, dict):
        failure = {
            "category": _failure_category(failure_value),
            "message": str(failure_value.get("message") or "本次请求未能完成。")[:500],
            "retryable": bool(failure_value.get("retryable")),
        }
    return PublicAgentRun.model_validate(
        {
            "run_id": int(value["task_run_id"]),
            "goal_id": _optional_positive_int(value.get("goal_id")),
            "goal_revision": _optional_nonnegative_int(value.get("goal_revision")),
            "status": status,
            "activity": (
                _ACTIVITY_BY_STAGE.get(str(value.get("stage") or ""))
                if status in {"queued", "running", "cancelling"}
                else None
            ),
            "cancellable": status in {"queued", "running"},
            "failure": failure,
        }
    )


def public_patch_set_view(value: dict[str, Any]) -> PublicPatchSet:
    return PublicPatchSet.model_validate(public_patch_set_payload(value))


def public_patch_review_view(value: dict[str, Any]) -> PublicPatchReviewResult:
    return PublicPatchReviewResult.model_validate(public_patch_review_payload(value))


def public_patch_response_view(value: dict[str, Any]) -> PublicPatchResponse:
    goal = value.get("goal")
    continuation = value.get("continuation_run")
    return PublicPatchResponse(
        patch=public_patch_set_view(value),
        review=public_patch_review_view(value),
        revision=int(value.get("draft_revision") or value.get("base_draft_revision") or 0),
        goal=None if goal is None else PublicGoalSession.model_validate(goal),
        continuation_run=(
            None if continuation is None else public_agent_run_view(continuation)
        ),
    )


def public_routing_feedback_view(
    value: dict[str, Any],
) -> PublicRoutingFeedbackReceipt:
    interpretation = value.get("interpretation")
    if not isinstance(interpretation, str):
        raise RuntimeError("Routing feedback result has no public interpretation")
    return PublicRoutingFeedbackReceipt.model_validate(
        {
            "message_id": int(value["message_id"]),
            "acknowledged": True,
            "interpretation": interpretation,
        }
    )


def public_routing_interpretation(value: Any) -> str | None:
    intent = value.get("intent") if isinstance(value, dict) else value
    if not isinstance(intent, str):
        return None
    return _PUBLIC_INTERPRETATION_BY_INTENT.get(intent)


def internal_intent_for_public_interpretation(value: Any) -> str | None:
    raw = getattr(value, "value", value)
    if raw is None:
        return None
    if not isinstance(raw, str) or raw not in _INTERNAL_INTENT_BY_INTERPRETATION:
        raise ValueError("Unsupported public routing interpretation")
    return _INTERNAL_INTENT_BY_INTERPRETATION[raw]


def _public_findings(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    findings: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title = item.get("title")
        statement = item.get("statement")
        finding_id = item.get("finding_id")
        if not all(
            isinstance(part, str) and part.strip() for part in (title, statement, finding_id)
        ):
            continue
        findings.append(
            {
                "finding_id": str(finding_id)[:160],
                "severity": {"S1": "blocker", "S2": "warning"}.get(
                    str(item.get("severity")), "note"
                ),
                "title": str(title)[:200],
                "statement": str(statement)[:2000],
            }
        )
    return findings


def _public_references(value: dict[str, Any]) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    slots = (
        ("referenced_object_ids", "story_item", "卷宗内容"),
        ("referenced_event_ids", "event", "事件"),
        ("referenced_validation_issue_ids", "finding", "验证发现"),
    )
    for key, kind, label in slots:
        raw = value.get(key)
        if not isinstance(raw, list):
            continue
        references.extend(
            {"kind": kind, "target_id": item[:160], "label": label}
            for item in raw
            if isinstance(item, str) and item
        )
    return references[:200]


def _response_kind(
    *,
    role: str,
    status: str,
    patch: PublicPatchSet | None,
    findings: list[dict[str, str]],
    result: dict[str, Any],
) -> str:
    if role == "user":
        return "message"
    if status in {"failed", "cancelled"}:
        return "failure"
    if patch is not None:
        return "patch_proposal"
    if findings:
        return "findings"
    interpretation = public_routing_interpretation(result.get("routing"))
    if interpretation == "clarification":
        return "clarification"
    if interpretation in {"analysis", "logic_review"}:
        return "analysis"
    return "answer"


def _failure_category(value: dict[str, Any]) -> str:
    code = str(value.get("code") or "")
    if code == "public_output_policy_failed":
        return "output_rejected"
    if code.startswith("provider_"):
        return "temporarily_unavailable"
    return "request_failed"


def _message_status(value: Any) -> str:
    status = str(value or "failed")
    return status if status in {"pending", "completed", "failed", "cancelled"} else "failed"


def _optional_positive_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and value >= 1 else None


def _optional_nonnegative_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and value >= 0 else None


def _required_dict(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise RuntimeError(f"Missing legacy Chat view field: {key}")
    return result


__all__ = [
    "internal_intent_for_public_interpretation",
    "public_agent_message_receipt_view",
    "public_agent_message_view",
    "public_agent_run_view",
    "public_patch_review_view",
    "public_patch_response_view",
    "public_patch_set_view",
    "public_routing_feedback_view",
    "public_routing_interpretation",
]
