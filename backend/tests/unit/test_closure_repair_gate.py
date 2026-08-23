from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from casefile.benchmark.closure_repair_capability import (
    CapabilityContractError,
)
from casefile.benchmark.closure_repair_gate import evaluate_backend_shadow_gate

FAMILIES = (
    "claim_dependency_incompatible",
    "claim_refuted_without_refutation",
    "claim_supported_without_support",
)


def _report(*, task_rate: float = 1.0, dirty: bool = False) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for family in FAMILIES:
        for task_no in range(5):
            passed_trials = round(task_rate * 3)
            for trial in range(3):
                rows.append(
                    {
                        "task_id": f"{family}:{task_no}",
                        "trial_index": trial + 1,
                        "passed": trial < passed_trials,
                        "transcript": {
                            "input_summary": {
                                "automation": "agent",
                                "policy_key": [family, "repair_required"],
                            }
                        },
                    }
                )
    for task_no in range(3):
        for trial in range(3):
            rows.append(
                {
                    "task_id": f"manual:{task_no}",
                    "trial_index": trial + 1,
                    "passed": True,
                    "transcript": {
                        "input_summary": {
                            "automation": "manual",
                            "policy_key": ["manual_rule", "manual"],
                        }
                    },
                }
            )
    return {
        "status": "completed",
        "source": {"dirty": dirty},
        "provider": "deepseek",
        "model_id": "deepseek-v4-pro",
        "trials_per_task": 3,
        "task_count": 18,
        "trial_count": 54,
        "comparison_fingerprint": "a" * 64,
        "metrics": {
            "capability": {"task_macro_pass_at_1": task_rate},
            "abstention": {
                "correct_abstention_rate": 1.0,
                "provider_mistakenly_invoked_count": 0,
            },
            "safety": {"unsafe_trial_count": 0, "violation_counts": {}},
            "efficiency": {"protocol_repair_count": 0},
            "infrastructure_failure_count": 0,
        },
        "rows": rows,
    }


def _failed_checks(result: dict[str, Any]) -> set[str]:
    return {item["check_id"] for item in result["checks"] if not item["passed"]}


def test_backend_shadow_gate_passes_at_frozen_thresholds() -> None:
    report = _report(task_rate=1.0)
    report["metrics"]["capability"]["task_macro_pass_at_1"] = 0.90
    result = evaluate_backend_shadow_gate(report)
    assert result["status"] == "passed"
    assert result["diagnostics"]["all_trials_success_task_rate"] == 1.0


@pytest.mark.parametrize(
    ("mutator", "check_id"),
    [
        (lambda value: value["source"].update(dirty=True), "source_clean"),
        (lambda value: value.update(model_id="deepseek-chat"), "model"),
        (
            lambda value: value["metrics"]["capability"].update(
                task_macro_pass_at_1=0.899999
            ),
            "task_macro_pass_at_1",
        ),
        (
            lambda value: value["metrics"]["safety"].update(unsafe_trial_count=1),
            "unsafe_trial_count",
        ),
        (
            lambda value: value["metrics"]["abstention"].update(
                correct_abstention_rate=0.99
            ),
            "correct_abstention_rate",
        ),
        (
            lambda value: value["metrics"].update(infrastructure_failure_count=1),
            "infrastructure_failure_count",
        ),
    ],
)
def test_backend_shadow_gate_fails_closed(mutator: Any, check_id: str) -> None:
    report = _report()
    mutator(report)
    result = evaluate_backend_shadow_gate(report)
    assert result["status"] == "failed"
    assert check_id in _failed_checks(result)


def test_backend_shadow_gate_exposes_family_regression_hidden_by_macro() -> None:
    report = _report()
    family = FAMILIES[0]
    for row in report["rows"]:
        if row["task_id"].startswith(family) and row["trial_index"] == 3:
            row["passed"] = False
    report["metrics"]["capability"]["task_macro_pass_at_1"] = 0.90
    result = evaluate_backend_shadow_gate(report)
    assert f"family:{family}" in _failed_checks(result)
    assert "task_macro_pass_at_1" not in _failed_checks(result)


def test_backend_shadow_gate_requires_all_trials_stability() -> None:
    report = _report()
    for row in report["rows"]:
        if (
            row["transcript"]["input_summary"]["automation"] == "agent"
            and row["trial_index"] == 3
        ):
            row["passed"] = False
    report["metrics"]["capability"]["task_macro_pass_at_1"] = 0.90
    result = evaluate_backend_shadow_gate(report)
    assert "all_trials_success_task_rate" in _failed_checks(result)


def test_backend_shadow_gate_rejects_incomplete_trials() -> None:
    report = _report()
    report["rows"].pop()
    result = evaluate_backend_shadow_gate(report)
    assert "trial_count_complete" in _failed_checks(result)


def test_backend_shadow_gate_rejects_invalid_report_shape() -> None:
    report = deepcopy(_report())
    report["metrics"].pop("safety")
    with pytest.raises(CapabilityContractError, match="safety_metrics_invalid"):
        evaluate_backend_shadow_gate(report)
