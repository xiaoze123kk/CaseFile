"""Failure triage for CaseFile chat outcome Eval reports.

The triage never replaces a human Transcript review. It groups failing rows by
deterministic failure signature so the reviewer opens the right Transcripts
first and asks the right question: Agent error, Grader error, or Task error.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AGENT_ERROR_SIGNATURES = {
    "blank_answer",
    "duplicate_reference",
    "draft_changed_without_apply",
    "forbidden_reference",
    "forbidden_suggestion_path",
    "unnecessary_suggestion",
}
CAPABILITY_SIGNATURES = {
    "missing_edit_suggestion",
    "missing_required_suggestion",
    "reference_precision",
    "reference_recall",
    "suggestion_legality",
}


@dataclass(frozen=True, slots=True)
class ChatOutcomeTriageReport:
    failing_tasks: tuple[str, ...]
    failure_signature_counts: dict[str, int] = field(default_factory=dict)
    suggested_categories: dict[str, tuple[str, ...]] = field(default_factory=dict)
    action_lines: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "failing_tasks": list(self.failing_tasks),
            "failure_signature_counts": dict(self.failure_signature_counts),
            "suggested_categories": {
                task_id: list(categories)
                for task_id, categories in self.suggested_categories.items()
            },
            "action_lines": list(self.action_lines),
        }


def _row_failures(row: dict[str, Any]) -> tuple[str, ...]:
    raw = row.get("failures")
    if isinstance(raw, list):
        return tuple(value for value in raw if isinstance(value, str))
    return ()


def _categories_for_failures(
    failures: tuple[str, ...],
    *,
    danger_miss: bool,
    route_source: str,
) -> tuple[str, ...]:
    categories: set[str] = set()
    if "provider_error" in failures:
        return ("environment_error",)
    if any(signature in AGENT_ERROR_SIGNATURES for signature in failures):
        categories.add("agent_error")
    if any(signature in CAPABILITY_SIGNATURES for signature in failures):
        categories.add("agent_or_grader_error")
    if danger_miss:
        categories.add("agent_error")
    if categories and route_source == "fallback":
        categories.add("task_or_grader_error")
    if not categories:
        categories.add("needs_review")
    return tuple(sorted(categories))


def triage_chat_outcome_report(report: dict[str, Any]) -> ChatOutcomeTriageReport:
    """Group one M2 report's failing rows into reviewable categories."""

    raw_rows = report.get("rows")
    rows = list(raw_rows) if isinstance(raw_rows, list) else []
    failing_rows = [row for row in rows if isinstance(row, dict) and row.get("passed") is False]
    signature_counter: Counter[str] = Counter()
    suggested_categories: dict[str, set[str]] = {}
    for row in failing_rows:
        failures = _row_failures(row)
        for signature in failures:
            signature_counter[signature] += 1
        task_id = str(row.get("task_id") or "unknown")
        categories = _categories_for_failures(
            failures,
            danger_miss=bool(row.get("danger_miss")),
            route_source=str(row.get("route_source") or ""),
        )
        suggested_categories.setdefault(task_id, set()).update(categories)

    failing_tasks = tuple(sorted({str(row.get("task_id") or "unknown") for row in failing_rows}))
    environment_count = sum(
        1 for categories in suggested_categories.values() if "environment_error" in categories
    )
    agent_count = sum(
        1 for categories in suggested_categories.values() if "agent_error" in categories
    )
    grader_or_agent_count = sum(
        1 for categories in suggested_categories.values() if "agent_or_grader_error" in categories
    )
    action_lines = (
        (
            f"先处理 {environment_count} 个 environment_error 任务："
            "重建 Provider 客户端与凭据后重跑。"
        ),
        (
            f"对 {agent_count} 个 agent_error 任务："
            "打开对应 Trial 的 Transcript 确认模型确实违规，再修 Prompt/路由。"
        ),
        (
            f"对 {grader_or_agent_count} 个 agent_or_grader_error 任务："
            "先核对 Grader 口径，确认是模型错误后修 Agent，"
            "确认是误拒后修 Grader 并回灌 Reference Solution。"
        ),
        (
            "若失败集中在同一 Task 且 Reference Solution 已通过，"
            "优先怀疑 Task 描述歧义或难度层级错误。"
        ),
    )
    return ChatOutcomeTriageReport(
        failing_tasks=failing_tasks,
        failure_signature_counts=dict(
            sorted(signature_counter.items(), key=lambda item: (-item[1], item[0]))
        ),
        suggested_categories={
            task_id: tuple(sorted(categories))
            for task_id, categories in sorted(suggested_categories.items())
        },
        action_lines=action_lines,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Triage a CaseFile chat outcome Eval report JSON")
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    payload = json.loads(arguments.report.read_text(encoding="utf-8"))
    report = triage_chat_outcome_report(payload)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))


__all__ = [
    "ChatOutcomeTriageReport",
    "triage_chat_outcome_report",
]

if __name__ == "__main__":
    main()
