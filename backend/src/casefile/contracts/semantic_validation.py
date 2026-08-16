"""Deterministic narrative-semantics checks surfaced in the workbench quality view.

These checks are intentionally separate from :func:`casefile.contracts.validate_casefile`.
The latter gates generation on structural and reference integrity; this module
reports narrative-logic problems (knowledge-state ordering and temporal
exclusivity) without blocking generation, so a generated deep draft is not
rejected for a mild knowledge-state inconsistency before an author reviews it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

_DETECTION_TYPE = "deterministic"


@dataclass(frozen=True)
class ResolvedSpan:
    """A comparable wall-clock span for one event."""

    start: datetime
    end: datetime


def _parse_wall_clock(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return None
    return parsed


def _shift(value: datetime, minutes: float) -> datetime:
    return value + timedelta(minutes=minutes)


def resolve_event_spans(events: list[dict[str, Any]]) -> dict[str, ResolvedSpan]:
    """Resolve every event's time to a comparable naive-datetime span.

    ``exact``/``approximate`` collapse to a point; ``range`` keeps its bounds;
    ``relative`` is resolved along the anchor chain; ``unknown`` is skipped.
    """
    by_id = {event["id"]: event for event in events}
    spans: dict[str, ResolvedSpan] = {}
    resolving: set[str] = set()

    def resolve(event_id: str) -> ResolvedSpan | None:
        cached = spans.get(event_id)
        if cached is not None:
            return cached
        if event_id in resolving:
            return None
        event = by_id.get(event_id)
        if event is None:
            return None
        time = event.get("time") or {}
        kind = time.get("kind")
        if kind in ("exact", "approximate"):
            value = _parse_wall_clock(time.get("value"))
            if value is None:
                return None
            span = ResolvedSpan(value, value)
        elif kind == "range":
            start = _parse_wall_clock(time.get("start"))
            end = _parse_wall_clock(time.get("end"))
            if start is None or end is None:
                return None
            span = ResolvedSpan(start, end)
        elif kind == "relative":
            resolving.add(event_id)
            anchor_ref = time.get("anchor_event_ref") or {}
            anchor_id = anchor_ref.get("object_id")
            anchor = resolve(anchor_id) if isinstance(anchor_id, str) else None
            resolving.discard(event_id)
            if anchor is None:
                return None
            relation = time.get("relation")
            offset = time.get("offset_minutes")
            minutes = float(offset) if isinstance(offset, (int, float)) else 0.0
            if relation == "before":
                point = _shift(anchor.start, -minutes)
            elif relation == "after":
                point = _shift(anchor.end, minutes)
            else:  # same_time
                point = anchor.start
            span = ResolvedSpan(point, point)
        else:
            return None
        spans[event_id] = span
        return span

    for event in events:
        resolve(event["id"])
    return spans


def _information_source_events(document: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for unit in document.get("information_units", []):
        ref = unit.get("source_event_ref") or {}
        object_id = ref.get("object_id")
        if ref.get("object_type") == "event" and isinstance(object_id, str):
            result[unit["id"]] = object_id
    return result


def _knowledge_state_issues(
    document: dict[str, Any],
    spans: dict[str, ResolvedSpan],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    source_events = _information_source_events(document)
    for entity_index, entity in enumerate(document.get("entities", [])):
        for state_index, state in enumerate(entity.get("knowledge_states", [])):
            anchor_ref = state.get("as_of_event_ref") or {}
            anchor_id = anchor_ref.get("object_id")
            anchor = spans.get(anchor_id) if isinstance(anchor_id, str) else None
            if anchor is None:
                continue
            base = f"/entities/{entity_index}/knowledge_states/{state_index}"
            for field in ("knows_refs", "believes_refs"):
                for ref_index, ref in enumerate(state.get(field, [])):
                    if ref.get("object_type") != "information_unit":
                        continue
                    info_id = ref.get("object_id")
                    source_event_id = (
                        source_events.get(info_id) if isinstance(info_id, str) else None
                    )
                    if source_event_id is None:
                        continue
                    source = spans.get(source_event_id)
                    if source is None:
                        continue
                    if source.start > anchor.start:
                        issues.append(
                            {
                                "code": "knowledge_state_available_before_source",
                                "path": f"{base}/{field}/{ref_index}",
                                "message": "角色引用了在其知识状态锚点之后才产生的信息",
                                "severity": "S1",
                                "evidence_refs": [
                                    {
                                        "object_type": "information_unit",
                                        "object_id": info_id,
                                    }
                                ],
                                "impact_refs": [
                                    {"object_type": "entity", "object_id": entity["id"]},
                                    {"object_type": "event", "object_id": anchor_id},
                                    {"object_type": "event", "object_id": source_event_id},
                                ],
                                "fix_hint": (
                                    "将知识状态锚点移到信息产生之后，"
                                    "或调整事件时间使该信息更早产生。"
                                ),
                                "explanation": (
                                    f"信息 {info_id} 由事件 {source_event_id} 产生，"
                                    f"晚于该知识状态锚点事件 {anchor_id}。"
                                ),
                                "detection_type": _DETECTION_TYPE,
                            }
                        )
    return issues


def _temporal_exclusivity_issues(
    document: dict[str, Any],
    spans: dict[str, ResolvedSpan],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    entity_events: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for event_index, event in enumerate(document.get("events", [])):
        for ref in event.get("participant_refs", []):
            if ref.get("object_type") == "entity" and isinstance(
                ref.get("object_id"), str
            ):
                entity_events.setdefault(ref["object_id"], []).append(
                    (event_index, event)
                )

    for entity_id, events in entity_events.items():
        for left in range(len(events)):
            for right in range(left + 1, len(events)):
                left_index, left_event = events[left]
                right_index, right_event = events[right]
                left_span = spans.get(left_event["id"])
                right_span = spans.get(right_event["id"])
                if left_span is None or right_span is None:
                    continue
                if (
                    left_span.end < right_span.start
                    or right_span.end < left_span.start
                ):
                    continue
                left_location = (left_event.get("location_ref") or {}).get("object_id")
                right_location = (right_event.get("location_ref") or {}).get(
                    "object_id"
                )
                if not (left_location and right_location) or left_location == right_location:
                    continue
                issues.append(
                    {
                        "code": "temporal_exclusivity_violation",
                        "path": f"/events/{right_index}/participant_refs",
                        "message": "同一角色在时间重叠的事件中出现在不同地点",
                        "severity": "S1",
                        "evidence_refs": [
                            {"object_type": "event", "object_id": left_event["id"]},
                            {"object_type": "event", "object_id": right_event["id"]},
                        ],
                        "impact_refs": [{"object_type": "entity", "object_id": entity_id}],
                        "fix_hint": "区分两个事件的时间，或解释为同一地点、非本人到场。",
                        "explanation": (
                            f"事件 {left_event['id']} 与 {right_event['id']} "
                            "时间重叠，但地点不同。"
                        ),
                        "detection_type": _DETECTION_TYPE,
                    }
                )
    return issues


def validate_casefile_semantics(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deterministic narrative-semantics issues for a structurally valid document."""
    spans = resolve_event_spans(document.get("events", []))
    issues = _knowledge_state_issues(document, spans)
    issues.extend(_temporal_exclusivity_issues(document, spans))
    return issues
