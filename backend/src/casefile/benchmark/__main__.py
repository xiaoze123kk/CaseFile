"""Command-line entry point for CaseFile benchmarks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from casefile.benchmark.runner import BenchmarkOptions, run_benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CaseFile Brief-to-Draft benchmark")
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--model", default="gpt-5.6-sol")
    arguments = parser.parse_args()
    report = run_benchmark(
        BenchmarkOptions(
            fixture=arguments.fixture,
            mode=arguments.mode,
            repeats=arguments.repeats,
            model_id=arguments.model,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
