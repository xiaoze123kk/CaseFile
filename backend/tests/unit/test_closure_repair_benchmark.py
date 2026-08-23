from __future__ import annotations

import json
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import pytest
from casefile.agent_runtime import (
    ClosureRepairOperationOutputV1,
    ClosureRepairOutputV1,
    DeepSeekAgentsProvider,
    OpenAIAgentsProvider,
)
from casefile.agent_runtime.closure_repair import ClosureRepairProviderResult
from casefile.benchmark.closure_repair_capability import (
    CapabilityContractError,
    assert_comparable_reports,
    compare_controlled_experiment_reports,
    load_capability_suite,
    run_capability_benchmark,
    validate_capability_references,
)
from casefile.benchmark.closure_repair_eval import (
    evaluate_closure_repair_release_gates,
    run_closure_repair_benchmark,
)
from casefile.benchmark.closure_repair_eval import main as closure_repair_main
from casefile.domain.logical_mutation.repair import repair_policies

ROOT = Path(__file__).resolve().parents[3]
SUITE = ROOT / "fixtures/closure_repair_benchmark/v1-scenarios.json"


def test_fake_golden_matrix_passes_every_contract_and_safety_gate() -> None:
    report = run_closure_repair_benchmark(repo_root=ROOT)

    assert report["status"] == "passed"
    assert report["mode"] == "deterministic_fake"
    assert report["scenario_count"] >= 20
    assert report["metrics"]["safety_violation_count"] == 0
    assert report["suite_kind"] == "regression_safety"
    assert report["evaluation_scope"] == "production_kernel"
    assert report["metrics"]["golden_contract_failure_count"] == 0
    assert all(report["gates"].values())
    assert all(row["passed"] for row in report["rows"])


def test_golden_suite_covers_required_success_and_fail_closed_boundaries() -> None:
    payload = json.loads(SUITE.read_text(encoding="utf-8"))
    scenarios = payload["scenarios"]
    tags = {tag for scenario in scenarios for tag in scenario["tags"]}
    ids = {scenario["scenario_id"] for scenario in scenarios}

    assert payload["schema_version"] == "casefile-closure-repair-benchmark-v1"
    assert len(scenarios) >= 20
    assert len(ids) == len(scenarios)
    assert {
        "claim_supported",
        "claim_refuted",
        "claim_dependency",
        "two_round",
        "scope_escape",
        "protected_path",
        "structure_lock",
        "delete_boundary",
        "unknown_object",
        "unknown_path",
        "illegal_value",
        "hard",
        "manual",
        "baseline_debt",
        "no_progress",
        "cycle",
        "stale",
        "rebase_mismatch",
    }.issubset(tags)


def test_report_is_all_of_trials_and_does_not_expose_pass_at_k() -> None:
    report = run_closure_repair_benchmark(repo_root=ROOT, trials=2)

    assert report["metrics"]["trial_count"] == report["scenario_count"] * 2
    assert report["gates"]["all_trials_safe"] is True
    rendered = json.dumps(report)
    assert "pass@" not in rendered
    assert "pass_at" not in rendered


def test_capability_suite_covers_every_policy_without_mixing_score_classes() -> None:
    suite = load_capability_suite(ROOT)
    policies = repair_policies()

    assert len(suite.tasks) == 61
    assert len({task.task_id for task in suite.tasks}) == 61
    assert {task.policy_key for task in suite.tasks} == {
        (policy.rule_code, policy.closure_level) for policy in policies
    }
    assert sum(task.automation == "agent" for task in suite.tasks) == 12
    assert sum(task.automation == "manual" for task in suite.tasks) == 22
    assert sum(task.automation == "ineligible" for task in suite.tasks) == 27


def test_capability_references_prove_repairs_and_correct_abstention() -> None:
    result = validate_capability_references(ROOT)

    assert result == {
        "task_count": 61,
        "passed": True,
        "failure_count": 0,
        "failures": [],
    }


def test_capability_contract_rejects_unknown_task_fields(tmp_path: Path) -> None:
    source = ROOT / "fixtures/closure_repair_benchmark/capability/v1"
    target = tmp_path / "capability"
    shutil.copytree(source, target)
    task_path = next((target / "tasks").glob("*.json"))
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    task_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CapabilityContractError, match="capability_task_keys_invalid"):
        load_capability_suite(ROOT, target / "suite.json")


@pytest.mark.parametrize(
    ("mutation", "error"),
    (
        ("duplicate", "capability_task_id_duplicate"),
        ("missing_reference", "capability_reference_missing"),
        ("illegal_outcome", "capability_acceptable_outcome_invalid"),
        ("oracle_leak", "capability_oracle_leaked_into_input"),
    ),
)
def test_capability_contract_fails_closed_for_invalid_task_bank(
    tmp_path: Path, mutation: str, error: str
) -> None:
    source = ROOT / "fixtures/closure_repair_benchmark/capability/v1"
    target = tmp_path / mutation
    shutil.copytree(source, target)
    suite_path = target / "suite.json"
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    task_path = target / suite["tasks"][0]
    task = json.loads(task_path.read_text(encoding="utf-8"))
    if mutation == "duplicate":
        second = target / suite["tasks"][1]
        second_task = json.loads(second.read_text(encoding="utf-8"))
        second_task["task_id"] = task["task_id"]
        second.write_text(json.dumps(second_task), encoding="utf-8")
    elif mutation == "missing_reference":
        task["reference"] = "../references/missing.json"
        task_path.write_text(json.dumps(task), encoding="utf-8")
    elif mutation == "illegal_outcome":
        task["oracle"]["acceptable_outcomes"] = ["anything"]
        task_path.write_text(json.dumps(task), encoding="utf-8")
    else:
        task["input"]["original_intent"] = "oracle: bypass"
        task_path.write_text(json.dumps(task), encoding="utf-8")

    with pytest.raises(CapabilityContractError, match=error):
        load_capability_suite(ROOT, suite_path)


def test_capability_report_comparison_requires_identical_fingerprint() -> None:
    report = {"comparison_fingerprint": "a" * 64}
    assert_comparable_reports(report, dict(report))
    with pytest.raises(CapabilityContractError, match="fingerprint_mismatch"):
        assert_comparable_reports(report, {"comparison_fingerprint": "b" * 64})


def test_controlled_experiment_comparison_locks_eval_and_lists_contract_changes() -> None:
    locked = {
        "suite_fingerprint": "s",
        "grader_version": "g",
        "provider": "deepseek",
        "model_id": "deepseek-v4-pro",
        "trials_per_task": 3,
        "closure_policy_version": "logical-mutation-v1",
        "repair_policy_version": "closure-repair-v1",
    }
    comparison = compare_controlled_experiment_reports(
        {
            **locked,
            "prompt_version": "closure-repair-v1",
            "agent_version": "closure-repair-agent-v1",
        },
        {
            **locked,
            "prompt_version": "closure-repair-v2",
            "agent_version": "closure-repair-agent-v2",
        },
    )

    assert comparison["comparable"] is True
    assert set(comparison["allowed_changes"]) == {"prompt_version", "agent_version"}
    with pytest.raises(CapabilityContractError, match="model_id"):
        compare_controlled_experiment_reports(
            locked,
            {**locked, "model_id": "different-model"},
        )


def test_capability_report_separates_repair_abstention_safety_and_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        DeepSeekAgentsProvider,
        "repair_closure",
        lambda _self, request: _adapter_result(request),
    )
    artifacts = tmp_path / "trials"

    report = run_capability_benchmark(
        repo_root=ROOT,
        model_id="deepseek-protocol-test",
        api_key="test-key",
        trials=1,
        artifact_dir=artifacts,
    ).as_dict()

    assert report["status"] == "completed"
    assert report["release_gate_eligible"] is False
    assert report["prompt_version"] == "closure-repair-v2"
    assert report["agent_version"] == "closure-repair-agent-v2"
    assert report["output_schema_id"] == "closure-repair-output-v2"
    assert report["context_version"] == "closure-repair-context-v2"
    assert report["task_count"] == 61
    assert report["repair_task_count"] == 12
    assert report["abstention_task_count"] == 49
    assert report["metrics"]["capability"]["evaluable_trial_count"] == 12
    assert report["metrics"]["capability"]["trial_success_rate"] == 1.0
    assert report["metrics"]["capability"]["semantic_round_2_entry_count"] == 0
    assert report["metrics"]["capability"]["conditional_round_2_recovery_rate"] == 0.0
    assert (
        report["metrics"]["capability"]["two_round_recovery_rate_denominator"]
        == "all_evaluable_repair_trials"
    )
    assert report["metrics"]["abstention"]["correct_abstention_rate"] == 1.0
    assert report["metrics"]["safety"]["unsafe_trial_count"] == 0
    assert report["metrics"]["safety"]["all_of_1_safe"] is True
    assert report["metrics"]["infrastructure_failure_count"] == 0
    assert len(list(artifacts.glob("*.json"))) == 61


def test_capability_cli_fails_closed_with_structured_missing_credential(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CASEFILE_DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "benchmark",
            "--suite",
            "capability",
            "--provider",
            "deepseek",
            "--model",
            "deepseek-test",
            "--live",
        ],
    )

    with pytest.raises(SystemExit, match="2"):
        closure_repair_main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"
    assert payload["blocked_reason"] == "credential_missing"


def test_one_unsafe_trial_fails_the_all_of_trials_release_gate() -> None:
    report = run_closure_repair_benchmark(repo_root=ROOT)
    rows = deepcopy(report["rows"])
    escaped = next(row for row in rows if "scope_escape" in row["tags"])
    escaped["actual"].update(
        status="repaired", proof_complete=True, patchset_eligible=True
    )
    escaped["safety_violations"] = ["unsafe_candidate_accepted"]

    metrics, gates = evaluate_closure_repair_release_gates(rows)

    assert metrics["safety_violation_count"] == 1
    assert gates["scope_protected_lock_escape_accepted_zero"] is False
    assert gates["all_trials_safe"] is False


def _adapter_result(request: Any) -> ClosureRepairProviderResult:
    operations = []
    for obligation in request.context["obligations"]:
        subject_id = str(obligation["subject_object_ids"][0])
        paths = {
            path
            for value in obligation["allowed_paths"]
            if value["object_id"] == subject_id
            for path in value["field_paths"]
        }
        field_path = "/status" if "/status" in paths else sorted(paths)[0]
        operations.append(
            ClosureRepairOperationOutputV1(
                obligation_keys=[str(obligation["obligation_key"])],
                object_id=subject_id,
                field_path=field_path,
                value_json='"unresolved"' if field_path == "/status" else "[]",
                reason="协议测试的最小修复。",
            )
        )
    return ClosureRepairProviderResult(
        candidate=ClosureRepairOutputV1(operations=operations),
        usage={
            "requests": 1,
            "input_tokens": 20,
            "output_tokens": 8,
            "total_tokens": 28,
        },
    )


@pytest.mark.parametrize(
    "provider_type", (OpenAIAgentsProvider, DeepSeekAgentsProvider)
)
def test_live_shadow_is_opt_in_and_exercises_each_adapter_protocol(
    monkeypatch: pytest.MonkeyPatch,
    provider_type: type[OpenAIAgentsProvider] | type[DeepSeekAgentsProvider],
) -> None:
    monkeypatch.setattr(
        provider_type,
        "repair_closure",
        lambda _self, request: _adapter_result(request),
    )
    provider_name: Literal["openai", "deepseek"] = (
        "openai" if provider_type is OpenAIAgentsProvider else "deepseek"
    )

    report = run_closure_repair_benchmark(
        repo_root=ROOT,
        provider_name=provider_name,
        model_id="protocol-test",
        api_key="test-key",
        trials=2,
        live=True,
    )

    assert report["status"] == "passed"
    assert report["mode"] == "live_shadow"
    assert report["scenario_count"] == 3
    assert report["metrics"]["trial_count"] == 6
    assert report["metrics"]["total_tokens"] == 168
    assert all(row["actual"]["proof_complete"] for row in report["rows"])


def test_live_shadow_fails_closed_without_explicit_live_contract_or_credential() -> None:
    with pytest.raises(ValueError, match="live_provider_mismatch"):
        run_closure_repair_benchmark(repo_root=ROOT, provider_name="openai")
    with pytest.raises(ValueError, match="live_credential_missing"):
        run_closure_repair_benchmark(
            repo_root=ROOT, provider_name="openai", live=True
        )
