"""Deterministic planning and proof of semantic repair alternatives."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast

import rfc8785
from casefile_contracts import Status as ClaimStatus

from casefile.domain.logical_mutation.models import MutationSet, UpdateField
from casefile.domain.logical_mutation.repair.assessment import assess_closure_repair
from casefile.domain.logical_mutation.repair.models import (
    ClosureRepairAssessment,
    CompanionRepairOperation,
    RepairAlternative,
    RepairScopeV1,
    RepairUpdateOperation,
)

if TYPE_CHECKING:
    from casefile.domain.verification_engine import MutationSimulation, VerificationEngine


def plan_repair_alternatives(
    baseline_document: Mapping[str, Any],
    original_mutation: MutationSet,
    original_simulation: MutationSimulation,
    current_simulation: MutationSimulation,
    assessment: ClosureRepairAssessment,
    scope: RepairScopeV1,
    accumulated_repairs: Mapping[tuple[str, str], CompanionRepairOperation],
    *,
    verifier: VerificationEngine,
) -> tuple[RepairAlternative, ...]:
    """Enumerate bounded field choices and retain only proof-backed progress."""

    before_keys = tuple(sorted(item.obligation_key for item in assessment.obligations))
    objects = _objects_by_id(current_simulation.document)
    candidates: list[tuple[str, RepairUpdateOperation]] = []
    for object_id in scope.read_write_object_ids:
        current = objects[object_id]
        paths = scope.allowed_paths_for(object_id)
        if "/status" in paths:
            obligation_keys = _obligation_keys(scope, object_id, "/status")
            for status in ClaimStatus:
                if status.value == current.get("status"):
                    continue
                candidates.append(
                    (
                        "change_claim_status",
                        RepairUpdateOperation(
                            obligation_keys,
                            object_id,
                            "/status",
                            status.value,
                            "server_proved_change_claim_status",
                        ),
                    )
                )
        if "/dependency_claim_refs" in paths:
            incompatible_ids, obligation_keys = _incompatible_prerequisites(
                assessment, scope, object_id
            )
            current_refs = deepcopy(current.get("dependency_claim_refs", []))
            if incompatible_ids and isinstance(current_refs, list):
                retained = [
                    value
                    for value in current_refs
                    if not (
                        isinstance(value, Mapping) and value.get("object_id") in incompatible_ids
                    )
                ]
                if retained != current_refs:
                    candidates.append(
                        (
                            "remove_incompatible_dependencies",
                            RepairUpdateOperation(
                                obligation_keys,
                                object_id,
                                "/dependency_claim_refs",
                                retained,
                                "server_proved_remove_incompatible_dependencies",
                            ),
                        )
                    )

    alternatives: list[RepairAlternative] = []
    seen: set[tuple[str, str]] = set()
    for kind, operation in candidates:
        identity = (operation.object_id, operation.field_path)
        candidate_repairs = dict(accumulated_repairs)
        original_value = _pointer_value(
            _objects_by_id(original_simulation.document)[operation.object_id],
            operation.field_path,
        )
        if operation.new_value == original_value:
            candidate_repairs.pop(identity, None)
        else:
            candidate_repairs[identity] = CompanionRepairOperation(
                repair_round=0,
                obligation_keys=operation.obligation_keys,
                object_id=operation.object_id,
                field_path=operation.field_path,
                new_value=deepcopy(operation.new_value),
                reason=operation.reason,
            )
        candidate = verifier.simulate_mutation_set(
            baseline_document,
            _combined_mutation(original_mutation, candidate_repairs),
        )
        next_assessment = assess_closure_repair(original_mutation, candidate)
        after_keys = (
            tuple(sorted(item.obligation_key for item in next_assessment.obligations))
            if next_assessment.status in {"eligible", "manual_required"}
            else ()
        )
        progressing = bool(set(before_keys) - set(after_keys)) and len(after_keys) <= len(
            before_keys
        )
        if not candidate.can_apply and not (next_assessment.status == "eligible" and progressing):
            continue
        dedupe = (candidate.candidate_hash, kind)
        if dedupe in seen:
            continue
        seen.add(dedupe)
        payload = {
            "candidate_hash_before": current_simulation.candidate_hash,
            "kind": kind,
            "operations": [operation.as_dict()],
            "obligation_keys_before": list(before_keys),
            "obligation_keys_after": list(after_keys),
            "candidate_hash_after": candidate.candidate_hash,
        }
        alternative_id = "alt_" + hashlib.sha256(rfc8785.dumps(cast(Any, payload))).hexdigest()[:24]
        alternatives.append(
            RepairAlternative(
                alternative_id=alternative_id,
                kind=kind,
                obligation_keys_before=before_keys,
                obligation_keys_after=after_keys,
                operations=(operation,),
                candidate_hash_after=candidate.candidate_hash,
                outcome="repaired" if candidate.can_apply else "repair_required",
            )
        )
    return tuple(sorted(alternatives, key=lambda item: item.alternative_id))


def _obligation_keys(scope: RepairScopeV1, object_id: str, field_path: str) -> tuple[str, ...]:
    return tuple(
        item.obligation_key
        for item in scope.obligations
        if any(
            allowed.object_id == object_id and field_path in allowed.field_paths
            for allowed in item.allowed_paths
        )
    )


def _incompatible_prerequisites(
    assessment: ClosureRepairAssessment,
    scope: RepairScopeV1,
    object_id: str,
) -> tuple[set[str], tuple[str, ...]]:
    scoped = {
        item.obligation_key: item
        for item in scope.obligations
        if object_id in item.subject_object_ids
        and "/dependency_claim_refs" in scope.allowed_paths_for(object_id)
        and item.rule_code == "claim_dependency_incompatible"
    }
    ids: set[str] = set()
    keys: list[str] = []
    for obligation in assessment.obligations:
        if obligation.obligation_key not in scoped:
            continue
        keys.append(obligation.obligation_key)
        ids.update(ref.object_id for ref in obligation.object_refs if ref.role == "prerequisite")
    return ids, tuple(keys)


def _combined_mutation(
    original: MutationSet,
    repairs: Mapping[tuple[str, str], CompanionRepairOperation],
) -> MutationSet:
    companion = tuple(
        UpdateField(
            operation_id=f"repair_candidate_{index:02d}",
            object_id=item.object_id,
            field_path=item.field_path,
            new_value=deepcopy(item.new_value),
        )
        for index, item in enumerate(
            sorted(repairs.values(), key=lambda value: (value.object_id, value.field_path)),
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


def _objects_by_id(document: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for values in document.values():
        if not isinstance(values, Sequence) or isinstance(values, str | bytes):
            continue
        for value in values:
            if isinstance(value, Mapping) and isinstance(value.get("id"), str):
                result[str(value["id"])] = value
    return result


def _pointer_value(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for raw in path[1:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        else:
            raise ValueError("repair_alternative_path_missing")
    return current


__all__ = ["plan_repair_alternatives"]
