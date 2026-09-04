"""Pacing-only two-call experiment, source isolation and regression gates."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_quality_critic import execute_mirrored_pairwise_quality
from casefile.benchmark.prose_quality_diagnostic import (
    DiagnosticFakeProvider,
    run_quality_diagnostic,
)
from casefile.benchmark.prose_quality_pacing import (
    PACING_DIMENSION,
    PACING_ROOT,
    PACING_TASK_IDS,
    load_pacing_experiment,
    load_pacing_package,
    pacing_comparison,
    pacing_policy,
)


def test_new_fixtures_are_reproducible_reviewed_and_position_balanced() -> None:
    spec = importlib.util.spec_from_file_location("pacing_generator", PACING_ROOT / "generate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generated = module.build_assets()
    for asset in generated:
        assert asset == json.loads(
            (PACING_ROOT / "tasks" / f"{asset['task_id']}.json").read_text(encoding="utf-8")
        )
        assert asset["review"]["reviewer_independence"] is False
        assert asset["review"]["semantic_origin"] == "reviewed_synthetic_fixture_not_live_council"
    assert [asset["gold"]["overall_preference"] for asset in generated] == ["a", "b", "b", "a"]
    for asset in generated:
        a, b = (asset[f"render_{side}"]["character_count"] for side in ("a", "b"))
        winning = asset["gold"]["overall_preference"]
        longer = "a" if a > b else "b"
        assert (winning == longer) == (asset["group"] == "functional")


def test_pacing_prompt_only_appends_rubric_and_preserves_baseline_wire_hashes() -> None:
    baseline_prompt = load_prompt("prose_quality_pairwise", "prose-quality-pairwise-v1")
    candidate_prompt = load_prompt("prose_quality_pairwise", "prose-quality-pairwise-v4")
    assert candidate_prompt.system_prompt.startswith(baseline_prompt.system_prompt)
    assert load_prompt("prose_quality_pairwise").version == baseline_prompt.version
    for forbidden in ("林岚", "沈舟", "tradeoff_tie", "Gold", "档案室", "渡口"):
        assert forbidden not in candidate_prompt.system_prompt
    asset = load_pacing_package()["tasks"][0]["asset"]
    observed = []

    class Provider(DiagnosticFakeProvider):
        def assess_quality(self, request: Any) -> Any:
            observed.append(request)
            return super().assess_quality(request)

    for policy in (None, pacing_policy()):
        result = execute_mirrored_pairwise_quality(
            Provider(asset["gold"]),
            checklist=asset["checklist"],
            original_render=asset["render_a"],
            polished_render=asset["render_b"],
            profile=asset["profile"],
            preservation_consensus=asset["semantic_consensus_b"],
            model_id="deepseek-v4-flash",
            api_key="fake",
            pairwise_policy=policy,
        )
        assert result.status == "completed" and len(result.calls) == 2
    assert [r.request_fingerprint for r in observed[:2]] == [
        "567c9f868a16cf3bbb6a7f461839ff01cbe0d50cd380bab174574afdc11bf6c4",
        "e2d331002e46ed800680edda1c74b85732fd75b3a022b0306e8c52cbd9dcd0ca",
    ]
    for baseline, candidate in zip(observed[:2], observed[2:], strict=True):
        assert baseline.input_payload["untrusted_data"] == candidate.input_payload["untrusted_data"]
        assert baseline.position_mapping == candidate.position_mapping
        assert baseline.request_fingerprint != candidate.request_fingerprint
        assert baseline.temperature == candidate.temperature == 0
        assert baseline.candidate_schema == candidate.candidate_schema is None


@pytest.fixture(scope="module")
def fake_report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return run_quality_diagnostic(
        attempt_id="pacing-test",
        experiment="pacing-v1",
        output_dir=tmp_path_factory.mktemp("pacing") / "attempt",
    )


def test_frozen_schedule_denominators_and_exact_two_call_arms(fake_report: dict[str, Any]) -> None:
    descriptor, package = load_pacing_experiment()
    assert len(package["tasks"]) == 12 and len(descriptor["schedule"]) == 72
    assert descriptor["call_budget"] == {"baseline": 72, "candidate": 72, "total": 144}
    assert fake_report["status"] == "completed"
    for arm in ("baseline", "candidate"):
        assert fake_report["arms"][arm]["call_count"] == 72
        assert fake_report["arms"][arm]["first_accuracy"] == {"passed": 36, "total": 36}
        assert fake_report["arms"][arm]["first_dimension_accuracy"] == {"passed": 180, "total": 180}
        assert fake_report["cohorts"]["legacy"][arm]["first_accuracy"]["total"] == 24
        assert fake_report["cohorts"]["functional"][arm]["first_accuracy"]["total"] == 6
    assert all(row["call_count"] == 2 for row in fake_report["rows"])
    assert not fake_report["shared_candidate_assessments"]
    assert not fake_report["comparison"]["worth_further_validation"]
    assert not fake_report["qualified"]


def improved_rows(fake_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = copy.deepcopy(fake_report["rows"])
    for row in rows:
        if row["arm"] == "baseline" and row["cohort"] == "redundant":
            row["first_dimension_results"][PACING_DIMENSION] = False
            row["reverse_dimension_results"][PACING_DIMENSION] = False
    return rows


def test_gate_requires_new_improvement_without_length_bias_or_legacy_loss(
    fake_report: dict[str, Any],
) -> None:
    rows = improved_rows(fake_report)
    assert pacing_comparison(rows, complete=True, live=True)["worth_further_validation"]
    for cohort, field, dimension in (
        ("functional", "reverse_dimension_results", PACING_DIMENSION),
        ("legacy", "first_dimension_results", "scene_specificity"),
        ("legacy", "reverse_dimension_results", PACING_DIMENSION),
    ):
        changed = copy.deepcopy(rows)
        row = next(r for r in changed if r["arm"] == "candidate" and r["cohort"] == cohort)
        row[field][dimension] = False
        assert not pacing_comparison(changed, complete=True, live=True)["worth_further_validation"]
    assert not pacing_comparison(rows, complete=False, live=True)["worth_further_validation"]
    assert not pacing_comparison(rows, complete=True, live=False)["worth_further_validation"]


def test_preserves_disputed_legacy_gold_separately() -> None:
    review = json.loads((PACING_ROOT / "gold-review.json").read_text(encoding="utf-8"))
    assert review["legacy_review"][1]["status"] == "overall_tradeoff_disputed"
    package = load_pacing_package()
    task = next(
        t for t in package["tasks"] if t["asset"]["task_id"] == "quality_dev_07_tradeoff_tie"
    )
    assert task["asset"]["gold"]["overall_preference"] == "tie"


def test_modified_review_cannot_silently_change_experiment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from casefile.benchmark import prose_quality_pacing as pacing

    # Change only the attestation reader; source tasks and original Gold remain intact.
    read = pacing._read

    def changed(path: Path) -> dict[str, Any]:
        value = read(path)
        if path.name == "gold-review.json":
            value["reviewer_independence"] = True
        return value

    monkeypatch.setattr(pacing, "_read", changed)
    with pytest.raises(ValueError, match="experiment_drift"):
        pacing.load_pacing_experiment()
    assert len(PACING_TASK_IDS) == 4
