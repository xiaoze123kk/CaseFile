"""Read-only impact previews for Current Draft event-time edits."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from casefile.application.casefile_v1 import build_casefile_document
from casefile.application.errors import ApplicationError, not_found, revision_conflict
from casefile.contracts import (
    ContractValidationError,
    public_validation_issues,
    validate_casefile,
)
from casefile.data_postgres.repositories import ProjectRepository


def _absolute_start(event: dict[str, Any]) -> datetime | None:
    time = event.get("time")
    if not isinstance(time, dict):
        return None
    kind = time.get("kind")
    value = time.get("start") if kind == "range" else time.get("value")
    if kind not in {"exact", "approximate", "range"} or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is None else None


def _absolute_order(document: dict[str, Any]) -> list[str]:
    ordered: list[tuple[datetime, int, str]] = []
    for index, event in enumerate(document.get("events", [])):
        start = _absolute_start(event)
        event_id = event.get("id")
        if start is not None and isinstance(event_id, str):
            ordered.append((start, index, event_id))
    return [event_id for _start, _index, event_id in sorted(ordered)]


def _crossed_event_ids(
    event_id: str,
    current_order: list[str],
    proposed_order: list[str],
) -> list[str]:
    if event_id not in current_order or event_id not in proposed_order:
        return []
    current_positions = {item: index for index, item in enumerate(current_order)}
    proposed_positions = {item: index for index, item in enumerate(proposed_order)}
    shared = set(current_positions) & set(proposed_positions)
    crossed = {
        item
        for item in shared
        if item != event_id
        and (current_positions[item] < current_positions[event_id])
        != (proposed_positions[item] < proposed_positions[event_id])
    }
    return [item for item in proposed_order if item in crossed]


def build_time_change_preview(
    document: dict[str, Any],
    event_id: str,
    proposed_time: dict[str, Any],
) -> dict[str, Any]:
    """Compare one proposed v2 time value without mutating the source document."""

    if document.get("schema_version") != "2.0":
        raise ApplicationError(
            "timeline_time_edit_requires_v2",
            "历史 v1 工作稿仅供读取，请先升级为当前时间契约后再编辑。",
            status_code=409,
        )
    events = document.get("events")
    if not isinstance(events, list):
        raise not_found("Event")
    event_index = next(
        (
            index
            for index, event in enumerate(events)
            if isinstance(event, dict) and event.get("id") == event_id
        ),
        None,
    )
    if event_index is None:
        raise not_found("Event")

    candidate = deepcopy(document)
    before_time = deepcopy(events[event_index]["time"])
    candidate["events"][event_index]["time"] = deepcopy(proposed_time)
    validation_errors: list[dict[str, Any]] = []
    try:
        validate_casefile(candidate)
    except ContractValidationError as error:
        validation_errors = error.errors

    current_order = _absolute_order(document)
    proposed_order = _absolute_order(candidate)
    current_position = (
        current_order.index(event_id) if event_id in current_order else None
    )
    proposed_position = (
        proposed_order.index(event_id) if event_id in proposed_order else None
    )
    crossed_event_ids = _crossed_event_ids(event_id, current_order, proposed_order)
    relative_dependent_event_ids = [
        event["id"]
        for event in events
        if isinstance(event, dict)
        and event.get("id") != event_id
        and isinstance(event.get("time"), dict)
        and event["time"].get("kind") == "relative"
        and event["time"].get("anchor_event_ref", {}).get("object_id") == event_id
    ]
    affected_event_ids = list(
        dict.fromkeys(
            [event_id, *crossed_event_ids, *relative_dependent_event_ids]
        )
    )
    issues = public_validation_issues(validation_errors)
    return {
        "event_id": event_id,
        "before_time": before_time,
        "proposed_time": deepcopy(proposed_time),
        "can_confirm": not validation_errors,
        "order_change": {
            "from_index": current_position,
            "to_index": proposed_position,
            "crossed_event_ids": crossed_event_ids,
        },
        "relative_dependent_event_ids": relative_dependent_event_ids,
        "affected_event_ids": affected_event_ids,
        "validation": {
            "status": "passed" if not validation_errors else "failed",
            "issue_count": len(validation_errors),
            "issues": issues,
        },
    }


class TimelineService:
    """Authorize and build read-only Current Draft timeline previews."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

    def preview_event_time(
        self,
        actor_user_id: int,
        project_id: int,
        event_id: str,
        *,
        expected_draft_id: int,
        expected_revision: int,
        proposed_time: dict[str, Any],
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id)
            if owned is None:
                raise not_found("Project")
            if (
                owned.draft.id != expected_draft_id
                or owned.draft.revision != expected_revision
            ):
                raise revision_conflict(
                    expected=owned.draft.revision,
                    received=expected_revision,
                )
            document = build_casefile_document(self.session, owned)
            preview = build_time_change_preview(document, event_id, proposed_time)
            return {
                "draft_id": owned.draft.id,
                "base_revision": owned.draft.revision,
                **preview,
            }


__all__ = ["TimelineService", "build_time_change_preview"]
