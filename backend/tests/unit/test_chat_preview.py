import json

import pytest
from casefile.agent_runtime.chat_preview import AnswerPreview, answer_prefix


@pytest.mark.parametrize("text", ['中文。引号"与换行\n', "转义\\路径", "😀𠮷。", "普通回答"])
def test_answer_prefix_handles_every_chunk_boundary(text: str) -> None:
    raw = json.dumps({"before": [1, {"answer": "not public"}], "answer": text, "after": 1})
    previous = ""
    for offset in range(len(raw) + 1):
        value = answer_prefix(raw[:offset])
        assert text.startswith(value)
        assert value.startswith(previous)
        previous = value
    assert previous == text


def test_duplicate_answer_is_rejected() -> None:
    with pytest.raises(ValueError):
        answer_prefix('{"answer":"one","answer":"two"}')


def test_preview_never_exposes_non_answer_fields() -> None:
    events: list[tuple[str, dict]] = []
    preview = AnswerPreview(lambda event, payload: events.append((event, payload)))
    raw = json.dumps({"answer": "已核对当前卷宗。", "internal": "model_id secret"})
    for char in raw:
        preview.feed(char)
    preview.finish()
    assert [item[1]["text"] for item in events if item[0].endswith("delta")] == ["已核对当前卷宗。"]


@pytest.mark.parametrize(
    "unsafe", ["model_id", "evt_secret", "PrivateKeyCanary123456", '```json\n{"x":1}']
)
def test_cross_chunk_protected_text_is_never_published(unsafe: str) -> None:
    events: list[tuple[str, dict]] = []
    preview = AnswerPreview(
        lambda event, payload: events.append((event, payload)),
        sensitive_values=("PrivateKeyCanary123456",),
    )
    for char in json.dumps({"answer": "这里包含" + unsafe}, ensure_ascii=False):
        preview.feed(char)
    preview.finish()
    assert not any(event.endswith("delta") for event, _ in events)
    assert events[-1] == ("message.preview_invalidated", {"discard": True})


def test_truncated_json_discards_preview() -> None:
    events: list[tuple[str, dict]] = []
    preview = AnswerPreview(lambda event, payload: events.append((event, payload)))
    preview.feed('{"answer":"尚未完成')
    preview.finish()
    assert events[-1][0] == "message.preview_invalidated"


def test_long_answer_arrives_before_complete_json() -> None:
    events: list[tuple[str, dict]] = []
    preview = AnswerPreview(lambda event, payload: events.append((event, payload)))
    preview.feed('{"answer":"' + "这是一条需要继续核对的线索。" * 80)
    assert any(event.endswith("delta") for event, _ in events)
