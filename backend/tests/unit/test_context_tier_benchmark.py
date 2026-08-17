"""Step 4.3 deterministic multi-tier context A/B benchmark tests."""

from __future__ import annotations

from casefile.benchmark.context_tier_benchmark import (
    CONTEXT_TIERS,
    evaluate_context_tiers,
)


def test_four_tier_registry_is_frozen_in_order() -> None:
    assert [tier["tier_id"] for tier in CONTEXT_TIERS] == [
        "legacy_full",
        "context_v1",
        "compaction",
        "dashboard_tools",
    ]


def test_tier_benchmark_passes_all_acceptance_gates() -> None:
    report = evaluate_context_tiers()
    assert report.passed is True, report.failures
    by_tier = {tier["tier_id"]: tier for tier in report.tiers}
    assert by_tier["legacy_full"]["fallback_count"] == 0
    assert by_tier["context_v1"]["fallback_count"] == 0
    assert by_tier["compaction"]["fallback_count"] == 0
    assert by_tier["dashboard_tools"]["fallback_count"] == 0
    assert by_tier["dashboard_tools"]["guardrail_violations"] == 0
    assert by_tier["dashboard_tools"]["peak_input_tokens"] <= (
        by_tier["legacy_full"]["peak_input_tokens"]
    )
    assert len(report.rows) == 4 * 4
