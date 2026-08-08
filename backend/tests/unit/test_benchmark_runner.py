"""Zero-cost benchmark regression tests."""

from __future__ import annotations

from pathlib import Path

import casefile.benchmark.runner as benchmark_runner
from casefile.agent_runtime import GenerationRequest, GenerationResult
from casefile.benchmark.runner import BenchmarkOptions, run_benchmark
from pytest import MonkeyPatch


def test_fake_brief_to_draft_benchmark_records_component_rates() -> None:
    fixture = Path(__file__).resolve().parents[3] / "fixtures" / "benchmark" / "brief_to_draft.json"
    report = run_benchmark(BenchmarkOptions(fixture=fixture, repeats=2))

    assert report["mode"] == "fake"
    assert report["evaluation_scope"] == "provider"
    assert report["release_gate_eligible"] is False
    assert report["status"] == "passed"
    assert report["runs"] == 2
    assert report["runs_attempted"] == 2
    assert report["metrics"]["structure_validity_rate"] == 1.0
    assert report["metrics"]["structural_retries"] == {"total": 0, "max": 0}
    assert report["metrics"]["tools"] == {
        "started": 0,
        "completed": 0,
        "failed": 0,
        "completion_rate": 0.0,
    }
    assert report["metrics"]["model_calls"] == {
        "started": 8,
        "completed": 8,
        "failed": 0,
        "completion_rate": 1.0,
    }
    assert report["metrics"]["candidate_adoption"] == {
        "checked": False,
        "reason": "provider benchmark does not create or adopt a persisted candidate",
    }


def test_v8_provider_benchmark_reports_generation_is_not_adoption() -> None:
    fixture = Path(__file__).resolve().parents[3] / "fixtures" / "benchmark" / "brief_to_draft.json"
    report = run_benchmark(
        BenchmarkOptions(
            fixture=fixture,
            repeats=2,
            prompt_version="brief-to-draft-v8",
        )
    )

    assert report["status"] == "passed"
    assert report["metrics"]["tools"] == {
        "started": 0,
        "completed": 0,
        "failed": 0,
        "completion_rate": 0.0,
    }
    assert report["metrics"]["model_calls"] == {
        "started": 8,
        "completed": 8,
        "failed": 0,
        "completion_rate": 1.0,
    }


def test_live_benchmark_stops_after_a_systemic_authentication_failure(
    monkeypatch: MonkeyPatch,
) -> None:
    fixture = Path(__file__).resolve().parents[3] / "fixtures" / "benchmark" / "brief_to_draft.json"

    class AuthenticationFailureProvider:
        def generate(self, request: GenerationRequest) -> GenerationResult:
            request.emit(
                "agent.model_call.failed",
                "planning",
                {
                    "component_id": "case_blueprint_planner",
                    "failure_layer": "transport",
                    "schema_id": "case-blueprint-v1",
                    "issues": [],
                },
            )
            raise RuntimeError("AuthenticationError: 401 invalid API key")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(
        benchmark_runner,
        "_provider",
        lambda _mode, _provider: AuthenticationFailureProvider(),
    )

    report = run_benchmark(
        BenchmarkOptions(
            fixture=fixture,
            mode="live",
            provider="deepseek",
            repeats=30,
        )
    )

    assert report["status"] == "blocked"
    assert report["blocked_reason"] == "provider_authentication"
    assert report["runs"] == 30
    assert report["runs_attempted"] == 1
    assert report["failures"][0]["failure_class"] == "provider_authentication"


def test_diagnostic_coverage_includes_every_failed_event(monkeypatch: MonkeyPatch) -> None:
    fixture = Path(__file__).resolve().parents[3] / "fixtures" / "benchmark" / "brief_to_draft.json"

    class MultipleDiagnosticFailureProvider:
        def generate(self, request: GenerationRequest) -> GenerationResult:
            request.emit(
                "agent.model_call.failed",
                "domain_drafting",
                {
                    "component_id": "story_world",
                    "failure_layer": "pydantic",
                    "schema_id": "story-world-ir-v1",
                    "issues": [{"code": "missing"}],
                },
            )
            request.emit(
                "agent.step.failed",
                "domain_drafting",
                {
                    "component_id": "story_world",
                    "failure_layer": "structured_output",
                    "schema_id": "story-world-ir-v1",
                    "issues": [{"code": "missing", "path": "/entities/0/title"}],
                },
            )
            raise RuntimeError("model output invalid")

    monkeypatch.setattr(
        benchmark_runner,
        "_provider",
        lambda _mode, _provider: MultipleDiagnosticFailureProvider(),
    )

    report = run_benchmark(BenchmarkOptions(fixture=fixture, repeats=1))

    assert report["status"] == "failed"
    assert report["metrics"]["failed_diagnostic_events"] == 2
    assert report["metrics"]["diagnostic_coverage_rate"] == 0.5
