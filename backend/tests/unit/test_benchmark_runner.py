"""Zero-cost benchmark regression tests."""

from __future__ import annotations

from pathlib import Path

from casefile.benchmark.runner import BenchmarkOptions, run_benchmark, run_to_report

FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "benchmark"
REQUIRED_FIXTURES = [
    "brief_to_draft.json",
    "brief_to_draft_identity.json",
    "brief_to_draft_path.json",
    "brief_to_draft_rule.json",
    "brief_to_draft_decision.json",
    "brief_to_draft_relationship.json",
]

# 覆盖 4 类推理任务: 因果解释(ferry) + 身份判断 + 路径探索 + 规则发现 + 决策推理 + 关系推理
# 覆盖 3 类结论模式: author_anchored (全部) -- Brief 层级仅有 author_anchored/agent_proposed/open


def _metric_value(report: dict, name: str) -> float:
    for m in report["metrics"]:
        if m["name"] == name:
            return m["value"]
    raise KeyError(f"metric {name!r} not found")


def test_all_fixtures_pass_fake_benchmark() -> None:
    """Verify every registered fixture passes structure validity in fake mode."""
    for fixture_name in REQUIRED_FIXTURES:
        fixture_path = FIXTURE_ROOT / fixture_name
        run = run_benchmark(BenchmarkOptions(fixture=fixture_path, repeats=2))
        report = run_to_report(run)

        assert report["mode"] == "fake"
        assert report["repeats"] == 2
        assert report["status"] == "completed"
        assert report["dimension"] == "ai_model"
        assert report["prompt_version"] == "brief-to-draft-v3"
        assert report["schema_version"] == "1.0"

        svr = _metric_value(report, "structure_validity_rate")
        assert svr == 1.0, f"{fixture_name}: structure_validity_rate={svr}"
        retries = _metric_value(report, "structure_retries_total")
        assert retries == 0.0, f"{fixture_name}: retries_total={retries}"


def test_fake_brief_to_draft_benchmark_records_tool_rates() -> None:
    fixture = FIXTURE_ROOT / "brief_to_draft.json"
    run = run_benchmark(BenchmarkOptions(fixture=fixture, repeats=2))
    report = run_to_report(run)

    assert report["mode"] == "fake"
    assert report["repeats"] == 2
    assert _metric_value(report, "structure_validity_rate") == 1.0
    assert _metric_value(report, "structure_retries_total") == 0.0
    assert _metric_value(report, "structure_retries_max") == 0.0
    assert _metric_value(report, "tool_calls") == 2.0
    assert _metric_value(report, "tool_validity_rate") == 1.0
    assert _metric_value(report, "tool_execution_success_rate") == 1.0
    assert _metric_value(report, "tool_result_adoption_rate") == 1.0
