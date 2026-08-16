"""Deterministic rule-route query rewrite and preservation lint.

R1 has no retrieval channel, so route-specific rewrite only supplies canonical
task descriptions for the four presets. The original query always remains the
authoritative representation passed to the executor prompt.
"""

from __future__ import annotations

import re
from typing import Any

from casefile.agent_runtime.models import ChatTaskUnderstanding, QueryRewriteResult

PRESET_REWRITE_TEMPLATES: dict[str, str] = {
    "inspect": (
        "对整个卷宗执行健康检查：按验证问题严重程度分级列出待处理问题，"
        "并说明时间线与推理的收束情况。"
    ),
    "evidence": (
        "汇总当前证据链：逐份说明关键证据支撑了哪些主张，"
        "并如实指出支撑不完整或存在断点的地方。"
    ),
    "compare": (
        "对比卷宗中实际存在的候选解释与推理路径收束状态，"
        "指出仍存在竞争的解释。"
    ),
    "gate": "按编译中心发布门禁口径执行导出前检查，结论必须与验证快照一致。",
    "issue_action": (
        "解释焦点验证问题失败的原因，并针对该问题绑定的焦点对象"
        "给出可逐项审阅的字段修改建议。"
    ),
}

_NEGATION_TERMS = (
    "不能",
    "不得",
    "不要",
    "别",
    "不",
    "禁止",
    "无需",
)
_TEMPORAL_TERMS = (
    "时间线",
    "时间",
    "时刻",
    "日期",
    "日程",
    "凌晨",
    "上午",
    "下午",
    "晚上",
)
_ACTION_TERMS = (
    "删除",
    "清空",
    "覆盖",
    "撤销",
    "回退",
    "修改",
    "补充",
    "检查",
    "导出",
    "对比",
    "保留",
)
_OBJECT_ID_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*:[A-Za-z0-9_.\-]+")
_QUOTED_NAME_PATTERN = re.compile(r"「([^」]+)」|『([^』]+)』|\"([^\"]+)\"")


def build_rule_rewrite(
    task_understanding: ChatTaskUnderstanding,
    original_query: str,
) -> QueryRewriteResult:
    """Build the deterministic Dual Representation for one rule route."""

    normalized_query = original_query.strip()
    preset_id = _preset_reason_code(task_understanding)
    if preset_id in PRESET_REWRITE_TEMPLATES and preset_id != "issue_action":
        canonical_query = PRESET_REWRITE_TEMPLATES[preset_id]
        rewrite_decision = "CONTEXTUALIZE"
    else:
        canonical_query = normalized_query
        rewrite_decision = "KEEP"
    return QueryRewriteResult(
        original_query=original_query,
        normalized_query=normalized_query,
        canonical_query=canonical_query,
        retrieval_queries=(),
        rewrite_decision=rewrite_decision,
        preservation_checks=preservation_lint(original_query, canonical_query),
    )


def route_specific_rewrite_strategy(
    task_understanding: ChatTaskUnderstanding,
) -> str:
    """Decide whether the route needs a post-route LLM rewrite call.

    R2 only invokes the rewriter for the two explicitly designated strategies;
    everything else reuses the conservative intent call output.
    """

    if task_understanding.primary_intent == "analysis":
        if set(task_understanding.sub_intents) & {
            "compare_candidates",
            "compare_paths",
        }:
            return "MULTI_QUERY"
        if set(task_understanding.sub_intents) & {"evidence_chain"}:
            return "DECOMPOSE"
        if set(task_understanding.sub_intents) & {"healthcheck"}:
            return "MULTI_QUERY"
    if task_understanding.primary_intent == "question":
        if set(task_understanding.sub_intents) & {
            "cross_reference",
            "timeline_relations",
        }:
            return "MULTI_QUERY"
    return "KEEP"


def llm_rewrite_strategy(
    task_understanding: ChatTaskUnderstanding,
    original_query: str,
    conservative_canonical_query: str,
) -> str:
    """Pick the pre-route strategy for one LLM intent call."""

    if task_understanding.primary_intent in {
        "validate_request",
        "unsupported_action",
        "clarify",
        "out_of_scope",
    }:
        return "KEEP"
    if conservative_canonical_query.strip() != original_query.strip():
        return "CONTEXTUALIZE"
    return "KEEP"


def build_llm_rewrite(
    task_understanding: ChatTaskUnderstanding,
    original_query: str,
    conservative_canonical_query: str,
) -> QueryRewriteResult:
    """Build Dual Representation from the intent call's conservative canonical.

    Preservation lint is the hard gate: if the model dropped a negation, entity,
    temporal or action signal, the canonical is discarded and KEEP is used.
    """

    normalized_query = original_query.strip()
    strategy = llm_rewrite_strategy(
        task_understanding,
        original_query,
        conservative_canonical_query,
    )
    canonical_query = conservative_canonical_query.strip() or normalized_query
    checks = preservation_lint(original_query, canonical_query)
    if not all(checks.values()):
        canonical_query = normalized_query
        strategy = "KEEP"
        checks = preservation_lint(original_query, canonical_query)
    return QueryRewriteResult(
        original_query=original_query,
        normalized_query=normalized_query,
        canonical_query=canonical_query,
        retrieval_queries=(),
        rewrite_decision=strategy,
        preservation_checks=checks,
    )


def preservation_lint(original: str, canonical: str) -> dict[str, Any]:
    """Check the hard signal classes that must survive a rewrite."""

    return {
        "negations_preserved": _all_terms_preserved(original, canonical, _NEGATION_TERMS),
        "entities_preserved": _entities_preserved(original, canonical),
        "temporal_mentions_preserved": _all_terms_preserved(
            original, canonical, _TEMPORAL_TERMS
        ),
        "action_semantics_preserved": _all_terms_preserved(
            original, canonical, _ACTION_TERMS
        ),
    }


def _preset_reason_code(task_understanding: ChatTaskUnderstanding) -> str | None:
    for reason_code in task_understanding.reason_codes:
        if reason_code.startswith("rule_preset:"):
            return reason_code.removeprefix("rule_preset:")
        if reason_code == "rule_ui:issue_action":
            return "issue_action"
    return None


def _all_terms_preserved(original: str, canonical: str, terms: tuple[str, ...]) -> bool:
    return all(term not in original or term in canonical for term in terms)


def _entities_preserved(original: str, canonical: str) -> bool:
    object_ids = set(_OBJECT_ID_PATTERN.findall(original))
    if any(object_id not in canonical for object_id in object_ids):
        return False
    quoted_names = {
        group
        for match in _QUOTED_NAME_PATTERN.finditer(original)
        for group in match.groups()
        if group
    }
    return all(name in canonical for name in quoted_names)


__all__ = [
    "PRESET_REWRITE_TEMPLATES",
    "build_llm_rewrite",
    "build_rule_rewrite",
    "llm_rewrite_strategy",
    "preservation_lint",
    "route_specific_rewrite_strategy",
]
