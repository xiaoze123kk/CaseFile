"""Deterministic event-time impact preview tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from casefile.application.errors import ApplicationError
from casefile.application.timeline import build_time_change_preview

REPO_ROOT = Path(__file__).resolve().parents[3]


def _document() -> dict[str, Any]:
    return json.loads(
        (REPO_ROOT / "fixtures" / "casefiles" / "restart_loop.casefile.json").read_text(
            encoding="utf-8"
        )
    )


def test_preview_reports_crossed_and_relative_dependent_events_without_mutation() -> None:
    document = _document()
    original = copy.deepcopy(document)
    middle = copy.deepcopy(document["events"][0])
    middle.update(
        {
            "id": "evt_restart_middle",
            "title": "中间校验事件",
            "time": {
                "kind": "exact",
                "value": "2042-06-01T20:10",
                "precision": "minute",
            },
        }
    )
    dependent = copy.deepcopy(document["events"][0])
    dependent.update(
        {
            "id": "evt_restart_dependent",
            "title": "相对依赖事件",
            "time": {
                "kind": "relative",
                "anchor_event_ref": {
                    "object_type": "event",
                    "object_id": "evt_restart_seven",
                },
                "relation": "after",
                "offset_minutes": 5,
            },
        }
    )
    document["events"].extend([middle, dependent])

    preview = build_time_change_preview(
        document,
        "evt_restart_seven",
        {
            "kind": "range",
            "start": "2042-06-01T20:15",
            "end": "2042-06-01T20:18",
            "precision": "minute",
        },
    )

    assert preview["can_confirm"] is True
    assert preview["order_change"] == {
        "from_index": 0,
        "to_index": 1,
        "crossed_event_ids": ["evt_restart_middle"],
    }
    assert preview["relative_dependent_event_ids"] == ["evt_restart_dependent"]
    assert preview["affected_event_ids"] == [
        "evt_restart_seven",
        "evt_restart_middle",
        "evt_restart_dependent",
    ]
    assert document == {**original, "events": document["events"]}
    assert document["events"][0]["time"] == original["events"][0]["time"]


def test_preview_returns_validation_failure_and_supports_moving_off_axis() -> None:
    document = _document()
    invalid = build_time_change_preview(
        document,
        "evt_restart_seven",
        {
            "kind": "range",
            "start": "2042-06-01T20:10",
            "end": "2042-06-01T20:00",
            "precision": "minute",
        },
    )
    assert invalid["can_confirm"] is False
    assert invalid["validation"]["status"] == "failed"
    assert invalid["validation"]["issues"][0]["code"] == "invalid_time_range"

    unknown = build_time_change_preview(
        document,
        "evt_restart_seven",
        {"kind": "unknown"},
    )
    assert unknown["can_confirm"] is True
    assert unknown["order_change"]["from_index"] == 0
    assert unknown["order_change"]["to_index"] is None


def test_preview_rejects_historic_v1_documents() -> None:
    document = _document()
    document["schema_version"] = "1.0"
    with pytest.raises(ApplicationError) as caught:
        build_time_change_preview(
            document,
            "evt_restart_seven",
            {"kind": "unknown"},
        )
    assert caught.value.code == "timeline_time_edit_requires_v2"
