"""N4.4 ScenePlan benchmark contract tests; no live Provider is used."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.agent_runtime.scene_compiler import (
    SceneFillBatchRequest,
    SceneFillBatchResult,
)
from casefile.benchmark.scene_plan_eval import run_suite, validate_suite


def _object_ref_keys(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        object_type = value.get("object_type")
        object_id = value.get("object_id")
        if isinstance(object_type, str) and isinstance(object_id, str):
            refs.add(f"{object_type}:{object_id}")
        for nested in value.values():
            refs.update(_object_ref_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            refs.update(_object_ref_keys(nested))
    return refs


def test_scene_plan_suite_is_audited_24_task_matrix() -> None:
    validated = validate_suite()

    assert len(validated["suite"]["tasks"]) == 24
    assert len(validated["alternatives"]) == 8
    assert len(validated["mutations"]) >= 10
    assert all(
        invariant["input_evidence_paths"]
        for task in validated["suite"]["tasks"]
        for invariant in task["outcome_invariants"]
    )
    assert validated["suite"]["qualification"] == {
        "status": "uncalibrated",
        "qualified": False,
        "reason": "live_baseline_not_run",
    }
    assert all(
        item["schema_id"] == "compiler.scene-compiler-input.v2"
        for item in validated["runtime_inputs"].values()
    )
    assert all(
        item["schema_id"] == "compiler.scene-compiler-model-view.v1"
        for item in validated["model_views"].values()
    )
    for model_view in validated["model_views"].values():
        for batch in model_view["batches"]:
            catalog = {
                f"{item['object_ref']['object_type']}:{item['object_ref']['object_id']}"
                for item in batch["object_catalog"]
            }
            assert _object_ref_keys(batch["scenes"]) <= catalog
            assert _object_ref_keys(batch["state_seed"]) <= catalog


def test_scene_plan_reference_and_alternative_outcomes_pass() -> None:
    capability = run_suite(suite_kind="capability")
    regression = run_suite(suite_kind="regression")

    assert capability["status"] == "passed"
    assert capability["metrics"]["trial_count"] == 24
    assert regression["status"] == "passed"
    assert regression["metrics"]["trial_count"] == 8
    assert capability["qualification"]["qualified"] is False
    assert capability["schema_id"] == "benchmark.scene-plan-report.v3"
    assert capability["frozen"]["pipeline_version"] == ("compiler.scene-compiler.shadow.v1")
    assert all(item["provider_invoked"] for item in capability["trials"])


def test_scene_plan_safety_mutations_are_rejected_by_expected_reason() -> None:
    report = run_suite(suite_kind="safety")

    assert report["status"] == "passed"
    assert report["metrics"]["trial_count"] >= 10
    assert all(not trial["candidate_accepted"] for trial in report["trials"])
    assert all(trial["expected_reason_code"] for trial in report["trials"])
    assert report["metrics"]["failure_taxonomy"]["Infrastructure"] == 0


def test_live_runtime_path_uses_provider_and_formal_contract_is_fail_closed() -> None:
    diagnostic = run_suite(
        suite_kind="capability",
        mode="live",
        provider_name="deepseek",
        model_id="deepseek-v4-pro",
        repeats=1,
        task_ids=("scene_decomposition__basic",),
        provider_factory=FakeProvider,
        api_key_override="test-only",
        diagnostic_payload_policy="failed-proposal",
    )

    assert diagnostic["status"] == "passed"
    assert diagnostic["metrics"]["trial_count"] == 1
    assert diagnostic["trials"][0]["provider_invoked"] is True
    assert "diagnostic_payload" not in diagnostic["trials"][0]["stages"][0]

    with pytest.raises(ValueError, match="exactly 3 trials"):
        run_suite(
            suite_kind="capability",
            mode="live",
            provider_name="deepseek",
            model_id="deepseek-v4-pro",
            repeats=1,
            provider_factory=FakeProvider,
            api_key_override="test-only",
        )


def test_rejected_provider_output_retains_stage_usage_evidence() -> None:
    class InvalidReferenceProvider:
        def fill_scene_batch(self, request: SceneFillBatchRequest) -> SceneFillBatchResult:
            result = FakeProvider().fill_scene_batch(request)
            proposal = deepcopy(result.proposal)
            proposal["scenes"][0]["beats"][0]["actor_refs"] = [
                {"object_type": "entity", "object_id": "ent_unknown"}
            ]
            return SceneFillBatchResult(
                proposal=proposal,
                usage={"requests": 1, "total_tokens": 42},
                raw_output="invalid-reference-output",
            )

    report = run_suite(
        suite_kind="capability",
        task_ids=("scene_decomposition__basic",),
        provider_factory=InvalidReferenceProvider,
        diagnostic_payload_policy="failed-proposal",
    )

    assert report["status"] == "failed"
    assert report["trials"][0]["reason_code"] == ("compiler_scene_fill_actor_invalid")
    assert report["trials"][0]["stages"][0]["raw_output_hash"]
    assert report["trials"][0]["stages"][0]["diagnostic_payload"]
    evidence = report["trials"][0]["failure_evidence"]
    assert {key: value for key, value in evidence.items() if key != "allowed_ref_hash"} == {
        "batch_id": "scene_batch_chapter_1_001",
        "json_path": "/scenes/0/beats/0/actor_refs/0",
        "scene_id": "scene_1",
        "beat_local_key": "beat_local_scene_1_001",
        "emitted_ref": {"object_type": "entity", "object_id": "ent_unknown"},
        "allowed_ref_count": 2,
    }
    assert len(evidence["allowed_ref_hash"]) == 64
    assert report["metrics"]["failure_batch_ordinals"] == {"1": 1}
    assert report["metrics"]["usage_total"] == {
        "requests": 1.0,
        "total_tokens": 42.0,
    }


def test_scene_plan_report_fingerprint_is_deterministic() -> None:
    first = run_suite(suite_kind="regression")
    second = run_suite(suite_kind="regression")

    assert first["fingerprint"] == second["fingerprint"]
    assert first["frozen"] == second["frozen"]
    assert first["metrics"]["task_results"] == second["metrics"]["task_results"]
    assert first["metrics"]["usage_total"] == second["metrics"]["usage_total"]
    retained = run_suite(
        suite_kind="regression", diagnostic_payload_policy="failed-proposal"
    )
    assert retained["fingerprint"] != first["fingerprint"]


def test_scene_plan_rejects_unknown_diagnostic_payload_policy() -> None:
    with pytest.raises(ValueError, match="diagnostic payload policy"):
        run_suite(suite_kind="capability", diagnostic_payload_policy="raw-output")
