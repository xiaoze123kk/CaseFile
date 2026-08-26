"""Novel Plan benchmark contract and deterministic safety gates."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from casefile.benchmark import novel_plan_eval
from casefile.benchmark.novel_plan_eval import run_suite, validate_suite


def test_capability_suite_is_exact_24_task_matrix_with_valid_references() -> None:
    validated = validate_suite()
    assert len(validated["suite"]["tasks"]) == 24
    assert len(set(validated["reference_hashes"].values())) >= 8
    assert len({task["planner_inputs"]["v1"]["hash"] for task in validated["suite"]["tasks"]}) >= 6
    assert all(
        invariant["input_evidence_paths"]
        for task in validated["suite"]["tasks"]
        for invariant in task["outcome_invariants"]
    )


def test_v2_suite_inputs_are_valid_and_distinct_from_v1() -> None:
    v1 = validate_suite(planner_input_version="v1")
    v2 = validate_suite(planner_input_version="v2")
    assert set(v1["planner_inputs"]) == set(v2["planner_inputs"])
    assert all(
        item["schema_id"] == "compiler.story-planner-input.v2"
        for item in v2["planner_inputs"].values()
    )
    assert all(
        v1["planner_inputs"][task_id] != v2["planner_inputs"][task_id]
        for task_id in v1["planner_inputs"]
    )


def test_every_outcome_invariant_rejects_a_targeted_negative_mutation() -> None:
    validated = validate_suite(formal_capability=True)
    for task in validated["suite"]["tasks"]:
        task_id = task["task_id"]
        candidate = copy.deepcopy(validated["references"][task_id])
        invariant = task["outcome_invariants"][0]
        kind = invariant["kind"]
        scenes = candidate["scenes"]
        if kind == "all_presentation_modes":
            scenes[0]["presentation_mode"] = "flashback"
        elif kind == "presentation_mode_present":
            for scene in scenes:
                scene["presentation_mode"] = "linear"
        elif kind == "min_distinct_participant_refs":
            for scene in scenes:
                scene["participant_refs"] = []
        elif kind == "exposure_action_present":
            for scene in scenes:
                scene["exposure"] = []
        elif kind == "basis_refs_include_all":
            forbidden = {novel_plan_eval._object_ref_key(ref) for ref in invariant["refs"]}
            for scene in scenes:
                scene["basis_refs"] = [
                    ref
                    for ref in scene["basis_refs"]
                    if novel_plan_eval._object_ref_key(ref) not in forbidden
                ]
        elif kind == "resolution_actions":
            for scene in scenes:
                for placement in scene["resolutions"]:
                    placement["action"] = "intentionally_unresolved"
        elif kind == "flashback_after_event":
            for scene in scenes:
                scene["presentation_mode"] = "linear"
        else:
            raise AssertionError(f"No negative mutation for {kind}")
        assert not novel_plan_eval._grade_outcome(candidate, task["outcome_invariants"]), task_id


@pytest.mark.parametrize("suite_kind", ["regression", "safety", "capability"])
def test_fake_suites_pass_deterministic_gates(suite_kind: str, tmp_path: Path) -> None:
    report = run_suite(
        suite_kind=suite_kind,
        mode="fake",
        provider_name="openai",
        model_id="fake-story-planner",
        quality_grader_model=None,
        repeats=1,
        checkpoint_path=tmp_path / f"{suite_kind}.json",
        resume=False,
    )
    assert report["status"] == "passed"
    assert report["metrics"]["unsafe_trial_rate"] == 0
    assert report["metrics"]["infrastructure_failure_rate"] == 0


def test_v2_fake_capability_reports_audited_groups_and_promotion_state(
    tmp_path: Path,
) -> None:
    report = run_suite(
        suite_kind="capability",
        mode="fake",
        provider_name="openai",
        model_id="fake-story-planner",
        quality_grader_model=None,
        repeats=1,
        checkpoint_path=tmp_path / "capability-v2.json",
        resume=False,
        planner_input_version="v2",
    )
    assert report["frozen"]["planner_input_version"] == "v2"
    assert len(report["metrics"]["by_capability"]) == 8
    assert len(report["metrics"]["by_variant"]) == 3
    assert report["metrics"]["valid_but_g2_failed_trial_count"] == 0
    assert report["promotion_gate"]["evaluated"] is True
    assert report["promotion_gate"]["qualified"] is False


def test_v3_fake_capability_uses_compact_provider_view_and_full_audit_bundle(
    tmp_path: Path,
) -> None:
    report = run_suite(
        suite_kind="capability",
        mode="fake",
        provider_name="openai",
        model_id="fake-story-planner",
        quality_grader_model=None,
        repeats=1,
        checkpoint_path=tmp_path / "capability-v3.json",
        resume=False,
        planner_input_version="v3",
        prompt_version="story-planner-v7",
    )
    assert report["frozen"]["planner_input_version"] == "v3"
    assert report["frozen"]["prompt"] == "story-planner-v7"
    assert report["metrics"]["infrastructure_failure_rate"] == 0
    assert report["promotion_gate"]["checks"]["g2_stronger_than_v3"] is False
    assert report["frozen"]["comparison_baseline"]["outcome_passed_trial_count"] == 64


def test_capability_task_selection_is_sorted_fingerprinted_and_non_formal(
    tmp_path: Path,
) -> None:
    selected = ("multiple_suspects__dense", "complex_mixed__decoy")
    reversed_report = run_suite(
        suite_kind="capability",
        mode="fake",
        provider_name="openai",
        model_id="fake-story-planner",
        quality_grader_model=None,
        repeats=1,
        checkpoint_path=tmp_path / "selected-reversed.json",
        resume=False,
        task_ids=selected,
    )
    sorted_report = run_suite(
        suite_kind="capability",
        mode="fake",
        provider_name="openai",
        model_id="fake-story-planner",
        quality_grader_model=None,
        repeats=1,
        checkpoint_path=tmp_path / "selected-sorted.json",
        resume=False,
        task_ids=tuple(sorted(selected)),
    )

    assert reversed_report["fingerprint"] == sorted_report["fingerprint"]
    assert reversed_report["frozen"]["selection"] == {
        "task_ids": sorted(selected),
        "is_complete": False,
    }
    assert {trial["task_id"] for trial in reversed_report["trials"]} == set(selected)
    assert reversed_report["promotion_gate"] == {
        "evaluated": False,
        "qualified": False,
        "reason": "partial_task_selection",
        "checks": {},
    }


def test_capability_task_selection_rejects_invalid_scope_and_identities() -> None:
    suite = validate_suite()["suite"]
    with pytest.raises(ValueError, match="only available for Capability"):
        novel_plan_eval._task_selection(
            suite,
            suite_kind="regression",
            task_ids=("linear_mystery__basic",),
        )
    with pytest.raises(ValueError, match="duplicate"):
        novel_plan_eval._task_selection(
            suite,
            suite_kind="capability",
            task_ids=("linear_mystery__basic", "linear_mystery__basic"),
        )
    with pytest.raises(ValueError, match="Unknown Novel Plan task IDs"):
        novel_plan_eval._task_selection(
            suite,
            suite_kind="capability",
            task_ids=("missing__dense",),
        )


def test_constraint_first_diagnostic_selection_freezes_current_failure_baseline() -> None:
    descriptor = novel_plan_eval._read_json(
        novel_plan_eval.SUITE_ROOT / "constraint_first_diagnostic_v1.json"
    )
    suite_ids = {
        str(task["task_id"]) for task in validate_suite()["suite"]["tasks"]
    }

    assert descriptor["schema_id"] == "benchmark.novel-plan-diagnostic-selection.v1"
    assert descriptor["task_ids"] == sorted(descriptor["task_ids"])
    assert len(descriptor["task_ids"]) == 6
    assert set(descriptor["task_ids"]) <= suite_ids
    assert descriptor["trials_per_task"] == 3
    assert descriptor["baseline"] == {
        "trial_count": 18,
        "contract_valid_trial_count": 15,
        "semantic_valid_trial_count": 10,
        "outcome_passed_trial_count": 7,
        "production_rejections": {
            "compiler_story_plan_exposure_violation": 1,
            "compiler_story_plan_temporal_order_invalid": 4,
            "compiler_story_planner_structural_repair_exhausted": 3,
        },
        "g2_outcome_failures": {"min_distinct_participant_refs": 3},
    }


def test_candidate_preserving_repair_improves_same_trial_without_g2_regression() -> None:
    validated = validate_suite(planner_input_version="v3")
    task = next(
        item for item in validated["suite"]["tasks"] if item["task_id"] == "complex_mixed__basic"
    )
    task_id = task["task_id"]
    candidate = copy.deepcopy(validated["references"][task_id])
    earlier_ref = {"object_type": "event", "object_id": "evt_restart_six"}
    later_ref = {"object_type": "event", "object_id": "evt_restart_seven"}
    candidate["scenes"][0]["event_refs"] = [earlier_ref]
    candidate["scenes"][0]["story_time_refs"] = [earlier_ref]
    candidate["scenes"][1]["event_refs"] = [later_ref]
    candidate["scenes"][1]["story_time_refs"] = [later_ref]

    trial = novel_plan_eval._run_trial(
        task=task,
        trial_index=1,
        suite_kind="capability",
        mode="fake",
        provider_name="openai",
        model_id="fake-story-planner",
        quality_grader_model=None,
        bundle=validated["planner_inputs"][task_id],
        provider_input=validated["planner_model_views"][task_id],
        reference=candidate,
        outcome_invariants=task["outcome_invariants"],
        prompt_version="story-planner-v7",
    )

    assert trial["raw_semantic_valid"] is False
    assert trial["candidate_repair_applied"] is True
    assert trial["semantic_valid"] is True
    assert trial["passed"] is True
    assert trial["candidate_repair_g2_regression"] is False
    assert len(trial["candidate_repair_changes"]) == 2


def test_semantic_rejections_keep_provider_usage_in_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectingProvider:
        def __init__(self, candidate: dict[str, Any]) -> None:
            self.candidate = copy.deepcopy(candidate)

        def plan_story(self, _request: object) -> novel_plan_eval.StoryPlannerProviderResult:
            candidate = copy.deepcopy(self.candidate)
            for scene in candidate["scenes"]:
                scene["resolutions"] = []
            return novel_plan_eval.StoryPlannerProviderResult(
                candidate=candidate,
                usage={"requests": 1, "total_tokens": 123},
            )

    monkeypatch.setattr(
        novel_plan_eval,
        "_provider",
        lambda _mode, _provider_name, candidate: RejectingProvider(candidate),
    )

    report = run_suite(
        suite_kind="regression",
        mode="fake",
        provider_name="openai",
        model_id="fake-story-planner",
        quality_grader_model=None,
        repeats=1,
        checkpoint_path=None,
        resume=False,
    )

    assert report["metrics"]["semantic_valid_trial_count"] == 0
    assert report["metrics"]["usage_total"] == {"requests": 2, "total_tokens": 246}


def test_prompt_version_is_frozen_in_benchmark_fingerprint(tmp_path: Path) -> None:
    baseline = run_suite(
        suite_kind="regression",
        mode="fake",
        provider_name="openai",
        model_id="fake-story-planner",
        quality_grader_model=None,
        repeats=1,
        checkpoint_path=tmp_path / "v3.json",
        resume=False,
        prompt_version="story-planner-v3",
    )
    candidate = run_suite(
        suite_kind="regression",
        mode="fake",
        provider_name="openai",
        model_id="fake-story-planner",
        quality_grader_model=None,
        repeats=1,
        checkpoint_path=tmp_path / "v5.json",
        resume=False,
        prompt_version="story-planner-v5",
    )
    assert baseline["fingerprint"] != candidate["fingerprint"]
    assert candidate["frozen"]["prompt"] == "story-planner-v5"


def test_resume_rejects_incompatible_fingerprint(tmp_path: Path) -> None:
    checkpoint = tmp_path / "partial.json"
    run_suite(
        suite_kind="regression",
        mode="fake",
        provider_name="openai",
        model_id="fake-story-planner",
        quality_grader_model=None,
        repeats=1,
        checkpoint_path=checkpoint,
        resume=False,
    )
    with pytest.raises(ValueError, match="fingerprint"):
        run_suite(
            suite_kind="safety",
            mode="fake",
            provider_name="openai",
            model_id="fake-story-planner",
            quality_grader_model=None,
            repeats=1,
            checkpoint_path=checkpoint,
            resume=True,
        )


def test_live_capability_requires_pro_models_and_three_trials() -> None:
    with pytest.raises(ValueError, match="exactly 3"):
        run_suite(
            suite_kind="capability",
            mode="live",
            provider_name="openai",
            model_id="gpt-standard",
            quality_grader_model="gpt-standard",
            repeats=1,
            checkpoint_path=None,
            resume=False,
        )

    with pytest.raises(ValueError, match="exact Pro model IDs"):
        run_suite(
            suite_kind="capability",
            mode="live",
            provider_name="deepseek",
            model_id="deepseek-v4-pro-preview",
            quality_grader_model="deepseek-v4-pro-preview",
            repeats=3,
            checkpoint_path=None,
            resume=False,
        )


def test_audited_invariant_rejects_hidden_or_empty_evidence() -> None:
    bundle = validate_suite()["planner_inputs"]["linear_mystery__basic"]
    with pytest.raises(ValueError, match="hidden oracle"):
        novel_plan_eval._validate_audited_invariant(
            {"kind": "purpose_present", "expectation_class": "capability"},
            bundle,
            "hidden",
        )
    with pytest.raises(ValueError, match="does not resolve"):
        novel_plan_eval._validate_audited_invariant(
            {
                "kind": "purpose_present",
                "expectation_class": "capability",
                "input_evidence_paths": ["/missing"],
            },
            bundle,
            "missing",
        )


def test_live_g3_receives_the_model_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    validated = validate_suite()
    first_task_id = validated["suite"]["tasks"][0]["task_id"]
    reference = validated["references"][first_task_id]
    model_candidate = {**reference, "chapters": [dict(reference["chapters"][0])]}
    model_candidate["chapters"][0]["title"] = "MODEL OUTPUT"
    observed: list[dict[str, object]] = []

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        novel_plan_eval,
        "_provider",
        lambda *_args: novel_plan_eval.FrozenProvider(model_candidate),
    )
    monkeypatch.setattr(
        novel_plan_eval,
        "_live_quality_scores",
        lambda _provider, _model, candidate: (
            observed.append(candidate)
            or {
                "scores": novel_plan_eval._quality_scores(True),
                "usage": {},
                "latency_ms": 0.0,
            }
        ),
    )

    report = run_suite(
        suite_kind="regression",
        mode="live",
        provider_name="openai",
        model_id="test-model",
        quality_grader_model="test-grader",
        repeats=1,
        checkpoint_path=None,
        resume=False,
    )

    assert report["status"] == "passed"
    assert observed
    assert all(item["chapters"][0]["title"] == "MODEL OUTPUT" for item in observed)


def test_report_usage_includes_rejected_rounds_and_g3_excludes_ungraded_trials() -> None:
    trials = [
        {
            "rounds": [{"usage": {"requests": 1, "total_tokens": 10}}],
            "graders": {
                "g3_scores": novel_plan_eval._quality_scores(False),
                "g3_usage": {},
            },
        },
        {
            "rounds": [{"usage": {"requests": 1, "total_tokens": 20}}],
            "graders": {
                "g3_scores": novel_plan_eval._quality_scores(True),
                "g3_usage": {"requests": 1},
            },
        },
    ]

    assert novel_plan_eval._usage_total(trials) == {"requests": 2, "total_tokens": 30}
    distribution = novel_plan_eval._g3_distribution(trials)
    assert distribution["graded_trial_count"] == 1
    assert distribution["count"] == 6.0
    assert distribution["mean"] == 1.0


def test_report_does_not_count_production_rejection_as_g2_failure() -> None:
    report = novel_plan_eval._report(
        {"suite": {"tasks": []}, "suite_kind": "regression"},
        "a" * 64,
        "regression",
        [
            {
                "task_id": "rejected",
                "trial_index": 1,
                "passed": False,
                "contract_valid": True,
                "semantic_valid": False,
                "reason_code": "compiler_story_plan_reference_invalid",
                "outcome_failures": ["candidate_missing"],
                "rounds": [],
                "graders": {},
            }
        ],
    )
    assert report["metrics"]["production_rejections"] == {
        "compiler_story_plan_reference_invalid": 1
    }
    assert report["metrics"]["g2_outcome_failures"] == {}


def test_report_does_not_classify_infrastructure_failure_as_ok_or_production_rejection() -> None:
    report = novel_plan_eval._report(
        {"suite": {"tasks": []}, "suite_kind": "capability"},
        "a" * 64,
        "capability",
        [
            {
                "task_id": "provider_failure",
                "trial_index": 1,
                "passed": False,
                "infrastructure_failure": {"type": "APIConnectionError"},
                "rounds": [],
                "graders": {},
            }
        ],
    )

    assert report["metrics"]["failure_rates"] == {"infrastructure_failure": 1}
    assert report["metrics"]["infrastructure_failures_by_type"] == {"APIConnectionError": 1}
    assert report["metrics"]["production_rejections"] == {}
