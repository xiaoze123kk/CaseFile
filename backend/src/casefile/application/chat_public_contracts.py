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
    PublicPatchResponse,
    PublicPatchReviewResult,
    PublicPatchSet,
)

_ACTIVITY_BY_STAGE = {
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
}

_TYPE_LABELS = {
    "resolution_spec": "谜题解答",
    "entity": "人物或对象",
    "relationship": "关系",
    "location": "地点",
    "event": "事件",
    "information_unit": "信息",
    "claim": "主张",
    "hypothesis": "假设",
    "reasoning_path": "推理路径",
    "constraint": "约束",
    "structure_lock": "结构锁定",
    "resolution_specs": "谜题解答",
    "entities": "人物或对象",
    "relationships": "关系",
    "locations": "地点",
    "events": "事件",
    "information_units": "信息",
    "claims": "主张",
    "hypotheses": "假设",
    "reasoning_paths": "推理路径",
    "constraints": "约束",
    "structure_locks": "结构锁定",
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
            "interpretation": _public_interpretation(result.get("routing")),
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
    return PublicAgentMessageReceipt(
        user_message=public_agent_message_view(_required_dict(value, "user_message")),
        assistant_message=public_agent_message_view(_required_dict(value, "assistant_message")),
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
    raw_operations = value.get("operations")
    operations = raw_operations if isinstance(raw_operations, list) else []
    status = "stale" if value.get("is_stale") else str(value.get("status") or "rejected")
    if status not in {"pending", "applied", "undone", "stale", "rejected"}:
        status = "rejected"
    changes = [
        _public_patch_change(operation) for operation in operations if isinstance(operation, dict)
    ]
    contains_delete = bool(value.get("contains_delete")) or any(
        change["kind"] == "delete" for change in changes
    )
    review_rule = "atomic" if value.get("review_mode") == "atomic" else "selective"
    return PublicPatchSet.model_validate(
        {
            "patch_id": int(value["patch_set_id"]),
            "title": "修改建议",
            "summary": f"这组建议包含 {len(changes)} 项卷宗修改。",
            "status": status,
            "review_rule": review_rule,
            "base_revision": int(value.get("base_draft_revision") or 0),
            "impact": {
                "summary": (
                    f"共涉及 {len(changes)} 项修改"
                    f"，{'包含删除' if contains_delete else '不包含删除'}。"
                ),
                "affected_change_count": len(changes),
                "has_deletions": contains_delete,
            },
            "changes": changes,
            "actions": {
                "can_simulate": status == "pending",
                "can_undo": status == "applied",
                "can_redo": status == "undone",
            },
        }
    )


def public_patch_review_view(value: dict[str, Any]) -> PublicPatchReviewResult:
    simulation = value.get("simulation")
    simulation = simulation if isinstance(simulation, dict) else {}
    can_apply = bool(value.get("can_apply", simulation.get("can_apply", False)))
    confirmation_token = value.get("impact_hash")
    if not isinstance(confirmation_token, str) or not confirmation_token:
        confirmation_token = None
    authorization = simulation.get("authorization_required_finding_keys")
    authorization_count = len(authorization) if isinstance(authorization, list) else 0
    blockers = (
        []
        if can_apply
        else [
            {
                "notice_id": "review_blocker_1",
                "message": "这组修改尚未通过应用前检查。",
            }
        ]
    )
    warnings = [
        {
            "notice_id": f"review_warning_{index}",
            "message": "这项影响需要作者审阅确认。",
        }
        for index in range(1, authorization_count + 1)
    ]
    return PublicPatchReviewResult.model_validate(
        {
            "patch_id": int(value["patch_set_id"]),
            "can_apply": can_apply,
            "blockers": blockers,
            "warnings": warnings,
            "requires_author_confirmation": bool(confirmation_token or warnings),
            "confirmation_token": confirmation_token,
        }
    )


def public_patch_response_view(value: dict[str, Any]) -> PublicPatchResponse:
    return PublicPatchResponse(
        patch=public_patch_set_view(value),
        review=public_patch_review_view(value),
        revision=int(value.get("draft_revision") or value.get("base_draft_revision") or 0),
    )


def _public_patch_change(operation: dict[str, Any]) -> dict[str, Any]:
    operation_type = str(operation.get("operation_type") or "update_field")
    kind = {
        "create_object": "create",
        "delete_object": "delete",
    }.get(operation_type, "update")
    target_type = str(operation.get("object_type") or operation.get("target_collection") or "")
    type_label = _TYPE_LABELS.get(target_type, "卷宗内容")
    old_value = operation.get("old_value")
    new_value = operation.get("new_value")
    target_value = new_value if kind == "create" else old_value
    target_name = _display_name(target_value) or type_label
    target_id = operation.get("object_id") or operation.get("target_object_key")
    base = {
        "change_id": int(operation["operation_id"]),
        "kind": kind,
        "relationship": "requested",
        "target": {
            "target_id": str(target_id) if target_id else None,
            "type_label": type_label,
            "name": target_name,
        },
        "explanation": (
            "按你的要求新增这项内容。"
            if kind == "create"
            else "按你的要求删除这项内容。"
            if kind == "delete"
            else "按你的要求调整这项内容。"
        ),
    }
    if kind == "create":
        return {**base, "after": _display_value(new_value)}
    if kind == "delete":
        return {**base, "before": _display_value(old_value)}
    return {
        **base,
        "field_label": "卷宗内容",
        "before": _display_value(old_value),
        "after": _display_value(new_value),
    }


def _display_value(value: Any) -> dict[str, str]:
    if value is None:
        return {"kind": "empty", "text": "未填写"}
    if isinstance(value, bool):
        return {"kind": "boolean", "text": "是" if value else "否"}
    if isinstance(value, (int, float)):
        return {"kind": "number", "text": str(value)}
    if isinstance(value, str):
        return {"kind": "text", "text": value[:4000]}
    if isinstance(value, list):
        simple = [str(item) for item in value if isinstance(item, (str, int, float, bool))]
        text = "、".join(simple) if len(simple) == len(value) else f"{len(value)} 项内容"
        return {"kind": "list", "text": text[:4000]}
    if isinstance(value, dict):
        name = _display_name(value)
        if name:
            return {"kind": "reference", "text": name[:4000]}
        start = value.get("start")
        end = value.get("end")
        if isinstance(start, str) or isinstance(end, str):
            return {
                "kind": "time_range",
                "text": f"{start or '未指定'} 至 {end or '未指定'}"[:4000],
            }
        return {"kind": "text", "text": "多项卷宗内容"}
    return {"kind": "text", "text": "卷宗内容"}


def _display_name(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("name", "title"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:240]
    return None


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
    if status == "failed":
        return "failure"
    if patch is not None:
        return "patch_proposal"
    if findings:
        return "findings"
    interpretation = _public_interpretation(result.get("routing"))
    if interpretation == "clarification":
        return "clarification"
    if interpretation in {"analysis", "logic_review"}:
        return "analysis"
    return "answer"


def _public_interpretation(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    intent = value.get("intent")
    if not isinstance(intent, str):
        return None
    return {
        "question": "conversation",
        "analysis": "analysis",
        "explain_issue": "analysis",
        "logic_audit": "logic_review",
        "validate_request": "logic_review",
        "edit_request": "change_request",
        "clarify": "clarification",
        "unsupported_action": "clarification",
        "out_of_scope": "clarification",
    }.get(intent)


def _failure_category(value: dict[str, Any]) -> str:
    code = str(value.get("code") or "")
    if code == "public_output_policy_failed":
        return "output_rejected"
    if code.startswith("provider_"):
        return "temporarily_unavailable"
    return "request_failed"


def _message_status(value: Any) -> str:
    status = str(value or "failed")
    return status if status in {"pending", "completed", "failed"} else "failed"


def _required_dict(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise RuntimeError(f"Missing legacy Chat view field: {key}")
    return result


__all__ = [
    "public_agent_message_receipt_view",
    "public_agent_message_view",
    "public_agent_run_view",
    "public_patch_review_view",
    "public_patch_response_view",
    "public_patch_set_view",
]
