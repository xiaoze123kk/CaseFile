from __future__ import annotations

from casefile.benchmark.general_mutation_eval import run_qualification


def test_general_mutation_regression_safety_qualification_passes() -> None:
    report = run_qualification()

    assert report["status"] == "passed"
    assert report["suite"]["suite_kind"] == "kernel_regression"
    assert report["suite"]["task_count"] == 10
    assert report["metrics"]["kernel_regression_pass_rate"] == 1
    assert report["metrics"]["kernel_failure_count"] == 0
    assert report["provider"] == {
        "invoked": False,
        "model_id": None,
        "formal_capability": False,
    }
    assert len(report["runtime_fingerprint"]) == 64
