"""Executable Brief-to-Draft benchmark with explicit fake and live modes."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from casefile.agent_runtime import FakeProvider, GenerationRequest, OpenAIAgentsProvider
from casefile.agent_runtime.prompt import AGENT_VERSION, PROMPT_VERSION
from casefile.agent_runtime.providers import GenerationProvider
from casefile.agent_runtime.tools import TOOLSET_VERSION
from casefile.application.snapshot import casefile_content_hash
from casefile.benchmark.models import (
    BenchmarkDimension,
    BenchmarkMetric,
    BenchmarkRun,
    BenchmarkStatus,
    MetricSeverity,
)
from casefile.contracts import CASEFILE_SCHEMA_VERSION, validate_casefile

BenchmarkMode = Literal["fake", "live"]


@dataclass(frozen=True, slots=True)
class BenchmarkOptions:
    fixture: Path
    mode: BenchmarkMode = "fake"
    repeats: int = 3
    model_id: str = "gpt-5.6-sol"


def run_benchmark(options: BenchmarkOptions) -> BenchmarkRun:
    """Run one reproducible fixture repeatedly and return a typed BenchmarkRun."""

    if options.repeats < 1:
        raise ValueError("repeats must be at least one")
    fixture = _load_fixture(options.fixture)
    provider = _provider(options.mode)

    durations: list[float] = []
    structure_successes = 0
    retry_counts: list[int] = []
    tool_totals = {"calls": 0, "valid_calls": 0, "successful_calls": 0, "adopted_results": 0}
    content_hashes: list[str] = []
    total_tokens = 0

    started_at = datetime.now(timezone.utc)
    for run_index in range(options.repeats):
        events: list[dict[str, Any]] = []
        request = _request(
            fixture,
            model_id=options.model_id,
            api_key=os.environ.get("OPENAI_API_KEY") if options.mode == "live" else None,
            task_run_id=run_index + 1,
            events=events,
        )
        run_started = time.perf_counter()
        result = provider.generate(request)
        durations.append((time.perf_counter() - run_started) * 1000)
        validate_casefile(result.candidate)
        structure_successes += 1
        retry_counts.append(
            sum(event["event_type"] == "model.repair_started" for event in events)
        )
        metrics = result.tools.as_dict()
        for key in tool_totals:
            tool_totals[key] += int(metrics[key])
        content_hashes.append(casefile_content_hash(result.candidate))
        if hasattr(result, "usage") and result.usage:
            total_tokens += getattr(result.usage, "total_tokens", 0)

    completed_at = datetime.now(timezone.utc)
    duration_ms = int((completed_at - started_at).total_seconds() * 1000)

    return BenchmarkRun(
        id=uuid4(),
        status=BenchmarkStatus.COMPLETED,
        dimension=BenchmarkDimension.AI_MODEL,
        fixture_id=fixture["fixture_id"],
        mode=options.mode,
        model_name=options.model_id,
        prompt_version=PROMPT_VERSION,
        agent_version=AGENT_VERSION,
        toolset_version=TOOLSET_VERSION,
        schema_version=CASEFILE_SCHEMA_VERSION,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        total_tokens=total_tokens,
        repeats=options.repeats,
        content_hashes=content_hashes,
        metrics=_build_metrics(
            runs=options.repeats,
            structure_successes=structure_successes,
            retry_counts=retry_counts,
            durations=durations,
            tool_totals=tool_totals,
        ),
        error_message=None,
    )


def run_to_report(run: BenchmarkRun) -> dict[str, Any]:
    """Convert a BenchmarkRun to a JSON-serializable report dict."""
    return {
        "id": str(run.id),
        "status": run.status.value,
        "dimension": run.dimension.value,
        "fixture_id": run.fixture_id,
        "mode": run.mode,
        "model_name": run.model_name,
        "prompt_version": run.prompt_version,
        "agent_version": run.agent_version,
        "toolset_version": run.toolset_version,
        "schema_version": run.schema_version,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "duration_ms": run.duration_ms,
        "total_tokens": run.total_tokens,
        "repeats": run.repeats,
        "content_hashes": run.content_hashes,
        "error_message": run.error_message,
        "metrics": [
            {
                "name": m.name,
                "value": m.value,
                "unit": m.unit,
                "target": m.target,
                "severity": m.severity.value,
                "metadata": m.metadata,
            }
            for m in run.metrics
        ],
    }


def _build_metrics(
    *,
    runs: int,
    structure_successes: int,
    retry_counts: list[int],
    durations: list[float],
    tool_totals: dict[str, int],
) -> list[BenchmarkMetric]:
    total_retries = sum(retry_counts)
    p50_latency = _percentile(durations, 0.50) if durations else 0.0
    p95_latency = _percentile(durations, 0.95) if durations else 0.0
    calls = tool_totals["calls"]
    valid = tool_totals["valid_calls"]
    successful = tool_totals["successful_calls"]
    adopted = tool_totals["adopted_results"]

    return [
        BenchmarkMetric(
            id=uuid4(),
            name="structure_validity_rate",
            value=_rate(structure_successes, runs),
            unit="percent",
            target=0.95,
            severity=MetricSeverity.GATE,
        ),
        BenchmarkMetric(
            id=uuid4(),
            name="structure_retries_total",
            value=float(total_retries),
            unit="count",
            target=0.0,
            severity=MetricSeverity.TARGET,
        ),
        BenchmarkMetric(
            id=uuid4(),
            name="structure_retries_max",
            value=float(max(retry_counts)) if retry_counts else 0.0,
            unit="count",
            severity=MetricSeverity.OBSERVATION,
        ),
        BenchmarkMetric(
            id=uuid4(),
            name="latency_p50_ms",
            value=round(p50_latency, 3),
            unit="ms",
            severity=MetricSeverity.OBSERVATION,
        ),
        BenchmarkMetric(
            id=uuid4(),
            name="latency_p95_ms",
            value=round(p95_latency, 3),
            unit="ms",
            severity=MetricSeverity.OBSERVATION,
        ),
        BenchmarkMetric(
            id=uuid4(),
            name="tool_calls",
            value=float(calls),
            unit="count",
            severity=MetricSeverity.OBSERVATION,
        ),
        BenchmarkMetric(
            id=uuid4(),
            name="tool_validity_rate",
            value=_rate(valid, calls),
            unit="percent",
            target=0.90,
            severity=MetricSeverity.TARGET,
        ),
        BenchmarkMetric(
            id=uuid4(),
            name="tool_execution_success_rate",
            value=_rate(successful, valid),
            unit="percent",
            target=0.90,
            severity=MetricSeverity.TARGET,
        ),
        BenchmarkMetric(
            id=uuid4(),
            name="tool_result_adoption_rate",
            value=_rate(adopted, successful),
            unit="percent",
            target=0.90,
            severity=MetricSeverity.TARGET,
        ),
    ]


def _load_fixture(path: Path) -> dict[str, Any]:
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("benchmark fixture must be a JSON object")
    return value


def _provider(mode: BenchmarkMode) -> GenerationProvider:
    if mode == "fake":
        return FakeProvider()
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise RuntimeError("OPENAI_API_KEY is required in live benchmark mode")
    return OpenAIAgentsProvider()


def _request(
    fixture: dict[str, Any],
    *,
    model_id: str,
    api_key: str | None,
    task_run_id: int,
    events: list[dict[str, Any]],
) -> GenerationRequest:
    context = fixture["frozen_context"]
    version = context["version"]
    brief_ref = context["brief_ref"]
    return GenerationRequest(
        task_run_id=task_run_id,
        brief=fixture["brief"],
        casefile_id=context["casefile_id"],
        brief_id=brief_ref["brief_id"],
        brief_version=brief_ref["version"],
        version_id=version["version_id"],
        version_no=version["version_no"],
        parent_version_id=version["parent_version_id"],
        model_id=model_id,
        api_key=api_key,
        max_turns=int(fixture.get("max_turns", 12)),
        emit=lambda event_type, stage, payload: events.append(
            {"event_type": event_type, "stage": stage, "payload": payload}
        ),
    )


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _rate(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


__all__ = ["BenchmarkOptions", "run_benchmark", "run_to_report"]
