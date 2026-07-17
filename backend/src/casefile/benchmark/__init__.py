"""Benchmark module -- evaluation orchestration layer.

Responsibilities:
- Manage benchmark suites and scenarios (what to measure, how, with what data)
- Orchestrate benchmark runs (schedule, execute, collect metrics)
- Store and query benchmark results (runs, metrics, trends)
- Provide comparison views (version vs version, model vs model)

Boundary:
- Does NOT implement AI model calls (delegates to agent_runtime)
- Does NOT implement validation rules (delegates to validation)
- Does NOT implement simulation logic (delegates to simulation)
- Does NOT implement compilation (delegates to compiler)
"""

from casefile.benchmark.models import (  # noqa: F401
    BenchmarkMetric,
    BenchmarkRun,
    BenchmarkScenario,
    BenchmarkSuite,
)

__all__ = [
    "BenchmarkMetric",
    "BenchmarkRun",
    "BenchmarkScenario",
    "BenchmarkSuite",
]
