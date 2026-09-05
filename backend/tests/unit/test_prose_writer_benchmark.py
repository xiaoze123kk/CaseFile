"""N4.5-04 public Writer development suite and Fake baseline tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from casefile.agent_runtime.prose_judge import (
    FakeProseJudgeProvider,
    build_server_evidence_catalog,
)
from casefile.agent_runtime.prose_writer import FakeProseWriterProvider
from casefile.benchmark.prose_writer_eval import (
    ABILITIES,
    DEFAULT_ATTESTATION,
    DEFAULT_SUITE,
    VARIANTS,
    ProseWriterSuiteError,
    canonical_hash,
    load_prose_writer_dev_suite,
    run_writer_development_baseline,
)

ROOT = Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "fixtures/prose_writer_benchmark/v1/generate.py"


@pytest.fixture(scope="module")
def loaded_suite() -> dict[str, Any]:
    return load_prose_writer_dev_suite()


@pytest.fixture(scope="module")
def fake_report() -> dict[str, Any]:
    return run_writer_development_baseline()


def test_suite_has_exact_matrix_unique_lineage_and_continuations(
    loaded_suite: dict[str, Any],
) -> None:
    tasks = loaded_suite["tasks"]
    assert len(tasks) == 24
    assert len({task["descriptor"]["input_fingerprint"] for task in tasks}) == 24
    assert {
        (task["descriptor"]["ability"], task["descriptor"]["variant"])
        for task in tasks
    } == {(ability, variant) for ability in ABILITIES for variant in VARIANTS}
    assert sum(task["asset"]["previous_scene_render"] is None for task in tasks) == 8
    assert sum(task["asset"]["previous_scene_render"] is not None for task in tasks) == 16
    assert loaded_suite["attestation"]["reviewer_independence"] is False
    assert loaded_suite["attestation"]["holdout_qualification"] is False


@pytest.mark.parametrize("target", ("suite", "descriptor", "asset", "attestation"))
def test_suite_and_all_reviewed_assets_are_hash_bound(tmp_path: Path, target: str) -> None:
    suite = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    attestation = json.loads(DEFAULT_ATTESTATION.read_text(encoding="utf-8"))
    suite_path = tmp_path / "suite.json"
    attestation_path = tmp_path / "attestation.json"
    if target == "suite":
        suite["suite_id"] += "-drift"
    elif target == "descriptor":
        suite["tasks"][0]["scene_id"] = "scene_drift"
    elif target == "attestation":
        attestation["allowed_use"] = "qualification"
    else:
        binding = suite["tasks"][0]["task_asset"]
        binding["hash"] = "0" * 64
    suite_path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
    attestation_path.write_text(json.dumps(attestation, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ProseWriterSuiteError):
        load_prose_writer_dev_suite(suite_path, attestation_path)


def test_private_or_untracked_path_is_rejected_before_loading(tmp_path: Path) -> None:
    suite = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    suite["tasks"][0]["task_asset"]["path"] = "backend/var/benchmark/private/leak.json"
    suite["tasks"][0]["content_hash"] = canonical_hash(
        {key: value for key, value in suite["tasks"][0].items() if key != "content_hash"}
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
    attestation_path.write_text(json.dumps(attestation, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ProseWriterSuiteError, match="task_asset_path_invalid"):
        load_prose_writer_dev_suite(suite_path, attestation_path)


def test_generator_rebuilds_the_tracked_suite_without_writing() -> None:
    spec = importlib.util.spec_from_file_location("prose_writer_fixture_generator", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)
    spec.loader.exec_module(module)
    suite, attestation, assets = module.build_suite()
    assert suite == json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    assert attestation == json.loads(DEFAULT_ATTESTATION.read_text(encoding="utf-8"))
    assert len(assets) == 24
    for task_id, asset in assets.items():
        path = ROOT / f"fixtures/prose_writer_benchmark/v1/tasks/{task_id}.json"
        assert asset == json.loads(path.read_text(encoding="utf-8"))


def test_fake_baseline_runs_exactly_once_per_task_and_never_qualifies(
    fake_report: dict[str, Any], loaded_suite: dict[str, Any]
) -> None:
    assert fake_report["status"] == "completed"
    assert fake_report["development_baseline"] is True
    assert fake_report["qualified"] is False
    assert fake_report["qualification_eligible"] is False
    assert fake_report["task_count"] == fake_report["completed_task_count"] == 24
    assert fake_report["initial_semantic_pass"] == {"passed": 24, "total": 24}
    assert fake_report["writer_call_count"] == 24
    assert fake_report["judge_call_count"] == 24
    assert fake_report["call_count"] == 48
    assert fake_report["council_policy_id"] == "fidelity-only-v1"
    assert fake_report["failures"] == {
        "semantic": 0,
        "writer_protocol": 0,
        "writer_infrastructure": 0,
        "council_protocol": 0,
        "council_infrastructure": 0,
    }
    expected_checks = sum(len(task["checklist"]["checks"]) for task in loaded_suite["tasks"])
    observed_checks = sum(
        sum(values.values()) for values in fake_report["check_kind_metrics"].values()
    )
    assert observed_checks == expected_checks
    assert all(row["council_status"] == "completed" for row in fake_report["rows"])
    assert fake_report["report_hash"] == canonical_hash(
        {key: value for key, value in fake_report.items() if key != "report_hash"}
    )


def test_report_contains_hashes_not_requests_responses_or_credentials(
    fake_report: dict[str, Any], loaded_suite: dict[str, Any]
) -> None:
    serialized = json.dumps(fake_report, ensure_ascii=False)
    first_text = loaded_suite["tasks"][0]["asset"]["fake_candidate"]["blocks"][0]["text"]
    for forbidden in (
        first_text,
        "fake_candidate",
        "request_payload",
        "raw_response",
        "api_key",
        "Authorization",
    ):
        assert forbidden not in serialized


def _judge_candidate(
    task: dict[str, Any], render: dict[str, Any], *, fail_first: bool
) -> dict[str, Any]:
    catalog = build_server_evidence_catalog(render)
    by_hash = {
        canonical_hash({key: value for key, value in item.items() if key != "evidence_id"}): item[
            "evidence_id"
        ]
        for item in catalog
    }
    assessments = []
    for index, item in enumerate(task["asset"]["gold"]["assessments"]):
        verdict = "fail" if fail_first and index == 0 else item["verdict"]
        evidence_ids = [] if verdict == "fail" else [
            by_hash[canonical_hash(evidence)] for evidence in item["evidence"]
        ]
        assessments.append(
            {
                "check_id": item["check_id"],
                "verdict": verdict,
                "evidence_ids": evidence_ids,
                "rationale": "注入语义失败。" if verdict == "fail" else item["rationale"],
            }
        )
    return {"schema_id": "compiler.prose-judge-candidate.v1", "assessments": assessments}


def test_semantic_protocol_and_infrastructure_failures_are_separate() -> None:
    loaded = load_prose_writer_dev_suite()
    ids = [task["descriptor"]["task_id"] for task in loaded["tasks"]]

    def writer_factory(task: dict[str, Any]) -> FakeProseWriterProvider:
        task_id = task["descriptor"]["task_id"]
        if task_id == ids[0]:
            return FakeProseWriterProvider(failure_at_call=1)
        if task_id == ids[1]:
            return FakeProseWriterProvider(
                candidates=(
                    {"schema_id": "compiler.scene-render-candidate.v1", "blocks": []},
                )
            )
        return FakeProseWriterProvider(candidates=(task["asset"]["fake_candidate"],))

    def judge_factory(
        task: dict[str, Any], render: dict[str, Any]
    ) -> FakeProseJudgeProvider:
        task_id = task["descriptor"]["task_id"]
        if task_id == ids[2]:
            return FakeProseJudgeProvider(failure_at_call=1)
        if task_id == ids[3]:
            return FakeProseJudgeProvider(judge_reports=({},))
        return FakeProseJudgeProvider(
            judge_reports=(
                _judge_candidate(task, render, fail_first=task_id == ids[4]),
            )
        )

    report = run_writer_development_baseline(
        writer_provider_factory=writer_factory,
        judge_provider_factory=judge_factory,
    )
    assert report["status"] == "inconclusive"
    assert report["completed_task_count"] == 24
    assert report["failures"] == {
        "semantic": 1,
        "writer_protocol": 1,
        "writer_infrastructure": 1,
        "council_protocol": 1,
        "council_infrastructure": 1,
    }
    assert report["initial_semantic_pass"] == {"passed": 19, "total": 24}
    assert report["writer_call_count"] == 24
    assert report["judge_call_count"] == 22
