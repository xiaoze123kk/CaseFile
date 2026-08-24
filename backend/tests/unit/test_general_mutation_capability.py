from __future__ import annotations

import json
from pathlib import Path

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


def test_general_mutation_capability_references_prove_tasks() -> None:
    suite = load_capability_suite()

    assert len(suite.tasks) == 7
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
    assert report["metrics"]["classification_counts"] == {"success": 7}


def test_general_mutation_07a_gate_requires_complete_7_by_5() -> None:
    suite = load_capability_suite()
    report = run_capability_benchmark(
        model_id="deepseek-v4-pro",
        api_key="test-key-not-sent",
        trials=5,
        provider=OrderedReferenceProvider(suite, trials=5),
    )

    gate = report["gates"]["m3_4_07a"]
    assert gate["passed"] is True
    assert gate["cross_reference_passed"] == 5
    assert gate["general_mutation_ref_shape_invalid_count"] == 0
