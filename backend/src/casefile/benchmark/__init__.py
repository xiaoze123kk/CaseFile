"""Benchmark module -- evaluation orchestration layer.

Responsibilities:
- Define benchmark domain models (Suite, Scenario, Run, Metric)
- Execute reproducible benchmarks against agent_runtime
- Collect and aggregate metrics (structure validity, latency, tool rates)
- Output typed BenchmarkRun reports

Boundary:
- Does NOT implement AI model calls (delegates to agent_runtime)
- Does NOT implement validation rules (delegates to contracts)
- Does NOT depend on ORM or Web frameworks
"""

from casefile.benchmark.models import (
    BenchmarkDimension,
    BenchmarkMetric,
    BenchmarkRun,
    BenchmarkScenario,
    BenchmarkStatus,
    BenchmarkSuite,
    MetricSeverity,
)
from casefile.benchmark.runner import BenchmarkOptions, run_benchmark, run_to_report

__all__ = [
    "BenchmarkDimension",
    "BenchmarkMetric",
    "BenchmarkOptions",
    "BenchmarkRun",
    "BenchmarkScenario",
    "BenchmarkStatus",
    "BenchmarkSuite",
    "MetricSeverity",
    "run_benchmark",
    "run_to_report",
]
