"""Memory budgets protect raw prose and preserve complete recent exchanges."""

import pytest

from casefile.agent_runtime.novel_context import (
    govern_context,
    history_messages,
    plan_history,
    tokens,
    validate_memory,
)


def turn(number, content="减少对白"):
    return {
        "id": number,
        "instruction": content,
        "answer": "建议增加动作。",
        "revision": 1,
        "scope": "selection",
        "edit_status": {"accepted": 0},
    }


def test_recent_exchanges_are_complete_and_older_turns_compacted_once():
    plan = plan_history([turn(i) for i in range(1, 9)], {})
    assert [t["id"] for t in plan["recent"]] == [6, 7, 8]
    assert [t["id"] for b in plan["batches"] for t in b] == [1, 2, 3, 4, 5]
    messages = history_messages(plan["recent"])
    assert [m["role"] for m in messages] == ["user", "assistant"] * 3
    assert all(m["edit_status"]["accepted"] == 0 for m in messages)


def test_long_history_uses_token_batches_and_never_slices_user_instruction():
    raw = [turn(i, "不要删掉关键证据。" * 600) for i in range(1, 6)]
    plan = plan_history(raw, {})
    assert all(tokens(batch) <= 12000 for batch in plan["batches"])
    assert [t for batch in plan["batches"] for t in batch] + plan["recent"] == raw
    with pytest.raises(ValueError, match="turn_too_large"):
        plan_history([turn(9, "文" * 13000)], {})


def test_memory_rejects_invented_sources_and_oversized_output():
    item = {"kind": "author_preference", "text": "减少对白", "source_ids": [1]}
    result = validate_memory({"items": [item]}, {}, [turn(1)])
    assert result["through_id"] == 1
    assert validate_memory({"items": [item]}, result, [turn(2)])["through_id"] == 2
    with pytest.raises(ValueError, match="source_invalid"):
        validate_memory({"items": [{**item, "source_ids": [99]}]}, result, [turn(2)])
    with pytest.raises(ValueError, match="memory_too_large"):
        validate_memory({"items": [{**item, "text": "文" * 500}] * 6}, {}, [turn(1)])


def test_optional_settings_trimmed_before_protected_text():
    payload = {
        "target": "正文" * 19000,
        "instruction": "保持人名",
        "target_start": 5,
        "history": [],
        "related_settings": ["设定" * 10000],
    }
    result = govern_context(payload, {})
    assert result["target"] == payload["target"]
    assert result["instruction"] == payload["instruction"]
    assert result["target_start"] == 5
    assert result["related_settings"] == []
    assert payload["related_settings"]
    with pytest.raises(ValueError, match="protected_budget"):
        govern_context({"target": "文" * 49000, "instruction": "保留"}, {})
