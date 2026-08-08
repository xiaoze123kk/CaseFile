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
    parser.add_argument("--provider", choices=("openai", "deepseek"), default="openai")
    parser.add_argument("--prompt-version")
    parser.add_argument("--report-path", type=Path)
    arguments = parser.parse_args()
    report = run_benchmark(
        BenchmarkOptions(
            fixture=arguments.fixture,
            mode=arguments.mode,
            repeats=arguments.repeats,
            model_id=arguments.model,
            provider=arguments.provider,
            prompt_version=arguments.prompt_version,
        )
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if arguments.report_path is not None:
        arguments.report_path.parent.mkdir(parents=True, exist_ok=True)
        arguments.report_path.write_text(rendered + "\n", encoding="utf-8")
    if report["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
