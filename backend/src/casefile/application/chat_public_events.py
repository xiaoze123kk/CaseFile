"""Allowlist-only projection from internal TaskEvents to public Chat events."""

from __future__ import annotations

from typing import Any

from casefile_contracts import PublicAgentEvent, PublicAgentRun

from casefile.application.chat_public_contracts import public_agent_run_view

_ACTIVITY_EVENTS = {
    "task.started": "understanding",
    "task.recovered": "understanding",
    "intent.understood": "understanding",
    "query.rewritten": "understanding",
    "route.decided": "understanding",
    "tool.started": "reading",
    "tool.completed": "reading",
    "agent.model_call.started": "reading",
    "agent.model_call.completed": "checking",
    "agent.step.started": "checking",
    "agent.step.completed": "checking",
    "agent.step.reused": "checking",
    "general_mutation.planned": "preparing_changes",
    "general_mutation.simulated": "preparing_changes",
    "general_mutation.blocked": "checking",
    "general_mutation.bind_failed": "checking",
    "goal.started": "understanding",
    "goal.capability_started": "reading",
    "goal.capability_completed": "checking",
    "goal.completed": "finalizing",
    "goal.failed": "finalizing",
    "goal.qualification_failed": "understanding",
    "goal.shadow_evaluated": "understanding",
    "task.cancel_requested": "finalizing",
}

_CONTEXT_EVENTS = {
    "context.built": "normal",
    "context.compaction_requested": "near_limit",
    "context.guardrail": "near_limit",
    "context.compacted": "compacted",
    "context.compaction_failed": "normal",
    "context.compaction_skipped": "normal",
}

_VERIFICATION_EVENTS = {
    "verification.started": ("started", "正在检查修改与卷宗约束。"),
    "verification.finding": ("blocked", "发现需要作者复查的内容。"),
    "verification.completed": ("passed", "卷宗检查已完成。"),
    "verification.failed": ("blocked", "卷宗检查未能通过。"),
}


def public_agent_event_view(
    event: dict[str, Any],
    run: dict[str, Any] | PublicAgentRun,
) -> PublicAgentEvent | None:
    """Project one known event; return ``None`` for every unknown event."""

    sequence = int(event["sequence_no"])
    event_type = str(event.get("event_type") or "")
    public_run = run if isinstance(run, PublicAgentRun) else public_agent_run_view(run)
    if event_type == "task.queued":
        return PublicAgentEvent.model_validate(
            {
                "sequence": sequence,
                "event": "run.accepted",
                "run": _run_snapshot(
                    public_run,
                    status="queued",
                    activity="understanding",
                    cancellable=True,
                ),
            }
        )
    if event_type in _ACTIVITY_EVENTS:
        return PublicAgentEvent.model_validate(
            {
                "sequence": sequence,
                "event": "run.activity",
                "activity": _ACTIVITY_EVENTS[event_type],
            }
        )
    if event_type in _CONTEXT_EVENTS:
        return PublicAgentEvent.model_validate(
            {
                "sequence": sequence,
                "event": "run.context",
                "context_state": _CONTEXT_EVENTS[event_type],
            }
        )
    if event_type in _VERIFICATION_EVENTS:
        verification_status, summary = _VERIFICATION_EVENTS[event_type]
        return PublicAgentEvent.model_validate(
            {
                "sequence": sequence,
                "event": "run.verification",
                "verification_status": verification_status,
                "summary": summary,
            }
        )
    if event_type == "task.succeeded":
        return PublicAgentEvent.model_validate(
            {
                "sequence": sequence,
                "event": "run.completed",
                "run": _run_snapshot(
                    public_run,
                    status="succeeded",
                    activity=None,
                    cancellable=False,
                ),
            }
        )
    if event_type == "task.failed":
        failure = public_run.failure or {
            "category": "request_failed",
            "message": "本次请求未能完成。",
            "retryable": False,
        }
        return PublicAgentEvent.model_validate(
            {
                "sequence": sequence,
                "event": "run.failed",
                "failure": failure,
            }
        )
    if event_type == "task.cancelled":
        return PublicAgentEvent.model_validate(
            {
                "sequence": sequence,
                "event": "run.cancelled",
                "message": "任务已安全停止。",
            }
        )
    return None


def _run_snapshot(
    run: PublicAgentRun,
    *,
    status: str,
    activity: str | None,
    cancellable: bool,
) -> dict[str, Any]:
    snapshot = run.model_dump(mode="json")
    snapshot.update(
        status=status,
        activity=activity,
        cancellable=cancellable,
        failure=None if status != "failed" else snapshot.get("failure"),
    )
    return snapshot


__all__ = ["public_agent_event_view"]


def public_feedback_events(
    events: list[dict[str, Any]],
    run: PublicAgentRun,
    *,
    draft_id: int | None,
    draft_revision: int | None,
) -> list[PublicAgentEvent]:
    """Replay presentation state from authoritative events, without exposing payloads."""
    activities: dict[str, int] = {}
    result: list[PublicAgentEvent] = []
    discard_before = max(
        (
            int(event["sequence_no"])
            for event in events
            if event["event_type"] == "message.preview_invalidated"
            and event["payload"].get("discard") is True
        ),
        default=0,
    )
    for event in events:
        sequence = int(event["sequence_no"])
        kind = event["event_type"]
        payload = event.get("payload", {})
        if kind.startswith("message.preview_"):
            if kind == "message.preview_started":
                value = {"sequence": sequence, "event": kind}
            elif kind == "message.preview_delta":
                if sequence < discard_before:
                    continue
                value = {
                    "sequence": sequence,
                    "event": kind,
                    "preview_sequence": payload["preview_sequence"],
                    "offset": payload["offset"],
                    "final": payload.get("final") is True,
                    "text": payload["text"],
                }
            elif kind == "message.preview_invalidated":
                value = {"sequence": sequence, "event": kind, "discard": payload["discard"]}
            else:
                continue
            result.append(PublicAgentEvent.model_validate(value))
            continue
        stage = str(event.get("stage", ""))
        activity = (
            "finalizing"
            if stage in {"finalizing", "goal_finalizing"}
            else "preparing_changes"
            if stage in {"general_mutation", "mutation"}
            else "understanding"
            if stage in {"goal_understanding", "goal_deciding", "routing"}
            else _ACTIVITY_EVENTS.get(kind)
        )
        if (
            kind
            in {
                "tool.started",
                "tool.completed",
                "agent.model_call.started",
                "agent.model_call.completed",
                "agent.model_call.failed",
            }
            and activity
        ):
            key = str(payload.get("tool") or payload.get("component_id") or stage)
            status = (
                "started"
                if kind.endswith("started")
                else (
                    "failed"
                    if kind.endswith("failed") or payload.get("valid") is False
                    else "completed"
                )
            )
            if status == "started":
                activities[key] = sequence
            activity_id = activities.get(key, sequence)
            refs = (
                payload.get("object_ids", [])
                if kind == "tool.completed" and payload.get("valid") is True
                else []
            )
            if (
                kind == "tool.completed"
                and payload.get("valid") is True
                and isinstance(payload.get("object_id"), str)
            ):
                refs = [*refs, payload["object_id"]]
            result.append(
                PublicAgentEvent.model_validate(
                    {
                        "sequence": sequence,
                        "event": "run.activity_detail",
                        "activity_id": activity_id,
                        "activity": activity,
                        "status": status,
                        "object_ids": list(
                            dict.fromkeys(ref for ref in refs if isinstance(ref, str))
                        )[:50],
                        "draft_id": draft_id,
                        "draft_revision": draft_revision,
                    }
                )
            )
        else:
            public = public_agent_event_view(event, run)
            if public is not None:
                result.append(public)
    return result
