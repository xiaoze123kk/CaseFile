from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from benchmark_preparation import reuse_document_findings
from casefile.agent_runtime.closure_repair import (
    ClosureRepairOutputV3,
    ClosureRepairProviderResult,
)
from casefile.agent_runtime.general_mutation import (
    GeneralMutationPlannerResult,
    MutationPlanV2,
)
from casefile.benchmark.general_mutation_capability import (
    _calibration_gate,
    _dev_gate,
    _matches,
    _metrics,
    _pointer_get,
    _transport_gate,
    load_capability_suite,
    run_capability_benchmark,
)

ROOT = Path(__file__).resolve().parents[3]
V1_SUITE = ROOT / "fixtures/general_mutation_benchmark/capability/v1/suite.json"


class OrderedReferenceProvider:
    def __init__(self, suite, trials: int = 1) -> None:  # type: ignore[no-untyped-def]
        self.plans = iter(
            json.loads(Path(task.reference_path).read_text(encoding="utf-8"))["plan"]
            for task in suite.tasks
            for _trial in range(trials)
        )

    def plan_general_mutation(self, request):  # type: ignore[no-untyped-def]
        del request
        return GeneralMutationPlannerResult(
            MutationPlanV2.model_validate(next(self.plans)),
            {"requests": 1, "total_tokens": 1},
        )

    def repair_closure(self, request):  # type: ignore[no-untyped-def]
        alternatives = request.context["repair_alternatives"]
        selected = next(
            (
                item
                for item in alternatives
                if any(
                    operation["field_path"] == "/status" and operation["new_value"] == "unresolved"
                    for operation in item["operations"]
                )
            ),
            alternatives[0],
        )
        return ClosureRepairProviderResult(
            ClosureRepairOutputV3(
                selected_alternative_id=selected["alternative_id"],
                reason="选择与作者目标一致的服务端修复方案",
            ),
            {"requests": 1, "total_tokens": 1},
        )


class FallbackReferenceProvider(OrderedReferenceProvider):
    def plan_general_mutation(self, request):  # type: ignore[no-untyped-def]
        request.emit(
            "model.output_protocol_fallback",
            "general_mutation",
            {"from": "strict_tool", "to": "json_object"},
        )
        return super().plan_general_mutation(request)


def test_general_mutation_capability_missing_list_item_is_not_a_harness_failure() -> None:
    assert _pointer_get({"aliases": []}, "/aliases/0") is None
    assert _matches(["读取日志", "检修备用系统"], {"$contains": "检修备用系统"})


def test_text_equivalent_ignores_only_terminal_punctuation_when_oracle_opts_in() -> None:
    expected = {"$text_equivalent": "备用系统执行安全重启"}

    assert _matches("备用系统执行安全重启。", expected)
    assert _matches("备用系统执行安全重启", expected)
    assert not _matches("备用系统执行紧急重启。", expected)
    assert not _matches("备用系统执行安全重启。", "备用系统执行安全重启")


@pytest.fixture(scope="module")
def capability_report() -> dict[str, Any]:
    suite = load_capability_suite()
    with reuse_document_findings():
        return run_capability_benchmark(
            model_id="deepseek-v4-pro",
            api_key="test-key-not-sent",
            trials=1,
            provider=OrderedReferenceProvider(suite),
        )


def test_general_mutation_capability_grades_final_state_not_plan_path(
    capability_report: dict[str, Any],
) -> None:
    report = capability_report
    # The runner validates all references before exercising the real pipeline once.
    assert report["suite"]["task_count"] == 40
    assert len(report["suite"]["suite_fingerprint"]) == 64
    assert report["status"] == "completed"
    assert report["formal_capability"] is False
    assert report["release_gate_eligible"] is False
    assert report["metrics"]["task_macro_pass_at_1"] == 1
    assert report["metrics"]["unsafe_escape_count"] == 0
    assert report["metrics"]["classification_counts"] == {"success": 40}


def test_general_mutation_07a_and_07b_gates_require_complete_frozen_7_by_5() -> None:
    suite = load_capability_suite(
        suite_path=V1_SUITE
    )
    report = run_capability_benchmark(
        model_id="deepseek-v4-pro",
        api_key="test-key-not-sent",
        trials=1,
        provider=OrderedReferenceProvider(suite),
        suite_path=V1_SUITE,
    )

    rows = _repeat_rows(report["rows"], 5)
    metrics = _metrics(rows, suite.tasks, 5)
    gate = _calibration_gate(rows, suite.tasks, 5, metrics)
    assert gate["passed"] is True
    assert gate["cross_reference_passed"] == 5
    assert gate["general_mutation_ref_shape_invalid_count"] == 0

    gate = _transport_gate(rows, suite.tasks, 5, metrics)
    assert gate["passed"] is True
    assert gate["checks"]["fallback_event_zero"] is True
    assert gate["output_protocol_fallback_event_count"] == 0
    assert report["lineage"]["transport_version"] == "general-mutation-json-object-v1"


def test_general_mutation_07b_gate_counts_transcript_fallback_events() -> None:
    suite = load_capability_suite(
        suite_path=V1_SUITE
    )
    report = run_capability_benchmark(
        model_id="deepseek-v4-pro",
        api_key="test-key-not-sent",
        trials=1,
        provider=FallbackReferenceProvider(suite),
        suite_path=V1_SUITE,
    )

    rows = _repeat_rows(report["rows"], 5)
    gate = _transport_gate(rows, suite.tasks, 5, _metrics(rows, suite.tasks, 5))
    assert gate["passed"] is False
    assert gate["output_protocol_fallback_event_count"] == 35


def test_general_mutation_07c_gate_requires_complete_safe_40_by_5(
    capability_report: dict[str, Any],
) -> None:
    suite = load_capability_suite()
    # Each task has already run through the real pipeline once. Repeated rows
    # exercise report aggregation, not model reliability or live qualification.
    rows = _repeat_rows(capability_report["rows"], 5)
    metrics = _metrics(rows, suite.tasks, 5)
    assert _dev_gate(rows, suite.tasks, 5, metrics)["passed"] is True
    assert metrics["family_min_pass_at_1"] == 1
    assert metrics["reliable_task_rate_at_5"] == 1

    incomplete = rows[:-1]
    assert _dev_gate(incomplete, suite.tasks, 5, _metrics(incomplete, suite.tasks, 5))[
        "passed"
    ] is False
    unsafe = [{**rows[0], "classification": "unsafe_escape", "passed": False}, *rows[1:]]
    assert _dev_gate(unsafe, suite.tasks, 5, _metrics(unsafe, suite.tasks, 5))[
        "passed"
    ] is False


def _repeat_rows(rows: list[dict[str, Any]], trials: int) -> list[dict[str, Any]]:
    """Exercise gate denominators using real rows, without repeating deterministic execution."""
    return [
        {**row, "trial_id": f"{row['task_id']}:{trial}", "trial_index": trial}
        for row in rows
        for trial in range(1, trials + 1)
    ]
