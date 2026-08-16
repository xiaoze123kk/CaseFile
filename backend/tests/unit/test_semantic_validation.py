"""Deterministic narrative-semantics checks: knowledge state and temporal exclusivity."""

from datetime import datetime

from casefile.contracts import validate_casefile_semantics
from casefile.contracts.semantic_validation import resolve_event_spans


def _event(event_id: str, kind: str, **time_fields: object) -> dict[str, object]:
    return {
        "id": event_id,
        "time": {"kind": kind, **time_fields},
        "participant_refs": [],
        "location_ref": None,
    }


def _document(
    entities: list[dict[str, object]],
    events: list[dict[str, object]],
    information_units: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "casefile_id": "case_semantics_test",
        "schema_version": "2.0",
        "entities": entities,
        "events": events,
        "information_units": information_units,
    }


def _knowledge_state(anchor_id: str, knows: list[str]) -> dict[str, object]:
    return {
        "as_of_event_ref": {"object_type": "event", "object_id": anchor_id},
        "knows_refs": [
            {"object_type": "information_unit", "object_id": info_id}
            for info_id in knows
        ],
        "believes_refs": [],
        "false_belief_refs": [],
    }


def test_resolve_event_spans_resolves_exact_and_relative() -> None:
    events = [
        _event("evt_anchor", "exact", value="2026-08-07T09:00", precision="minute"),
        {
            "id": "evt_relative",
            "time": {
                "kind": "relative",
                "anchor_event_ref": {"object_type": "event", "object_id": "evt_anchor"},
                "relation": "after",
                "offset_minutes": 15,
            },
        },
    ]
    spans = resolve_event_spans(events)
    assert spans["evt_anchor"].start == datetime.fromisoformat("2026-08-07T09:00")
    assert spans["evt_relative"].start == datetime.fromisoformat("2026-08-07T09:15")


def test_resolve_event_spans_skips_unknown_and_cycles() -> None:
    events = [
        _event("evt_unknown", "unknown"),
        {
            "id": "evt_cycle",
            "time": {
                "kind": "relative",
                "anchor_event_ref": {"object_type": "event", "object_id": "evt_cycle"},
                "relation": "same_time",
                "offset_minutes": 0,
            },
        },
    ]
    spans = resolve_event_spans(events)
    assert spans == {}


def test_knowledge_state_flags_info_produced_after_anchor() -> None:
    document = _document(
        entities=[
            {
                "id": "ent_analyst",
                "knowledge_states": [_knowledge_state("evt_anchor", ["info_late"])],
            }
        ],
        events=[
            _event("evt_anchor", "exact", value="2026-08-07T09:00", precision="minute"),
            _event("evt_source", "exact", value="2026-08-07T10:00", precision="minute"),
        ],
        information_units=[
            {
                "id": "info_late",
                "source_event_ref": {"object_type": "event", "object_id": "evt_source"},
            }
        ],
    )
    issues = validate_casefile_semantics(document)
    issue = next(
        issue
        for issue in issues
        if issue["code"] == "knowledge_state_available_before_source"
    )
    assert issue["severity"] == "S1"
    assert issue["evidence_refs"] == [
        {"object_type": "information_unit", "object_id": "info_late"}
    ]
    assert issue["fix_hint"]
    assert issue["explanation"]


def test_knowledge_state_ignores_info_produced_before_anchor() -> None:
    document = _document(
        entities=[
            {
                "id": "ent_analyst",
                "knowledge_states": [_knowledge_state("evt_anchor", ["info_early"])],
            }
        ],
        events=[
            _event("evt_source", "exact", value="2026-08-07T08:00", precision="minute"),
            _event("evt_anchor", "exact", value="2026-08-07T09:00", precision="minute"),
        ],
        information_units=[
            {
                "id": "info_early",
                "source_event_ref": {"object_type": "event", "object_id": "evt_source"},
            }
        ],
    )
    assert validate_casefile_semantics(document) == []


def test_temporal_exclusivity_flags_overlapping_locations() -> None:
    event_a = _event("evt_a", "exact", value="2026-08-07T09:00", precision="minute")
    event_a["participant_refs"] = [{"object_type": "entity", "object_id": "ent_analyst"}]
    event_a["location_ref"] = {"object_type": "location", "object_id": "loc_a"}
    event_b = _event("evt_b", "exact", value="2026-08-07T09:00", precision="minute")
    event_b["participant_refs"] = [{"object_type": "entity", "object_id": "ent_analyst"}]
    event_b["location_ref"] = {"object_type": "location", "object_id": "loc_b"}

    document = _document([], [event_a, event_b], [])
    issues = validate_casefile_semantics(document)
    issue = next(
        issue
        for issue in issues
        if issue["code"] == "temporal_exclusivity_violation"
    )
    assert issue["severity"] == "S1"
    assert issue["evidence_refs"] == [
        {"object_type": "event", "object_id": "evt_a"},
        {"object_type": "event", "object_id": "evt_b"},
    ]


def test_temporal_exclusivity_ignores_non_overlapping_or_same_location() -> None:
    event_a = _event("evt_a", "exact", value="2026-08-07T09:00", precision="minute")
    event_a["participant_refs"] = [{"object_type": "entity", "object_id": "ent_analyst"}]
    event_a["location_ref"] = {"object_type": "location", "object_id": "loc_a"}
    event_b = _event("evt_b", "exact", value="2026-08-07T11:00", precision="minute")
    event_b["participant_refs"] = [{"object_type": "entity", "object_id": "ent_analyst"}]
    event_b["location_ref"] = {"object_type": "location", "object_id": "loc_a"}

    document = _document([], [event_a, event_b], [])
    assert validate_casefile_semantics(document) == []
