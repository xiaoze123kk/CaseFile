"""Command-line entry point for CaseFile benchmarks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from casefile.benchmark.runner import BenchmarkOptions, run_benchmark, run_to_report


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "novel-plan":
        from casefile.benchmark.novel_plan_eval import main as novel_plan_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        novel_plan_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "general-mutation-backend-release":
        from casefile.benchmark.general_mutation_backend_release import main as release_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        release_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "general-mutation-safety":
        from casefile.benchmark.general_mutation_safety import main as mutation_safety_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        mutation_safety_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "general-mutation-capability":
        from casefile.benchmark.general_mutation_capability import main as mutation_capability_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        mutation_capability_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "general-mutation":
        from casefile.benchmark.general_mutation_eval import main as mutation_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        mutation_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "closure-repair":
        from casefile.benchmark.closure_repair_eval import main as repair_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        repair_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "validator":
        from casefile.benchmark.validator_eval import main as validator_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        validator_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "chat-context-baseline":
        from casefile.benchmark.chat_context_eval import main as context_baseline_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        context_baseline_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "chat-feedback":
        from casefile.benchmark.chat_feedback_metrics import main as feedback_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        feedback_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "audit-feedback":
        from casefile.benchmark.audit_feedback_export import main as audit_feedback_main

        sys.argv = [sys.argv[0], *sys.argv[2:]]
        audit_feedback_main()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "chat-outcome":
        remaining = sys.argv[2:]
        if len(remaining) >= 2 and remaining[:2] == ["--mode", "live"]:
            from casefile.benchmark.chat_outcome_live_eval import main as live_main

            sys.argv = [sys.argv[0], *remaining[2:]]
            live_main()
            return
        from casefile.benchmark.chat_outcome_eval import main as calibrate_main

        sys.argv = [sys.argv[0], *remaining]
        calibrate_main()
        return
    parser = argparse.ArgumentParser(description="Run the CaseFile Brief-to-Draft benchmark")
    parser.add_argument("--fixture", type=Path, help="Single fixture file to run")
    parser.add_argument(
        "--suite", type=Path, help="Directory of fixture files to run as a regression suite"
    )
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--provider", choices=("openai", "deepseek"), default="openai")
    parser.add_argument("--prompt-version")
    parser.add_argument("--report-path", type=Path)
    arguments = parser.parse_args()
    if not arguments.fixture and not arguments.suite:
        parser.error("one of --fixture or --suite is required")

    if arguments.suite:
        payload = _run_suite(arguments)
    else:
        payload = run_to_report(
            run_benchmark(
                BenchmarkOptions(
                    fixture=arguments.fixture,
                    mode=arguments.mode,
                    repeats=arguments.repeats,
                    model_id=arguments.model,
                    provider=arguments.provider,
                    prompt_version=arguments.prompt_version,
                )
            )
        )

    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if arguments.report_path is not None:
        arguments.report_path.parent.mkdir(parents=True, exist_ok=True)
        arguments.report_path.write_text(rendered + "\n", encoding="utf-8")
    if payload.get("status") not in {"completed", "passed"}:
        raise SystemExit(2)


def _run_suite(arguments: argparse.Namespace) -> dict[str, Any]:
    suite_dir = arguments.suite
    fixture_files = sorted(suite_dir.glob("*.json"))
    if not fixture_files:
        raise SystemExit(f"No .json fixture files found in {suite_dir}")

    results: list[dict[str, Any]] = []
    for fixture_path in fixture_files:
        run = run_benchmark(
            BenchmarkOptions(
                fixture=fixture_path,
                mode=arguments.mode,
                repeats=arguments.repeats,
                model_id=arguments.model,
                provider=arguments.provider,
                prompt_version=arguments.prompt_version,
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
        "status": (
            "completed" if all(result["status"] == "completed" for result in results) else "failed"
        ),
    }
    return summary


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
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
