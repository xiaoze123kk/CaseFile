"""Provider-level Brief-to-Draft benchmark with explicit fake and live modes.

This runner intentionally calls ``GenerationProvider.generate`` directly.  It
measures model/protocol/IR-to-CaseFile behaviour, but it does *not* stand in
for the API/Worker/PostgreSQL release-acceptance suite.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from casefile.agent_runtime import (
    CandidateStrategy,
    DeepSeekAgentsProvider,
    FakeProvider,
    GenerationRequest,
    OpenAIAgentsProvider,
)
from casefile.agent_runtime.prompt import agent_version_for_task
from casefile.agent_runtime.prompt_repository import prompt_version_for_task
from casefile.agent_runtime.providers import GenerationProvider
from casefile.agent_runtime.tools import TOOLSET_VERSION
from casefile.application.snapshot import casefile_content_hash
from casefile.contracts import CASEFILE_SCHEMA_VERSION, validate_casefile

BenchmarkMode = Literal["fake", "live"]

_FAILED_EVENT_TYPES = frozenset({"agent.step.failed", "agent.model_call.failed"})
_STRUCTURAL_FAILURE_LAYERS = frozenset(
    {
        "pydantic",
        "structured_output",
        "reference_linker",
        "casefile_schema",
        "quality_gate",
        "description_gate",
        "frozen_context",
    }
)
_SYSTEMIC_FAILURES = frozenset(
    {
        "provider_authentication",
        "provider_rate_limited",
        "provider_unavailable",
    }
)


@dataclass(frozen=True, slots=True)
class BenchmarkOptions:
    fixture: Path
    mode: BenchmarkMode = "fake"
    repeats: int = 3
    model_id: str = "gpt-5.6-sol"
    provider: Literal["openai", "deepseek"] = "openai"
    prompt_version: str | None = None
    stop_on_systemic_failure: bool = True


def run_benchmark(options: BenchmarkOptions) -> dict[str, Any]:
    """Run one fixture repeatedly and aggregate provider-level evidence."""

    if options.repeats < 1:
        raise ValueError("repeats must be at least one")
    fixture = _load_fixture(options.fixture)
    prompt_version = options.prompt_version or prompt_version_for_task("brief_to_draft")
    try:
        api_key = _api_key(options.mode, options.provider)
    except RuntimeError as error:
        return _report(
            fixture=fixture,
            options=options,
            prompt_version=prompt_version,
            status="blocked",
            blocked_reason="credential_missing",
            setup_error=type(error).__name__,
        )
    provider = _provider(options.mode, options.provider)
    durations: list[float] = []
    structure_successes = 0
    retry_counts: list[int] = []
    tool_totals = {"started": 0, "completed": 0, "failed": 0}
    model_call_totals = {"started": 0, "completed": 0, "failed": 0}
    content_hashes: list[str] = []
    generation_tool_totals = {
        "calls": 0,
        "valid_calls": 0,
        "successful_calls": 0,
        "adopted_results": 0,
    }
    failures: list[dict[str, Any]] = []
    failed_diagnostics: list[dict[str, Any]] = []
    status: Literal["passed", "failed", "blocked"] = "passed"
    blocked_reason: str | None = None
    strategies = (
        CandidateStrategy.STRUCTURE_FIRST,
        CandidateStrategy.ATMOSPHERE_FIRST,
        CandidateStrategy.REASONING_FIRST,
    )
    for run_index in range(options.repeats):
        events: list[dict[str, Any]] = []
        request = _request(
            fixture,
            model_id=options.model_id,
            api_key=api_key,
            task_run_id=run_index + 1,
            events=events,
            prompt_version=prompt_version,
            candidate_strategy=strategies[run_index % len(strategies)],
        )
        started = time.perf_counter()
        try:
            result = provider.generate(request)
            durations.append((time.perf_counter() - started) * 1000)
            validate_casefile(result.candidate)
            structure_successes += 1
            retry_counts.append(_repair_count(events))
            _accumulate_event_metrics(events, tool_totals, model_call_totals)
            _accumulate_generation_tool_metrics(result, generation_tool_totals)
            content_hashes.append(casefile_content_hash(result.candidate))
        except Exception as error:
            durations.append((time.perf_counter() - started) * 1000)
            retry_counts.append(_repair_count(events))
            _accumulate_event_metrics(events, tool_totals, model_call_totals)
            diagnostics = _failed_diagnostics(events)
            failed_diagnostics.extend(diagnostics)
            failure = _failure_record(
                run_index=run_index,
                strategy=strategies[run_index % len(strategies)],
                error=error,
                diagnostics=diagnostics,
            )
            failures.append(failure)
            if (
                options.mode == "live"
                and options.stop_on_systemic_failure
                and failure["failure_class"] in _SYSTEMIC_FAILURES
            ):
                status = "blocked"
                blocked_reason = str(failure["failure_class"])
                break

    if status == "passed" and failures:
        status = "failed"
    return _report(
        fixture=fixture,
        options=options,
        prompt_version=prompt_version,
        status=status,
        blocked_reason=blocked_reason,
        durations=durations,
        structure_successes=structure_successes,
        retry_counts=retry_counts,
        tool_totals=tool_totals,
        model_call_totals=model_call_totals,
        generation_tool_totals=generation_tool_totals,
        content_hashes=content_hashes,
        failures=failures,
        failed_diagnostics=failed_diagnostics,
    )


def _load_fixture(path: Path) -> dict[str, Any]:
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("benchmark fixture must be a JSON object")
    return value


def run_to_report(report: dict[str, Any]) -> dict[str, Any]:
    """Convert the detailed provider report to the typed benchmark JSON shape."""

    metrics = report["metrics"]
    generation_tools = metrics["generation_tools"]
    return {
        "id": str(uuid4()),
        "status": "completed" if report["status"] == "passed" else "failed",
        "dimension": "ai_model",
        "fixture_id": report["fixture"],
        "mode": report["mode"],
        "model_name": report["model_id"],
        "prompt_version": report["prompt_version"],
        "agent_version": report["agent_version"],
        "toolset_version": report["toolset_version"],
        "schema_version": report["schema_version"],
        "started_at": None,
        "completed_at": None,
        "duration_ms": round(metrics["latency_ms"]["p95"]),
        "total_tokens": 0,
        "repeats": report["runs"],
        "content_hashes": report["content_hashes"],
        "error_message": report["blocked_reason"] or report["setup_error"],
        "metrics": [
            {"name": "structure_validity_rate", "value": metrics["structure_validity_rate"]},
            {
                "name": "structure_retries_total",
                "value": float(metrics["structural_retries"]["total"]),
            },
            {
                "name": "structure_retries_max",
                "value": float(metrics["structural_retries"]["max"]),
            },
            {"name": "latency_p50_ms", "value": metrics["latency_ms"]["p50"]},
            {"name": "latency_p95_ms", "value": metrics["latency_ms"]["p95"]},
            {"name": "tool_calls", "value": float(generation_tools["calls"])},
            {
                "name": "tool_validity_rate",
                "value": generation_tools["validity_rate"],
            },
            {
                "name": "tool_execution_success_rate",
                "value": generation_tools["execution_success_rate"],
            },
            {
                "name": "tool_result_adoption_rate",
                "value": generation_tools["result_adoption_rate"],
            },
        ],
    }


def _provider(mode: BenchmarkMode, provider: Literal["openai", "deepseek"]) -> GenerationProvider:
    if mode == "fake":
        return FakeProvider()
    return DeepSeekAgentsProvider() if provider == "deepseek" else OpenAIAgentsProvider()


def _api_key(mode: BenchmarkMode, provider: Literal["openai", "deepseek"]) -> str | None:
    if mode == "fake":
        return None
    variable = "DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY"
    value = os.environ.get(variable, "").strip()
    if not value:
        raise RuntimeError(f"{variable} is required in live benchmark mode")
    return value


def _report(
    *,
    fixture: dict[str, Any],
    options: BenchmarkOptions,
    prompt_version: str,
    status: Literal["passed", "failed", "blocked"],
    blocked_reason: str | None = None,
    setup_error: str | None = None,
    durations: list[float] | None = None,
    structure_successes: int = 0,
    retry_counts: list[int] | None = None,
    tool_totals: dict[str, int] | None = None,
    model_call_totals: dict[str, int] | None = None,
    generation_tool_totals: dict[str, int] | None = None,
    content_hashes: list[str] | None = None,
    failures: list[dict[str, Any]] | None = None,
    failed_diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Produce a stable report that states the scope and untested boundaries."""

    durations = durations or []
    retry_counts = retry_counts or []
    tool_totals = tool_totals or {
        "started": 0,
        "completed": 0,
        "failed": 0,
    }
    model_call_totals = model_call_totals or {"started": 0, "completed": 0, "failed": 0}
    generation_tool_totals = generation_tool_totals or {
        "calls": 0,
        "valid_calls": 0,
        "successful_calls": 0,
        "adopted_results": 0,
    }
    content_hashes = content_hashes or []
    failures = failures or []
    failed_diagnostics = failed_diagnostics or []
    runs_attempted = len(durations)
    complete_diagnostics = sum(_diagnostic_is_complete(item) for item in failed_diagnostics)
    return {
        "suite": "brief_to_draft",
        "evaluation_scope": "provider",
        "release_gate_eligible": False,
        "not_checked": [
            "task_attempt_persistence",
            "agent_step_run_persistence",
            "agent_model_call_persistence",
            "worker_lease_and_resume",
            "api_and_sse_projection",
            "draft_snapshot_or_canon_write_boundary",
            "candidate_adoption_boundary",
        ],
        "status": status,
        "blocked_reason": blocked_reason,
        "setup_error": setup_error,
        "fixture": fixture["fixture_id"],
        "mode": options.mode,
        "model_id": options.model_id,
        "provider": options.provider,
        "prompt_version": prompt_version,
        "agent_version": agent_version_for_task("brief_to_draft", prompt_version),
        "toolset_version": TOOLSET_VERSION,
        "schema_version": CASEFILE_SCHEMA_VERSION,
        "runs": options.repeats,
        "runs_attempted": runs_attempted,
        "metrics": {
            "structure_validity_rate": _rate(structure_successes, options.repeats),
            "attempted_structure_validity_rate": _rate(structure_successes, runs_attempted),
            "diagnostic_coverage_rate": (
                1.0
                if not failed_diagnostics
                else _rate(complete_diagnostics, len(failed_diagnostics))
            ),
            "failed_diagnostic_events": len(failed_diagnostics),
            "structural_retries": {
                "total": sum(retry_counts),
                "max": max(retry_counts, default=0),
            },
            "latency_ms": {
                "p50": round(_percentile(durations, 0.50), 3) if durations else 0.0,
                "p95": round(_percentile(durations, 0.95), 3) if durations else 0.0,
            },
            "tools": {
                **tool_totals,
                "completion_rate": _rate(tool_totals["completed"], tool_totals["started"]),
            },
            "model_calls": {
                **model_call_totals,
                "completion_rate": _rate(
                    model_call_totals["completed"], model_call_totals["started"]
                ),
            },
            "generation_tools": {
                **generation_tool_totals,
                "validity_rate": _rate(
                    generation_tool_totals["valid_calls"], generation_tool_totals["calls"]
                ),
                "execution_success_rate": _rate(
                    generation_tool_totals["successful_calls"],
                    generation_tool_totals["valid_calls"],
                ),
                "result_adoption_rate": _rate(
                    generation_tool_totals["adopted_results"],
                    generation_tool_totals["successful_calls"],
                ),
            },
            "candidate_adoption": {
                "checked": False,
                "reason": "provider benchmark does not create or adopt a persisted candidate",
            },
        },
        "content_hashes": content_hashes,
        "failures": failures,
    }


def _failed_diagnostics(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") not in _FAILED_EVENT_TYPES:
            continue
        payload = event.get("payload")
        if isinstance(payload, dict):
            diagnostics.append(payload)
    return diagnostics


def _repair_count(events: list[dict[str, Any]]) -> int:
    return sum(
        event.get("event_type") in {"model.repair_started", "model.output_repair_started"}
        for event in events
    )


def _accumulate_event_metrics(
    events: list[dict[str, Any]],
    tool_totals: dict[str, int],
    model_call_totals: dict[str, int],
) -> None:
    """Count observed protocol events instead of overloaded provider internals."""

    for event in events:
        event_type = event.get("event_type")
        if event_type == "tool.started":
            tool_totals["started"] += 1
        elif event_type == "tool.completed":
            tool_totals["completed"] += 1
        elif event_type == "tool.failed":
            tool_totals["failed"] += 1
        elif event_type == "agent.model_call.started":
            model_call_totals["started"] += 1
        elif event_type == "agent.model_call.completed":
            model_call_totals["completed"] += 1
        elif event_type == "agent.model_call.failed":
            model_call_totals["failed"] += 1


def _accumulate_generation_tool_metrics(result: Any, totals: dict[str, int]) -> None:
    metrics = getattr(result, "tools", None)
    if metrics is None or not hasattr(metrics, "as_dict"):
        return
    values = metrics.as_dict()
    for key in totals:
        totals[key] += int(values.get(key, 0))


def _failure_record(
    *,
    run_index: int,
    strategy: CandidateStrategy,
    error: Exception,
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    last_payload = diagnostics[-1] if diagnostics else {}
    return {
        "run": run_index + 1,
        "strategy": strategy.value,
        "exception_type": type(error).__name__,
        "failure_class": _failure_class(error, diagnostics),
        "component_id": last_payload.get("component_id"),
        "failure_layer": last_payload.get("failure_layer"),
        "schema_id": last_payload.get("schema_id"),
        "issues": last_payload.get("issues", []),
        "diagnostic_event_count": len(diagnostics),
    }


def _failure_class(error: Exception, diagnostics: list[dict[str, Any]]) -> str:
    messages = [type(error).__name__, str(error)]
    for diagnostic in diagnostics:
        messages.extend(
            str(issue.get("message", ""))
            for issue in diagnostic.get("issues", [])
            if isinstance(issue, dict)
        )
    haystack = " ".join(messages).lower()
    if "authentication" in haystack or "unauthorized" in haystack or "401" in haystack:
        return "provider_authentication"
    if "rate limit" in haystack or "ratelimit" in haystack or "429" in haystack:
        return "provider_rate_limited"
    if any(
        marker in haystack
        for marker in ("connection", "timeout", "temporarily unavailable", "503", "502")
    ):
        return "provider_unavailable"
    if any(
        diagnostic.get("failure_layer") in _STRUCTURAL_FAILURE_LAYERS
        for diagnostic in diagnostics
    ):
        return "candidate_validation"
    return "provider_or_runtime"


def _diagnostic_is_complete(diagnostic: dict[str, Any]) -> bool:
    if not diagnostic.get("component_id") or not diagnostic.get("failure_layer"):
        return False
    if diagnostic.get("failure_layer") not in _STRUCTURAL_FAILURE_LAYERS:
        return True
    if not diagnostic.get("schema_id"):
        return False
    issues = diagnostic.get("issues")
    return bool(
        isinstance(issues, list)
        and issues
        and all(isinstance(issue, dict) and "path" in issue for issue in issues)
    )


def _request(
    fixture: dict[str, Any],
    *,
    model_id: str,
    api_key: str | None,
    task_run_id: int,
    events: list[dict[str, Any]],
    prompt_version: str | None = None,
    candidate_strategy: CandidateStrategy = CandidateStrategy.STRUCTURE_FIRST,
) -> GenerationRequest:
    context = fixture["frozen_context"]
    version = context["version"]
    brief_ref = context["brief_ref"]
    resolved_prompt_version = prompt_version or prompt_version_for_task("brief_to_draft")
    return GenerationRequest(
        task_run_id=task_run_id,
        prompt_version=resolved_prompt_version,
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
        candidate_strategy=candidate_strategy,
        agent_version=agent_version_for_task("brief_to_draft", resolved_prompt_version),
        toolset_version=TOOLSET_VERSION,
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
