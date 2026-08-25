"""Novel Plan benchmark contract and deterministic safety gates."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from casefile.benchmark import novel_plan_eval
from casefile.benchmark.novel_plan_eval import run_suite, validate_suite


def test_capability_suite_is_exact_24_task_matrix_with_valid_references() -> None:
    validated = validate_suite()
    assert len(validated["suite"]["tasks"]) == 24
    assert len(set(validated["reference_hashes"].values())) >= 8
    assert len({task["planner_input_hash"] for task in validated["suite"]["tasks"]}) >= 6


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
