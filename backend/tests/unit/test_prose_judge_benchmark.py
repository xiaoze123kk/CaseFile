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
    PROVIDER_SMOKE_CASES,
    ProseJudgeSuiteError,
    _gold_report,
    canonical_hash,
    freeze_selected_policy,
    load_prose_judge_dev_suite,
    run_development_ablation,
    run_provider_protocol_smoke,
)


@pytest.fixture(scope="module")
def fake_ablation_report() -> dict[str, Any]:
    loaded = load_prose_judge_dev_suite()
    checklist = loaded["checklist"]

    def factory(
        sample: dict[str, Any], policy: ProseCouncilPolicy
    ) -> FakeProseJudgeProvider:
        return FakeProseJudgeProvider(
            judge_reports=tuple(
                _gold_report(
                    sample["gold"], checklist, sample["render"], role=role
                )
                for role in policy.roles
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
def test_suite_text_labels_and_reviews_are_hash_bound(
    tmp_path: Path, target: str
) -> None:
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
    assert report["call_count"] == 432
    assert [item["metrics"]["median_requests_per_task"] for item in report["policies"]] == [
        3,
        6,
        9,
    ]
    assert all(item["eligible"] for item in report["policies"])
    assert report["report_hash"] == canonical_hash(
        {key: value for key, value in report.items() if key != "report_hash"}
    )


def test_live_attempt_stops_on_first_infrastructure_failure() -> None:
    calls = 0

    def factory(
        _sample: dict[str, Any], _policy: ProseCouncilPolicy
    ) -> FakeProseJudgeProvider:
        nonlocal calls
        calls += 1
        return FakeProseJudgeProvider(failure_at_call=1)

    report = run_development_ablation(
        provider_factory=factory,
        api_key="fake",
        mode="live",
    )
    assert report["status"] == "inconclusive"
    assert report["selected_policy_id"] is None
    assert calls == 1
    assert len(report["policies"]) == 1


def test_provider_protocol_smoke_is_fixed_three_call_and_non_qualifying(
    tmp_path: Path,
) -> None:
    loaded = load_prose_judge_dev_suite()
    tasks = {task["task_id"]: task for task in loaded["suite"]["tasks"]}
    reports = []
    for task_id, sample_kind in PROVIDER_SMOKE_CASES:
        sample = tasks[task_id]["samples"][sample_kind]
        reports.append(
            _gold_report(
                sample["gold"],
                loaded["checklist"],
                sample["render"],
                role="fidelity",
            )
        )
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
    invalid = _gold_report(
        sample["gold"], loaded["checklist"], sample["render"], role="fidelity"
    )
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
    assert report["call_count"] == 0
    assert provider.call_count == 1
    assert report["infrastructure_failures"] == 1
    assert len(report["rows"]) == 1


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
    assert descriptor["development_report_hash"] == compact["report_hash"]
    assert "raw_response" not in compact_path.read_text(encoding="utf-8")
    assert "qualified=false" in markdown_path.read_text(encoding="utf-8")
