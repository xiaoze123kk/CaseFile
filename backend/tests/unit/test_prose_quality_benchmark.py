"""N4.5 B3 public Quality development suite tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from casefile.agent_runtime.prose_quality_critic import FakeProseQualityCriticProvider
from casefile.benchmark.prose_quality_eval import (
    DEFAULT_ATTESTATION,
    DEFAULT_PRIVATE_QUALIFICATION_SUITE,
    DEFAULT_QUALIFICATION_DESCRIPTOR,
    DEFAULT_SUITE,
    POLISHER_QUALIFICATION_GATES,
    QUALITY_FOCI,
    QUALITY_QUALIFICATION_GATES,
    ProseQualityQualificationBlocked,
    ProseQualitySuiteError,
    canonical_hash,
    load_prose_quality_dev_suite,
    load_prose_quality_qualification_suite,
    run_prose_quality_development_baseline,
)
from casefile.domain.narrative_compiler import QUALITY_DIMENSIONS

ROOT = Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "fixtures/prose_quality_benchmark/v2/generate.py"


@pytest.fixture(scope="module")
def loaded_suite() -> dict[str, Any]:
    return load_prose_quality_dev_suite()


@pytest.fixture(scope="module")
def fake_report() -> dict[str, Any]:
    return run_prose_quality_development_baseline()


def test_suite_freezes_eight_semantically_valid_pairs(
    loaded_suite: dict[str, Any],
) -> None:
    tasks = loaded_suite["tasks"]
    assert len(tasks) == 8
    assert {
        preference: sum(
            task["asset"]["gold"]["overall_preference"] == preference
            for task in tasks
        )
        for preference in ("a", "b", "tie")
    } == {"a": 2, "b": 4, "tie": 2}
    for task in tasks:
        asset = task["asset"]
        assert asset["render_a"]["stage"] == "writer"
        assert asset["render_b"]["stage"] == "polished"
        assert asset["render_b"]["previous_render_hash"] == canonical_hash(
            asset["render_a"]
        )
        assert [
            item["dimension"] for item in asset["gold"]["dimension_preferences"]
        ] == list(QUALITY_DIMENSIONS)


def test_generator_rebuilds_all_public_assets_without_writing() -> None:
    spec = importlib.util.spec_from_file_location("quality_fixture_generator", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)
    spec.loader.exec_module(module)
    suite, attestation, assets = module.build_suite()
    assert suite == json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    assert attestation == json.loads(DEFAULT_ATTESTATION.read_text(encoding="utf-8"))
    for task_id, asset in assets.items():
        path = ROOT / f"fixtures/prose_quality_benchmark/v1/tasks/{task_id}.json"
        assert asset == json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("target", ("suite", "attestation", "descriptor", "asset"))
def test_suite_and_assets_fail_closed_on_hash_drift(
    tmp_path: Path, target: str
) -> None:
    suite = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    attestation = json.loads(DEFAULT_ATTESTATION.read_text(encoding="utf-8"))
    if target == "suite":
        suite["suite_id"] += "-drift"
    elif target == "attestation":
        attestation["qualification"] = True
    elif target == "descriptor":
        suite["tasks"][0]["focus"] += "-drift"
    else:
        suite["tasks"][0]["task_asset"]["hash"] = "0" * 64
    suite_path = tmp_path / "suite.json"
    attestation_path = tmp_path / "attestation.json"
    suite_path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
    attestation_path.write_text(
        json.dumps(attestation, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(ProseQualitySuiteError):
        load_prose_quality_dev_suite(suite_path, attestation_path)


def test_fake_runner_scores_gold_and_position_symmetry(
    fake_report: dict[str, Any],
) -> None:
    assert fake_report["status"] == "completed"
    assert fake_report["task_count"] == fake_report["completed_task_count"] == 8
    assert fake_report["overall_accuracy"] == {"passed": 8, "total": 8}
    assert fake_report["mirrored_consistency"] == {"passed": 8, "total": 8}
    assert fake_report["dimension_accuracy"] == {"passed": 40, "total": 40}
    assert fake_report["model_call_count"] == 16
    assert fake_report["development_gate_passed"] is True
    assert fake_report["qualified"] is False
    assert fake_report["qualification_eligible"] is False
    assert fake_report["report_hash"] == canonical_hash(
        {key: value for key, value in fake_report.items() if key != "report_hash"}
    )


def test_gold_accuracy_and_mirrored_consistency_are_orthogonal(
    loaded_suite: dict[str, Any],
) -> None:
    first_id = loaded_suite["tasks"][0]["asset"]["task_id"]

    def factory(task: dict[str, Any]) -> FakeProseQualityCriticProvider:
        gold = task["asset"]["gold"]
        first = {
            "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
            **gold,
        }
        swap = {"a": "b", "b": "a", "tie": "tie"}
        second = {
            "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
            "overall_preference": swap[gold["overall_preference"]],
            "dimension_preferences": [
                {
                    "dimension": item["dimension"],
                    "preference": swap[item["preference"]],
                }
                for item in gold["dimension_preferences"]
            ],
        }
        if task["asset"]["task_id"] == first_id:
            second = first
        return FakeProseQualityCriticProvider(pairwise_candidates=(first, second))

    report = run_prose_quality_development_baseline(provider_factory=factory)
    assert report["overall_accuracy"] == {"passed": 8, "total": 8}
    assert report["dimension_accuracy"] == {"passed": 40, "total": 40}
    assert report["mirrored_consistency"] == {"passed": 7, "total": 8}
    assert report["development_gate_passed"] is False


def test_failures_keep_fixed_denominator(loaded_suite: dict[str, Any]) -> None:
    first_id = loaded_suite["tasks"][0]["asset"]["task_id"]

    def factory(task: dict[str, Any]) -> FakeProseQualityCriticProvider:
        if task["asset"]["task_id"] == first_id:
            return FakeProseQualityCriticProvider(failure_at_call=1)
        gold = task["asset"]["gold"]
        swap = {"a": "b", "b": "a", "tie": "tie"}
        reversed_gold = {
            "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
            "overall_preference": swap[gold["overall_preference"]],
            "dimension_preferences": [
                {
                    "dimension": item["dimension"],
                    "preference": swap[item["preference"]],
                }
                for item in gold["dimension_preferences"]
            ],
        }
        return FakeProseQualityCriticProvider(
            pairwise_candidates=(
                {
                    "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
                    **gold,
                },
                reversed_gold,
            )
        )

    report = run_prose_quality_development_baseline(provider_factory=factory)
    assert report["status"] == "inconclusive"
    assert report["task_count"] == report["completed_task_count"] == 8
    assert report["overall_accuracy"]["total"] == 8
    assert report["dimension_accuracy"]["total"] == 40
    assert report["failure_counts"] == {"protocol": 0, "infrastructure": 1}


def test_report_is_hash_only_and_redacts_prose(
    fake_report: dict[str, Any], loaded_suite: dict[str, Any]
) -> None:
    serialized = json.dumps(fake_report, ensure_ascii=False)
    first_text = loaded_suite["tasks"][0]["asset"]["render_a"]["blocks"][0]["text"]
    for forbidden in (
        first_text,
        "semantic_consensus_a",
        "semantic_consensus_b",
        "request_payload",
        "raw_response",
        "api_key",
        "Authorization",
    ):
        assert forbidden not in serialized


def test_runner_writes_self_hashed_report(tmp_path: Path) -> None:
    report = run_prose_quality_development_baseline(output_dir=tmp_path)
    written = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert written == report
    assert written["report_hash"] == canonical_hash(
        {key: value for key, value in written.items() if key != "report_hash"}
    )


def test_qualification_descriptor_freezes_private_cohorts_and_review() -> None:
    descriptor = json.loads(
        DEFAULT_QUALIFICATION_DESCRIPTOR.read_text(encoding="utf-8")
    )
    assert descriptor["quality_holdout_count"] == 16
    assert descriptor["polisher_task_count"] == 24
    assert descriptor["quality_focus_distribution"] == {
        focus: 2 for focus in QUALITY_FOCI
    }
    assert descriptor["polisher_focus_distribution"] == {
        focus: 3 for focus in QUALITY_FOCI
    }
    assert descriptor["quality_gate_thresholds"] == QUALITY_QUALIFICATION_GATES
    assert descriptor["polisher_gate_thresholds"] == POLISHER_QUALIFICATION_GATES
    assert descriptor["review_policy"] == "codex-owner-accepted-review-v1"
    assert descriptor["review_status"] == "codex_reviewed"
    assert descriptor["qualification_eligible"] is True
    assert descriptor["descriptor_hash"] == canonical_hash(
        {key: value for key, value in descriptor.items() if key != "descriptor_hash"}
    )


def test_unreviewed_qualification_blocks_before_private_package_read(
    tmp_path: Path,
) -> None:
    descriptor = json.loads(
        DEFAULT_QUALIFICATION_DESCRIPTOR.read_text(encoding="utf-8")
    )
    descriptor["review_status"] = "pending_codex_review"
    descriptor["qualification_eligible"] = False
    descriptor["descriptor_hash"] = canonical_hash(
        {key: value for key, value in descriptor.items() if key != "descriptor_hash"}
    )
    path = tmp_path / "descriptor.json"
    path.write_text(json.dumps(descriptor, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(
        ProseQualityQualificationBlocked,
        match="prose_quality_qualification_review_pending",
    ):
        load_prose_quality_qualification_suite(tmp_path / "missing.json", path)


def test_qualification_rejects_nonprivate_suite_path() -> None:
    with pytest.raises(
        ProseQualitySuiteError,
        match="prose_quality_qualification_private_path_invalid",
    ):
        load_prose_quality_qualification_suite(DEFAULT_SUITE)


@pytest.mark.skipif(
    not DEFAULT_PRIVATE_QUALIFICATION_SUITE.is_file(),
    reason="private B3 qualification package is local-only",
)
def test_local_private_qualification_package_is_fully_reviewed_and_valid() -> None:
    loaded = load_prose_quality_qualification_suite()
    assert len(loaded["quality_tasks"]) == 16
    assert len(loaded["polisher_tasks"]) == 24
    assert loaded["reviewer"]["reviewer"] == "Codex"
    assert loaded["reviewer"]["reviewer_independence"] is False
    assert loaded["reviewer"]["unresolved_findings"] == []
