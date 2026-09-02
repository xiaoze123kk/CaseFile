"""N4.5 B2 Rewrite development and qualification suite tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from casefile.agent_runtime.prose_judge import FakeProseJudgeProvider
from casefile.agent_runtime.prose_rewriter import FakeProseRewriterProvider
from casefile.benchmark.prose_rewrite_eval import (
    DEFAULT_ATTESTATION,
    DEFAULT_QUALIFICATION_DESCRIPTOR,
    DEFAULT_SUITE,
    FAMILIES,
    VARIANTS,
    ProseRewriteQualificationBlocked,
    ProseRewriteSuiteError,
    canonical_hash,
    load_prose_rewrite_dev_suite,
    load_prose_rewrite_qualification_suite,
    run_prose_rewrite_development_baseline,
)

ROOT = Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "fixtures/prose_rewrite_benchmark/v1/generate.py"


@pytest.fixture(scope="module")
def loaded_suite() -> dict[str, Any]:
    return load_prose_rewrite_dev_suite()


@pytest.fixture(scope="module")
def fake_report() -> dict[str, Any]:
    return run_prose_rewrite_development_baseline()


def test_suite_is_exact_8x3_bad_render_matrix(loaded_suite: dict[str, Any]) -> None:
    tasks = loaded_suite["tasks"]
    assert len(tasks) == 24
    assert {
        (task["descriptor"]["defect_family"], task["descriptor"]["variant"])
        for task in tasks
    } == {(family, variant) for family in FAMILIES for variant in VARIANTS}
    assert len({task["descriptor"]["input_fingerprint"] for task in tasks}) == 24
    assert len({canonical_hash(task["asset"]["initial_render"]) for task in tasks}) == 24
    assert sum(task["descriptor"]["expected_rescue_round"] == 1 for task in tasks) == 16
    assert sum(task["descriptor"]["expected_rescue_round"] == 2 for task in tasks) == 8


def test_each_task_binds_failed_consensus_and_previous_passes(
    loaded_suite: dict[str, Any],
) -> None:
    for task in loaded_suite["tasks"]:
        asset = task["asset"]
        consensus = asset["initial_consensus"]
        final_by_id = {
            item["check_id"]: item["final_verdict"] for item in consensus["checks"]
        }
        assert consensus["scene_verdict"] == "fail"
        assert asset["original_issue_check_ids"]
        assert all(final_by_id[item] == "fail" for item in asset["original_issue_check_ids"])
        assert all(final_by_id[item] == "pass" for item in asset["initial_passed_check_ids"])
        assert set(asset["original_issue_check_ids"]).isdisjoint(
            asset["initial_passed_check_ids"]
        )
        assert asset["initial_render"]["stage"] == "writer"


@pytest.mark.parametrize("target", ("suite", "attestation", "descriptor", "asset"))
def test_suite_attestation_and_every_asset_are_hash_bound(
    tmp_path: Path, target: str
) -> None:
    suite = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    attestation = json.loads(DEFAULT_ATTESTATION.read_text(encoding="utf-8"))
    if target == "suite":
        suite["suite_id"] += "-drift"
    elif target == "attestation":
        attestation["qualification"] = True
    elif target == "descriptor":
        suite["tasks"][0]["scene_id"] = "scene_drift"
    else:
        suite["tasks"][0]["task_asset"]["hash"] = "0" * 64
    suite_path = tmp_path / "suite.json"
    attestation_path = tmp_path / "attestation.json"
    suite_path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
    attestation_path.write_text(
        json.dumps(attestation, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(ProseRewriteSuiteError):
        load_prose_rewrite_dev_suite(suite_path, attestation_path)


def test_development_suite_rejects_private_asset_reference(tmp_path: Path) -> None:
    suite = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    suite["tasks"][0]["task_asset"]["path"] = (
        "backend/var/benchmark/private/prose-rewrite/leak.json"
    )
    suite["tasks"][0]["content_hash"] = canonical_hash(
        {
            key: value
            for key, value in suite["tasks"][0].items()
            if key != "content_hash"
        }
    )
    suite["suite_hash"] = canonical_hash(
        {key: value for key, value in suite.items() if key != "suite_hash"}
    )
    attestation = json.loads(DEFAULT_ATTESTATION.read_text(encoding="utf-8"))
    attestation["suite_hash"] = suite["suite_hash"]
    attestation["attestation_hash"] = canonical_hash(
        {key: value for key, value in attestation.items() if key != "attestation_hash"}
    )
    suite_path = tmp_path / "suite.json"
    attestation_path = tmp_path / "attestation.json"
    suite_path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
    attestation_path.write_text(
        json.dumps(attestation, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(ProseRewriteSuiteError, match="task_asset_path_invalid"):
        load_prose_rewrite_dev_suite(suite_path, attestation_path)


def test_generator_rebuilds_all_public_assets_without_writing() -> None:
    spec = importlib.util.spec_from_file_location("prose_rewrite_fixture_generator", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)
    spec.loader.exec_module(module)
    suite, attestation, assets = module.build_suite()
    assert suite == json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    assert attestation == json.loads(DEFAULT_ATTESTATION.read_text(encoding="utf-8"))
    assert len(assets) == 24
    for task_id, asset in assets.items():
        path = ROOT / f"fixtures/prose_rewrite_benchmark/v1/tasks/{task_id}.json"
        assert asset == json.loads(path.read_text(encoding="utf-8"))


def test_fake_baseline_reports_round_rescue_and_hard_gates(
    fake_report: dict[str, Any],
) -> None:
    assert fake_report["status"] == "completed"
    assert fake_report["task_count"] == fake_report["completed_task_count"] == 24
    assert fake_report["round_one_rescue"] == {"passed": 16, "total": 24}
    assert fake_report["round_two_incremental_rescue"] == {"passed": 8, "total": 24}
    assert fake_report["final_rescue"] == {"passed": 24, "total": 24}
    assert fake_report["preservation_tasks"] == {"passed": 24, "total": 24}
    assert fake_report["new_critical_issue_count"] == 0
    assert fake_report["extra_rewrite_call_count"] == 0
    assert fake_report["failure_counts"] == {
        "semantic": 0,
        "protocol": 0,
        "infrastructure": 0,
    }
    assert fake_report["model_call_count"] == 88
    assert fake_report["development_gate_passed"] is True
    assert fake_report["qualified"] is False
    assert fake_report["qualification_eligible"] is False
    assert fake_report["report_hash"] == canonical_hash(
        {key: value for key, value in fake_report.items() if key != "report_hash"}
    )


def test_report_is_hash_only_and_contains_no_private_or_model_prose(
    fake_report: dict[str, Any], loaded_suite: dict[str, Any]
) -> None:
    serialized = json.dumps(fake_report, ensure_ascii=False)
    first_text = loaded_suite["tasks"][0]["asset"]["initial_render"]["blocks"][0]["text"]
    for forbidden in (
        first_text,
        "initial_judge_report",
        "initial_consensus",
        "fake_rewrite_candidates",
        "request_payload",
        "raw_response",
        "api_key",
        "Authorization",
    ):
        assert forbidden not in serialized


def test_semantic_protocol_and_infrastructure_failures_keep_denominator(
    loaded_suite: dict[str, Any],
) -> None:
    tasks = loaded_suite["tasks"]
    ids = [task["descriptor"]["task_id"] for task in tasks]

    def rewriter_factory(task: dict[str, Any]) -> FakeProseRewriterProvider:
        task_id = task["descriptor"]["task_id"]
        if task_id == ids[0]:
            return FakeProseRewriterProvider(failure_at_call=1)
        if task_id == ids[1]:
            return FakeProseRewriterProvider(
                candidates=({"schema_id": "invalid", "blocks": []},)
            )
        if task_id == ids[4]:
            candidate = {
                "schema_id": "compiler.scene-render-candidate.v1",
                "blocks": [
                    {"text": block["text"]}
                    for block in task["asset"]["initial_render"]["blocks"]
                ],
            }
            return FakeProseRewriterProvider(candidates=(candidate, candidate))
        return FakeProseRewriterProvider(
            candidates=tuple(task["asset"]["fake_rewrite_candidates"])
        )

    def judge_factory(task: dict[str, Any]) -> FakeProseJudgeProvider:
        task_id = task["descriptor"]["task_id"]
        if task_id == ids[2]:
            return FakeProseJudgeProvider(failure_at_call=1)
        if task_id == ids[3]:
            return FakeProseJudgeProvider(judge_reports=({},))
        if task_id == ids[4]:
            initial = task["judge_candidates"][0]
            return FakeProseJudgeProvider(judge_reports=(initial, initial, initial))
        return FakeProseJudgeProvider(judge_reports=tuple(task["judge_candidates"]))

    report = run_prose_rewrite_development_baseline(
        rewriter_provider_factory=rewriter_factory,
        judge_provider_factory=judge_factory,
    )
    assert report["status"] == "inconclusive"
    assert report["completed_task_count"] == 24
    assert report["failure_counts"] == {
        "semantic": 1,
        "protocol": 2,
        "infrastructure": 2,
    }
    assert len(report["rows"]) == 24
    assert report["qualified"] is False


def test_unreviewed_qualification_is_blocked_before_package_read_or_provider_call(
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
    descriptor_path = tmp_path / "descriptor.json"
    descriptor_path.write_text(
        json.dumps(descriptor, ensure_ascii=False), encoding="utf-8"
    )
    missing_private_suite = tmp_path / "missing-suite.json"
    with pytest.raises(
        ProseRewriteQualificationBlocked,
        match="qualification_review_pending",
    ):
        load_prose_rewrite_qualification_suite(
            missing_private_suite, descriptor_path
        )


def test_qualification_descriptor_is_self_hashed_and_fail_closed(tmp_path: Path) -> None:
    descriptor = json.loads(
        DEFAULT_QUALIFICATION_DESCRIPTOR.read_text(encoding="utf-8")
    )
    assert descriptor["task_count"] == 24
    assert descriptor["review_policy"] == "codex-owner-accepted-review-v1"
    assert descriptor["review_status"] == "codex_reviewed"
    assert descriptor["qualification_eligible"] is True
    assert descriptor["public_development_suite_hash"] == json.loads(
        DEFAULT_SUITE.read_text(encoding="utf-8")
    )["suite_hash"]
    assert descriptor["descriptor_hash"] == canonical_hash(
        {key: value for key, value in descriptor.items() if key != "descriptor_hash"}
    )
    descriptor["qualification_eligible"] = False
    path = tmp_path / "descriptor.json"
    path.write_text(json.dumps(descriptor, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ProseRewriteSuiteError, match="descriptor_hash_invalid"):
        load_prose_rewrite_qualification_suite(tmp_path / "missing.json", path)
