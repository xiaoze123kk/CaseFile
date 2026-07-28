"""Zero-cost benchmark regression tests."""

from __future__ import annotations

from pathlib import Path

from casefile.benchmark.runner import BenchmarkOptions, run_benchmark


def test_fake_brief_to_draft_benchmark_records_tool_rates() -> None:
    fixture = Path(__file__).resolve().parents[3] / "fixtures" / "benchmark" / "brief_to_draft.json"
    report = run_benchmark(BenchmarkOptions(fixture=fixture, repeats=2))

    assert report["mode"] == "fake"
    assert report["runs"] == 2
    assert report["metrics"]["structure_validity_rate"] == 1.0
    assert report["metrics"]["structural_retries"] == {"total": 0, "max": 0}
    assert report["metrics"]["tools"] == {
        "calls": 2,
        "valid_calls": 2,
        "successful_calls": 2,
        "adopted_results": 2,
        "validity_rate": 1.0,
        "execution_success_rate": 1.0,
        "result_adoption_rate": 1.0,
    }
