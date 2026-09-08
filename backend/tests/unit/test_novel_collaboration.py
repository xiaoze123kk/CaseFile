"""Bounded context and exact edit anchors, including repeated text and Unicode."""

import pytest

from casefile.agent_runtime.novel_collaboration import prepare_context, validate_edits


def request(**values):
    return {
        "chapter_id": "c1",
        "mode": "rewrite",
        "scope": "chapter",
        "instruction": "调整",
        "anchor": None,
        **values,
    }


def test_selected_duplicate_has_unique_scoped_anchor_and_codepoint_position():
    text = "重复。😀重复。"
    payload = prepare_context(
        [{"id": "c1", "title": "章", "text": text}],
        request(
            scope="selection",
            anchor={"chapter_id": "c1", "start": 4, "end": 7, "text": "重复。", "original": False},
        ),
        [],
    )
    edits = validate_edits(
        {
            "message": "改写",
            "edits": [{"before": "重复。", "after": "另一句。", "reason": "避免重复"}],
        },
        payload,
    )
    assert edits[0]["start"] == 4 and edits[0]["end"] == 7
    assert (
        text[: edits[0]["start"]] + edits[0]["after"] + text[edits[0]["end"] :]
        == "重复。😀另一句。"
    )


def test_ambiguous_or_outside_target_and_overlap_are_rejected():
    payload = prepare_context(
        [{"id": "c1", "title": "章", "text": "重复。重复。门开了。"}], request(), []
    )
    for edits in [
        [{"before": "重复。", "after": "改变", "reason": "表达"}],
        [{"before": "不存在", "after": "改变", "reason": "表达"}],
        [
            {"before": "重复。门开了。", "after": "改变", "reason": "表达"},
            {"before": "门开了。", "after": "改变", "reason": "表达"},
        ],
    ]:
        with pytest.raises(ValueError):
            validate_edits({"message": "修改", "edits": edits}, payload)


def test_large_target_is_rejected_without_truncating():
    with pytest.raises(ValueError, match="too_large"):
        prepare_context([{"id": "c1", "title": "章", "text": "文" * 40001}], request(), [])


def test_discussion_cannot_produce_edits_and_noop_is_not_an_edit():
    payload = prepare_context(
        [{"id": "c1", "title": "章", "text": "门开了。"}], request(mode="discuss"), []
    )
    with pytest.raises(ValueError, match="cannot_edit"):
        validate_edits(
            {
                "message": "修改",
                "edits": [{"before": "门开了。", "after": "门关了。", "reason": "改动"}],
            },
            payload,
        )
    assert (
        validate_edits(
            {
                "message": "不变",
                "edits": [{"before": "门开了。", "after": "门开了。", "reason": "无需调整"}],
            },
            payload,
        )
        == []
    )
