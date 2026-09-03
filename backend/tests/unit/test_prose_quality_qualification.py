"""N4.5 B3 one-shot qualification report and preflight tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from casefile.agent_runtime.prose_judge import FakeProseJudgeProvider
from casefile.agent_runtime.prose_polisher import FakeProsePolisherProvider
from casefile.agent_runtime.prose_quality_critic import FakeProseQualityCriticProvider
from casefile.benchmark.prose_quality_qualification import (
    ProseQualityQualificationError,
    _build_report,
    run_prose_quality_qualification,
)


def _manifest() -> dict[str, object]:
    source = {
        "revision": "a" * 40,
        "branch": "N4.5",
        "clean": True,
        "tracked_source_hash": "b" * 64,
    }
    return {
        "attempt_id": "test",
        "source_before": source,
        "quality_provider_adapter": "DeepSeekProseQualityCriticProvider",
        "polisher_provider_adapter": "DeepSeekProsePolisherProvider",
        "judge_provider_adapter": "DeepSeekProseJudgeProvider",
    }


def _quality_row(index: int) -> dict[str, object]:
    return {
        "task_id": f"quality_{index}",
        "status": "completed",
        "attempted": True,
        "overall_correct": index <= 14,
        "mirrored_consistent": index <= 15,
        "calls": [],
    }


def _polisher_row(index: int) -> dict[str, object]:
    accepted = index <= 18
    return {
        "task_id": f"polisher_{index}",
        "status": "finalized_polished" if accepted else "finalized_original",
        "attempted": True,
        "preservation_passed": True,
        "quality_non_loss": True,
        "polished_accepted": accepted,
        "rejected_polish": not accepted,
        "exact_original_rollback": not accepted,
        "critical_semantic_regression_check_ids": [],
        "calls": [],
    }


def test_report_qualifies_only_at_all_frozen_thresholds() -> None:
    manifest = _manifest()
    report = _build_report(
        manifest,
        [_quality_row(index) for index in range(1, 17)],
        [_polisher_row(index) for index in range(1, 25)],
        manifest["source_before"],  # type: ignore[arg-type]
        True,
    )
    assert report["qualified"] is True
    assert report["qualification_outcome"] == "passed"
    assert report["quality_overall_accuracy"] == {"passed": 14, "total": 16}
    assert report["quality_mirrored_consistency"] == {"passed": 15, "total": 16}
    assert report["preservation"] == {"passed": 24, "total": 24}
    assert report["polished_accepted"] == {"passed": 18, "total": 24}
    assert report["rejected_exact_rollback"] == {"passed": 6, "total": 6}


def test_report_fails_without_changing_fixed_denominators() -> None:
    manifest = _manifest()
    quality = [_quality_row(index) for index in range(1, 17)]
    polisher = [_polisher_row(index) for index in range(1, 25)]
    quality[0].update(status="protocol_failed", overall_correct=False)
    polisher[-1].update(
        status="not_run",
        attempted=False,
        preservation_passed=False,
        quality_non_loss=False,
        exact_original_rollback=False,
    )
    report = _build_report(
        manifest,
        quality,
        polisher,
        manifest["source_before"],  # type: ignore[arg-type]
        True,
    )
    assert report["qualified"] is False
    assert report["quality_overall_accuracy"]["total"] == 16
    assert report["preservation"]["total"] == 24
    assert report["failure_counts"] == {
        "protocol": 1,
        "infrastructure": 0,
        "not_run": 1,
    }


def test_report_contract_contains_no_prose_or_raw_response() -> None:
    manifest = _manifest()
    report = _build_report(
        manifest,
        [_quality_row(index) for index in range(1, 17)],
        [_polisher_row(index) for index in range(1, 25)],
        manifest["source_before"],  # type: ignore[arg-type]
        True,
    )
    serialized = json.dumps(report, ensure_ascii=False)
    for forbidden in (
        "raw_response",
        "request_payload",
        "quality_findings",
        "semantic_consensus",
        "api_key",
        "Authorization",
        "blocks",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize("attempt_id", ("", "bad/id", "x" * 81))
def test_invalid_attempt_id_stops_before_package_or_provider(
    attempt_id: str, tmp_path: Path
) -> None:
    quality = FakeProseQualityCriticProvider()
    polisher = FakeProsePolisherProvider()
    with pytest.raises(ProseQualityQualificationError, match="attempt_id_invalid"):
        run_prose_quality_qualification(
            attempt_id=attempt_id,
            api_key="fake",
            quality_provider=quality,
            polisher_provider=polisher,
            judge_provider=FakeProseJudgeProvider(),
            qualification_suite_path=tmp_path / "missing.json",
        )
    assert quality.call_count == 0
    assert polisher.call_count == 0


def test_existing_attempt_manifest_stops_before_package_read(tmp_path: Path) -> None:
    output = tmp_path / "attempt"
    output.mkdir()
    (output / "attempt-manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(
        ProseQualityQualificationError, match="attempt_already_exists"
    ):
        run_prose_quality_qualification(
            attempt_id="existing",
            api_key="fake",
            quality_provider=FakeProseQualityCriticProvider(),
            polisher_provider=FakeProsePolisherProvider(),
            judge_provider=FakeProseJudgeProvider(),
            qualification_suite_path=tmp_path / "missing.json",
            output_dir=output,
        )
