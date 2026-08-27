"""N4.4 ScenePlan benchmark contract tests; no live Provider is used."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.agent_runtime.scene_compiler import (
    SceneFillBatchRequest,
    SceneFillBatchResult,
)
from casefile.benchmark import scene_plan_eval
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
        "status": "gate_frozen",
        "qualified": False,
        "reason": "fresh_full_g3_g4_run_required",
    }
    assert validated["suite"]["schema_id"] == "benchmark.scene-plan-suite.v2"
    assert validated["rubric"]["judge_model_id"] == "deepseek-v4-flash"
    assert validated["rubric"]["known_limitations"] == [
        "same_provider_family_bias",
        "model_judge_not_human_literary_review",
    ]
    assert len(validated["runtime_references"]) == 24
    assert all(
        item["schema_id"] == "compiler.scene-plan.v2"
        for item in validated["runtime_references"].values()
    )
    assert all(
        item["schema_id"] == "compiler.scene-compiler-input.v2"
        for item in validated["runtime_inputs"].values()
    )
    assert all(
        item["schema_id"] == "compiler.scene-compiler-model-view.v1"
        for item in validated["model_views"].values()
    )
    for model_view in validated["model_views"].values():
        assert model_view["source"]["projection_version"] == (
            "compiler.scene-compiler-model-view-projection.v3"
        )
        for batch in model_view["batches"]:
            catalog = {
                f"{item['object_ref']['object_type']}:{item['object_ref']['object_id']}"
                for item in batch["object_catalog"]
            }
            assert _object_ref_keys(batch["scenes"]) <= catalog
            assert _object_ref_keys(batch["state_seed"]) <= catalog
            for scene in batch["scenes"]:
                expected = {
                    f"{ref['object_type']}:{ref['object_id']}"
                    for ref in [
                        *scene["basis_refs"],
                        *(
                            ref
                            for obligation in scene["obligations"]
                            for ref in obligation["basis_refs"]
                        ),
                    ]
                }
                assert {
                    f"{ref['object_type']}:{ref['object_id']}"
                    for ref in scene["beat_basis_allowlist"]
                } == expected


def test_scene_plan_reference_and_alternative_outcomes_pass() -> None:
    capability = run_suite(suite_kind="capability")
    regression = run_suite(suite_kind="regression")

    assert capability["status"] == "passed"
    assert capability["metrics"]["trial_count"] == 24
    assert regression["status"] == "passed"
    assert regression["metrics"]["trial_count"] == 8
    assert capability["qualification"]["qualified"] is False
    assert capability["schema_id"] == "benchmark.scene-plan-report.v4"
    assert capability["frozen"]["pipeline_version"] == ("compiler.scene-compiler.shadow.v2")
    assert capability["promotion_gate"]["evaluated"] is False
    assert capability["metrics"]["g3_quality_distribution"]["graded_trial_count"] == 0
    assert capability["metrics"]["g4_audited_trial_count"] == 24
    assert regression["metrics"]["g4_regression_equivalence_evaluated_count"] == 8
    assert regression["metrics"]["g4_audit_failure_count"] == 0
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

    with pytest.raises(ValueError, match="Flash G3 judge"):
        run_suite(
            suite_kind="capability",
            mode="live",
            provider_name="deepseek",
            model_id="deepseek-v4-pro",
            quality_grader_model=None,
            repeats=3,
            provider_factory=FakeProvider,
            api_key_override="test-only",
        )


def test_live_g3_flash_judge_scores_candidate_against_blind_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[dict[str, Any]] = []

    def fake_quality_comparison(**kwargs: Any) -> dict[str, Any]:
        observed.append(kwargs)
        scores = {dimension: 0.8 for dimension in scene_plan_eval.G3_DIMENSIONS}
        reference_scores = {dimension: 0.75 for dimension in scene_plan_eval.G3_DIMENSIONS}
        return {
            "status": "graded",
            "scores": scores,
            "reference_scores": reference_scores,
            "deltas": {dimension: 0.05 for dimension in scene_plan_eval.G3_DIMENSIONS},
            "candidate_slot": "plan_b",
            "usage": {"requests": 1, "total_tokens": 100},
            "latency_ms": 10.0,
            "infrastructure_failure": None,
        }

    monkeypatch.setattr(scene_plan_eval, "_live_quality_comparison", fake_quality_comparison)
    report = run_suite(
        suite_kind="capability",
        mode="live",
        provider_name="deepseek",
        model_id="deepseek-v4-pro",
        quality_grader_model="deepseek-v4-flash",
        repeats=1,
        task_ids=("scene_decomposition__basic",),
        provider_factory=FakeProvider,
        api_key_override="test-only",
    )

    assert report["status"] == "passed"
    assert observed[0]["model_id"] == "deepseek-v4-flash"
    assert observed[0]["candidate"]["schema_id"] == "compiler.scene-plan.v2"
    assert observed[0]["reference"]["schema_id"] == "compiler.scene-plan.v2"
    grader = report["trials"][0]["graders"]
    assert grader["g3_status"] == "graded"
    assert grader["g3_known_limitation"] == "same_provider_family_bias"
    assert grader["g3_candidate_slot"] == "plan_b"
    assert report["metrics"]["g3_quality_distribution"]["graded_trial_count"] == 1
    assert report["metrics"]["g3_paired_bootstrap"]["evaluated"] is True
    assert report["promotion_gate"]["evaluated"] is False


def test_g3_flash_protocol_blinds_slots_and_maps_scores_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []

    class FakeCompletions:
        def create(self, **kwargs: Any) -> Any:
            requests.append(kwargs)
            content = {
                "plan_a": {
                    dimension: 0.9 for dimension in scene_plan_eval.G3_DIMENSIONS
                },
                "plan_b": {
                    dimension: 0.4 for dimension in scene_plan_eval.G3_DIMENSIONS
                },
            }
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(content))
                    )
                ],
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    prompt_cache_hit_tokens=0,
                ),
            )

    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(scene_plan_eval, "OpenAI", FakeClient)
    monkeypatch.setattr(scene_plan_eval, "_runtime_api_key", lambda _provider: "test-key")
    result = scene_plan_eval._live_quality_comparison(
        model_id="deepseek-v4-flash",
        task_id="scene_decomposition__basic",
        trial_index=1,
        rubric=validate_suite()["rubric"],
        model_view={"schema_id": "test-context"},
        candidate={"schema_id": "candidate"},
        reference={"schema_id": "reference"},
    )

    payload = json.loads(requests[0]["messages"][1]["content"])
    assert requests[0]["model"] == "deepseek-v4-flash"
    assert payload[result["candidate_slot"]]["schema_id"] == "candidate"
    assert result["usage"]["requests"] == 1
    expected = 0.9 if result["candidate_slot"] == "plan_a" else 0.4
    assert set(result["scores"].values()) == {expected}


def test_g3_flash_protocol_retries_one_empty_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []
    valid_content = json.dumps(
        {
            slot: {dimension: 0.7 for dimension in scene_plan_eval.G3_DIMENSIONS}
            for slot in ("plan_a", "plan_b")
        }
    )
    contents = iter((None, valid_content))

    class FakeCompletions:
        def create(self, **kwargs: Any) -> Any:
            requests.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=next(contents)))],
                usage=SimpleNamespace(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    prompt_cache_hit_tokens=2,
                ),
            )

    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(scene_plan_eval, "OpenAI", FakeClient)
    monkeypatch.setattr(scene_plan_eval, "_runtime_api_key", lambda _provider: "test-key")
    result = scene_plan_eval._live_quality_comparison(
        model_id="deepseek-v4-flash",
        task_id="scene_decomposition__basic",
        trial_index=1,
        rubric=validate_suite()["rubric"],
        model_view={"schema_id": "test-context"},
        candidate={"schema_id": "candidate"},
        reference={"schema_id": "reference"},
    )

    assert len(requests) == 2
    assert result["usage"] == {
        "requests": 2,
        "input_tokens": 20,
        "output_tokens": 10,
        "total_tokens": 30,
        "cached_tokens": 4,
    }


def test_g3_paired_bootstrap_is_task_clustered_and_deterministic() -> None:
    trials = [
        {
            "task_id": task_id,
            "graders": {
                "g3_status": "graded",
                "g3_deltas": {dimension: delta for dimension in scene_plan_eval.G3_DIMENSIONS},
            },
        }
        for task_id, delta in (("task_a", 0.1), ("task_b", -0.1))
        for _ in range(3)
    ]
    thresholds = {"g3_bootstrap_seed": 7, "g3_bootstrap_iterations": 1000}

    first = scene_plan_eval._g3_paired_bootstrap(trials, thresholds)
    second = scene_plan_eval._g3_paired_bootstrap(trials, thresholds)

    assert first == second
    assert first["evaluated"] is True
    assert first["task_cluster_count"] == 2
    assert first["mean_delta"] == pytest.approx(0.0)
    assert set(first["dimension_mean_deltas"]) == set(scene_plan_eval.G3_DIMENSIONS)


def test_promotion_gate_requires_both_g3_non_regression_and_g4_audit() -> None:
    thresholds = validate_suite()["suite"]["promotion_gate"]
    metrics = {
        "passed_trial_count": 71,
        "pass_at_k_task_count": 24,
        "all_trials_pass_task_count": 23,
        "infrastructure_failure_count": 0,
        "g3_infrastructure_failure_count": 0,
        "accepted_trial_count": 71,
        "g3_quality_distribution": {"graded_trial_count": 71},
        "g3_paired_bootstrap": {
            "evaluated": True,
            "mean_delta_ci95_lower": -0.02,
            "dimension_mean_deltas": {
                dimension: -0.01 for dimension in scene_plan_eval.G3_DIMENSIONS
            },
        },
        "g4_audited_trial_count": 71,
        "g4_audit_failure_count": 0,
    }
    trials = [{} for _ in range(72)]

    qualified = scene_plan_eval._promotion_gate(
        formal=True,
        complete_capability=True,
        thresholds=thresholds,
        metrics=metrics,
        trials=trials,
    )
    g3_regressed = scene_plan_eval._promotion_gate(
        formal=True,
        complete_capability=True,
        thresholds=thresholds,
        metrics={
            **metrics,
            "g3_paired_bootstrap": {
                **metrics["g3_paired_bootstrap"],
                "mean_delta_ci95_lower": -0.04,
            },
        },
        trials=trials,
    )
    g4_failed = scene_plan_eval._promotion_gate(
        formal=True,
        complete_capability=True,
        thresholds=thresholds,
        metrics={**metrics, "g4_audit_failure_count": 1},
        trials=trials,
    )

    assert qualified["qualified"] is True
    assert g3_regressed["qualified"] is False
    assert g3_regressed["checks"]["g3_mean_delta_lower_bound"] is False
    assert g4_failed["qualified"] is False
    assert g4_failed["checks"]["g4_audit_failures"] is False


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
    retained = run_suite(suite_kind="regression", diagnostic_payload_policy="failed-proposal")
    assert retained["fingerprint"] != first["fingerprint"]


def test_scene_plan_rejects_unknown_diagnostic_payload_policy() -> None:
    with pytest.raises(ValueError, match="diagnostic payload policy"):
        run_suite(suite_kind="capability", diagnostic_payload_policy="raw-output")
