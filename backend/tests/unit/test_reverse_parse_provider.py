"""Unit tests for the reverse_parse provider contract."""

from casefile.agent_runtime import (
    FakeProvider,
    ReverseParseRequest,
)


def _request() -> ReverseParseRequest:
    return ReverseParseRequest(
        task_run_id=1,
        prompt_version="reverse-parse-v1",
        blocks=[{"block_no": 1, "text": "深夜，档案修复师林晚发现三份记录都指向一段不存在的时间。"}],
        input_hash="a" * 64,
        model_id="fake",
        api_key=None,
        max_turns=8,
        emit=lambda *_: None,
        network_retries=0,
    )


def test_fake_reverse_parse_returns_valid_items():
    result = FakeProvider().reverse_parse(_request())
    items = result.candidate.items
    assert len(items) >= 1
    for item in items:
        assert item.grading in {
            "explicit", "inferred", "needs_confirmation", "conflicting", "missing_important",
        }
        assert item.source_quote
        assert item.source_block_refs


def test_reverse_parse_input_contains_blocks():
    from casefile.agent_runtime.prompt import reverse_parse_input

    text = reverse_parse_input([{"block_no": 1, "text": "原文"}], "a" * 64)
    assert "[block_1]" in text or "block_no" in text
