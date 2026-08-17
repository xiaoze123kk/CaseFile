"""Deterministic rewrite templates and preservation lint tests."""

from __future__ import annotations

import pytest
from casefile.agent_runtime.models import ChatTaskUnderstanding
from casefile.agent_runtime.query_rewrite import (
    PRESET_REWRITE_TEMPLATES,
    build_rule_rewrite,
    preservation_lint,
)


def preset_understanding(preset_id: str) -> ChatTaskUnderstanding:
    return ChatTaskUnderstanding(
        primary_intent="analysis",
        confidence=1.0,
        reason_codes=(f"rule_preset:{preset_id}",),
    )


@pytest.mark.parametrize("preset_id", ["inspect", "evidence", "compare", "gate"])
def test_preset_rewrite_uses_template_and_keeps_original_authoritative(
    preset_id: str,
) -> None:
    original = f"原始预设指令 {preset_id}"
    rewrite = build_rule_rewrite(preset_understanding(preset_id), original)

    assert rewrite.original_query == original
    assert rewrite.normalized_query == original.strip()
    assert rewrite.canonical_query == PRESET_REWRITE_TEMPLATES[preset_id]
    assert rewrite.retrieval_queries == ()
    assert rewrite.rewrite_decision == "CONTEXTUALIZE"
    assert all(rewrite.preservation_checks.values()) is True


def test_issue_action_rewrite_keeps_original_query_verbatim() -> None:
    original = "请处理当前焦点中的验证问题：先解释原因，再给出修改建议。"
    rewrite = build_rule_rewrite(
        ChatTaskUnderstanding(
            primary_intent="explain_issue",
            confidence=1.0,
            reason_codes=("rule_ui:issue_action",),
        ),
        original,
    )

    assert rewrite.canonical_query == original
    assert rewrite.rewrite_decision == "KEEP"
    assert all(rewrite.preservation_checks.values()) is True


def test_fallback_rewrite_is_keep() -> None:
    original = "请帮我看看张三的时间线。"
    rewrite = build_rule_rewrite(
        ChatTaskUnderstanding(
            primary_intent="question",
            confidence=0.0,
            reason_codes=("rule_miss",),
        ),
        original,
    )

    assert rewrite.canonical_query == original
    assert rewrite.rewrite_decision == "KEEP"
    assert rewrite.preservation_checks == {
        "negations_preserved": True,
        "entities_preserved": True,
        "temporal_mentions_preserved": True,
        "action_semantics_preserved": True,
    }


def test_preservation_lint_detects_lost_negation_action_and_temporal_signal() -> None:
    checks = preservation_lint(
        "别动时间线，也不要删除对象 entity:ent_1。",
        "调整对象描述。",
    )

    assert checks["negations_preserved"] is False
    assert checks["temporal_mentions_preserved"] is False
    assert checks["action_semantics_preserved"] is False
    assert checks["entities_preserved"] is False


def test_preservation_lint_passes_when_signal_classes_are_absent() -> None:
    checks = preservation_lint("这段内容需要再克制一点。", "请改写这段内容。")

    assert checks == {
        "negations_preserved": True,
        "entities_preserved": True,
        "temporal_mentions_preserved": True,
        "action_semantics_preserved": True,
    }


def test_preservation_lint_keeps_quoted_entity_names() -> None:
    assert preservation_lint('把『张三』的描述改一下。', "修改『张三』的描述。")[
        "entities_preserved"
    ] is True
    assert preservation_lint('把『张三』的描述改一下。', "修改李四的描述。")[
        "entities_preserved"
    ] is False
