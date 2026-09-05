"""Zero-cost benchmark regression tests."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

import casefile.benchmark.runner as benchmark_runner
from casefile.agent_runtime import GenerationRequest, GenerationResult
from casefile.benchmark.runner import BenchmarkOptions, run_benchmark, run_to_report

FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "fixtures" / "benchmark"
REQUIRED_FIXTURES = [
    "brief_to_draft.json",
    "brief_to_draft_identity.json",
    "brief_to_draft_path.json",
    "brief_to_draft_rule.json",
    "brief_to_draft_decision.json",
    "brief_to_draft_relationship.json",
]


def test_fake_brief_to_draft_benchmark_records_component_rates() -> None:
    fixture = Path(__file__).resolve().parents[3] / "fixtures" / "benchmark" / "brief_to_draft.json"
    report = run_benchmark(BenchmarkOptions(fixture=fixture, repeats=2))

    assert report["mode"] == "fake"
    assert report["evaluation_scope"] == "provider"
    assert report["release_gate_eligible"] is False
    assert report["status"] == "passed"
    assert report["prompt_version"] == "brief-to-draft-v16"
    assert report["agent_version"] == "brief-to-draft-pipeline-v16"
    assert report["toolset_version"] == "casefile-generation-tools-v2"
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
        "started": 12,
        "completed": 12,
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
# 覆盖 4 类推理任务: 因果解释(ferry) + 身份判断 + 路径探索 + 规则发现 + 决策推理 + 关系推理
# 覆盖 3 类结论模式: author_anchored (全部) -- Brief 层级仅有 author_anchored/agent_proposed/open


def _metric_value(report: dict, name: str) -> float:
    for m in report["metrics"]:
        if m["name"] == name:
            return m["value"]
    raise KeyError(f"metric {name!r} not found")


def test_all_fixtures_pass_fake_benchmark() -> None:
    """Verify every registered fixture passes structure validity in fake mode."""
    for fixture_name in REQUIRED_FIXTURES:
        fixture_path = FIXTURE_ROOT / fixture_name
        run = run_benchmark(BenchmarkOptions(fixture=fixture_path, repeats=2))
        report = run_to_report(run)

        assert report["mode"] == "fake"
        assert report["repeats"] == 2
        assert report["status"] == "completed"
        assert report["dimension"] == "ai_model"
        assert report["prompt_version"].startswith("brief-to-draft-v")
        assert report["schema_version"] == "2.0"

        svr = _metric_value(report, "structure_validity_rate")
        assert svr == 1.0, f"{fixture_name}: structure_validity_rate={svr}"
        retries = _metric_value(report, "structure_retries_total")
        assert retries == 0.0, f"{fixture_name}: retries_total={retries}"


def test_fake_brief_to_draft_benchmark_records_tool_rates() -> None:
    fixture = FIXTURE_ROOT / "brief_to_draft.json"
    run = run_benchmark(BenchmarkOptions(fixture=fixture, repeats=2))
    report = run_to_report(run)

    assert report["mode"] == "fake"
    assert report["repeats"] == 2
    assert _metric_value(report, "structure_validity_rate") == 1.0
    assert _metric_value(report, "structure_retries_total") == 0.0
    assert _metric_value(report, "structure_retries_max") == 0.0
    assert 0.0 <= _metric_value(report, "tool_calls")
    assert 0.0 <= _metric_value(report, "tool_validity_rate") <= 1.0
    assert 0.0 <= _metric_value(report, "tool_execution_success_rate") <= 1.0
    assert 0.0 <= _metric_value(report, "tool_result_adoption_rate") <= 1.0
