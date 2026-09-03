"""N4.5 B2 one-shot Rewrite qualification executor tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from casefile.agent_runtime.prose_judge import (
    DeepSeekProseJudgeProvider,
    FakeProseJudgeProvider,
)
from casefile.agent_runtime.prose_rewriter import (
    DeepSeekProseRewriterProvider,
    FakeProseRewriterProvider,
)
from casefile.benchmark import prose_rewrite_qualification as qualification
from casefile.benchmark.prose_rewrite_eval import (
    canonical_hash,
    load_prose_rewrite_dev_suite,
)


@pytest.fixture(scope="module")
def qualification_package() -> dict[str, Any]:
    loaded = load_prose_rewrite_dev_suite()
    return {
        "descriptor": {
            "descriptor_hash": "d" * 64,
            "review_policy": "codex-owner-accepted-review-v1",
            "review_attestation_hash": "a" * 64,
        },
        "suite": {
            "schema_id": "casefile.prose-rewrite-qualification-suite.v1",
            "suite_id": "test-private-qualification",
            "suite_hash": "s" * 64,
        },
        "tasks": loaded["tasks"],
    }


def _source_state(*, suffix: str = "0") -> dict[str, Any]:
    return {
        "revision": "1" * 40,
        "branch": "N4.5",
        "clean": True,
        "tracked_source_hash": suffix * 64,
    }


def _provider_pair(
    package: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    rewrite_candidates: list[dict[str, Any]] | None = None,
    judge_candidates: list[dict[str, Any]] | None = None,
    rewriter_failure_at_call: int | None = None,
) -> tuple[
    DeepSeekProseRewriterProvider,
    DeepSeekProseJudgeProvider,
    FakeProseRewriterProvider,
    FakeProseJudgeProvider,
]:
    tasks = package["tasks"]
    rewrites = rewrite_candidates or [
        candidate
        for task in tasks
        for candidate in task["asset"]["fake_rewrite_candidates"]
    ]
    judges = judge_candidates or [
        candidate
        for task in tasks
        for candidate in task["judge_candidates"][1:]
    ]
    fake_rewriter = FakeProseRewriterProvider(
        candidates=tuple(rewrites), failure_at_call=rewriter_failure_at_call
    )
    fake_judge = FakeProseJudgeProvider(judge_reports=tuple(judges))
    rewriter = DeepSeekProseRewriterProvider()
    judge = DeepSeekProseJudgeProvider(retry_wait=lambda _seconds: None)
    monkeypatch.setattr(rewriter, "rewrite_scene", fake_rewriter.rewrite_scene)
    monkeypatch.setattr(judge, "judge_scene", fake_judge.judge_scene)
    monkeypatch.setattr(judge, "arbitrate_scene", fake_judge.arbitrate_scene)
    return rewriter, judge, fake_rewriter, fake_judge


def _install_package(
    monkeypatch: pytest.MonkeyPatch, package: dict[str, Any]
) -> None:
    monkeypatch.setattr(
        qualification,
        "load_prose_rewrite_qualification_suite",
        lambda _suite, _descriptor: package,
    )


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _all_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _all_keys(item)}
    return set()


def test_mocked_exact_adapters_run_fixed_24_once_and_can_qualify(
    qualification_package: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _install_package(monkeypatch, qualification_package)
    rewriter, judge, fake_rewriter, fake_judge = _provider_pair(
        qualification_package, monkeypatch
    )
    report = qualification.run_prose_rewrite_qualification(
        attempt_id="mocked-pass",
        api_key="secret-must-not-survive",
        rewriter_provider=rewriter,
        judge_provider=judge,
        source_probe=_source_state,
        output_dir=tmp_path / "attempt",
    )

    assert report["qualification_outcome"] == "passed"
    assert report["qualified"] is True
    assert report["attempted_task_count"] == 24
    assert report["round_one_rescue"] == {"passed": 16, "total": 24}
    assert report["round_two_incremental_rescue"] == {"passed": 8, "total": 24}
    assert report["final_rescue"] == {"passed": 24, "total": 24}
    assert report["preservation_tasks"] == {"passed": 24, "total": 24}
    assert report["failure_counts"] == {
        "semantic": 0,
        "protocol": 0,
        "infrastructure": 0,
        "not_run": 0,
    }
    assert report["logical_model_call_count"] == 64
    assert report["physical_transport_attempt_count"] == 64
    assert report["model_id"] == "deepseek-v4-pro"
    assert report["rewriter_prompt_version"] == "prose-rewriter-v3"
    assert report["judge_prompt_version"] == "prose-fidelity-judge-v6"
    assert report["council_policy_id"] == "fidelity-only-v1"
    assert report["max_rewrites_per_scene"] == 2
    assert report["scene_call_budget"] == 4
    assert fake_rewriter.call_count == fake_judge.call_count == 32
    rewrite_call_audits = [
        call
        for row in report["rows"]
        for call in row["calls"]
        if call["component"] == "rewriter"
    ]
    assert all(
        300 <= call["candidate_character_count"] <= 1200
        for call in rewrite_call_audits
    )
    assert all(call["candidate_block_count"] >= 1 for call in rewrite_call_audits)
    assert report["report_hash"] == canonical_hash(
        {key: value for key, value in report.items() if key != "report_hash"}
    )
    attempt_manifest = json.loads(
        (tmp_path / "attempt/attempt-manifest.json").read_text(encoding="utf-8")
    )
    persisted_report = json.loads(
        (tmp_path / "attempt/report.json").read_text(encoding="utf-8")
    )
    assert attempt_manifest["status"] == "started"
    assert attempt_manifest["attempt_manifest_hash"] == canonical_hash(
        {
            key: value
            for key, value in attempt_manifest.items()
            if key != "attempt_manifest_hash"
        }
    )
    assert persisted_report == report

    serialized = json.dumps(report, ensure_ascii=False)
    first_prose = qualification_package["tasks"][0]["asset"]["initial_render"][
        "blocks"
    ][0]["text"]
    for forbidden in (
        first_prose,
        "secret-must-not-survive",
        "request_payload",
        "raw_response",
        "api_key",
        "Authorization",
    ):
        assert forbidden not in serialized
    assert _all_keys(report).isdisjoint(
        {
            "api_key",
            "blocks",
            "candidate",
            "initial_consensus",
            "initial_judge_report",
            "raw_response",
            "request_payload",
            "text",
        }
    )


def test_protocol_failure_is_not_retried_and_keeps_24_denominator(
    qualification_package: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_package(monkeypatch, qualification_package)
    tasks = qualification_package["tasks"]
    remaining_rewrites = [
        candidate
        for task in tasks[1:]
        for candidate in task["asset"]["fake_rewrite_candidates"]
    ]
    remaining_judges = [
        candidate
        for task in tasks[1:]
        for candidate in task["judge_candidates"][1:]
    ]
    rewriter, judge, fake_rewriter, _ = _provider_pair(
        qualification_package,
        monkeypatch,
        rewrite_candidates=[{"schema_id": "invalid", "blocks": []}, *remaining_rewrites],
        judge_candidates=remaining_judges,
    )
    report = qualification.run_prose_rewrite_qualification(
        attempt_id="mocked-protocol",
        api_key="fake",
        rewriter_provider=rewriter,
        judge_provider=judge,
        source_probe=_source_state,
    )

    assert report["qualification_outcome"] == "failed"
    assert report["qualified"] is False
    assert report["attempted_task_count"] == 24
    assert report["failure_counts"]["protocol"] == 1
    assert report["failure_counts"]["not_run"] == 0
    assert report["rows"][0]["rewrite_count"] == 1
    assert fake_rewriter.call_count == 32


def test_infrastructure_failure_aborts_without_stitching_or_rerun(
    qualification_package: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_package(monkeypatch, qualification_package)
    rewriter, judge, fake_rewriter, fake_judge = _provider_pair(
        qualification_package, monkeypatch, rewriter_failure_at_call=1
    )
    report = qualification.run_prose_rewrite_qualification(
        attempt_id="mocked-infrastructure",
        api_key="fake",
        rewriter_provider=rewriter,
        judge_provider=judge,
        source_probe=_source_state,
    )

    assert report["qualification_outcome"] == "inconclusive_infrastructure"
    assert report["qualified"] is False
    assert report["attempted_task_count"] == 1
    assert report["failure_counts"] == {
        "semantic": 0,
        "protocol": 0,
        "infrastructure": 1,
        "not_run": 23,
    }
    assert fake_rewriter.call_count == 1
    assert fake_judge.call_count == 0
    assert len(report["rows"]) == 24
    assert all(row["status"] == "not_run" for row in report["rows"][1:])


def test_source_package_drift_and_non_deepseek_adapters_cannot_qualify(
    qualification_package: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_count = 0

    def load_package(_suite: Path, _descriptor: Path) -> dict[str, Any]:
        nonlocal load_count
        load_count += 1
        if load_count > 1:
            raise RuntimeError("private package changed")
        return qualification_package

    monkeypatch.setattr(
        qualification, "load_prose_rewrite_qualification_suite", load_package
    )
    rewrites = [
        candidate
        for task in qualification_package["tasks"]
        for candidate in task["asset"]["fake_rewrite_candidates"]
    ]
    judges = [
        candidate
        for task in qualification_package["tasks"]
        for candidate in task["judge_candidates"][1:]
    ]
    states = iter((_source_state(suffix="0"), _source_state(suffix="1")))
    report = qualification.run_prose_rewrite_qualification(
        attempt_id="mocked-source-drift",
        api_key="fake",
        rewriter_provider=FakeProseRewriterProvider(candidates=tuple(rewrites)),
        judge_provider=FakeProseJudgeProvider(judge_reports=tuple(judges)),
        source_probe=lambda: next(states),
    )

    assert report["qualified"] is False
    assert report["gates"]["source_stable"] is False
    assert report["gates"]["qualification_package_stable"] is False
    assert report["gates"]["exact_provider_adapters"] is False


def test_existing_attempt_blocks_before_suite_load_or_provider_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "existing"
    output_dir.mkdir()
    (output_dir / "report.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        qualification,
        "load_prose_rewrite_qualification_suite",
        lambda *_args: pytest.fail("suite must not be loaded"),
    )
    fake_rewriter = FakeProseRewriterProvider()
    fake_judge = FakeProseJudgeProvider()
    with pytest.raises(
        qualification.ProseRewriteQualificationError,
        match="attempt_already_exists",
    ):
        qualification.run_prose_rewrite_qualification(
            attempt_id="immutable-attempt",
            api_key="fake",
            rewriter_provider=fake_rewriter,
            judge_provider=fake_judge,
            output_dir=output_dir,
            source_probe=_source_state,
        )
    assert fake_rewriter.call_count == fake_judge.call_count == 0


def test_dirty_source_blocks_before_provider_call(
    qualification_package: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_package(monkeypatch, qualification_package)
    fake_rewriter = FakeProseRewriterProvider()
    fake_judge = FakeProseJudgeProvider()
    dirty = {**_source_state(), "clean": False}
    with pytest.raises(
        qualification.ProseRewriteQualificationError,
        match="clean_source_required",
    ):
        qualification.run_prose_rewrite_qualification(
            attempt_id="dirty-source",
            api_key="fake",
            rewriter_provider=fake_rewriter,
            judge_provider=fake_judge,
            source_probe=lambda: dirty,
        )
    assert fake_rewriter.call_count == fake_judge.call_count == 0


def test_cli_requires_exact_live_confirmation_before_credential_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prose-rewrite-qualification",
            "--attempt-id",
            "blocked",
            "--output-dir",
            "ignored",
        ],
    )
    monkeypatch.delenv("CASEFILE_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(
        qualification.ProseRewriteQualificationError,
        match="explicit_live_confirmation_required",
    ):
        qualification.main()
