"""Frozen CaseFile Chat subagent suite and release-gate tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from casefile.benchmark.chat_subagent_live_eval import (
    BudgetExhausted,
    EvaluationBudget,
    PriceSnapshot,
    _prices_payload,
    load_prices,
)
from casefile.benchmark.chat_subagent_qualification import (
    MODEL_ID,
    build_suite_tasks,
    load_suite,
    qualify_reports,
)


def test_subagent_suite_is_frozen_8_by_8_by_8() -> None:
    suite = load_suite()
    tasks = build_suite_tasks()

    assert len(tasks) == 24
    assert {task.task_id for task in tasks} == {item.case_id for item in suite.cases}
    assert sum(item.category == "simple" for item in suite.cases) == 8
    assert sum(item.category == "investigation" for item in suite.cases) == 8
    assert sum(item.category == "audit" for item in suite.cases) == 8


def _report(protocol: str, *, complex_passes: int, delegated: bool) -> dict[str, object]:
    suite = load_suite()
    complex_seen = 0
    rows: list[dict[str, object]] = []
    for item in suite.cases:
        for trial in range(1, 4):
            passed = True
            if item.category != "simple":
                passed = complex_seen < complex_passes
                complex_seen += 1
            rows.append(
                {
                    "task_id": item.case_id,
                    "trial_no": trial,
                    "protocol": protocol,
                    "passed": passed,
                    "elapsed_ms": 100,
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "audit_finding_count": 0,
                    "audit_finding_evidence_valid_count": 1,
                    "audit_finding_evidence_total_count": 1,
                    "tool_metrics": {
                        "subagent_tasks": 1 if delegated and item.category != "simple" else 0,
                        "subagent_partial": 0,
                        "subagent_failed": 0,
                    },
                }
            )
    return {"model_id": MODEL_ID, "trials": 3, "rows": rows}


def test_qualification_gate_keeps_default_disabled_without_clear_gain() -> None:
    baseline = _report("casefile-chat-tools-v6", complex_passes=36, delegated=False)
    matched = _report("casefile-chat-tools-v6", complex_passes=36, delegated=False)
    subagents = _report("casefile-chat-tools-v8", complex_passes=36, delegated=True)

    report = qualify_reports(
        baseline,
        matched,
        subagents,
        input_price_per_million_cny=Decimal("1"),
        output_price_per_million_cny=Decimal("2"),
    )

    assert report["qualified"] is False
    assert report["default_rollout"] == "disabled"
    assert report["metrics"]["subagents"]["delegation_rate"] > 0


def test_live_budget_reserves_before_a_trial_and_stops_before_overspend() -> None:
    prices = PriceSnapshot(
        model_id=MODEL_ID,
        cached_per_million=Decimal("0.04"),
        uncached_per_million=Decimal("2"),
        output_per_million=Decimal("8"),
        source="verified-test-snapshot",
        verified_at="2026-09-16T00:00:00Z",
    )
    budget = EvaluationBudget(
        prices,
        Decimal("0.019"),
        max_input_tokens_per_trial=1000,
        max_output_tokens_per_trial=1000,
    )

    budget.reserve()
    budget.settle({"input_tokens": 500, "output_tokens": 500})
    budget.reserve()
    budget.settle({"input_tokens": 500, "output_tokens": 500})

    with pytest.raises(BudgetExhausted):
        budget.reserve()


def test_flash_price_snapshot_loads_decimal_rates() -> None:
    path = Path(__file__).parents[2] / "src/casefile/benchmark/prices/deepseek-flash-20260916.json"
    prices = load_prices(path)

    assert prices.model_id == MODEL_ID
    assert prices.uncached_per_million == Decimal("2")
    assert _prices_payload(prices)["uncached_per_million"] == "2"
