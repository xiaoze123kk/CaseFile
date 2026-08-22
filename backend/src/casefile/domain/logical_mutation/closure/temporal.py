"""Conservative travel-time observations for the v2 shadow policy."""

from __future__ import annotations

from datetime import datetime

from casefile.domain.logical_mutation.closure.common import issue
from casefile.domain.logical_mutation.closure.context import ClosureContext
from casefile.domain.logical_mutation.models import ClosureIssue


def _certain_span(event: dict[str, object]) -> tuple[datetime, datetime] | None:
    time = event.get("time")
    if not isinstance(time, dict):
        return None
    try:
        if time.get("kind") == "exact":
            value = datetime.fromisoformat(str(time["value"]))
            return value, value
        if time.get("kind") == "range":
            return (
                datetime.fromisoformat(str(time["start"])),
                datetime.fromisoformat(str(time["end"])),
            )
    except (KeyError, ValueError):
        return None
    return None


def evaluate_travel_time_rules(context: ClosureContext) -> list[ClosureIssue]:
    travel_minutes: dict[tuple[str, str], float] = {}
    parent_by_location: dict[str, str] = {}
    for location in context.candidate.get("locations", []):
        source_id = str(location["id"])
        parent = location.get("parent_ref")
        if isinstance(parent, dict) and parent.get("object_id"):
            parent_by_location[source_id] = str(parent["object_id"])
        for travel in location.get("travel_times", []):
            travel_minutes[(source_id, str(travel["to_ref"]["object_id"]))] = float(
                travel["minutes"]
            )

    by_participant: dict[str, list[tuple[datetime, datetime, str, str]]] = {}
    for event in context.candidate.get("events", []):
        span = _certain_span(event)
        location = (event.get("location_ref") or {}).get("object_id")
        if span is None or not location:
            continue
        for ref in event.get("participant_refs", []):
            by_participant.setdefault(str(ref["object_id"]), []).append(
                (span[0], span[1], str(location), str(event["id"]))
            )

    result: list[ClosureIssue] = []
    for participant_id, appearances in by_participant.items():
        ordered = sorted(appearances)
        for left, right in zip(ordered, ordered[1:], strict=False):
            _left_start, left_end, left_location, left_event_id = left
            right_start, _right_end, right_location, right_event_id = right
            if left_location == right_location or right_start <= left_end:
                continue
            if (
                parent_by_location.get(left_location) == right_location
                or parent_by_location.get(right_location) == left_location
            ):
                continue
            required = travel_minutes.get((left_location, right_location))
            if required is None:
                continue
            available = (right_start - left_end).total_seconds() / 60
            if available < required:
                result.append(
                    issue(
                        context,
                        "temporal_travel_time_violation",
                        "warning",
                        "事件间移动时间不足",
                        "同一参与者在相邻事件间的可用时间短于显式声明的单向移动时间。",
                        (participant_id, left_event_id, right_event_id),
                    )
                )
    return result
