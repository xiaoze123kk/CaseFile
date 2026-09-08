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


def test_chapter_rewrite_binds_complete_unicode_target_and_neighbor_excerpts():
    chapters = [
        {"id": "prev", "title": "前章", "text": "前" * 2500},
        {"id": "c1", "title": "当前章", "text": "重复。😀重复。\n门开了。"},
        {"id": "next", "title": "后章", "text": "后" * 2500},
    ]
    payload = prepare_context(chapters, request(scope="chapter_rewrite"), [])
    assert payload["target"] == chapters[1]["text"]
    assert payload["previous_chapter"]["excerpt"] == "前" * 2000
    assert payload["next_chapter"]["excerpt"] == "后" * 2000
    edits = validate_edits(
        {"message": "已生成候选", "text": "新章。😀\n木门推开。", "reason": "调整节奏"}, payload
    )
    assert len(edits) == 1
    assert edits[0]["before"] == chapters[1]["text"]
    assert edits[0]["start"] == 0 and edits[0]["end"] == len(chapters[1]["text"])


@pytest.mark.parametrize(
    "values",
    [
        {"mode": "discuss"},
        {"anchor": {"chapter_id": "c1", "start": 0, "end": 1, "text": "文", "original": False}},
    ],
)
def test_chapter_rewrite_rejects_incompatible_scope(values):
    with pytest.raises(ValueError, match="scope_invalid"):
        prepare_context(
            [{"id": "c1", "title": "章", "text": "正文"}],
            request(scope="chapter_rewrite", **values),
            [],
        )


def test_chapter_rewrite_limits_and_empty_candidate():
    with pytest.raises(ValueError, match="too_large"):
        prepare_context(
            [{"id": "c1", "title": "章", "text": "文" * 12001}],
            request(scope="chapter_rewrite"),
            [],
        )
    payload = prepare_context(
        [{"id": "c1", "title": "章", "text": "正文"}], request(scope="chapter_rewrite"), []
    )
    assert payload["previous_chapter"] is None and payload["next_chapter"] is None
    for text in [" ", "文" * 16001]:
        with pytest.raises(ValueError):
            validate_edits({"message": "候选", "text": text, "reason": "改写"}, payload)
    assert validate_edits({"message": "保留", "text": "正文", "reason": "无需修改"}, payload) == []


def test_chapter_provider_reuses_rewriter_with_dedicated_schema(monkeypatch):
    from casefile.agent_runtime.novel_collaboration import NovelCollaborationProvider
    from casefile.agent_runtime.prose_rewriter import DeepSeekProseRewriterProvider

    captured = []
    monkeypatch.setattr(
        DeepSeekProseRewriterProvider, "rewrite_scene", lambda self, req: captured.append(req)
    )
    payload = prepare_context(
        [{"id": "c1", "title": "章", "text": "正文"}], request(scope="chapter_rewrite"), []
    )
    NovelCollaborationProvider().edit(payload, "fake-key", "fake-model")
    req = captured[0]
    assert req.prompt_version == "novel-chapter-rewrite-v2"
    assert req.input_payload["response_schema"]["required"] == ["message", "text", "reason"]
    assert req.max_output_tokens == 32768 and req.network_retries == 0
