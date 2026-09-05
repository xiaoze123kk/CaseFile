"""N4.5-02 public B0 suite, Fake gate and lineage tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from casefile.agent_runtime.prose_judge import FakeProseJudgeProvider, ProseCouncilPolicy
from casefile.benchmark.prose_judge_eval import (
    DEFAULT_ATTESTATION,
    DEFAULT_SUITE,
    PROVIDER_COUNCIL_SMOKE_CASE,
    PROVIDER_SEMANTIC_SMOKE_CASES,
    PROVIDER_SMOKE_CASES,
    ProseJudgeSuiteError,
    _gold_candidate,
    canonical_hash,
    freeze_selected_policy,
    load_prose_judge_dev_suite,
    run_development_ablation,
    run_provider_council_protocol_smoke,
    run_provider_protocol_smoke,
    run_provider_semantic_smoke,
)


@pytest.fixture(scope="module")
def fake_ablation_report() -> dict[str, Any]:
    def factory(sample: dict[str, Any], policy: ProseCouncilPolicy) -> FakeProseJudgeProvider:
        return FakeProseJudgeProvider(
            judge_reports=tuple(
                _gold_candidate(sample["gold"], sample["render"]) for _role in policy.roles
            )
        )

    return run_development_ablation(
        provider_factory=factory,
        api_key="fake",
        mode="fake",
    )


def test_public_suite_has_frozen_distribution_gold_and_review_attestation() -> None:
    loaded = load_prose_judge_dev_suite()
    tasks = loaded["suite"]["tasks"]
    assert len(tasks) == 24
    assert len({task["ability"] for task in tasks}) == 8
    assert len({task["variant"] for task in tasks}) == 3
    assert sum(task["critical"] for task in tasks) == 8
    assert loaded["attestation"]["reviewer"] == "Codex"
    assert loaded["attestation"]["reviewer_independence"] is False
    assert loaded["attestation"]["holdout_qualification"] is False


@pytest.mark.parametrize("target", ("suite", "task", "text", "label", "review"))
def test_suite_text_labels_and_reviews_are_hash_bound(tmp_path: Path, target: str) -> None:
    suite = json.loads(DEFAULT_SUITE.read_text(encoding="utf-8"))
    attestation = json.loads(DEFAULT_ATTESTATION.read_text(encoding="utf-8"))
    if target == "suite":
        suite["suite_id"] += "-drift"
    elif target == "task":
        suite["tasks"][0]["mutation_kind"] += "-drift"
    elif target == "text":
        suite["tasks"][0]["samples"]["base"]["render"]["blocks"][0]["text"] += "漂移"
    elif target == "label":
        suite["tasks"][0]["samples"]["base"]["gold"]["scene_verdict"] = "fail"
    else:
        suite["tasks"][0]["review"]["semantic_pass"] = "rejected"
    suite_path = tmp_path / "suite.json"
    attestation_path = tmp_path / "attestation.json"
    suite_path.write_text(json.dumps(suite, ensure_ascii=False), encoding="utf-8")
    attestation_path.write_text(json.dumps(attestation, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ProseJudgeSuiteError):
        load_prose_judge_dev_suite(suite_path, attestation_path)


def test_fake_ablation_runs_all_policies_and_selects_lowest_request_policy(
    fake_ablation_report: dict[str, Any],
) -> None:
    report = fake_ablation_report
    assert report["status"] == "completed"
    assert report["oracle_backed"] is True
    assert report["qualification_eligible"] is False
    assert report["selected_policy_id"] == "fidelity-only-v1"
    assert report["schema_id"] == "casefile.prose-judge-dev-ablation.v2"
    assert report["call_count"] == 432
    assert report["successful_response_count"] == 432
    assert report["transport_attempt_count"] == 432
    assert report["transport_retry_count"] == 0
    assert report["terminal_transport_failure_count"] == 0
    assert [item["metrics"]["median_requests_per_task"] for item in report["policies"]] == [
        3,
        6,
        9,
    ]
    assert all(item["eligible"] for item in report["policies"])
    assert report["report_hash"] == canonical_hash(
        {key: value for key, value in report.items() if key != "report_hash"}
    )


def test_live_attempt_stops_on_first_infrastructure_failure(tmp_path: Path) -> None:
    calls = 0

    def factory(_sample: dict[str, Any], _policy: ProseCouncilPolicy) -> FakeProseJudgeProvider:
        nonlocal calls
        calls += 1
        return FakeProseJudgeProvider(failure_at_call=1)

    report = run_development_ablation(
        provider_factory=factory,
        api_key="fake",
        mode="live",
        output_dir=tmp_path / "failed-live",
    )
    assert report["status"] == "inconclusive"
    assert report["selected_policy_id"] is None
    assert calls == 1
    assert len(report["policies"]) == 1
    assert report["call_count"] == 1
    assert report["successful_response_count"] == 0
    assert report["terminal_transport_failure_count"] == 1
    raw = json.loads(
        (tmp_path / "failed-live" / "raw-call-bundle.json").read_text(encoding="utf-8")
    )
    assert raw["schema_id"] == "casefile.prose-judge-raw-bundle.v2"
    assert raw["calls"] == []
    assert len(raw["failed_calls"]) == 1
    assert raw["failed_calls"][0]["api_key_persisted"] is False
    assert "fake" not in json.dumps(raw["failed_calls"][0]["request_payload"])
    assert report["raw_call_bundle_hash"] == canonical_hash(raw)


def test_provider_protocol_smoke_is_fixed_three_call_and_non_qualifying(
    tmp_path: Path,
) -> None:
    loaded = load_prose_judge_dev_suite()
    tasks = {task["task_id"]: task for task in loaded["suite"]["tasks"]}
    reports = []
    for task_id, sample_kind in PROVIDER_SMOKE_CASES:
        sample = tasks[task_id]["samples"][sample_kind]
        reports.append(_gold_candidate(sample["gold"], sample["render"]))
    provider = FakeProseJudgeProvider(judge_reports=tuple(reports))
    output_dir = tmp_path / "smoke"
    report = run_provider_protocol_smoke(
        provider=provider,
        api_key="fake",
        output_dir=output_dir,
    )

    assert report["status"] == "passed"
    assert report["qualification_eligible"] is False
    assert report["call_count"] == provider.call_count == 3
    assert all(report["gates"].values())
    assert [row["task_id"] for row in report["rows"]] == [
        task_id for task_id, _sample_kind in PROVIDER_SMOKE_CASES
    ]
    assert report["report_hash"] == canonical_hash(
        {key: value for key, value in report.items() if key != "report_hash"}
    )
    assert (output_dir / "raw-call-bundle.json").is_file()
    assert (output_dir / "report.json").is_file()


def test_provider_protocol_smoke_stops_on_first_protocol_failure() -> None:
    loaded = load_prose_judge_dev_suite()
    tasks = {task["task_id"]: task for task in loaded["suite"]["tasks"]}
    task_id, sample_kind = PROVIDER_SMOKE_CASES[0]
    sample = tasks[task_id]["samples"][sample_kind]
    invalid = _gold_candidate(sample["gold"], sample["render"])
    invalid["render_hash"] = loaded["checklist"]["source"]["scene_plan_hash"]
    provider = FakeProseJudgeProvider(judge_reports=(invalid,))

    report = run_provider_protocol_smoke(provider=provider, api_key="fake")

    assert report["status"] == "failed"
    assert report["call_count"] == provider.call_count == 1
    assert report["protocol_failures"] == 1
    assert report["gates"]["server_bindings_exact"] is False
    assert len(report["rows"]) == 1


def test_provider_protocol_smoke_stops_on_first_infrastructure_failure() -> None:
    provider = FakeProseJudgeProvider(failure_at_call=1)

    report = run_provider_protocol_smoke(provider=provider, api_key="fake")

    assert report["status"] == "inconclusive"
    assert report["call_count"] == 1
    assert report["successful_response_count"] == 0
    assert provider.call_count == 1
    assert report["infrastructure_failures"] == 1
    assert len(report["rows"]) == 1


def _council_smoke_reports(
    loaded: dict[str, Any],
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    task_id, sample_kind = PROVIDER_COUNCIL_SMOKE_CASE
    task = next(item for item in loaded["suite"]["tasks"] if item["task_id"] == task_id)
    sample = task["samples"][sample_kind]
    reports = tuple(
        _gold_candidate(sample["gold"], sample["render"])
        for _role in ("fidelity", "adversarial", "coherence")
    )
    arbiter = _gold_candidate(sample["gold"], sample["render"])
    disputed = next(
        item
        for item in arbiter["assessments"]
        if item["verdict"] == "pass" and item["evidence_ids"]
    )
    arbiter["assessments"] = [disputed]
    return reports, arbiter


def test_provider_council_smoke_is_exact_four_role_calls_and_non_qualifying(
    tmp_path: Path,
) -> None:
    loaded = load_prose_judge_dev_suite()
    reports, arbiter = _council_smoke_reports(loaded)
    provider = FakeProseJudgeProvider(
        judge_reports=reports,
        arbiter_reports=(arbiter,),
    )

    report = run_provider_council_protocol_smoke(
        provider=provider,
        api_key="fake",
        output_dir=tmp_path / "council-smoke",
    )

    assert report["status"] == "passed"
    assert report["qualification_eligible"] is False
    assert report["call_count"] == provider.call_count == 4
    assert [row["role"] for row in report["rows"]] == [
        "fidelity",
        "adversarial",
        "coherence",
        "arbiter",
    ]
    assert all(report["gates"].values())
    assert report["forced_dispute"]["qualification_eligible"] is False


def test_provider_council_smoke_stops_on_judge_protocol_failure() -> None:
    loaded = load_prose_judge_dev_suite()
    reports, _arbiter = _council_smoke_reports(loaded)
    invalid = deepcopy(reports[1])
    invalid["render_hash"] = "0" * 64
    provider = FakeProseJudgeProvider(judge_reports=(reports[0], invalid))

    report = run_provider_council_protocol_smoke(provider=provider, api_key="fake")

    assert report["status"] == "failed"
    assert report["call_count"] == provider.call_count == 2
    assert report["protocol_failures"] == 1
    assert [row["role"] for row in report["rows"]] == [
        "fidelity",
        "adversarial",
    ]


def test_provider_council_smoke_stops_on_arbiter_infrastructure_failure() -> None:
    loaded = load_prose_judge_dev_suite()
    reports, arbiter = _council_smoke_reports(loaded)
    provider = FakeProseJudgeProvider(
        judge_reports=reports,
        arbiter_reports=(arbiter,),
        failure_at_call=4,
    )

    report = run_provider_council_protocol_smoke(provider=provider, api_key="fake")

    assert report["status"] == "inconclusive"
    assert report["call_count"] == 4
    assert report["successful_response_count"] == 3
    assert provider.call_count == 4
    assert report["infrastructure_failures"] == 1
    assert report["rows"][-1]["role"] == "arbiter"


def test_provider_semantic_smoke_is_fixed_fourteen_exact_calls(
    tmp_path: Path,
) -> None:
    loaded = load_prose_judge_dev_suite()
    tasks = {task["task_id"]: task for task in loaded["suite"]["tasks"]}
    reports = tuple(
        _gold_candidate(
            tasks[task_id]["samples"][sample_kind]["gold"],
            tasks[task_id]["samples"][sample_kind]["render"],
        )
        for task_id, sample_kind in PROVIDER_SEMANTIC_SMOKE_CASES
    )
    provider = FakeProseJudgeProvider(judge_reports=reports)
    report = run_provider_semantic_smoke(
        provider=provider,
        api_key="fake",
        output_dir=tmp_path / "semantic-smoke",
    )
    assert report["status"] == "passed"
    assert report["qualification_eligible"] is False
    assert report["call_count"] == provider.call_count == 14
    assert report["transport_attempt_count"] == 14
    assert all(report["gates"].values())
    assert [(row["task_id"], row["sample_kind"]) for row in report["rows"]] == list(
        PROVIDER_SEMANTIC_SMOKE_CASES
    )
    raw = json.loads(
        (tmp_path / "semantic-smoke" / "raw-call-bundle.json").read_text(encoding="utf-8")
    )
    assert raw["schema_id"] == "casefile.prose-judge-raw-bundle.v2"
    assert report["raw_call_bundle_hash"] == canonical_hash(raw)


def test_changed_attestation_hash_is_rejected(tmp_path: Path) -> None:
    suite = DEFAULT_SUITE.read_text(encoding="utf-8")
    attestation = json.loads(DEFAULT_ATTESTATION.read_text(encoding="utf-8"))
    attestation["reviewer_independence"] = True
    attestation["attestation_hash"] = canonical_hash(
        {key: value for key, value in attestation.items() if key != "attestation_hash"}
    )
    suite_path = tmp_path / "suite.json"
    attestation_path = tmp_path / "attestation.json"
    suite_path.write_text(suite, encoding="utf-8")
    attestation_path.write_text(json.dumps(attestation), encoding="utf-8")
    with pytest.raises(ProseJudgeSuiteError, match="attestation_invalid"):
        load_prose_judge_dev_suite(suite_path, attestation_path)


def test_policy_freeze_writes_only_compact_non_qualifying_evidence(
    tmp_path: Path, fake_ablation_report: dict[str, Any]
) -> None:
    report = deepcopy(fake_ablation_report)
    report["mode"] = "live"
    report["report_hash"] = canonical_hash(
        {key: value for key, value in report.items() if key != "report_hash"}
    )
    descriptor_path = tmp_path / "policy.json"
    compact_path = tmp_path / "result.json"
    markdown_path = tmp_path / "result.md"
    descriptor, compact = freeze_selected_policy(
        report,
        descriptor_path=descriptor_path,
        compact_report_path=compact_path,
        markdown_path=markdown_path,
    )
    assert descriptor["qualified"] is False
    assert compact["qualified"] is False
    assert descriptor["schema_id"] == "casefile.prose-council-policy.v2"
    assert compact["schema_id"] == "casefile.prose-judge-dev-result.v2"
    assert descriptor["network_retries"] == 1
    assert descriptor["candidate_schema_id_binding"] == (
        "compiler.prose-judge-candidate.v1"
    )
    assert len(descriptor["candidate_schema_hash"]) == 64
    assert descriptor["development_report_hash"] == compact["report_hash"]
    assert "raw_response" not in compact_path.read_text(encoding="utf-8")
    assert "qualified=false" in markdown_path.read_text(encoding="utf-8")
