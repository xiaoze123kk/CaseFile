"""Conservative CaseFile chat reference autofill safety-net tests."""

from __future__ import annotations

import os
from unittest.mock import patch

from casefile.agent_runtime.chat_reference_autofill import (
    REFERENCE_AUTOFILL_ENV,
    autofill_chat_references,
    autofill_reference_ids,
    build_reference_autofill_index,
    reference_autofill_enabled,
)

CASEFILE = {
    "entities": [
        {"id": "ent_researcher", "name": "林研究员"},
        {"id": "ent_witness", "name": "研究员"},
    ],
    "locations": [{"id": "loc_depot", "name": "三号库区"}],
    "events": [
        {"id": "evt_restart", "title": "三号库区失火"},
        {"id": "evt_2", "title": "三号库区失火"},
    ],
}


def test_reference_autofill_is_opt_in_and_fail_closed() -> None:
    with patch.dict(os.environ, {}, clear=True):
        assert reference_autofill_enabled() is False
    with patch.dict(os.environ, {REFERENCE_AUTOFILL_ENV: "1"}):
        assert reference_autofill_enabled() is True
    with patch.dict(os.environ, {REFERENCE_AUTOFILL_ENV: "garbage"}):
        assert reference_autofill_enabled() is False


def test_reference_autofill_defaults_on_for_v3_rollout_and_respects_override() -> None:
    with patch.dict(
        os.environ,
        {"CASEFILE_CHAT_CONTEXT_ROLLOUT": "casefile-chat-context-v3"},
        clear=True,
    ):
        assert reference_autofill_enabled() is True
        with patch.dict(os.environ, {REFERENCE_AUTOFILL_ENV: "0"}):
            assert reference_autofill_enabled() is False
        with patch.dict(os.environ, {REFERENCE_AUTOFILL_ENV: "1"}):
            assert reference_autofill_enabled() is True
    with patch.dict(
        os.environ,
        {"CASEFILE_CHAT_CONTEXT_ROLLOUT": "casefile-chat-context-v1"},
        clear=True,
    ):
        assert reference_autofill_enabled() is False


def test_autofill_adds_only_unique_entity_and_event_labels() -> None:
    object_ids, event_ids = autofill_chat_references(
        "林研究员负责主持重启调查。",
        CASEFILE,
    )

    assert object_ids == ["ent_researcher"]
    assert event_ids == []
    # ``三号库区失火`` maps to two event IDs, so it must stay ambiguous.
    assert "evt_restart" not in event_ids
    assert "evt_2" not in event_ids


def test_event_title_autofill_uses_the_event_slot_only() -> None:
    casefile = {
        "entities": [{"id": "ent_researcher", "name": "林研究员"}],
        "events": [{"id": "evt_restart", "title": "重启事件"}],
    }

    object_ids, event_ids = autofill_chat_references(
        "重启事件发生在凌晨。",
        casefile,
    )

    assert object_ids == []
    assert event_ids == ["evt_restart"]


def test_duplicate_labels_are_ambiguous_and_never_autofilled() -> None:
    casefile = {
        "entities": [
            {"id": "ent_a", "name": "同名者"},
            {"id": "ent_b", "name": "同名者"},
        ]
    }

    assert autofill_chat_references("同名者负责现场调度。", casefile) == ([], [])


def test_overlapping_matched_labels_keep_only_the_longer_specific_name() -> None:
    # ``研究员`` is a substring of ``林研究员``. The generic shorter label is
    # discarded while the exact longer record name is kept.
    assert autofill_chat_references("林研究员负责现场调度。", CASEFILE) == (
        ["ent_researcher"],
        [],
    )


def test_short_and_unknown_labels_are_never_autofilled() -> None:
    casefile = {
        "entities": [{"id": "ent_x", "name": "X"}],
        "events": [],
    }

    assert autofill_chat_references("X 与未知人员都在场。", casefile) == ([], [])
    assert autofill_chat_references("李四不在卷宗里。", casefile) == ([], [])


def test_index_keeps_only_valid_ids_and_name_title_labels() -> None:
    casefile = {
        "entities": [
            {"id": "ent_1", "name": "张三", "title": "调查员"},
            {"id": "ent_2", "name": "  "},
            {"id": 3, "name": "非字符串 ID"},
        ],
        "events": [],
    }
    object_index, event_index = build_reference_autofill_index(casefile)

    assert object_index == {
        "张三": ["ent_1"],
        "调查员": ["ent_1"],
    }
    assert event_index == {}


def test_autofill_reference_ids_orders_ids_by_first_answer_occurrence() -> None:
    index = {"张三": ["ent_1"], "李四": ["ent_2"]}

    assert autofill_reference_ids("李四先到，张三后到。", index) == ["ent_2", "ent_1"]
