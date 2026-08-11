"""Non-live tests for the v11 runtime acceptance policy and scenario inputs."""

from __future__ import annotations

from pathlib import Path
from runpy import run_path

from casefile.agent_runtime.brief_to_draft_v8.workflow import (
    _extract_allowed_wgs84_coordinates,
)

_SUPPORT = run_path(
    str(
        Path(__file__).resolve().parents[1]
        / "integration"
        / "test_brief_to_draft_v8_live_acceptance.py"
    )
)
_V11_SCENARIOS = _SUPPORT["_V11_SCENARIOS"]
_brief = _SUPPORT["_brief"]
_report_status = _SUPPORT["_report_status"]
_summarize_execution_metrics = _SUPPORT["_summarize_execution_metrics"]


def _passing_report() -> dict[str, object]:
    scenario_summary = {
        scenario.scenario_id: {
            "attempted": 6,
            "task_succeeded": 6 if index < 2 else 5,
            "scenario_passed": 6 if index < 2 else 5,
        }
        for index, scenario in enumerate(_V11_SCENARIOS)
    }
    return {
        "suite": "brief_to_draft_v11",
        "status": "running",
        "runs_attempted": 30,
        "successful_runs": 27,
        "scenario_passed_runs": 27,
        "invariant_violations": [],
        "failed_runs": [{"diagnostics_complete": True}] * 3,
        "scenario_summary": scenario_summary,
    }


def test_v11_acceptance_brief_freezes_scenario_text_and_wgs84_allowlist() -> None:
    scenario = next(item for item in _V11_SCENARIOS if item.scenario_id == "spatial_wgs84")

    brief = _brief(41, scenario)

    assert scenario.source_text in str(brief["creative_intent"])
    assert [item.model_dump() for item in _extract_allowed_wgs84_coordinates(brief)] == [
        {"latitude": 31.2304, "longitude": 121.4737}
    ]


def test_v11_acceptance_summary_counts_successes_and_failures_per_scenario() -> None:
    report: dict[str, object] = {
        "successful_run_details": [
            {
                "scenario": "time_exact_range",
                "scenario_passed": True,
                "latency_ms": 1,
                "model_calls": 4,
                "component_steps": 8,
            },
            {
                "scenario": "time_exact_range",
                "scenario_passed": False,
                "latency_ms": 2,
                "model_calls": 4,
                "component_steps": 8,
            },
        ],
        "failed_runs": [{"scenario": "time_exact_range"}],
    }

    _summarize_execution_metrics(report)

    assert report["scenario_summary"] == {
        "time_exact_range": {
            "attempted": 3,
            "task_succeeded": 2,
            "scenario_passed": 1,
        }
    }
    assert report["scenario_passed_runs"] == 1


def test_v11_acceptance_policy_requires_total_and_each_scenario_threshold() -> None:
    report = _passing_report()
    assert _report_status(report, expected_runs=30) == "passed"

    report["scenario_summary"]["competition_matrix"]["scenario_passed"] = 4  # type: ignore[index]
    assert _report_status(report, expected_runs=30) == "failed"

    report = _passing_report()
    report["scenario_passed_runs"] = 26
    assert _report_status(report, expected_runs=30) == "failed"


def test_v11_acceptance_policy_rejects_invariant_or_incomplete_diagnostics() -> None:
    report = _passing_report()
    report["invariant_violations"] = [{"violation": "automatic_current_draft_write"}]
    assert _report_status(report, expected_runs=30) == "failed"

    report = _passing_report()
    report["failed_runs"] = [{"diagnostics_complete": False}]
    assert _report_status(report, expected_runs=30) == "failed"
