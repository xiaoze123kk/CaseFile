"""Executable Brief-to-Draft benchmark with explicit fake and live modes."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from casefile.agent_runtime import FakeProvider, GenerationRequest, OpenAIAgentsProvider
from casefile.agent_runtime.providers import GenerationProvider
from casefile.application.snapshot import casefile_content_hash
from casefile.contracts import validate_casefile

BenchmarkMode = Literal["fake", "live"]


@dataclass(frozen=True, slots=True)
class BenchmarkOptions:
    fixture: Path
    mode: BenchmarkMode = "fake"
    repeats: int = 3
    model_id: str = "gpt-5.6-sol"


def run_benchmark(options: BenchmarkOptions) -> dict[str, Any]:
    """Run one reproducible fixture repeatedly and aggregate release-facing metrics."""

    if options.repeats < 1:
        raise ValueError("repeats must be at least one")
    fixture = _load_fixture(options.fixture)
    provider = _provider(options.mode)
    durations: list[float] = []
    structure_successes = 0
    retry_counts: list[int] = []
    tool_totals = {"calls": 0, "valid_calls": 0, "successful_calls": 0, "adopted_results": 0}
    content_hashes: list[str] = []

    for run_index in range(options.repeats):
        events: list[dict[str, Any]] = []
        request = _request(
            fixture,
            model_id=options.model_id,
            api_key=os.environ.get("OPENAI_API_KEY") if options.mode == "live" else None,
            task_run_id=run_index + 1,
            events=events,
        )
        started = time.perf_counter()
        result = provider.generate(request)
        durations.append((time.perf_counter() - started) * 1000)
        validate_casefile(result.candidate)
        structure_successes += 1
        retry_counts.append(
            sum(event["event_type"] == "model.repair_started" for event in events)
        )
        metrics = result.tools.as_dict()
        for key in tool_totals:
            tool_totals[key] += int(metrics[key])
        content_hashes.append(casefile_content_hash(result.candidate))

    return {
        "suite": "brief_to_draft",
        "fixture": fixture["fixture_id"],
        "mode": options.mode,
        "model_id": options.model_id,
        "runs": options.repeats,
        "metrics": {
            "structure_validity_rate": _rate(structure_successes, options.repeats),
            "structural_retries": {
                "total": sum(retry_counts),
                "max": max(retry_counts),
            },
            "latency_ms": {
                "p50": round(_percentile(durations, 0.50), 3),
                "p95": round(_percentile(durations, 0.95), 3),
            },
            "tools": {
                **tool_totals,
                "validity_rate": _rate(tool_totals["valid_calls"], tool_totals["calls"]),
                "execution_success_rate": _rate(
                    tool_totals["successful_calls"], tool_totals["valid_calls"]
                ),
                "result_adoption_rate": _rate(
                    tool_totals["adopted_results"], tool_totals["successful_calls"]
                ),
            },
        },
        "content_hashes": content_hashes,
    }


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


__all__ = ["BenchmarkOptions", "run_benchmark"]
