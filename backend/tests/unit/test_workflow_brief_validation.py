"""Focused validation boundaries for editable Brief assistance."""

from __future__ import annotations

from casefile.application.workflow_brief_validation import (
    normalize_author_answer_suggestion_context,
)


def test_author_answer_suggestion_accepts_an_entirely_incomplete_brief() -> None:
    context = normalize_author_answer_suggestion_context({})

    assert context == {
        "creative_intent": "当前创意尚未填写",
        "reasoning_proposition": "尚待明确的核心推理问题",
        "resolution_mode": "agent_proposed",
        "author_answer": None,
        "boundary_text": None,
    }


def test_author_answer_suggestion_uses_only_normalized_current_context() -> None:
    context = normalize_author_answer_suggestion_context(
        {
            "creative_intent": "  时间档案  ",
            "reasoning_proposition": " 谁改写了记录？ ",
            "resolution_mode": "author_anchored",
            "author_answer": "   ",
            "boundary_text": "  必须可以验证  ",
            "ignored": "不会进入 Agent 上下文",
        }
    )

    assert context == {
        "creative_intent": "时间档案",
        "reasoning_proposition": "谁改写了记录？",
        "resolution_mode": "author_anchored",
        "author_answer": None,
        "boundary_text": "必须可以验证",
    }
