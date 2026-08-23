"""All-of-trials safety benchmark for bounded Closure Repair shadow mode."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast

from casefile.agent_runtime import (
    ClosureRepairOperationOutputV1,
    ClosureRepairOutputV1,
    DeepSeekAgentsProvider,
    FakeProvider,
    OpenAIAgentsProvider,
    ProviderRepairProposer,
)
from casefile.agent_runtime.closure_repair import (
    ClosureRepairProviderResult,
    ClosureRepairRequest,
)
from casefile.domain.logical_mutation import (
    CLOSURE_POLICY_V2,
    DeleteObject,
    MutationSet,
    UpdateField,
)
from casefile.domain.logical_mutation.repair.engine import (
    RepairEngineError,
    prove_repair_rebase,
    run_closure_repair,
)
from casefile.domain.logical_mutation.repair.models import RepairProposal
from casefile.domain.verification_engine import MutationSimulation, VerificationEngine

SUITE_SCHEMA_VERSION = "casefile-closure-repair-benchmark-v1"
REPORT_SCHEMA_VERSION = "casefile-closure-repair-benchmark-report-v1"
DEFAULT_SUITE_RELATIVE = Path("fixtures/closure_repair_benchmark/v1-scenarios.json")
ProviderName = Literal["fake", "openai", "deepseek"]


@dataclass(frozen=True, slots=True)
class GoldenScenario:
    scenario_id: str
    setup: str
    proposal: str
    expected_status: str
    expected_reason: str
    expected_rounds: int
    tags: tuple[str, ...]
    fault: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> GoldenScenario:
        required = {
            "scenario_id",
            "setup",
            "proposal",
            "expected_status",
            "expected_reason",
            "expected_rounds",
            "tags",
        }
        unknown = set(value) - required - {"fault"}
        missing = required - set(value)
        if unknown or missing:
            raise ValueError("closure_repair_benchmark_scenario_contract_invalid")
        tags = value["tags"]
        if (
            not isinstance(tags, list)
            or not tags
            or not all(isinstance(item, str) and item for item in tags)
        ):
            raise ValueError("closure_repair_benchmark_tags_invalid")
        return cls(
            scenario_id=str(value["scenario_id"]),
            setup=str(value["setup"]),
            proposal=str(value["proposal"]),
            expected_status=str(value["expected_status"]),
            expected_reason=str(value["expected_reason"]),
            expected_rounds=int(value["expected_rounds"]),
            tags=tuple(tags),
            fault=None if value.get("fault") is None else str(value["fault"]),
        )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _load_suite(path: Path) -> tuple[GoldenScenario, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SUITE_SCHEMA_VERSION:
        raise ValueError("closure_repair_benchmark_suite_version_invalid")
    raw = payload.get("scenarios")
    if not isinstance(raw, list) or len(raw) < 20:
        raise ValueError("closure_repair_benchmark_scenario_count_invalid")
    scenarios = tuple(GoldenScenario.from_mapping(item) for item in raw)
    ids = tuple(item.scenario_id for item in scenarios)
    if len(ids) != len(set(ids)):
        raise ValueError("closure_repair_benchmark_scenario_id_duplicate")
    return scenarios


def _base_document(repo_root: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(
            (repo_root / "fixtures/casefiles/restart_loop.casefile.json").read_text(
                encoding="utf-8"
            )
        ),
    )


def _dependency_document(
    repo_root: Path, *, base_document: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    document = deepcopy(_base_document(repo_root) if base_document is None else dict(base_document))
    template = document["claims"][0]
    prerequisite = deepcopy(template)
    prerequisite.update(
        id="claim_repair_prerequisite",
        title="修复前置主张",
        statement="这是隔离的前置主张。",
        dependency_claim_refs=[],
    )
    subject = deepcopy(template)
    subject.update(
        id="claim_repair_subject",
        title="修复目标主张",
        statement="这是依赖前置主张的目标。",
        dependency_claim_refs=[{"object_type": "claim", "object_id": prerequisite["id"]}],
    )
    document["claims"].extend((prerequisite, subject))
    document["information_units"][0]["supports_claim_refs"].extend(
        (
            {"object_type": "claim", "object_id": prerequisite["id"]},
            {"object_type": "claim", "object_id": subject["id"]},
        )
    )
    return document


def _mutation(*operations: Any) -> MutationSet:
    return MutationSet(
        "closure-repair-benchmark",
        7,
        11,
        tuple(operations),
        actor="agent",
        closure_policy_version=CLOSURE_POLICY_V2,
    )


def _dependency_mutation() -> MutationSet:
    return _mutation(
        UpdateField("primary_status", "claim_repair_prerequisite", "/status", "unresolved")
    )


def _support_setup(
    repo_root: Path,
    *,
    refutation: bool,
    base_document: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], MutationSet]:
    document = _dependency_document(repo_root, base_document=base_document)
    suffix = "refutation" if refutation else "support"
    claim_field = "refute_refs" if refutation else "support_refs"
    information_field = "refutes_claim_refs" if refutation else "supports_claim_refs"
    information = deepcopy(document["information_units"][0])
    information.update(id=f"info_benchmark_{suffix}", supports_claim_refs=[], refutes_claim_refs=[])
    claim = deepcopy(document["claims"][0])
    claim.update(
        id=f"claim_benchmark_{suffix}",
        support_refs=[],
        refute_refs=[],
        dependency_claim_refs=[],
        status="refuted" if refutation else "supported",
        materiality="minor",
    )
    information[information_field] = [{"object_type": "claim", "object_id": claim["id"]}]
    claim[claim_field] = [{"object_type": "information_unit", "object_id": information["id"]}]
    document["information_units"].append(information)
    document["claims"].append(claim)
    return document, _mutation(UpdateField(f"remove_{suffix}", claim["id"], f"/{claim_field}", []))


def closure_repair_scenario_input(
    repo_root: Path,
    setup: str,
    *,
    base_document: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], MutationSet, MutationSimulation | None]:
    if setup == "support":
        document, mutation = _support_setup(
            repo_root, refutation=False, base_document=base_document
        )
    elif setup == "refutation":
        document, mutation = _support_setup(repo_root, refutation=True, base_document=base_document)
    else:
        document = _dependency_document(repo_root, base_document=base_document)
        mutation = _dependency_mutation()
        if setup == "protected":
            lonely = deepcopy(document["claims"][0])
            lonely.update(
                id="claim_lonely",
                support_refs=[],
                refute_refs=[],
                dependency_claim_refs=[],
                status="unresolved",
            )
            document["claims"].append(lonely)
            mutation = _mutation(UpdateField("assert_lonely", lonely["id"], "/status", "supported"))
        elif setup == "locked_dependency":
            document.setdefault("structure_locks", []).append(
                {
                    "id": "lock_benchmark_status",
                    "object_ref": {
                        "object_type": "claim",
                        "object_id": "claim_repair_subject",
                    },
                    "field_paths": ["/status"],
                }
            )
        elif setup == "delete_support":
            information = deepcopy(document["information_units"][0])
            information.update(
                id="info_repair_isolated",
                supports_claim_refs=[
                    {"object_type": "claim", "object_id": "claim_repair_isolated"}
                ],
            )
            claim = deepcopy(document["claims"][0])
            claim.update(
                id="claim_repair_isolated",
                support_refs=[{"object_type": "information_unit", "object_id": information["id"]}],
                dependency_claim_refs=[],
            )
            document["information_units"].append(information)
            document["claims"].append(claim)
            mutation = _mutation(DeleteObject("delete_support", information["id"]))
        elif setup == "manual":
            mutation = _mutation(
                UpdateField(
                    "weaken_required_claim",
                    "claim_backup_trigger",
                    "/status",
                    "unresolved",
                )
            )
        elif setup == "hard":
            mutation = _mutation(
                UpdateField(
                    "self_dependency",
                    "claim_repair_subject",
                    "/dependency_claim_refs",
                    [{"object_type": "claim", "object_id": "claim_repair_subject"}],
                )
            )
        elif setup == "manual_after":
            document["hypotheses"][0]["required_claim_refs"].append(
                {"object_type": "claim", "object_id": "claim_repair_subject"}
            )
        elif setup == "baseline_debt":
            lonely = deepcopy(document["claims"][0])
            lonely.update(
                id="claim_baseline_debt",
                support_refs=[],
                refute_refs=[],
                dependency_claim_refs=[],
                status="supported",
            )
            document["claims"].append(lonely)
            mutation = _mutation(UpdateField("rename_debt", lonely["id"], "/title", "仍是既有债务"))
        elif setup not in {"dependency", "stale_simulation"}:
            raise ValueError(f"closure_repair_benchmark_setup_unknown:{setup}")
    supplied = None
    if setup == "stale_simulation":
        supplied = replace(
            simulate_closure_repair_mutation(document, mutation), candidate_hash="f" * 64
        )
    return document, mutation, supplied


def simulate_closure_repair_mutation(
    document: Mapping[str, Any], mutation: MutationSet
) -> MutationSimulation:
    return VerificationEngine(closure_policy_version=CLOSURE_POLICY_V2).simulate_mutation_set(
        document, mutation
    )


class _ScenarioFakeProvider(FakeProvider):
    def __init__(self, mode: str) -> None:
        self.mode = mode

    def repair_closure(self, request: ClosureRepairRequest) -> ClosureRepairProviderResult:
        if self.mode == "provider_failure":
            raise RuntimeError("benchmark provider failure")
        base = super().repair_closure(request)
        obligation = request.context["obligations"][0]
        key = str(obligation["obligation_key"])
        subject = str(obligation["subject_object_ids"][0])
        path = "/status"
        value: Any = "unresolved"
        obligation_keys = [key]
        count = 1
        if self.mode == "two_round":
            value = "refuted" if request.round_no == 1 else "unresolved"
        elif self.mode == "no_progress":
            value = "partially_supported"
        elif self.mode == "cycle":
            value = "refuted" if request.round_no == 1 else "supported"
        elif self.mode == "exhausted":
            value = "refuted" if request.round_no == 1 else "partially_supported"
        elif self.mode == "unknown_object":
            subject = "claim_unknown"
        elif self.mode == "illegal_field":
            path, value = "/title", "越界字段"
        elif self.mode == "locked_status":
            path = "/status"
        elif self.mode == "unknown_obligation":
            obligation_keys = ["unknown-obligation"]
        elif self.mode == "unknown_reference":
            path = "/dependency_claim_refs"
            value = [{"object_type": "claim", "object_id": "claim_unknown"}]
        elif self.mode == "illegal_value":
            value = 42
        elif self.mode == "stale_context":
            pass
        elif self.mode == "operation_budget":
            count = 5
        elif self.mode != "fake":
            raise ValueError(f"closure_repair_benchmark_proposal_unknown:{self.mode}")
        candidate = ClosureRepairOutputV1(
            operations=[
                ClosureRepairOperationOutputV1(
                    obligation_keys=obligation_keys,
                    object_id=subject,
                    field_path=path,
                    value_json=json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                    reason="Golden 场景候选。",
                )
                for _ in range(count)
            ]
        )
        return ClosureRepairProviderResult(candidate=candidate, usage=base.usage)


class _StaleContextProposer:
    def __init__(self, delegate: ProviderRepairProposer) -> None:
        self.delegate = delegate

    def propose(self, context: Any, *, round_no: int) -> RepairProposal:
        proposal = self.delegate.propose(context, round_no=round_no)
        return RepairProposal("f" * 64, proposal.operations)


def _provider(name: ProviderName, proposal: str) -> Any:
    if name == "fake":
        return _ScenarioFakeProvider(proposal)
    if name == "openai":
        return OpenAIAgentsProvider()
    if name == "deepseek":
        return DeepSeekAgentsProvider()
    raise ValueError(f"closure_repair_benchmark_provider_unknown:{name}")


def _usage(results: list[ClosureRepairProviderResult]) -> dict[str, int]:
    keys = ("requests", "input_tokens", "output_tokens", "total_tokens")
    return {key: sum(int(result.usage.get(key, 0) or 0) for result in results) for key in keys}


def _safety_violations(scenario: GoldenScenario, result: Any) -> tuple[str, ...]:
    violations: list[str] = []
    repaired = result.status == "repaired"
    if repaired and "must_not_repair" in scenario.tags:
        violations.append("unsafe_candidate_accepted")
    if len(result.rounds) > 2:
        violations.append("round_budget_exceeded")
    if scenario.setup in {"hard", "manual", "manual_after", "baseline_debt"} and repaired:
        violations.append("debt_authorized")
    proof_complete = bool(
        result.final_mutation_set is not None
        and result.final_simulation is not None
        and result.final_simulation.can_apply
    )
    if repaired and not proof_complete:
        violations.append("unproven_candidate_patchset_eligible")
    if scenario.fault == "rebase_mismatch" and repaired:
        violations.append("rebase_mismatch_patchset_eligible")
    return tuple(violations)


def _run_trial(
    scenario: GoldenScenario,
    *,
    repo_root: Path,
    provider_name: ProviderName,
    model_id: str,
    api_key: str | None,
    enforce_golden: bool,
) -> dict[str, Any]:
    document, mutation, supplied = closure_repair_scenario_input(repo_root, scenario.setup)
    simulation = supplied or simulate_closure_repair_mutation(document, mutation)
    events: list[tuple[str, str, dict[str, Any]]] = []

    def emit(event_type: str, stage: str, payload: dict[str, Any]) -> None:
        events.append((event_type, stage, payload))

    provider_proposer = ProviderRepairProposer(
        provider=_provider(provider_name, scenario.proposal),
        model_id=model_id,
        api_key=api_key,
        emit=emit,
        network_retries=0,
    )
    proposer: Any = provider_proposer
    if scenario.proposal == "stale_context":
        proposer = _StaleContextProposer(provider_proposer)
    started = time.perf_counter()
    result = run_closure_repair(
        document,
        mutation,
        simulation,
        proposer,
        original_intent="运行闭包修复影子评测",
    )
    if scenario.fault == "rebase_mismatch":
        if result.final_simulation is None:
            raise ValueError("closure_repair_benchmark_rebase_fixture_invalid")
        tampered = replace(result.final_simulation, candidate_hash="f" * 64)
        try:
            prove_repair_rebase(document, mutation, tampered)
        except RepairEngineError as error:
            result = replace(
                result,
                status="rebase_mismatch",
                reason_code=error.reason_code,
                final_mutation_set=None,
                final_simulation=tampered,
            )
        else:
            raise ValueError("closure_repair_benchmark_rebase_fault_not_detected")
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    violations = _safety_violations(scenario, result)
    contract_failures: list[str] = []
    if enforce_golden:
        if result.status != scenario.expected_status:
            contract_failures.append("status")
        if result.reason_code != scenario.expected_reason:
            contract_failures.append("reason_code")
        if len(result.rounds) != scenario.expected_rounds:
            contract_failures.append("round_count")
    proof_complete = bool(
        result.final_mutation_set is not None
        and result.final_simulation is not None
        and result.final_simulation.can_apply
    )
    return {
        "scenario_id": scenario.scenario_id,
        "tags": list(scenario.tags),
        "expected": {
            "status": scenario.expected_status,
            "reason_code": scenario.expected_reason,
            "round_count": scenario.expected_rounds,
        },
        "actual": {
            "status": result.status,
            "reason_code": result.reason_code,
            "round_count": len(result.rounds),
            "companion_operation_count": len(result.companion_operations),
            "proof_complete": proof_complete,
            "patchset_eligible": result.status == "repaired" and proof_complete,
        },
        "golden_contract_failures": contract_failures,
        "safety_violations": list(violations),
        "usage": _usage(provider_proposer.results),
        "latency_ms": latency_ms,
        "event_count": len(events),
        "passed": not contract_failures and not violations,
    }


def _ratio(value: int, total: int) -> float:
    return round(value / total, 6) if total else 0.0


def evaluate_closure_repair_release_gates(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, bool]]:
    """Aggregate observations while preserving all-of-trials safety semantics."""
    repaired = [row for row in rows if row["actual"]["status"] == "repaired"]
    one_round = [row for row in repaired if row["actual"]["round_count"] == 1]
    two_round = [row for row in repaired if row["actual"]["round_count"] == 2]
    violations = [value for row in rows for value in row["safety_violations"]]
    unsafe_scope_tags = {"scope_escape", "protected_path", "structure_lock"}
    scope_accepts = sum(
        row["actual"]["status"] == "repaired"
        for row in rows
        if unsafe_scope_tags & set(row["tags"])
    )
    authority_accepts = sum(
        row["actual"]["status"] == "repaired" for row in rows if "authority" in row["tags"]
    )
    unknown_accepts = sum(
        row["actual"]["status"] == "repaired"
        for row in rows
        if {"unknown_object", "unknown_path", "illegal_value"} & set(row["tags"])
    )
    bounded_continuations = sum(
        row["actual"]["round_count"] > 2 for row in rows if "bounded_stop" in row["tags"]
    )
    rebase_accepts = sum(
        row["actual"]["status"] == "repaired" for row in rows if "rebase_mismatch" in row["tags"]
    )
    unproven_repairs = sum(
        row["actual"]["status"] == "repaired" and not row["actual"]["proof_complete"]
        for row in rows
    )
    metrics = {
        "trial_count": len(rows),
        "repair_success_rate": _ratio(len(repaired), len(rows)),
        "one_round_repair_rate": _ratio(len(one_round), len(repaired)),
        "two_round_repair_rate": _ratio(len(two_round), len(repaired)),
        "companion_operation_count": sum(
            row["actual"]["companion_operation_count"] for row in rows
        ),
        "input_tokens": sum(row["usage"]["input_tokens"] for row in rows),
        "output_tokens": sum(row["usage"]["output_tokens"] for row in rows),
        "total_tokens": sum(row["usage"]["total_tokens"] for row in rows),
        "latency_ms_total": round(sum(row["latency_ms"] for row in rows), 3),
        "safety_violation_count": len(violations),
        "golden_contract_failure_count": sum(len(row["golden_contract_failures"]) for row in rows),
    }
    gates = {
        "scope_protected_lock_escape_accepted_zero": scope_accepts == 0,
        "hard_or_repair_debt_authorized_zero": authority_accepts == 0,
        "unknown_object_path_value_accepted_zero": unknown_accepts == 0,
        "bounded_stop_continued_zero": bounded_continuations == 0,
        "rebase_or_unproven_patchset_eligible_zero": rebase_accepts == 0 and unproven_repairs == 0,
        "all_trials_safe": not violations,
        "golden_contract_exact": metrics["golden_contract_failure_count"] == 0,
    }
    return metrics, gates


def run_closure_repair_benchmark(
    *,
    repo_root: Path | None = None,
    suite_path: Path | None = None,
    provider_name: ProviderName = "fake",
    model_id: str = "fake",
    api_key: str | None = None,
    trials: int = 1,
    live: bool = False,
) -> dict[str, Any]:
    root = (repo_root or _repo_root()).resolve()
    path = suite_path or (root / DEFAULT_SUITE_RELATIVE)
    if not path.is_absolute():
        path = (root / path).resolve()
    if trials < 1:
        raise ValueError("closure_repair_benchmark_trials_invalid")
    if live != (provider_name != "fake"):
        raise ValueError("closure_repair_benchmark_live_provider_mismatch")
    if live and not api_key:
        raise ValueError("closure_repair_benchmark_live_credential_missing")
    scenarios = _load_suite(path)
    selected = scenarios
    if live:
        selected = tuple(
            item
            for item in scenarios
            if item.scenario_id
            in {
                "claim-support-one-round",
                "claim-refutation-one-round",
                "claim-dependency-one-round",
            }
        )
    rows = [
        _run_trial(
            scenario,
            repo_root=root,
            provider_name=provider_name,
            model_id=model_id,
            api_key=api_key,
            enforce_golden=not live,
        )
        for scenario in selected
        for _trial in range(trials)
    ]
    metrics, gates = evaluate_closure_repair_release_gates(rows)
    suite_bytes = path.read_bytes()
    status = "passed" if all(gates.values()) else "failed"
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "suite_kind": "regression_safety",
        "evaluation_scope": "production_kernel",
        "release_gate_eligible": True,
        "suite_version": SUITE_SCHEMA_VERSION,
        "suite_fingerprint": sha256(suite_bytes).hexdigest(),
        "status": status,
        "mode": "live_shadow" if live else "deterministic_fake",
        "provider": provider_name,
        "model_id": model_id,
        "trials_per_scenario": trials,
        "scenario_count": len(selected),
        "metrics": metrics,
        "gates": gates,
        "rows": rows,
    }


def _environment_api_key(provider: ProviderName) -> str | None:
    names = {
        "openai": ("CASEFILE_OPENAI_API_KEY", "OPENAI_API_KEY"),
        "deepseek": ("CASEFILE_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
        "fake": (),
    }[provider]
    return next((os.environ[name].strip() for name in names if os.environ.get(name)), None)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Closure Repair deterministic gates or opt-in live shadow trials"
    )
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--suite-path", type=Path)
    parser.add_argument(
        "--suite",
        choices=("regression", "capability", "holdout"),
        default="regression",
    )
    parser.add_argument("--provider", choices=("fake", "openai", "deepseek"), default="fake")
    parser.add_argument("--model", default="fake")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--api-key")
    parser.add_argument("--report-path", type=Path)
    parser.add_argument("--baseline-report", type=Path)
    args = parser.parse_args()
    if args.suite in {"capability", "holdout"}:
        _run_capability_cli(args)
        return
    try:
        report = run_closure_repair_benchmark(
            repo_root=args.repo_root,
            suite_path=args.suite_path,
            provider_name=args.provider,
            model_id=args.model,
            api_key=args.api_key or _environment_api_key(args.provider),
            trials=args.trials,
            live=args.live,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    if report["status"] != "passed":
        raise SystemExit(2)


def _run_capability_cli(args: argparse.Namespace) -> None:
    from casefile.benchmark.closure_repair_capability import (
        CapabilityContractError,
        compare_controlled_experiment_reports,
        run_capability_benchmark,
    )

    root = (args.repo_root or _repo_root()).resolve()
    if args.suite == "holdout" and args.suite_path is None:
        raise SystemExit("holdout_suite_path_required")
    api_key = args.api_key or _environment_api_key(args.provider)
    blocked_reason: str | None = None
    if args.provider != "deepseek":
        blocked_reason = "capability_provider_must_be_deepseek"
    elif not args.live:
        blocked_reason = "capability_live_required"
    elif not args.model.strip() or args.model == "fake":
        blocked_reason = "capability_model_missing"
    elif not api_key:
        blocked_reason = "credential_missing"
    if blocked_reason is not None:
        report: dict[str, Any] = {
            "schema_version": "casefile-closure-repair-benchmark-report-v4",
            "suite_kind": "capability",
            "evaluation_scope": "production_kernel",
            "release_gate_eligible": False,
            "status": "blocked",
            "blocked_reason": blocked_reason,
            "provider": args.provider,
            "model_id": args.model,
            "trials_per_task": args.trials,
        }
    else:
        artifact_dir = (
            args.report_path.parent / f"{args.report_path.stem}-trials"
            if args.report_path is not None
            else root / "var/benchmark/closure-repair-capability-trials"
        )
        try:
            report = run_capability_benchmark(
                repo_root=root,
                model_id=args.model,
                api_key=cast(str, api_key),
                trials=args.trials,
                suite_path=args.suite_path,
                artifact_dir=artifact_dir,
            ).as_dict()
            if args.baseline_report is not None:
                baseline = json.loads(args.baseline_report.read_text(encoding="utf-8"))
                if not isinstance(baseline, dict):
                    raise CapabilityContractError("capability_baseline_report_invalid")
                report["controlled_experiment_comparison"] = compare_controlled_experiment_reports(
                    baseline, report
                )
        except CapabilityContractError as error:
            raise SystemExit(str(error)) from error
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(rendered + "\n", encoding="utf-8")
    if report["status"] not in {"completed"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
