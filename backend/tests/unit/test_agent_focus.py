"""Unit tests for freezing the workbench focus into Agent task input."""

from __future__ import annotations

from casefile.application.agent_collaboration import (
    focused_patch_target_ids,
    freeze_agent_focus,
)


def make_casefile() -> dict:
    collections = [
        "resolution_specs",
        "entities",
        "relationships",
        "locations",
        "events",
        "information_units",
        "claims",
        "hypotheses",
        "reasoning_paths",
        "constraints",
        "structure_locks",
    ]
    casefile = {collection: [] for collection in collections}
    casefile["events"] = [
        {"id": "event:known", "title": "已知事件"},
    ]
    casefile["entities"] = [
        {"id": "object:person_1", "name": "调查员"},
    ]
    return casefile


def test_freezes_valid_focus_without_trimming() -> None:
    frozen = freeze_agent_focus(
        make_casefile(),
        {
            "object_ids": ["object:person_1"],
            "event_ids": ["event:known"],
            "validation_issue_ids": ["validator:issue-1"],
            "view": "timeline",
        },
        known_validation_issue_ids={"validator:issue-1"},
    )

    assert frozen == {
        "object_ids": ["object:person_1"],
        "event_ids": ["event:known"],
        "validation_issue_ids": ["validator:issue-1"],
        "view": "timeline",
        "pruned": {
            "object_ids": [],
            "event_ids": [],
            "validation_issue_ids": [],
        },
    }


def test_prunes_dangling_object_and_event_refs_and_records_them() -> None:
    frozen = freeze_agent_focus(
        make_casefile(),
        {
            "object_ids": ["object:person_1", "object:missing"],
            "event_ids": ["event:known", "event:missing"],
            "validation_issue_ids": [],
            "view": "relations",
        },
        known_validation_issue_ids=set(),
    )

    assert frozen["object_ids"] == ["object:person_1"]
    assert frozen["event_ids"] == ["event:known"]
    assert frozen["pruned"] == {
        "object_ids": ["object:missing"],
        "event_ids": ["event:missing"],
        "validation_issue_ids": [],
    }


def test_prunes_validation_issue_ids_against_the_current_issue_set() -> None:
    frozen = freeze_agent_focus(
        make_casefile(),
        {
            "object_ids": [],
            "event_ids": [],
            "validation_issue_ids": ["validator:issue-1", "validator:stale-2"],
            "view": None,
        },
        known_validation_issue_ids={"validator:issue-1"},
    )

    assert frozen["validation_issue_ids"] == ["validator:issue-1"]
    assert frozen["pruned"]["validation_issue_ids"] == ["validator:stale-2"]


def test_none_focus_freezes_an_empty_but_complete_shape() -> None:
    frozen = freeze_agent_focus(make_casefile(), None)

    assert frozen["object_ids"] == []
    assert frozen["event_ids"] == []
    assert frozen["validation_issue_ids"] == []
    assert frozen["view"] is None
    assert frozen["pruned"] == {
        "object_ids": [],
        "event_ids": [],
        "validation_issue_ids": [],
    }


def test_focused_patch_targets_are_object_and_event_ids_when_an_issue_is_selected() -> None:
    assert focused_patch_target_ids(
        {
            "object_ids": ["object:person_1"],
            "event_ids": ["event:known"],
            "validation_issue_ids": ["validator:issue-1"],
        }
    ) == {"object:person_1", "event:known"}


def test_no_issue_focus_means_no_patch_target_restriction() -> None:
    assert focused_patch_target_ids(
        {"object_ids": ["object:person_1"], "validation_issue_ids": []}
    ) is None
    assert focused_patch_target_ids(None) is None


def test_issue_focus_without_surviving_bound_objects_forbids_suggestions() -> None:
    assert focused_patch_target_ids({"validation_issue_ids": ["validator:issue-1"]}) == set()
