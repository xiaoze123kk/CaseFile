"""Bounded, provider-neutral closure repair orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from casefile.domain.logical_mutation.models import MutationSet, UpdateField
from casefile.domain.logical_mutation.repair.assessment import assess_closure_repair
from casefile.domain.logical_mutation.repair.context import (
    build_closure_repair_context,
)
from casefile.domain.logical_mutation.repair.document_diff import (
    RepairDocumentDiffError,
    build_mutation_from_document_diff,
)
from casefile.domain.logical_mutation.repair.models import (
    ClosureRepairAssessment,
    ClosureRepairResult,
    ClosureRepairRound,
    CompanionRepairOperation,
    RepairProposal,
    RepairRunStatus,
    RepairScopeV1,
    RepairUpdateOperation,
)
from casefile.domain.logical_mutation.repair.proposal import RepairProposer
from casefile.domain.logical_mutation.repair.scope import (
    MAX_REPAIR_OPERATIONS,
    RepairScopeError,
    build_repair_scope,
)

if TYPE_CHECKING:
    from casefile.domain.verification_engine import (
        MutationSimulation,
        VerificationEngine,
    )

MAX_REPAIR_ROUNDS = 2
_CLAIM_STATUSES = frozenset(
    {
        "unsupported",
        "partially_supported",
        "supported",
        "refuted",
        "disputed",
        "unresolved",
    }
)
class RepairEngineError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def run_closure_repair(
    baseline_document: Mapping[str, Any],
    original_mutation: MutationSet,
    original_simulation: MutationSimulation,
    proposer: RepairProposer,
    *,
    original_intent: str,
    max_rounds: int = MAX_REPAIR_ROUNDS,
) -> ClosureRepairResult:
    """Attempt at most two deterministic repair rounds, never applying data."""

    if max_rounds < 1 or max_rounds > MAX_REPAIR_ROUNDS:
        raise RepairEngineError("repair_round_budget_invalid")
    from casefile.domain.verification_engine import VerificationEngine

    verifier = VerificationEngine(
        profile="fast",
        closure_policy_version=original_mutation.closure_policy_version,
    )
    replayed_original = verifier.simulate_mutation_set(
        baseline_document, original_mutation
    )
    if not _simulations_match(original_simulation, replayed_original):
        return _result(
            "blocked",
            "repair_original_simulation_mismatch",
            original_simulation,
        )

    current = original_simulation
    assessment = assess_closure_repair(original_mutation, current)
    if assessment.status != "eligible":
        return _assessment_result(original_simulation, assessment)

    repairs: dict[tuple[str, str], CompanionRepairOperation] = {}
    rounds: list[ClosureRepairRound] = []
    seen_hashes = {current.candidate_hash}
    for round_no in range(1, max_rounds + 1):
        try:
            scope = build_repair_scope(original_mutation, current, assessment)
        except RepairScopeError as error:
            status: RepairRunStatus = (
                "intent_revision_required"
                if error.reason_code == "repair_requires_intent_revision"
                else "blocked"
            )
            return _result(
                status,
                error.reason_code,
                original_simulation,
                rounds=rounds,
                repairs=repairs,
                final_simulation=current,
            )
        try:
            context = build_closure_repair_context(
                original_mutation,
                current,
                assessment,
                scope,
                original_intent=original_intent,
            )
        except ValueError as error:
            return _result(
                "blocked",
                str(error).split(":", 1)[0] or "repair_context_invalid",
                original_simulation,
                rounds=rounds,
                repairs=repairs,
                final_simulation=current,
            )
        try:
            proposal = proposer.propose(context, round_no=round_no)
            validated = _validate_proposal(
                proposal,
                context_hash=context.context_hash,
                scope=scope,
                candidate_document=current.document,
                accumulated_operation_count=sum(
                    len(item.proposal.operations) for item in rounds
                ),
            )
        except (RepairEngineError, ValueError) as error:
            reason_code = (
                error.reason_code
                if isinstance(error, RepairEngineError)
                else str(error).split(":", 1)[0]
            )
            return _result(
                "proposal_rejected",
                reason_code or "repair_proposal_invalid",
                original_simulation,
                rounds=rounds,
                repairs=repairs,
                final_simulation=current,
            )
        except Exception:
            return _result(
                "proposal_rejected",
                "repair_proposer_failed",
                original_simulation,
                rounds=rounds,
                repairs=repairs,
                final_simulation=current,
            )

        before_keys = _obligation_keys(assessment)
        updated_repairs = dict(repairs)
        for operation in validated:
            identity = (operation.object_id, operation.field_path)
            original_value = _pointer_value(
                _objects_by_id(original_simulation.document)[operation.object_id][1],
                operation.field_path,
            )
            if operation.new_value == original_value:
                updated_repairs.pop(identity, None)
            else:
                updated_repairs[identity] = CompanionRepairOperation(
                    repair_round=round_no,
                    obligation_keys=operation.obligation_keys,
                    object_id=operation.object_id,
                    field_path=operation.field_path,
                    new_value=deepcopy(operation.new_value),
                    reason=operation.reason.strip(),
                )
        candidate_mutation = _combined_mutation(original_mutation, updated_repairs)
        candidate = verifier.simulate_mutation_set(
            baseline_document, candidate_mutation
        )
        next_assessment = assess_closure_repair(original_mutation, candidate)
        after_keys = (
            _obligation_keys(next_assessment)
            if next_assessment.status in {"eligible", "manual_required"}
            else ()
        )
        outcome = _round_outcome(candidate, next_assessment)
        round_result = ClosureRepairRound(
            round_no=round_no,
            context_hash=context.context_hash,
            proposal=proposal,
            obligation_keys_before=before_keys,
            obligation_keys_after=after_keys,
            candidate_hash_before=current.candidate_hash,
            candidate_hash_after=candidate.candidate_hash,
            outcome=outcome,
        )
        rounds.append(round_result)
        repairs = updated_repairs

        if candidate.can_apply:
            try:
                final_mutation, final_simulation = prove_repair_rebase(
                    baseline_document,
                    original_mutation,
                    candidate,
                    verifier=verifier,
                )
            except RepairEngineError as error:
                return _result(
                    "rebase_mismatch",
                    error.reason_code,
                    original_simulation,
                    rounds=rounds,
                    repairs=repairs,
                    final_simulation=candidate,
                )
            return _result(
                "repaired",
                "repair_succeeded",
                original_simulation,
                rounds=rounds,
                repairs=repairs,
                final_mutation=final_mutation,
                final_simulation=final_simulation,
            )
        if candidate.candidate_hash in seen_hashes:
            return _result(
                "cycle_detected",
                "repair_cycle_detected",
                original_simulation,
                rounds=rounds,
                repairs=repairs,
                final_simulation=candidate,
            )
        seen_hashes.add(candidate.candidate_hash)
        if next_assessment.status != "eligible":
            nested = _assessment_result(original_simulation, next_assessment)
            return _result(
                nested.status,
                nested.reason_code,
                original_simulation,
                rounds=rounds,
                repairs=repairs,
                final_simulation=candidate,
            )
        if not _made_progress(before_keys, after_keys):
            return _result(
                "no_progress",
                "repair_no_progress",
                original_simulation,
                rounds=rounds,
                repairs=repairs,
                final_simulation=candidate,
            )
        current = candidate
        assessment = next_assessment

    return _result(
        "exhausted",
        "repair_exhausted",
        original_simulation,
        rounds=rounds,
        repairs=repairs,
        final_simulation=current,
    )


def prove_repair_rebase(
    baseline_document: Mapping[str, Any],
    original_mutation: MutationSet,
    repaired_simulation: MutationSimulation,
    *,
    verifier: VerificationEngine | None = None,
) -> tuple[MutationSet, MutationSimulation]:
    """Independently diff and replay a repaired candidate for final proof."""

    from casefile.domain.verification_engine import VerificationEngine

    mechanical_paths = _mechanical_paths(repaired_simulation)
    try:
        final_mutation = build_mutation_from_document_diff(
            baseline_document,
            repaired_simulation.document,
            original_mutation,
            mechanical_paths=mechanical_paths,
        )
    except RepairDocumentDiffError as error:
        raise RepairEngineError(error.reason_code) from error
    resolved_verifier = verifier or VerificationEngine(
        profile="fast",
        closure_policy_version=original_mutation.closure_policy_version,
    )
    replayed = resolved_verifier.simulate_mutation_set(
        baseline_document, final_mutation
    )
    if (
        not replayed.valid
        or not replayed.can_apply
        or replayed.candidate_hash != repaired_simulation.candidate_hash
        or replayed.document != repaired_simulation.document
    ):
        raise RepairEngineError("repair_rebase_mismatch")
    return final_mutation, replayed


def _validate_proposal(
    proposal: RepairProposal,
    *,
    context_hash: str,
    scope: RepairScopeV1,
    candidate_document: Mapping[str, Any],
    accumulated_operation_count: int,
) -> tuple[RepairUpdateOperation, ...]:
    if proposal.context_hash != context_hash:
        raise RepairEngineError("repair_proposal_context_stale")
    if len(proposal.operations) > scope.max_operations:
        raise RepairEngineError("repair_operation_budget_exceeded")
    identities = tuple(
        (item.object_id, item.field_path) for item in proposal.operations
    )
    if len(identities) != len(set(identities)):
        raise RepairEngineError("repair_proposal_path_duplicate")
    if accumulated_operation_count + len(identities) > MAX_REPAIR_OPERATIONS:
        raise RepairEngineError("repair_operation_budget_exceeded")

    obligations = {item.obligation_key: item for item in scope.obligations}
    scoped_objects = _objects_by_id(candidate_document)
    for operation in proposal.operations:
        unknown = set(operation.obligation_keys) - set(obligations)
        if unknown:
            raise RepairEngineError("repair_proposal_obligation_unknown")
        if operation.object_id not in scope.read_write_object_ids:
            raise RepairEngineError("repair_scope_violation")
        if operation.field_path not in scope.allowed_paths_for(operation.object_id):
            raise RepairEngineError("repair_scope_violation")
        for obligation_key in operation.obligation_keys:
            obligation = obligations[obligation_key]
            if operation.object_id not in obligation.subject_object_ids:
                raise RepairEngineError("repair_scope_violation")
            allowed = next(
                (
                    item.field_paths
                    for item in obligation.allowed_paths
                    if item.object_id == operation.object_id
                ),
                (),
            )
            if operation.field_path not in allowed:
                raise RepairEngineError("repair_scope_violation")
        current_value = _pointer_value(
            scoped_objects[operation.object_id][1], operation.field_path
        )
        if current_value == operation.new_value:
            raise RepairEngineError("repair_proposal_noop")
        _validate_value(operation, obligations, scoped_objects, scope)
    return proposal.operations


def _validate_value(
    operation: RepairUpdateOperation,
    obligations: Mapping[str, Any],
    objects: Mapping[str, tuple[str, Mapping[str, Any]]],
    scope: RepairScopeV1,
) -> None:
    rules = {obligations[key].rule_code for key in operation.obligation_keys}
    if operation.field_path == "/status":
        if not isinstance(operation.new_value, str) or operation.new_value not in _CLAIM_STATUSES:
            raise RepairEngineError("repair_proposal_value_invalid")
        if (
            "claim_supported_without_support" in rules
            and operation.new_value == "supported"
        ):
            raise RepairEngineError("repair_proposal_value_invalid")
        if (
            "claim_refuted_without_refutation" in rules
            and operation.new_value == "refuted"
        ):
            raise RepairEngineError("repair_proposal_value_invalid")
        return
    if operation.field_path != "/dependency_claim_refs" or not isinstance(
        operation.new_value, list
    ):
        raise RepairEngineError("repair_proposal_value_invalid")
    allowed_ids = set(scope.read_write_object_ids) | set(scope.read_only_object_ids)
    identities: set[str] = set()
    for value in operation.new_value:
        if (
            not isinstance(value, Mapping)
            or set(value) != {"object_type", "object_id"}
            or value.get("object_type") != "claim"
            or not isinstance(value.get("object_id"), str)
        ):
            raise RepairEngineError("repair_proposal_value_invalid")
        object_id = str(value["object_id"])
        if (
            object_id == operation.object_id
            or object_id not in allowed_ids
            or objects.get(object_id, (None,))[0] != "claim"
            or object_id in identities
        ):
            raise RepairEngineError("repair_proposal_reference_invalid")
        identities.add(object_id)
    canonical = sorted(
        operation.new_value,
        key=lambda value: (str(value["object_type"]), str(value["object_id"])),
    )
    if operation.new_value != canonical:
        raise RepairEngineError("repair_proposal_value_invalid")


def _combined_mutation(
    original: MutationSet,
    repairs: Mapping[tuple[str, str], CompanionRepairOperation],
) -> MutationSet:
    companion = tuple(
        UpdateField(
            operation_id=(
                f"repair_r{item.repair_round}_{index:02d}"
            ),
            object_id=item.object_id,
            field_path=item.field_path,
            new_value=deepcopy(item.new_value),
        )
        for index, item in enumerate(
            sorted(
                repairs.values(),
                key=lambda value: (value.object_id, value.field_path),
            ),
            start=1,
        )
    )
    return MutationSet(
        mutation_set_id=f"{original.mutation_set_id}_repair_candidate",
        base_draft_id=original.base_draft_id,
        base_revision=original.base_revision,
        operations=(*original.operations, *companion),
        actor=original.actor,
        mode=original.mode,
        closure_policy_version=original.closure_policy_version,
    )


def _round_outcome(
    simulation: MutationSimulation, assessment: ClosureRepairAssessment
) -> str:
    if simulation.can_apply:
        return "repaired"
    if assessment.status == "eligible":
        return "repair_required"
    return assessment.reason_code


def _made_progress(before: tuple[str, ...], after: tuple[str, ...]) -> bool:
    return bool(set(before) - set(after)) and len(after) <= len(before)


def _obligation_keys(assessment: ClosureRepairAssessment) -> tuple[str, ...]:
    return tuple(sorted(item.obligation_key for item in assessment.obligations))


def _mechanical_paths(
    simulation: MutationSimulation,
) -> tuple[tuple[str, str], ...]:
    normalized = simulation.normalized_mutation or {}
    values = normalized.get("mechanical_operations", [])
    if not isinstance(values, Sequence) or isinstance(values, str | bytes):
        raise RepairEngineError("repair_rebase_mechanical_contract_invalid")
    result: list[tuple[str, str]] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise RepairEngineError("repair_rebase_mechanical_contract_invalid")
        object_id = value.get("object_id")
        field_path = value.get("field_path")
        if not isinstance(object_id, str) or not isinstance(field_path, str):
            raise RepairEngineError("repair_rebase_mechanical_contract_invalid")
        result.append((object_id, field_path))
    return tuple(result)


def _simulations_match(
    supplied: MutationSimulation, replayed: MutationSimulation
) -> bool:
    return (
        supplied.valid == replayed.valid
        and supplied.can_apply == replayed.can_apply
        and supplied.reason_code == replayed.reason_code
        and supplied.baseline_hash == replayed.baseline_hash
        and supplied.candidate_hash == replayed.candidate_hash
        and supplied.closure_policy_version == replayed.closure_policy_version
        and supplied.document == replayed.document
        and supplied.normalized_mutation == replayed.normalized_mutation
        and supplied.impact_cone == replayed.impact_cone
        and supplied.baseline_findings == replayed.baseline_findings
        and supplied.final_findings == replayed.final_findings
        and supplied.introduced_finding_keys == replayed.introduced_finding_keys
        and supplied.worsened_finding_keys == replayed.worsened_finding_keys
        and supplied.authorization_required_finding_keys
        == replayed.authorization_required_finding_keys
    )


def _assessment_result(
    original_simulation: MutationSimulation,
    assessment: ClosureRepairAssessment,
) -> ClosureRepairResult:
    if assessment.status == "manual_required":
        status: RepairRunStatus = "manual_required"
    elif assessment.status == "not_applicable":
        status = "not_applicable"
    else:
        status = "blocked"
    return _result(status, assessment.reason_code, original_simulation)


def _result(
    status: RepairRunStatus,
    reason_code: str,
    original_simulation: MutationSimulation,
    *,
    rounds: Sequence[ClosureRepairRound] = (),
    repairs: Mapping[tuple[str, str], CompanionRepairOperation] | None = None,
    final_mutation: MutationSet | None = None,
    final_simulation: MutationSimulation | None = None,
) -> ClosureRepairResult:
    return ClosureRepairResult(
        status=status,
        reason_code=reason_code,
        original_simulation=original_simulation,
        rounds=tuple(rounds),
        companion_operations=tuple(
            sorted(
                (repairs or {}).values(),
                key=lambda value: (value.object_id, value.field_path),
            )
        ),
        final_mutation_set=final_mutation,
        final_simulation=final_simulation,
    )


def _objects_by_id(
    document: Mapping[str, Any],
) -> dict[str, tuple[str, Mapping[str, Any]]]:
    result: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for collection, object_type in (
        ("claims", "claim"),
        ("structure_locks", "structure_lock"),
    ):
        values = document.get(collection, [])
        if not isinstance(values, Sequence) or isinstance(values, str | bytes):
            continue
        for value in values:
            if isinstance(value, Mapping) and isinstance(value.get("id"), str):
                result[str(value["id"])] = (object_type, value)
    return result


def _pointer_value(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for raw in path[1:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise RepairEngineError("repair_proposal_path_missing")
    return current


__all__ = [
    "MAX_REPAIR_ROUNDS",
    "RepairEngineError",
    "prove_repair_rebase",
    "run_closure_repair",
]
