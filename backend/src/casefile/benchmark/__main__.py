"""Command-line entry point for CaseFile benchmarks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from casefile.benchmark.runner import BenchmarkOptions, run_benchmark, run_to_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CaseFile Brief-to-Draft benchmark")
    parser.add_argument("--fixture", type=Path, help="Single fixture file to run")
    parser.add_argument(
        "--suite", type=Path, help="Directory of fixture files to run as a regression suite"
    )
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--model", default="gpt-5.6-sol")
    arguments = parser.parse_args()

    if not arguments.fixture and not arguments.suite:
        parser.error("one of --fixture or --suite is required")

    if arguments.suite:
        _run_suite(arguments)
    else:
        report = run_benchmark(
            BenchmarkOptions(
                fixture=arguments.fixture,
                mode=arguments.mode,
                repeats=arguments.repeats,
                model_id=arguments.model,
            )
        )
        print(json.dumps(run_to_report(report), ensure_ascii=False, indent=2))


def _run_suite(arguments: argparse.Namespace) -> None:
    suite_dir = arguments.suite
    fixture_files = sorted(suite_dir.glob("*.json"))
    if not fixture_files:
        raise SystemExit(f"No .json fixture files found in {suite_dir}")

    results: list[dict] = []
    for fixture_path in fixture_files:
        run = run_benchmark(
            BenchmarkOptions(
                fixture=fixture_path,
                mode=arguments.mode,
                repeats=arguments.repeats,
                model_id=arguments.model,
            )
        )
        results.append(run_to_report(run))

    summary = {
        "suite_dir": str(suite_dir),
        "fixture_count": len(fixture_files),
        "mode": arguments.mode,
        "model_name": arguments.model,
        "repeats": arguments.repeats,
        "runs": results,
        "aggregate": _aggregate(results),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def _aggregate(results: list[dict]) -> dict:
    """Compute suite-level aggregate metrics across all fixture runs."""
    all_metrics: dict[str, list[float]] = {}
    for result in results:
        for m in result["metrics"]:
            all_metrics.setdefault(m["name"], []).append(m["value"])

    aggregate: dict[str, float] = {}
    for name, values in all_metrics.items():
        if not values:
            continue
        aggregate[f"{name}_avg"] = sum(values) / len(values)
    return {"fixture_count": len(results), "metric_averages": aggregate}


if __name__ == "__main__":
    main()
