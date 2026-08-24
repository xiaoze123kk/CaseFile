from __future__ import annotations

import json
from pathlib import Path

from casefile.agent_runtime.closure_repair import (
    ClosureRepairOutputV3,
    ClosureRepairProviderResult,
)
from casefile.agent_runtime.general_mutation import (
    GeneralMutationPlannerResult,
    MutationPlanV2,
)
from casefile.benchmark.general_mutation_capability import (
    _matches,
    _pointer_get,
    load_capability_suite,
    run_capability_benchmark,
    validate_references,
)


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
                    operation["field_path"] == "/status"
                    and operation["new_value"] == "unresolved"
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


def test_general_mutation_capability_references_prove_tasks() -> None:
    suite = load_capability_suite()

    assert len(suite.tasks) == 40
    assert len(suite.fingerprint) == 64
    validate_references(suite)


def test_general_mutation_capability_missing_list_item_is_not_a_harness_failure() -> None:
    assert _pointer_get({"aliases": []}, "/aliases/0") is None
    assert _matches(["读取日志", "检修备用系统"], {"$contains": "检修备用系统"})


def test_general_mutation_capability_grades_final_state_not_plan_path() -> None:
    suite = load_capability_suite()
    report = run_capability_benchmark(
        model_id="deepseek-v4-pro",
        api_key="test-key-not-sent",
        trials=1,
        provider=OrderedReferenceProvider(suite),
    )

    assert report["status"] == "completed"
    assert report["formal_capability"] is False
    assert report["release_gate_eligible"] is False
    assert report["metrics"]["task_macro_pass_at_1"] == 1
    assert report["metrics"]["unsafe_escape_count"] == 0
    assert report["metrics"]["classification_counts"] == {"success": 40}


def test_general_mutation_07a_gate_requires_complete_7_by_5() -> None:
    suite = load_capability_suite(
        suite_path=Path("fixtures/general_mutation_benchmark/capability/v1/suite.json")
    )
    report = run_capability_benchmark(
        model_id="deepseek-v4-pro",
        api_key="test-key-not-sent",
        trials=5,
        provider=OrderedReferenceProvider(suite, trials=5),
        suite_path=Path("fixtures/general_mutation_benchmark/capability/v1/suite.json"),
    )

    gate = report["gates"]["m3_4_07a"]
    assert gate["passed"] is True
    assert gate["cross_reference_passed"] == 5
    assert gate["general_mutation_ref_shape_invalid_count"] == 0


def test_general_mutation_07b_gate_requires_frozen_transport_metrics() -> None:
    suite = load_capability_suite(
        suite_path=Path("fixtures/general_mutation_benchmark/capability/v1/suite.json")
    )
    report = run_capability_benchmark(
        model_id="deepseek-v4-pro",
        api_key="test-key-not-sent",
        trials=5,
        provider=OrderedReferenceProvider(suite, trials=5),
        suite_path=Path("fixtures/general_mutation_benchmark/capability/v1/suite.json"),
    )

    gate = report["gates"]["m3_4_07b"]
    assert gate["passed"] is True
    assert gate["checks"]["fallback_event_zero"] is True
    assert gate["output_protocol_fallback_event_count"] == 0
    assert report["lineage"]["transport_version"] == "general-mutation-json-object-v1"


def test_general_mutation_07b_gate_counts_transcript_fallback_events() -> None:
    suite = load_capability_suite(
        suite_path=Path("fixtures/general_mutation_benchmark/capability/v1/suite.json")
    )
    report = run_capability_benchmark(
        model_id="deepseek-v4-pro",
        api_key="test-key-not-sent",
        trials=5,
        provider=FallbackReferenceProvider(suite, trials=5),
        suite_path=Path("fixtures/general_mutation_benchmark/capability/v1/suite.json"),
    )

    gate = report["gates"]["m3_4_07b"]
    assert gate["passed"] is False
    assert gate["output_protocol_fallback_event_count"] == 35


def test_general_mutation_07c_gate_requires_complete_40_by_5() -> None:
    suite = load_capability_suite()
    report = run_capability_benchmark(
        model_id="deepseek-v4-pro",
        api_key="test-key-not-sent",
        trials=5,
        provider=OrderedReferenceProvider(suite, trials=5),
    )

    gate = report["gates"]["m3_4_07c"]
    assert gate["passed"] is True
    assert report["metrics"]["family_min_pass_at_1"] == 1
    assert report["metrics"]["reliable_task_rate_at_5"] == 1
