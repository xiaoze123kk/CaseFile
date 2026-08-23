from __future__ import annotations

import json
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
from casefile.benchmark.closure_repair_eval import (
    evaluate_closure_repair_release_gates,
    run_closure_repair_benchmark,
)

ROOT = Path(__file__).resolve().parents[3]
SUITE = ROOT / "fixtures/closure_repair_benchmark/v1-scenarios.json"


def test_fake_golden_matrix_passes_every_contract_and_safety_gate() -> None:
    report = run_closure_repair_benchmark(repo_root=ROOT)

    assert report["status"] == "passed"
    assert report["mode"] == "deterministic_fake"
    assert report["scenario_count"] >= 20
    assert report["metrics"]["safety_violation_count"] == 0
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
