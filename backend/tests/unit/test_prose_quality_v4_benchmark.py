"""B3 v4 public development and private qualification boundary tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from casefile.agent_runtime.prose_judge import FakeProseJudgeProvider
from casefile.agent_runtime.prose_patch_polisher import FakeProsePatchPolisherProvider
from casefile.agent_runtime.prose_quality_assessor import (
    FakeProseQualityAssessmentProvider,
)
from casefile.benchmark import prose_quality_v4_qualification as qualification
from casefile.benchmark.prose_quality_v4_eval import (
    POLISHER_V4_QUALIFICATION_GATES,
    QUALITY_V4_QUALIFICATION_GATES,
    load_prose_quality_v4_dev_suite,
    load_prose_quality_v4_qualification_suite,
    run_prose_quality_v4_dev_suite,
)


def test_public_v4_suite_runs_all_pointwise_and_delta_gold() -> None:
    package = load_prose_quality_v4_dev_suite()
    report = run_prose_quality_v4_dev_suite()
    assert len(package["tasks"]) == 8
    assert report["task_count"] == 8
    assert report["assessment_count"] == 16
    assert report["severity_exact"] == {"correct": 80, "total": 80}
    assert report["delta_accuracy"] == {"correct": 8, "total": 8}
    assert report["development_passed"] is True
    assert report["qualified"] is False
    serialized = json.dumps(report, ensure_ascii=False)
    for task in package["tasks"]:
        for block in task["asset"]["render_a"]["blocks"]:
            assert block["text"] not in serialized


def test_private_v4_package_is_fresh_reviewed_and_frozen() -> None:
    package = load_prose_quality_v4_qualification_suite()
    assert len(package["quality_tasks"]) == 16
    assert len(package["polisher_tasks"]) == 24
    assert sum(item["asset"]["metamorphic_neutral"] for item in package["quality_tasks"]) == 4
    assert package["descriptor"]["prior_quality_fingerprint_overlap"] == 0
    assert package["descriptor"]["prior_polisher_fingerprint_overlap"] == 0
    assert package["descriptor"]["quality_gate_thresholds"] == QUALITY_V4_QUALIFICATION_GATES
    assert package["descriptor"]["polisher_gate_thresholds"] == POLISHER_V4_QUALIFICATION_GATES


def _quality_rows() -> list[dict[str, Any]]:
    return [
        {
            "attempted": True,
            "status": "completed",
            "severity_correct": 9,
            "delta_correct": index != 0,
            "five_dimension_coverage": True,
            "evidence_valid": True,
            "metamorphic_stable": index < 4,
            "calls": [],
        }
        for index in range(16)
    ]


def _polisher_rows() -> list[dict[str, Any]]:
    return [
        {
            "attempted": True,
            "status": "completed",
            "patch_contract_valid": True,
            "outside_window_exact": True,
            "preservation_passed": True,
            "quality_non_loss": True,
            "polished_accepted": index < 18,
            "rejected_polish": index >= 18,
            "exact_original_rollback": index >= 18,
            "critical_semantic_regression_check_ids": [],
            "calls": [],
        }
        for index in range(24)
    ]


def test_v4_qualification_gate_boundaries_are_exact() -> None:
    source = {
        "revision": "a" * 40,
        "branch": "codex/n4.5-b3-v4",
        "clean": True,
        "tracked_source_hash": "b" * 64,
    }
    manifest = {
        "source_before": source,
        "descriptor_hash": "c" * 64,
        "private_suite_hash": "d" * 64,
        "quality_provider_adapter": "DeepSeekProseQualityAssessmentProvider",
        "polisher_provider_adapter": "DeepSeekProsePatchPolisherProvider",
        "judge_provider_adapter": "DeepSeekProseJudgeProvider",
    }
    report = qualification._build_report(
        manifest, _quality_rows(), _polisher_rows(), source, True
    )
    assert report["severity_exact"] == {"passed": 144, "total": 160}
    assert report["delta_accuracy"] == {"passed": 15, "total": 16}
    assert report["qualified"] is True
    rows = _quality_rows()
    rows[0]["severity_correct"] = 8
    failed = qualification._build_report(
        manifest, rows, _polisher_rows(), source, True
    )
    assert failed["qualified"] is False
    assert failed["gates"]["severity_exact"] is False


def test_v4_attempt_reuse_fails_before_loading_private_data(tmp_path: Path) -> None:
    output = tmp_path / "attempt"
    output.mkdir()
    (output / "attempt-manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(
        qualification.ProseQualityV4QualificationError,
        match="prose_quality_v4_qualification_attempt_already_exists",
    ):
        qualification.run_prose_quality_v4_qualification(
            attempt_id="duplicate",
            api_key="fake",
            assessment_provider=FakeProseQualityAssessmentProvider(),
            polisher_provider=FakeProsePatchPolisherProvider(),
            judge_provider=FakeProseJudgeProvider(),
            output_dir=output,
        )
