"""Feedback-export contract tests: TaskEvent payload → replayable Eval fixture."""

from __future__ import annotations

import json
from pathlib import Path

from casefile.benchmark.feedback_export import (
    FEEDBACK_EXPORT_SCHEMA,
    fixture_from_feedback,
    fixture_to_json,
    load_exported_fixtures,
    prompt_component_for_intent,
)

PAYLOAD = {
    "message_id": 2,
    "correct_intent": "question",
    "note": "用户只是在问职责。",
    "original": {
        "query": "Lucy 主要负责什么？",
        "routing_hint": {"entrypoint": "free_text", "preset_id": None},
        "route": {"intent": "analysis", "route_source": "llm"},
        "rewrite": {
            "rewrite_decision": "MULTI_QUERY",
            "retrieval_queries": ["Lucy 职责"],
        },
    },
}

INPUT = {
    "message": "Lucy 主要负责什么？",
    "focus": {
        "object_ids": ["ent_lucy"],
        "event_ids": [],
        "validation_issue_ids": [],
    },
    "history": [{"role": "assistant", "content": "上次我们聊过时间线。"}],
    "casefile": {"entities": [{"id": "ent_lucy", "name": "Lucy"}]},
    "validation": {"issues": [{"issue_id": "validator:issue-1"}]},
}


def test_feedback_payload_becomes_a_replayable_fixture() -> None:
    converted = fixture_from_feedback(
        event_id=9,
        task_run_id=7,
        payload=PAYLOAD,
        input_jsonb=INPUT,
    )

    assert converted is not None
    fixture, source = converted
    assert fixture.fixture_id == "feedback-7-9"
    assert fixture.expected_primary_intent == "question"
    assert fixture.expected_prompt_component == "chat"
    assert fixture.casefile == INPUT["casefile"]
    assert fixture.validation_issues == ({"issue_id": "validator:issue-1"},)
    assert fixture.history == ({"role": "assistant", "content": "上次我们聊过时间线。"},)
    assert source["observed_intent"] == "analysis"
    assert source["note"] == "用户只是在问职责。"


def test_feedback_export_roundtrips_through_json_loader(
    tmp_path: Path,
) -> None:
    converted = fixture_from_feedback(
        event_id=10,
        task_run_id=8,
        payload=PAYLOAD,
        input_jsonb=INPUT,
    )
    assert converted is not None
    payload = {
        "schema_version": FEEDBACK_EXPORT_SCHEMA,
        "fixtures": [fixture_to_json(converted[0])],
    }
    path = tmp_path / "feedback-fixtures-roundtrip-test.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    fixtures = load_exported_fixtures(path)
    assert fixtures[0] == converted[0]


def test_note_only_feedback_is_not_promoted_to_a_fixture() -> None:
    payload = {
        "correct_intent": None,
        "note": "路由不确定，需要人工复核。",
        "original": {"query": "这条消息……", "routing_hint": None, "route": None},
    }

    assert fixture_from_feedback(
        event_id=11,
        task_run_id=9,
        payload=payload,
        input_jsonb=None,
    ) is None


def test_prompt_component_mapping_tracks_the_routing_table() -> None:
    assert prompt_component_for_intent("question") == "chat"
    assert prompt_component_for_intent("analysis") == "analysis"
    assert prompt_component_for_intent("explain_issue") == "issue"
    assert prompt_component_for_intent("edit_request") == "edit"
    assert prompt_component_for_intent("validate_request") == "gate"
    assert prompt_component_for_intent("unsupported_action") == "scope"
