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
