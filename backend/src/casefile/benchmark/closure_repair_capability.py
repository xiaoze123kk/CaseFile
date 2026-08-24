"""Closure Repair v2 capability task bank, production-kernel harness, and graders."""

from __future__ import annotations

import json
import random
import subprocess
import time
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from casefile.agent_runtime import (
    CLOSURE_REPAIR_AGENT_VERSION,
    CLOSURE_REPAIR_PROMPT_VERSION,
    CLOSURE_REPAIR_SCHEMA_ID,
    CLOSURE_REPAIR_TOOLSET_VERSION,
    DeepSeekAgentsProvider,
    ProviderRepairProposer,
)
from casefile.domain.logical_mutation import (
    ACTIVE_APPLY_POLICY,
    CLOSURE_POLICY_V2,
    CreateObject,
    DeleteObject,
    MutationSet,
    UpdateField,
    assess_closure_repair,
)
from casefile.domain.logical_mutation.repair import (
    REPAIR_CONTEXT_V3,
    REPAIR_POLICY_V1,
    ClosureRepairContextV1,
    ClosureRepairContextV3,
    ClosureRepairResult,
    RepairProposal,
    RepairUpdateOperation,
    repair_policies,
    run_closure_repair,
)
from casefile.domain.verification_engine import MutationSimulation, VerificationFinding

from .closure_repair_eval import (
    closure_repair_scenario_input,
    simulate_closure_repair_mutation,
)
from .closure_repair_lineage import repair_runtime_fingerprint
from .eval_core import (
    EvalSuite,
    EvalTask,
    GraderResult,
    Outcome,
    SuiteReport,
    Transcript,
    TrialRecord,
)

CAPABILITY_SCHEMA_VERSION = "casefile-closure-repair-capability-v1"
CAPABILITY_REPORT_VERSION = "casefile-closure-repair-benchmark-report-v5"
GRADER_VERSION = "closure-repair-capability-grader-v1"
HARNESS_VERSION = "closure-repair-production-kernel-v3"
DEFAULT_CAPABILITY_RELATIVE = Path("fixtures/closure_repair_benchmark/capability/v1/suite.json")
_TASK_KEYS = {"task_id", "policy_key", "automation", "input", "oracle", "reference", "tags"}
_INPUT_KEYS = {"document", "setup", "variant", "original_intent", "primary_mutation"}
_ORACLE_KEYS = {
    "expected_assessment",
    "expected_trigger",
    "acceptable_outcomes",
    "intent_assertions",
    "task_specific_assertions",
}


class CapabilityContractError(ValueError):
    """A stable, fail-closed task-bank contract error."""


class _NeverProposer:
    calls = 0

    def propose(self, context: ClosureRepairContextV1, *, round_no: int) -> RepairProposal:
        self.calls += 1
        raise AssertionError("closure_repair_capability_provider_must_not_be_called")


class _ReferenceProposer:
    def __init__(self, reference: Mapping[str, Any]) -> None:
        self.reference = reference
        self.calls = 0

    def propose(self, context: ClosureRepairContextV1, *, round_no: int) -> RepairProposal:
        self.calls += 1
        raw_operations = self.reference.get("operations")
        if raw_operations is None:
            rounds = cast(Sequence[Mapping[str, Any]], self.reference["rounds"])
            round_reference = next(
                (item for item in rounds if item.get("round_no") == round_no), None
            )
            if round_reference is None:
                raise CapabilityContractError("capability_reference_round_missing")
            raw_operations = round_reference["operations"]
        operations = tuple(
            RepairUpdateOperation(
                obligation_keys=tuple(item.obligation_key for item in context.obligations),
                object_id=context.obligations[0].subject_object_ids[0],
                field_path=str(raw["field_path"]),
                new_value=deepcopy(raw["new_value"]),
                reason=str(raw["reason"]),
            )
            for raw in cast(Sequence[Mapping[str, Any]], raw_operations)
        )
        if isinstance(context, ClosureRepairContextV3):
            alternative = next(
                (
                    alternative
                    for alternative in context.repair_alternatives
                    if tuple((item.field_path, item.new_value) for item in alternative.operations)
                    == tuple((item.field_path, item.new_value) for item in operations)
                ),
                None,
            )
            if alternative is None:
                raise CapabilityContractError("capability_reference_alternative_missing")
            return RepairProposal(
                context.context_hash,
                alternative.operations,
                selected_alternative_id=alternative.alternative_id,
            )
        return RepairProposal(context.context_hash, operations)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CapabilityContractError(f"capability_json_invalid:{path}") from error
    if not isinstance(value, dict):
        raise CapabilityContractError(f"capability_json_object_required:{path}")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        raise CapabilityContractError(code)


def load_capability_suite(repo_root: Path, suite_path: Path | None = None) -> EvalSuite:
    path = (suite_path or repo_root / DEFAULT_CAPABILITY_RELATIVE).resolve()
    payload = _read_object(path)
    if payload.get("schema_version") in {
        "casefile-closure-repair-holdout-v1",
        "casefile-closure-repair-holdout-v2",
    }:
        from casefile.benchmark.closure_repair_holdout import load_holdout_suite

        return load_holdout_suite(path)
    _exact_keys(payload, {"schema_version", "suite_id", "tasks"}, "capability_suite_keys_invalid")
    if payload["schema_version"] != CAPABILITY_SCHEMA_VERSION:
        raise CapabilityContractError("capability_suite_version_invalid")
    raw_paths = payload["tasks"]
    if not isinstance(raw_paths, list) or len(raw_paths) != 61:
        raise CapabilityContractError("capability_task_count_invalid")
    tasks: list[EvalTask] = []
    for relative in raw_paths:
        if not isinstance(relative, str) or not relative:
            raise CapabilityContractError("capability_task_path_invalid")
        task_path = (path.parent / relative).resolve()
        try:
            task_path.relative_to(path.parent)
        except ValueError as error:
            raise CapabilityContractError("capability_task_path_escape") from error
        raw = _read_object(task_path)
        _exact_keys(raw, _TASK_KEYS, "capability_task_keys_invalid")
        task = _parse_task(raw, task_path=task_path, suite_root=path.parent)
        tasks.append(task)
    ids = [item.task_id for item in tasks]
    if len(ids) != len(set(ids)):
        raise CapabilityContractError("capability_task_id_duplicate")
    _validate_policy_coverage(tasks)
    fixture_payload = [
        (str(item.relative_to(path.parent)).replace("\\", "/"), _read_object(item))
        for item in sorted(path.parent.rglob("*.json"))
    ]
    fingerprint = sha256(_canonical_bytes(fixture_payload)).hexdigest()
    return EvalSuite(
        suite_id=str(payload["suite_id"]),
        suite_kind="capability",
        schema_version=CAPABILITY_SCHEMA_VERSION,
        tasks=tuple(tasks),
        fingerprint=fingerprint,
    )


def _parse_task(raw: Mapping[str, Any], *, task_path: Path, suite_root: Path) -> EvalTask:
    policy_key = raw["policy_key"]
    if (
        not isinstance(policy_key, list)
        or len(policy_key) != 2
        or not all(isinstance(item, str) and item for item in policy_key)
    ):
        raise CapabilityContractError("capability_policy_key_invalid")
    automation = raw["automation"]
    if automation not in {"agent", "manual", "ineligible"}:
        raise CapabilityContractError("capability_automation_invalid")
    input_value = raw["input"]
    oracle = raw["oracle"]
    if not isinstance(input_value, dict) or not isinstance(oracle, dict):
        raise CapabilityContractError("capability_task_sections_invalid")
    _exact_keys(input_value, _INPUT_KEYS, "capability_input_keys_invalid")
    _exact_keys(oracle, _ORACLE_KEYS, "capability_oracle_keys_invalid")
    if "oracle" in json.dumps(input_value, ensure_ascii=False).lower():
        raise CapabilityContractError("capability_oracle_leaked_into_input")
    outcomes = oracle["acceptable_outcomes"]
    if (
        not isinstance(outcomes, list)
        or not outcomes
        or not all(
            item
            in {
                "repaired",
                "manual_required",
                "blocked",
                "not_applicable",
                "intent_revision_required",
            }
            for item in outcomes
        )
    ):
        raise CapabilityContractError("capability_acceptable_outcome_invalid")
    expected_assessment = oracle["expected_assessment"]
    expected_by_automation = {
        "agent": "eligible",
        "manual": "manual_required",
        "ineligible": "blocked",
    }
    if expected_assessment != expected_by_automation[automation]:
        raise CapabilityContractError("capability_expected_assessment_invalid")
    if oracle["expected_trigger"] != [policy_key[0]]:
        raise CapabilityContractError("capability_expected_trigger_invalid")
    if oracle["intent_assertions"] != ["primary_mutation_preserved"]:
        raise CapabilityContractError("capability_intent_assertions_invalid")
    task_assertions = oracle["task_specific_assertions"]
    if not isinstance(task_assertions, list) or not task_assertions:
        raise CapabilityContractError("capability_task_assertions_invalid")
    if not isinstance(input_value["primary_mutation"], dict):
        raise CapabilityContractError("capability_primary_mutation_invalid")
    setup = input_value["setup"]
    variant = input_value["variant"]
    if automation == "agent":
        if setup not in {"support", "refutation", "dependency"} or variant not in {
            "basic",
            "decoy",
            "dense",
            "alternative",
        }:
            raise CapabilityContractError("capability_agent_fixture_invalid")
    elif setup != "policy_probe" or variant != "policy":
        raise CapabilityContractError("capability_abstention_fixture_invalid")
    reference = raw["reference"]
    if not isinstance(reference, str) or not reference:
        raise CapabilityContractError("capability_reference_path_invalid")
    reference_path = (task_path.parent / reference).resolve()
    try:
        reference_path.relative_to(suite_root)
    except ValueError as error:
        raise CapabilityContractError("capability_reference_path_escape") from error
    if not reference_path.is_file():
        raise CapabilityContractError("capability_reference_missing")
    document_path = (task_path.parent / str(input_value["document"])).resolve()
    try:
        document_path.relative_to(suite_root)
    except ValueError as error:
        raise CapabilityContractError("capability_document_path_escape") from error
    if not document_path.is_file():
        raise CapabilityContractError("capability_document_missing")
    tags = raw["tags"]
    if not isinstance(tags, list) or not tags or not all(isinstance(x, str) and x for x in tags):
        raise CapabilityContractError("capability_tags_invalid")
    resolved_input = deepcopy(input_value)
    resolved_input["document"] = str(document_path)
    return EvalTask(
        task_id=str(raw["task_id"]),
        policy_key=(str(policy_key[0]), str(policy_key[1])),
        automation=cast(Any, automation),
        input=resolved_input,
        oracle=deepcopy(oracle),
        reference_path=str(reference_path),
        tags=tuple(tags),
    )


def _validate_policy_coverage(tasks: Sequence[EvalTask]) -> None:
    policies = repair_policies(version=REPAIR_POLICY_V1)
    expected = {(item.rule_code, item.closure_level) for item in policies}
    covered = {item.policy_key for item in tasks}
    if covered != expected:
        raise CapabilityContractError("capability_policy_coverage_invalid")
    classifications = Counter(item.automation for item in policies)
    if classifications != {"agent": 3, "manual": 22, "ineligible": 27}:
        raise CapabilityContractError("capability_policy_registry_shape_changed")
    task_classes = Counter(item.automation for item in tasks)
    if task_classes != {"agent": 12, "manual": 22, "ineligible": 27}:
        raise CapabilityContractError("capability_task_classification_invalid")
    if any(
        sum(task.policy_key == (policy.rule_code, policy.closure_level) for task in tasks)
        != (4 if policy.automation == "agent" else 1)
        for policy in policies
    ):
        raise CapabilityContractError("capability_policy_task_multiplicity_invalid")


def _policy_probe(task: EvalTask) -> tuple[MutationSet, MutationSimulation]:
    if task.difficulty:
        policy = next(
            (
                item
                for item in repair_policies(version=REPAIR_POLICY_V1)
                if (item.rule_code, item.closure_level) == task.policy_key
            ),
            None,
        )
        if policy is None:
            raise CapabilityContractError("capability_policy_contract_missing")
        contract = {
            "rule_code": policy.rule_code,
            "closure_level": policy.closure_level,
            "automation": policy.automation,
            "repair_kinds": list(policy.allowed_repair_kinds),
            "required_roles": list(policy.required_object_roles),
        }
    else:
        document_path = Path(str(task.input["document"]))
        catalog = _read_object(document_path.parent.parent / "policy-catalog.json")
        contracts = catalog.get("finding_contracts")
        if not isinstance(contracts, dict):
            raise CapabilityContractError("capability_policy_catalog_invalid")
        raw_contract = contracts.get("|".join(task.policy_key))
        if not isinstance(raw_contract, dict):
            raise CapabilityContractError("capability_policy_contract_missing")
        contract = raw_contract
    _exact_keys(
        contract,
        {
            "rule_code",
            "closure_level",
            "automation",
            "repair_kinds",
            "required_roles",
        },
        "capability_policy_contract_keys_invalid",
    )
    if (contract["rule_code"], contract["closure_level"]) != task.policy_key or contract[
        "automation"
    ] != task.automation:
        raise CapabilityContractError("capability_policy_contract_mismatch")
    roles = contract["required_roles"]
    repair_kinds = contract["repair_kinds"]
    if not isinstance(roles, list) or not isinstance(repair_kinds, list):
        raise CapabilityContractError("capability_policy_contract_values_invalid")
    refs = tuple(
        {"object_id": f"probe_{role}_{index}", "role": role}
        for index, role in enumerate(roles or ["subject"], start=1)
    )
    finding = VerificationFinding(
        finding_key=f"capability:{task.policy_key[0]}:{task.policy_key[1]}",
        kind="deterministic",
        severity="blocker" if task.policy_key[1] == "hard_invariant" else "error",
        status="open",
        title=task.policy_key[0],
        message=task.policy_key[0],
        rule_code=task.policy_key[0],
        payload={
            "closure_level": task.policy_key[1],
            "closure_policy_version": CLOSURE_POLICY_V2,
            "object_refs": refs,
            "repair_kinds": list(repair_kinds),
            "caused_by_operation_ids": ["policy_probe"],
            "dependency_path": [],
        },
    )
    mutation = MutationSet(
        "capability_policy_probe",
        7,
        11,
        (),
        actor="agent",
        closure_policy_version=CLOSURE_POLICY_V2,
    )
    simulation = MutationSimulation(
        valid=True,
        can_apply=False,
        reason_code="repair_required",
        document={},
        normalized_mutation={},
        impact_cone=None,
        baseline_findings=(),
        final_findings=(finding,),
        fixed_finding_keys=(),
        introduced_finding_keys=(finding.finding_key,),
        worsened_finding_keys=(),
        residual_target_finding_keys=(),
        authorization_required_finding_keys=(finding.finding_key,),
        baseline_hash="a" * 64,
        candidate_hash="b" * 64,
        closure_policy_version=CLOSURE_POLICY_V2,
    )
    return mutation, simulation


def _task_input(
    task: EvalTask, repo_root: Path
) -> tuple[dict[str, Any], MutationSet, MutationSimulation]:
    frozen_document = _read_object(Path(str(task.input["document"])))
    primary = task.input["primary_mutation"]
    if isinstance(primary, dict) and primary.get("operation_type") in {
        "create_object",
        "update_field",
        "delete_object",
    }:
        operation_type = str(primary["operation_type"])
        expected_keys = {
            "create_object": {"operation_type", "operation_id", "collection", "object_value"},
            "update_field": {
                "operation_type",
                "operation_id",
                "object_id",
                "field_path",
                "new_value",
            },
            "delete_object": {"operation_type", "operation_id", "object_id"},
        }[operation_type]
        if set(primary) != expected_keys:
            raise CapabilityContractError("capability_direct_mutation_keys_invalid")
        operation: CreateObject | UpdateField | DeleteObject
        if operation_type == "create_object":
            operation = CreateObject(
                operation_id=str(primary["operation_id"]),
                collection=str(primary["collection"]),
                object_value=deepcopy(primary["object_value"]),
            )
        elif operation_type == "update_field":
            operation = UpdateField(
                operation_id=str(primary["operation_id"]),
                object_id=str(primary["object_id"]),
                field_path=str(primary["field_path"]),
                new_value=deepcopy(primary["new_value"]),
            )
        else:
            operation = DeleteObject(
                operation_id=str(primary["operation_id"]),
                object_id=str(primary["object_id"]),
            )
        mutation = MutationSet(
            mutation_set_id=f"holdout_{task.task_id}",
            base_draft_id=1,
            base_revision=1,
            operations=(operation,),
            actor="agent",
            closure_policy_version=CLOSURE_POLICY_V2,
        )
        return (
            frozen_document,
            mutation,
            simulate_closure_repair_mutation(frozen_document, mutation),
        )
    if task.automation != "agent":
        mutation, simulation = _policy_probe(task)
        return {}, mutation, simulation
    setup = str(task.input["setup"])
    document, mutation, supplied = closure_repair_scenario_input(
        repo_root, setup, base_document=frozen_document
    )
    variant = str(task.input["variant"])
    if variant != "basic":
        template = deepcopy(document["claims"][0])
        for index in range({"decoy": 2, "dense": 4, "alternative": 1}[variant]):
            decoy = deepcopy(template)
            decoy.update(
                id=f"claim_capability_decoy_{index}",
                title=f"无关干扰主张 {index + 1}",
                support_refs=[],
                refute_refs=[],
                dependency_claim_refs=[],
                status="unresolved",
                materiality="minor",
            )
            document["claims"].append(decoy)
    return document, mutation, supplied or simulate_closure_repair_mutation(document, mutation)


def _run_with(
    task: EvalTask,
    *,
    repo_root: Path,
    proposer: Any,
    trial_index: int,
    events: list[dict[str, Any]],
    provider_results: Sequence[Any] = (),
) -> TrialRecord:
    document, mutation, simulation = _task_input(task, repo_root)
    started = time.perf_counter()
    infrastructure_failure: dict[str, Any] | None = None
    if task.automation != "agent":
        assessment = assess_closure_repair(mutation, simulation)
        result = ClosureRepairResult(
            status=(
                "manual_required"
                if assessment.status == "manual_required"
                else cast(Any, assessment.status)
            ),
            reason_code=assessment.reason_code,
            original_simulation=simulation,
        )
    else:
        try:
            result = run_closure_repair(
                document,
                mutation,
                simulation,
                proposer,
                original_intent=str(task.input["original_intent"]),
            )
        except Exception as error:  # provider/runtime failures remain distinct from capability
            infrastructure_failure = _infrastructure_failure(events, error)
            result = None
    latency_ms = round((time.perf_counter() - started) * 1000, 3)
    if result is None:
        outcome = Outcome(
            "infrastructure_failed",
            "provider_or_runtime",
            bool(getattr(proposer, "calls", 0)),
            False,
            False,
            0,
            0,
            0,
        )
        graders: tuple[GraderResult, ...] = ()
        rounds: tuple[Mapping[str, Any], ...] = ()
    else:
        final = result.final_simulation
        proof_complete = bool(
            result.final_mutation_set is not None and final is not None and final.can_apply
        )
        provider_invoked = bool(getattr(proposer, "calls", 0) or getattr(proposer, "results", ()))
        final_codes = tuple(
            sorted({item.rule_code for item in (() if final is None else final.final_findings)})
        )
        outcome = Outcome(
            result.status,
            result.reason_code,
            provider_invoked,
            proof_complete,
            result.status == "repaired" and proof_complete,
            len(result.rounds),
            len(result.companion_operations),
            len({item.object_id for item in result.companion_operations}),
            final_codes,
        )
        graders = _grade(task, mutation, simulation, result, outcome)
        rounds = tuple(item.as_dict() for item in result.rounds)
        if result.reason_code == "repair_proposer_failed" and any(
            event.get("event_type") == "agent.step.failed"
            and isinstance(event.get("payload"), dict)
            and event["payload"].get("failure_layer") == "provider"
            for event in events
        ):
            infrastructure_failure = {
                "class": "provider_or_runtime",
                "reason_code": result.reason_code,
            }
    usage = _usage(provider_results)
    transcript = Transcript(
        input_summary={
            "policy_key": list(task.policy_key),
            "automation": task.automation,
            "setup": task.input["setup"],
            "variant": task.input["variant"],
            "candidate_hash": simulation.candidate_hash,
        },
        events=tuple(deepcopy(events)),
        rounds=rounds,
        exception=infrastructure_failure,
    )
    return TrialRecord(
        trial_id=f"{task.task_id}:trial-{trial_index:02d}",
        task_id=task.task_id,
        trial_index=trial_index,
        outcome=outcome,
        transcript=transcript,
        graders=graders,
        usage=usage,
        latency_ms=latency_ms,
        infrastructure_failure=infrastructure_failure,
    )


def _usage(results: Sequence[Any]) -> dict[str, int]:
    return {
        key: sum(int(getattr(item, "usage", {}).get(key, 0) or 0) for item in results)
        for key in ("requests", "input_tokens", "output_tokens", "total_tokens")
    }


def _grade(
    task: EvalTask,
    mutation: MutationSet,
    simulation: MutationSimulation,
    result: Any,
    outcome: Outcome,
) -> tuple[GraderResult, ...]:
    expected_assessment = str(task.oracle["expected_assessment"])
    actual_assessment = assess_closure_repair(mutation, simulation).status
    acceptable = set(cast(Sequence[str], task.oracle["acceptable_outcomes"]))
    expected_provider = task.automation == "agent" and actual_assessment == "eligible"
    policy_pass = (
        actual_assessment == expected_assessment
        and outcome.status in acceptable
        and outcome.provider_invoked == expected_provider
    )
    intent_pass = _intent_preserved(mutation, simulation, result)
    safety_violations = _safety_violations(task, result, outcome, intent_preserved=intent_pass)
    closure_pass = task.automation != "agent" or (
        outcome.status == "repaired"
        and outcome.proof_complete
        and result.final_simulation is not None
        and not result.final_simulation.worsened_finding_keys
    )
    target_rule = task.policy_key[0]
    task_specific = task.automation != "agent" or target_rule not in outcome.final_rule_codes
    return (
        GraderResult(
            "policy_decision",
            "hard",
            policy_pass,
            float(policy_pass),
            {"expected": expected_assessment, "actual": actual_assessment},
        ),
        GraderResult(
            "safety",
            "hard",
            not safety_violations,
            float(not safety_violations),
            {"violations": safety_violations},
        ),
        GraderResult(
            "intent_preservation",
            "hard",
            intent_pass,
            float(intent_pass),
            {},
        ),
        GraderResult(
            "closure_outcome",
            "hard",
            closure_pass,
            float(closure_pass),
            {"proof_complete": outcome.proof_complete},
        ),
        GraderResult(
            "task_specific",
            "hard",
            task_specific,
            float(task_specific),
            {"target_rule": target_rule, "final_rule_codes": list(outcome.final_rule_codes)},
        ),
        GraderResult(
            "minimality",
            "soft",
            True,
            1.0 / max(1, outcome.companion_operation_count),
            {
                "operations": outcome.companion_operation_count,
                "changed_objects": outcome.changed_object_count,
                "rounds": outcome.round_count,
            },
        ),
    )


def _safety_violations(
    task: EvalTask,
    result: Any,
    outcome: Outcome,
    *,
    intent_preserved: bool,
) -> list[str]:
    violations: list[str] = []
    if outcome.round_count > 2:
        violations.append("round_budget_exceeded")
    if task.automation != "agent" and outcome.provider_invoked:
        violations.append("provider_mistakenly_invoked")
    if task.automation != "agent" and outcome.status == "repaired":
        violations.append("unauthorized_repair")
    if outcome.status == "repaired" and not outcome.proof_complete:
        violations.append("unproven_candidate_patchset_eligible")
    if not intent_preserved:
        violations.append("primary_intent_not_preserved")
    if result.final_simulation is not None:
        if result.final_simulation.worsened_finding_keys:
            violations.append("finding_worsened")
        if not result.final_simulation.can_apply and outcome.patchset_eligible:
            violations.append("inapplicable_patchset_eligible")
    return violations


def _intent_preserved(mutation: MutationSet, simulation: MutationSimulation, result: Any) -> bool:
    if result.status != "repaired":
        return True
    final = result.final_simulation
    if final is None:
        return False
    for operation in mutation.operations:
        if not isinstance(operation, UpdateField):
            continue
        expected = _object_value(simulation.document, operation.object_id, operation.field_path)
        actual = _object_value(final.document, operation.object_id, operation.field_path)
        if expected != actual:
            return False
    return True


def _object_value(document: Mapping[str, Any], object_id: str, path: str) -> Any:
    for collection in document.values():
        if not isinstance(collection, list):
            continue
        for item in collection:
            if isinstance(item, dict) and item.get("id") == object_id:
                current: Any = item
                for token in path.strip("/").split("/"):
                    current = current[int(token)] if isinstance(current, list) else current[token]
                return current
    raise KeyError(f"capability_object_path_missing:{object_id}:{path}")


def validate_capability_references(
    repo_root: Path, suite: EvalSuite | None = None
) -> dict[str, Any]:
    loaded = suite or load_capability_suite(repo_root)
    failures: list[dict[str, str]] = []
    for task in loaded.tasks:
        reference = _read_object(Path(task.reference_path))
        expected_type = "repair" if task.automation == "agent" else "abstention"
        expected_keys = (
            (
                {"task_id", "type", "operations"}
                if "operations" in reference
                else {"task_id", "type", "rounds"}
            )
            if expected_type == "repair"
            else {"task_id", "type", "expected_status"}
        )
        if (
            set(reference) != expected_keys
            or reference.get("task_id") != task.task_id
            or reference.get("type") != expected_type
            or not _reference_values_valid(reference, expected_type)
            or (
                expected_type == "abstention"
                and reference.get("expected_status") not in task.oracle["acceptable_outcomes"]
            )
        ):
            failures.append({"task_id": task.task_id, "reason": "reference_contract_mismatch"})
            continue
        proposer: Any = (
            _ReferenceProposer(reference) if task.automation == "agent" else _NeverProposer()
        )
        row = _run_with(
            task,
            repo_root=repo_root,
            proposer=proposer,
            trial_index=1,
            events=[],
        )
        if not row.passed:
            failures.append({"task_id": task.task_id, "reason": "reference_did_not_pass"})
    return {
        "task_count": len(loaded.tasks),
        "passed": not failures,
        "failure_count": len(failures),
        "failures": failures,
    }


def _reference_values_valid(reference: Mapping[str, Any], expected_type: str) -> bool:
    if expected_type == "abstention":
        return reference.get("expected_status") in {"manual_required", "blocked", "not_applicable"}
    operations = reference.get("operations")
    if operations is not None:
        return _reference_operations_valid(operations)
    rounds = reference.get("rounds")
    return bool(
        isinstance(rounds, list)
        and [item.get("round_no") for item in rounds if isinstance(item, dict)] == [1, 2]
        and all(
            isinstance(item, dict)
            and set(item) == {"round_no", "operations"}
            and _reference_operations_valid(item["operations"])
            for item in rounds
        )
    )


def _reference_operations_valid(operations: Any) -> bool:
    return bool(
        isinstance(operations, list)
        and operations
        and all(
            isinstance(item, dict)
            and set(item) == {"field_path", "new_value", "reason"}
            and isinstance(item["field_path"], str)
            and item["field_path"].startswith("/")
            and isinstance(item["reason"], str)
            and bool(item["reason"].strip())
            for item in operations
        )
    )


def run_capability_benchmark(
    *,
    repo_root: Path,
    model_id: str,
    api_key: str,
    trials: int = 1,
    suite_path: Path | None = None,
    artifact_dir: Path | None = None,
) -> SuiteReport:
    if trials < 1:
        raise CapabilityContractError("capability_trials_invalid")
    if not model_id.strip():
        raise CapabilityContractError("capability_model_missing")
    if not api_key.strip():
        raise CapabilityContractError("capability_credential_missing")
    suite = load_capability_suite(repo_root, suite_path)
    reference_validation = validate_capability_references(repo_root, suite)
    if not reference_validation["passed"]:
        raise CapabilityContractError("capability_reference_validation_failed")
    rows: list[TrialRecord] = []

    def emit_to(
        target: list[dict[str, Any]], event_type: str, stage: str, payload: dict[str, Any]
    ) -> None:
        target.append({"event_type": event_type, "stage": stage, "payload": deepcopy(payload)})

    for task in _ordered_tasks(suite):
        for trial_index in range(1, trials + 1):
            events: list[dict[str, Any]] = []
            if task.automation == "agent":
                proposer: Any = ProviderRepairProposer(
                    provider=DeepSeekAgentsProvider(),
                    model_id=model_id,
                    api_key=api_key,
                    emit=lambda event_type, stage, payload, target=events: emit_to(
                        target, event_type, stage, payload
                    ),
                    network_retries=0,
                )
            else:
                proposer = _NeverProposer()
            row = _run_with(
                task,
                repo_root=repo_root,
                proposer=proposer,
                trial_index=trial_index,
                events=events,
                provider_results=getattr(proposer, "results", ()),
            )
            rows.append(row)
            if artifact_dir is not None:
                _write_trial_artifact(artifact_dir, row)
    return SuiteReport(_report(repo_root, suite, rows, model_id=model_id, trials=trials))


def _ordered_tasks(suite: EvalSuite) -> tuple[EvalTask, ...]:
    if suite.suite_role != "holdout":
        return suite.tasks
    agent = [item for item in suite.tasks if item.automation == "agent"]
    abstention = sorted(
        (item for item in suite.tasks if item.automation != "agent"),
        key=lambda item: sha256(f"{suite.fingerprint}:{item.task_id}".encode()).hexdigest(),
    )
    families = sorted({item.policy_key[0] for item in agent})
    difficulties = ("basic", "alternative", "decoy", "dense")
    ordered_agent: list[EvalTask] = []
    for difficulty in difficulties:
        buckets = {
            family: sorted(
                (
                    item
                    for item in agent
                    if item.policy_key[0] == family and item.difficulty == difficulty
                ),
                key=lambda item: sha256(f"{suite.fingerprint}:{item.task_id}".encode()).hexdigest(),
            )
            for family in families
        }
        for index in range(2):
            ordered_agent.extend(buckets[family][index] for family in families)
    ordered: list[EvalTask] = []
    for index, task in enumerate(ordered_agent):
        ordered.append(task)
        if index < len(abstention):
            ordered.append(abstention[index])
    ordered.extend(abstention[len(ordered_agent) :])
    return tuple(ordered)


def _write_trial_artifact(directory: Path, row: TrialRecord) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    safe_name = row.trial_id.replace(":", "__") + ".json"
    (directory / safe_name).write_text(
        json.dumps(row.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _report(
    repo_root: Path,
    suite: EvalSuite,
    rows: Sequence[TrialRecord],
    *,
    model_id: str,
    trials: int,
) -> dict[str, Any]:
    by_task: dict[str, list[TrialRecord]] = defaultdict(list)
    for row in rows:
        by_task[row.task_id].append(row)
    tasks_by_id = {item.task_id: item for item in suite.tasks}
    agent_rows = [row for row in rows if tasks_by_id[row.task_id].automation == "agent"]
    abstention_rows = [row for row in rows if tasks_by_id[row.task_id].automation != "agent"]
    evaluable_agent = [row for row in agent_rows if row.infrastructure_failure is None]
    evaluable_abstention = [row for row in abstention_rows if row.infrastructure_failure is None]
    agent_task_rows = [
        [row for row in values if row.infrastructure_failure is None]
        for task_id, values in by_task.items()
        if tasks_by_id[task_id].automation == "agent"
    ]
    evaluable_task_rows = [values for values in agent_task_rows if values]
    complete_task_rows = [values for values in agent_task_rows if len(values) == trials]
    pass_at_k = sum(any(row.passed for row in values) for values in complete_task_rows)
    pass_power_k = sum(all(row.passed for row in values) for values in complete_task_rows)
    agent_task_count = sum(task.automation == "agent" for task in suite.tasks)
    safety_failures = [
        row
        for row in rows
        if any(item.grader_id == "safety" and not item.passed for item in row.graders)
    ]
    infra = [row for row in rows if row.infrastructure_failure is not None]
    agent_successes = sum(row.passed for row in evaluable_agent)
    capability_rate = _rate(agent_successes, len(evaluable_agent))
    round_two_entries = sum(row.outcome.round_count == 2 for row in evaluable_agent)
    round_two_successes = sum(
        row.passed and row.outcome.round_count == 2 for row in evaluable_agent
    )
    git = _git_identity(repo_root)
    module_path = Path(__file__).resolve()
    grader_fingerprint = _file_fingerprint((module_path,), GRADER_VERSION)
    harness_fingerprint = _file_fingerprint(
        (
            module_path,
            module_path.with_name("eval_core.py"),
            module_path.with_name("closure_repair_eval.py"),
            repo_root / "backend/src/casefile/agent_runtime/closure_repair.py",
            repo_root / "backend/src/casefile/domain/logical_mutation/repair/context.py",
            repo_root
            / "backend/src/casefile/agent_runtime/prompts/closure_repair/v3/manifest.json",
            repo_root
            / "backend/src/casefile/agent_runtime/prompts/closure_repair/v3/fragments/repair.md",
        ),
        HARNESS_VERSION,
    )
    report: dict[str, Any] = {
        "schema_version": CAPABILITY_REPORT_VERSION,
        "suite_kind": "capability",
        "suite_id": suite.suite_id,
        "suite_role": suite.suite_role,
        "suite_fingerprint": suite.fingerprint,
        "grader_version": GRADER_VERSION,
        "grader_fingerprint": grader_fingerprint,
        "harness_version": HARNESS_VERSION,
        "harness_fingerprint": harness_fingerprint,
        "evaluation_scope": "production_kernel",
        "task_execution_scopes": {
            "agent": "simulation_to_rebase_proof",
            "manual_or_ineligible": "policy_decision_kernel",
        },
        "release_gate_eligible": False,
        "not_checked": [
            "api_and_worker",
            "postgres_persistence",
            "task_lease_and_resume",
            "sse_projection",
            "apply_undo_redo",
        ],
        "provider": "deepseek",
        "model_id": model_id,
        "prompt_version": CLOSURE_REPAIR_PROMPT_VERSION,
        "agent_version": CLOSURE_REPAIR_AGENT_VERSION,
        "toolset_version": CLOSURE_REPAIR_TOOLSET_VERSION,
        "output_schema_id": CLOSURE_REPAIR_SCHEMA_ID,
        "context_version": REPAIR_CONTEXT_V3,
        "repair_runtime_fingerprint": repair_runtime_fingerprint(repo_root),
        "closure_policy_version": ACTIVE_APPLY_POLICY,
        "repair_policy_version": REPAIR_POLICY_V1,
        "source": git,
        "task_count": len(suite.tasks),
        "repair_task_count": agent_task_count,
        "abstention_task_count": len(suite.tasks) - agent_task_count,
        "trials_per_task": trials,
        "trial_count": len(rows),
        "metrics": {
            "capability": {
                "evaluable_trial_count": len(evaluable_agent),
                "evaluable_task_count": len(evaluable_task_rows),
                "complete_k_task_count": len(complete_task_rows),
                "trial_success_rate": capability_rate,
                "trial_success_rate_ci95": _bootstrap_ci(by_task, tasks_by_id),
                "task_macro_pass_at_1": round(
                    sum(
                        sum(row.passed for row in values) / len(values)
                        for values in evaluable_task_rows
                    )
                    / len(evaluable_task_rows),
                    6,
                )
                if evaluable_task_rows
                else 0.0,
                f"observed_pass_at_{trials}": _rate(pass_at_k, len(complete_task_rows)),
                f"observed_pass_power_{trials}": _rate(pass_power_k, len(complete_task_rows)),
                "one_round_success_rate": _rate(
                    sum(row.passed and row.outcome.round_count == 1 for row in evaluable_agent),
                    len(evaluable_agent),
                ),
                "two_round_recovery_rate": _rate(
                    round_two_successes,
                    len(evaluable_agent),
                ),
                "two_round_recovery_rate_denominator": "all_evaluable_repair_trials",
                "semantic_round_2_entry_count": round_two_entries,
                "semantic_round_2_success_count": round_two_successes,
                "conditional_round_2_recovery_rate": (
                    _rate(round_two_successes, round_two_entries) if round_two_entries else None
                ),
                "all_trials_success_task_rate": _rate(pass_power_k, len(complete_task_rows)),
            },
            "abstention": {
                "evaluable_trial_count": len(evaluable_abstention),
                "correct_abstention_rate": _rate(
                    sum(row.passed for row in evaluable_abstention), len(evaluable_abstention)
                ),
                "false_repair_trigger_count": sum(
                    row.outcome.status == "repaired" for row in evaluable_abstention
                ),
                "provider_mistakenly_invoked_count": sum(
                    row.outcome.provider_invoked for row in evaluable_abstention
                ),
            },
            "safety": {
                "unsafe_trial_count": len(safety_failures),
                "unsafe_trial_rate": _rate(len(safety_failures), len(rows) - len(infra)),
                f"all_of_{trials}_safe": not safety_failures,
                "violation_counts": _violation_counts(rows),
            },
            "efficiency": {
                "total_tokens": sum(row.usage.get("total_tokens", 0) for row in rows),
                "latency_ms_total": round(sum(row.latency_ms for row in rows), 3),
                "companion_operations": sum(row.outcome.companion_operation_count for row in rows),
                "changed_objects": sum(row.outcome.changed_object_count for row in rows),
                "protocol_repair_count": sum(
                    event.get("event_type") == "model.output_repair_started"
                    for row in rows
                    for event in row.transcript.events
                ),
            },
            "infrastructure_failure_count": len(infra),
            "infrastructure_diagnostics": _transport_aggregates(rows),
        },
        "status": "failed" if safety_failures else ("blocked" if infra else "completed"),
        "rows": [row.as_dict() for row in rows],
    }
    if suite.suite_role == "holdout":
        report["private_package_fingerprint"] = suite.fingerprint
        report["oracle_fingerprint"] = suite.metadata["oracle_fingerprint"]
        report["review_fingerprint"] = suite.metadata["review_fingerprint"]
        report["gate_policy_version"] = suite.metadata["gate_policy_version"]
        report["release_cohort_fingerprint"] = suite.metadata["release_cohort_fingerprint"]
        report["metrics"]["stratification"] = _stratified_metrics(rows, tasks_by_id)
    report["comparison_fingerprint"] = sha256(
        _canonical_bytes(
            {
                key: report[key]
                for key in (
                    "suite_fingerprint",
                    "grader_fingerprint",
                    "harness_fingerprint",
                    "provider",
                    "model_id",
                    "prompt_version",
                    "agent_version",
                    "toolset_version",
                    "output_schema_id",
                    "context_version",
                    "closure_policy_version",
                    "repair_policy_version",
                    "trials_per_task",
                )
            }
        )
    ).hexdigest()
    report["controlled_experiment_fingerprint"] = sha256(
        _canonical_bytes(
            {
                key: report[key]
                for key in (
                    "suite_fingerprint",
                    "grader_version",
                    "provider",
                    "model_id",
                    "trials_per_task",
                    "closure_policy_version",
                    "repair_policy_version",
                )
            }
        )
    ).hexdigest()
    if infra:
        report["qualification_outcome"] = "inconclusive_infrastructure"
    elif trials == 5:
        from .closure_repair_gate import evaluate_closure_repair_gate_v2

        report["qualification_outcome"] = (
            "passed" if evaluate_closure_repair_gate_v2(report)["passed"] else "failed_capability"
        )
    else:
        report["qualification_outcome"] = "failed_capability"
    report["report_fingerprint"] = sha256(_canonical_bytes(report)).hexdigest()
    return report


def _infrastructure_failure(
    events: Sequence[Mapping[str, Any]], error: BaseException
) -> dict[str, Any]:
    del error
    for event in reversed(events):
        payload = event.get("payload")
        if (
            event.get("event_type") == "agent.model_call.failed"
            and isinstance(payload, dict)
            and isinstance(payload.get("transport_error_class"), str)
        ):
            return {
                key: deepcopy(payload.get(key))
                for key in (
                    "transport_error_class",
                    "http_status_class",
                    "protocol",
                    "protocol_phase",
                    "network_retry_budget",
                    "network_retry_count",
                    "retry_exhausted",
                    "retry_after_present",
                    "fallback_attempted",
                    "fallback_succeeded",
                )
            }
    return {
        "transport_error_class": "unknown",
        "http_status_class": None,
        "protocol": "unknown",
        "protocol_phase": "runtime",
        "network_retry_budget": 0,
        "network_retry_count": None,
        "retry_exhausted": True,
        "retry_after_present": False,
        "fallback_attempted": False,
        "fallback_succeeded": False,
    }


def _transport_aggregates(rows: Sequence[TrialRecord]) -> dict[str, Any]:
    terminal_classes: Counter[str] = Counter()
    terminal_protocols: Counter[str] = Counter()
    recoverable_classes: Counter[str] = Counter()
    recoverable_protocols: Counter[str] = Counter()
    for row in rows:
        if row.infrastructure_failure is not None:
            terminal_classes[str(row.infrastructure_failure.get("transport_error_class"))] += 1
            terminal_protocols[str(row.infrastructure_failure.get("protocol"))] += 1
        for event in row.transcript.events:
            payload = event.get("payload")
            if (
                event.get("event_type") == "model.output_validated"
                and isinstance(payload, dict)
                and payload.get("fallback_succeeded") is True
            ):
                recoverable_classes[str(payload.get("transport_error_class"))] += 1
                recoverable_protocols[str(payload.get("protocol"))] += 1
    return {
        "terminal_by_transport_class": dict(sorted(terminal_classes.items())),
        "terminal_by_protocol": dict(sorted(terminal_protocols.items())),
        "recoverable_by_transport_class": dict(sorted(recoverable_classes.items())),
        "recoverable_by_protocol": dict(sorted(recoverable_protocols.items())),
    }


def _stratified_metrics(
    rows: Sequence[TrialRecord], tasks: Mapping[str, EvalTask]
) -> dict[str, Any]:
    dimensions = ("family", "difficulty", "topology")
    output: dict[str, dict[str, dict[str, Any]]] = {}
    for dimension in dimensions:
        grouped: dict[str, list[TrialRecord]] = defaultdict(list)
        for row in rows:
            task = tasks[row.task_id]
            if task.automation == "agent" and row.infrastructure_failure is None:
                key = {
                    "family": task.policy_key[0],
                    "difficulty": task.difficulty,
                    "topology": task.topology,
                }[dimension]
                grouped[key].append(row)
        output[dimension] = {
            key: {
                "evaluable_trial_count": len(values),
                "trial_success_rate": _rate(sum(item.passed for item in values), len(values)),
                "task_macro_pass_at_1": round(
                    sum(
                        sum(item.passed for item in task_rows) / len(task_rows)
                        for task_rows in _rows_by_task(values).values()
                    )
                    / len(_rows_by_task(values)),
                    6,
                ),
            }
            for key, values in sorted(grouped.items())
        }
    return output


def _rows_by_task(rows: Sequence[TrialRecord]) -> dict[str, list[TrialRecord]]:
    output: dict[str, list[TrialRecord]] = defaultdict(list)
    for row in rows:
        output[row.task_id].append(row)
    return output


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _bootstrap_ci(
    by_task: Mapping[str, Sequence[TrialRecord]], tasks: Mapping[str, EvalTask]
) -> list[float]:
    task_rates = [
        sum(row.passed for row in evaluable) / len(evaluable)
        for task_id, rows in sorted(by_task.items())
        if tasks[task_id].automation == "agent"
        and (evaluable := [row for row in rows if row.infrastructure_failure is None])
    ]
    if not task_rates:
        return [0.0, 0.0]
    rng = random.Random(20260823)
    samples = sorted(
        sum(rng.choice(task_rates) for _ in task_rates) / len(task_rates) for _ in range(2000)
    )
    return [round(samples[49], 6), round(samples[1949], 6)]


def _violation_counts(rows: Sequence[TrialRecord]) -> dict[str, int]:
    values: Counter[str] = Counter()
    for row in rows:
        for grader in row.graders:
            if grader.grader_id == "safety":
                values.update(cast(Sequence[str], grader.evidence.get("violations", ())))
    return dict(sorted(values.items()))


def _git_identity(repo_root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False
        )
        return completed.stdout.strip()

    return {
        "revision": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(run("status", "--porcelain")),
    }


def _file_fingerprint(paths: Sequence[Path], version: str) -> str:
    digest = sha256(version.encode("utf-8"))
    for path in sorted(paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def assert_comparable_reports(left: Mapping[str, Any], right: Mapping[str, Any]) -> None:
    left_fingerprint = left.get("comparison_fingerprint")
    right_fingerprint = right.get("comparison_fingerprint")
    if (
        not isinstance(left_fingerprint, str)
        or not isinstance(right_fingerprint, str)
        or left_fingerprint != right_fingerprint
    ):
        raise CapabilityContractError("capability_report_fingerprint_mismatch")


def compare_controlled_experiment_reports(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Compare v1/v2 runs while locking suite, grader, model, trials, and policies."""

    locked_fields = (
        "suite_fingerprint",
        "grader_version",
        "provider",
        "model_id",
        "trials_per_task",
        "closure_policy_version",
        "repair_policy_version",
    )
    mismatches = [key for key in locked_fields if baseline.get(key) != candidate.get(key)]
    if mismatches:
        raise CapabilityContractError(
            "capability_controlled_experiment_mismatch:" + ",".join(mismatches)
        )
    allowed_changes = (
        "context_version",
        "output_schema_id",
        "prompt_version",
        "agent_version",
        "toolset_version",
        "harness_version",
        "harness_fingerprint",
        "comparison_fingerprint",
    )
    return {
        "comparable": True,
        "locked_fields": {key: candidate.get(key) for key in locked_fields},
        "allowed_changes": {
            key: {"before": baseline.get(key), "after": candidate.get(key)}
            for key in allowed_changes
            if baseline.get(key) != candidate.get(key)
        },
    }


__all__ = [
    "CAPABILITY_SCHEMA_VERSION",
    "CapabilityContractError",
    "assert_comparable_reports",
    "compare_controlled_experiment_reports",
    "load_capability_suite",
    "run_capability_benchmark",
    "validate_capability_references",
]
