"""Normalized immutable per-message context persistence and TaskRun projection."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.data_postgres.models import (
    AgentMessage,
    AgentMessageContext,
    AgentMessageContextRef,
    AgentThread,
)
from casefile.data_postgres.repositories import OwnedDraft


def message_context_snapshot(
    context: AgentMessageContext,
    refs: list[AgentMessageContextRef],
) -> dict[str, Any]:
    by_kind: dict[str, list[str]] = {
        "object": [],
        "event": [],
        "validation_issue": [],
    }
    for ref in sorted(refs, key=lambda item: item.ordinal):
        if ref.ref_kind in by_kind:
            by_kind[ref.ref_kind].append(ref.ref_id)
    return {
        "draft_id": context.draft_id,
        "draft_revision": context.draft_revision,
        "object_ids": by_kind["object"],
        "event_ids": by_kind["event"],
        "validation_issue_ids": by_kind["validation_issue"],
        "view": context.view,
    }


def persist_message_context(
    session: Session,
    owned: OwnedDraft,
    thread: AgentThread,
    message: AgentMessage,
    frozen_focus: dict[str, Any],
) -> tuple[AgentMessageContext, list[AgentMessageContextRef]]:
    context = AgentMessageContext(
        project_id=owned.project.id,
        casefile_id=owned.casefile.id,
        draft_id=owned.draft.id,
        thread_id=thread.id,
        message_id=message.id,
        draft_revision=owned.draft.revision,
        view=frozen_focus.get("view"),
    )
    session.add(context)
    session.flush()
    ordered_refs = [
        *(("object", value) for value in frozen_focus.get("object_ids", [])),
        *(("event", value) for value in frozen_focus.get("event_ids", [])),
        *(("validation_issue", value) for value in frozen_focus.get("validation_issue_ids", [])),
    ]
    refs = [
        AgentMessageContextRef(
            project_id=owned.project.id,
            context_id=context.id,
            ordinal=ordinal,
            ref_kind=kind,
            ref_id=value,
        )
        for ordinal, (kind, value) in enumerate(ordered_refs, start=1)
    ]
    session.add_all(refs)
    session.flush()
    return context, refs


def message_context_input(session: Session, project_id: int, message_id: int) -> dict[str, Any]:
    context = session.scalar(
        select(AgentMessageContext).where(
            AgentMessageContext.project_id == project_id,
            AgentMessageContext.message_id == message_id,
        )
    )
    snapshot = None
    if context is not None:
        refs = list(
            session.scalars(
                select(AgentMessageContextRef).where(
                    AgentMessageContextRef.project_id == project_id,
                    AgentMessageContextRef.context_id == context.id,
                )
            )
        )
        snapshot = message_context_snapshot(context, refs)
    focus = {
        key: snapshot[key] if snapshot else []
        for key in (
            "object_ids",
            "event_ids",
            "validation_issue_ids",
        )
    }
    focus["view"] = snapshot["view"] if snapshot else None
    focus["pruned"] = {key: [] for key in ("object_ids", "event_ids", "validation_issue_ids")}
    return {"context_snapshot": snapshot, "focus": focus}
