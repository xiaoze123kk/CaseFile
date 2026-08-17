"""Deterministic chat context baseline report tests."""

from __future__ import annotations

import json

import pytest
from casefile.benchmark.chat_context_eval import (
    boundary_scenario_from_dict,
    build_boundary_fixtures,
    build_context_baseline_samples,
    evaluate_boundary_fixtures,
    evaluate_context_baseline,
    validate_context_baseline_samples,
    write_boundary_report,
    write_context_baseline_report,
)


def test_builtin_samples_validate_and_evaluate_deterministically() -> None:
    samples = build_context_baseline_samples()
    assert len(samples) == 5
    assert validate_context_baseline_samples(samples) == []
    first = evaluate_context_baseline(samples)
    second = evaluate_context_baseline(samples)
    assert first.as_dict() == second.as_dict()
    assert first.total_samples == 5
    assert first.peak_input_tokens > 0
    assert first.total_input_tokens >= first.peak_input_tokens


def test_unknown_policy_sample_is_counted_as_fallback() -> None:
    report = evaluate_context_baseline(build_context_baseline_samples())
    assert report.fallback_count == 1
    unknown = next(
        entry for entry in report.samples if entry["sample_id"] == "unknown-policy"
    )
    assert unknown["fallback"] is not None
    assert unknown["fallback"]["code"] == "context_policy_unknown_fallback"
    assert unknown["manifest"]["policy_version"] == "agent-focus-v1"


def test_report_writes_json_without_loss(tmp_path) -> None:
    report = evaluate_context_baseline(build_context_baseline_samples())
    report_path = tmp_path / "context-baseline-v1.json"
    write_context_baseline_report(report, report_path)
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded == report.as_dict()


def test_boundary_fixtures_pass_all_phase3_gates_deterministically() -> None:
    fixtures = build_boundary_fixtures()
    assert len(fixtures) == 2
    first = evaluate_boundary_fixtures(fixtures)
    second = evaluate_boundary_fixtures(fixtures)
    assert first.as_dict() == second.as_dict()
    assert first.passed is True
    assert first.passed_scenarios == 2
    for comparison in first.comparisons:
        assert comparison["gates"]["task_success_preserved"] is True
        assert comparison["gates"]["state_recall_not_degraded"] is True
        assert comparison["gates"]["action_continuity_not_degraded"] is True
        assert comparison["gates"]["repeated_work_not_increased"] is True
        assert comparison["gates"]["peak_tokens_reduced"] is True
        assert comparison["compacted"]["total_input_tokens"] < comparison["full"][
            "total_input_tokens"
        ]


def test_boundary_report_writes_json_without_loss(tmp_path) -> None:
    report = evaluate_boundary_fixtures(build_boundary_fixtures())
    report_path = tmp_path / "context-boundary-v1.json"
    write_boundary_report(report, report_path)
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded == report.as_dict()


def test_boundary_scenario_parser_accepts_valid_record() -> None:
    scenario = boundary_scenario_from_dict(
        {
            "scenario_id": "boundary-1",
            "prefix_messages": [
                {"role": "user", "content": "张三在哪里？"},
                {"role": "assistant", "content": "在仓区。"},
            ],
            "continuation_message": "他第二天还在吗？",
            "expected_referenced_object_ids": ["object:person_1"],
            "expected_next_intent": "lookup",
        }
    )
    assert scenario.scenario_id == "boundary-1"
    assert len(scenario.prefix_messages) == 2
    assert scenario.expected_next_intent == "lookup"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({}, "scenario_id"),
        (
            {"scenario_id": "x", "prefix_messages": [{"role": "user"}]},
            "role/content",
        ),
        (
            {
                "scenario_id": "x",
                "prefix_messages": [],
                "continuation_message": "",
            },
            "continuation_message",
        ),
        (
            {
                "scenario_id": "x",
                "prefix_messages": [],
                "continuation_message": "继续",
                "expected_referenced_object_ids": [1],
            },
            "expected_referenced_object_ids",
        ),
    ],
)
def test_boundary_scenario_parser_rejects_malformed_records(
    raw: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        boundary_scenario_from_dict(raw)
