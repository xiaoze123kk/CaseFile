from __future__ import annotations

import json

from casefile.application.chat_public_contracts import (
    internal_intent_for_public_interpretation,
    public_agent_message_receipt_view,
    public_agent_message_view,
    public_patch_response_view,
    public_patch_review_view,
    public_routing_feedback_view,
    public_routing_interpretation,
)


def _legacy_patch() -> dict:
    return {
        "patch_set_id": 13,
        "base_draft_revision": 4,
        "review_mode": "atomic",
        "status": "pending",
        "is_stale": False,
        "contains_delete": False,
        "plan_hash": "internal-plan-hash",
        "operations": [
            {
                "operation_id": 31,
                "operation_key": "op_internal",
                "operation_type": "update_field",
                "object_id": "evt_discovery",
                "object_type": "event",
                "target_collection": "events",
                "target_object_key": "evt_discovery",
                "field_path": "/time/start",
                "old_value": "第 2 天 20:00",
                "new_value": "第 2 天 21:00",
                "reason": "internal reason",
            }
        ],
    }


def _legacy_message(*, role: str, message_id: int, sequence: int) -> dict:
    task = None
    patch = None
    if role == "assistant":
        patch = _legacy_patch()
        task = {
            "task_run_id": 21,
            "status": "running",
            "stage": "context",
            "provider": "deepseek-canary",
            "prompt_version": "casefile-chat-internal-canary",
            "usage": {"total_tokens": 999},
            "component_steps": [{"component_id": "internal-canary"}],
            "failure": None,
            "result": {
                "routing": {
                    "intent": "edit_request",
                    "route_source": "internal-canary",
                },
                "audit_findings": [],
            },
        }
    return {
        "message_id": message_id,
        "thread_id": 8,
        "sequence_no": sequence,
        "role": role,
        "status": "completed" if role == "user" else "pending",
        "content": "请调整发现现场的时间。" if role == "user" else None,
        "task": task,
        "referenced_object_ids": [],
        "referenced_event_ids": ["evt_discovery"] if role == "assistant" else [],
        "referenced_validation_issue_ids": [],
        "suggested_view": None,
        "patch_set": patch,
        "created_at": "2026-08-26T10:00:00+00:00",
        "updated_at": "2026-08-26T10:00:01+00:00",
    }


def test_message_projection_is_allowlist_only_and_uses_public_run() -> None:
    projected = public_agent_message_view(
        _legacy_message(role="assistant", message_id=56, sequence=4)
    ).model_dump(mode="json")
    serialized = json.dumps(projected)

    assert projected["response_kind"] == "patch_proposal"
    assert projected["interpretation"] == "change_request"
    assert projected["run"] == {
        "run_id": 21,
        "status": "running",
        "activity": "reading",
        "cancellable": True,
        "failure": None,
    }
    assert projected["patch"]["changes"][0]["field_label"] == "开始时间"
    for forbidden in (
        "task",
        "result",
        "provider",
        "prompt_version",
        "usage",
        "component_steps",
        "field_path",
        "operation_type",
        "plan_hash",
        "route_source",
    ):
        assert forbidden not in serialized


def test_message_receipt_atomically_drops_thread_and_task_envelopes() -> None:
    projected = public_agent_message_receipt_view(
        {
            "thread": {"thread_id": 8},
            "user_message": _legacy_message(role="user", message_id=55, sequence=3),
            "assistant_message": _legacy_message(role="assistant", message_id=56, sequence=4),
            "task": {"task_run_id": 21, "provider": "internal-canary"},
        }
    ).model_dump(mode="json")

    assert set(projected) == {"user_message", "assistant_message"}
    assert projected["assistant_message"]["run"]["run_id"] == 21


def test_patch_review_and_action_response_hide_finding_keys_and_hashes() -> None:
    legacy = {
        **_legacy_patch(),
        "draft_revision": 5,
        "impact_hash": "opaque-confirmation-token",
        "simulation": {
            "can_apply": False,
            "reason_code": "internal_reason_code",
            "authorization_required_finding_keys": ["internal_finding_key"],
            "baseline_hash": "internal-baseline-hash",
        },
    }

    review = public_patch_review_view(legacy).model_dump(mode="json")
    response = public_patch_response_view(legacy).model_dump(mode="json")
    serialized = json.dumps(response)

    assert review["can_apply"] is False
    assert review["requires_author_confirmation"] is True
    assert review["confirmation_token"] is None
    assert review["blockers"] == []
    assert review["warnings"][0]["notice_id"].startswith("warning_")
    assert response["revision"] == 5
    assert "internal_finding_key" not in serialized
    assert "internal-baseline-hash" not in serialized
    assert "internal_reason_code" not in serialized


def test_routing_feedback_uses_only_public_interpretation() -> None:
    assert public_routing_interpretation("logic_audit") == "logic_review"
    assert internal_intent_for_public_interpretation("change_request") == "edit_request"

    projected = public_routing_feedback_view(
        {
            "message_id": 56,
            "task_run_id": 21,
            "acknowledged": True,
            "interpretation": "analysis",
            "route_source": "internal-canary",
            "reason_code": "internal-reason-canary",
            "route": {"prompt_component": "internal-component-canary"},
        }
    ).model_dump(mode="json")

    assert projected == {
        "message_id": 56,
        "acknowledged": True,
        "interpretation": "analysis",
    }
