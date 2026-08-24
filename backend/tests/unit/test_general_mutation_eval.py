from __future__ import annotations

from casefile.benchmark.general_mutation_eval import run_qualification


def test_general_mutation_regression_safety_qualification_passes() -> None:
    report = run_qualification()

    assert report["status"] == "passed"
    assert report["suite"]["suite_kind"] == "regression_safety"
    assert report["suite"]["task_count"] == 10
    assert report["metrics"]["unsafe_trial_rate"] == 0
    assert report["provider"] == {
        "invoked": False,
        "model_id": None,
        "formal_capability": False,
    }
    assert len(report["runtime_fingerprint"]) == 64
