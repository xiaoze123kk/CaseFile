"""Deterministic write scope for bounded closure repair."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from casefile.domain.logical_mutation.graph import COLLECTION_BY_TYPE
from casefile.domain.logical_mutation.models import MutationSet, UpdateField
from casefile.domain.logical_mutation.policy import ACTIVE_APPLY_POLICY
from casefile.domain.logical_mutation.repair.models import (
    ClosureRepairAssessment,
    ProtectedRepairPath,
    RepairObjectPaths,
    RepairScopeV1,
    ScopedRepairObligation,
)
from casefile.domain.logical_mutation.repair.policy import (
    REPAIR_POLICY_V1,
    repair_policy,
)

if TYPE_CHECKING:
    from casefile.domain.verification_engine import MutationSimulation

REPAIR_SCOPE_V1 = "closure-repair-scope-v1"
MAX_REPAIR_CONTEXT_OBJECTS = 24
MAX_REPAIR_WRITE_OBJECTS = 6
MAX_REPAIR_OPERATIONS = 8

_KIND_PATHS = {
    "claim_supported_without_support": {
        "downgrade_claim_status": "/status",
    },
    "claim_refuted_without_refutation": {
        "change_claim_status": "/status",
    },
    "claim_dependency_incompatible": {
        "repair_dependency_claim": "/dependency_claim_refs",
        "change_claim_status": "/status",
    },
}


class RepairScopeError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class _DocumentObject:
    object_type: str
    value: Mapping[str, Any]


def build_repair_scope(
    mutation_set: MutationSet,
    simulation: MutationSimulation,
    assessment: ClosureRepairAssessment,
) -> RepairScopeV1:
    _validate_inputs(mutation_set, simulation, assessment)
    objects = _objects_by_id(simulation.document)
    protected = _primary_protections(mutation_set)
    protected.extend(_mechanical_protections(simulation.normalized_mutation))
    scoped_obligations: list[ScopedRepairObligation] = []
    allowed_by_object: dict[str, set[str]] = {}
    context_ids: set[str] = set()
    write_ids: set[str] = set()
    matching_lock_ids: set[str] = set()

    for obligation in assessment.obligations:
        subject_ids = tuple(
            dict.fromkeys(
                ref.object_id for ref in obligation.object_refs if ref.role == "subject"
            )
        )
        if not subject_ids:
            raise RepairScopeError("repair_scope_subject_missing")
        obligation_context_ids = {
            ref.object_id for ref in obligation.object_refs
        } | set(obligation.dependency_path)
        context_ids.update(obligation_context_ids)
        if any(object_id not in objects for object_id in obligation_context_ids):
            raise RepairScopeError("repair_scope_object_missing")
        if any(objects[object_id].object_type != "claim" for object_id in subject_ids):
            raise RepairScopeError("repair_scope_object_type_invalid")

        kind_paths = _KIND_PATHS.get(obligation.rule_code)
        if kind_paths is None:
            raise RepairScopeError("repair_scope_rule_unsupported")
        obligation_allowed: dict[str, set[str]] = {
            object_id: set() for object_id in subject_ids
        }
        effective_kinds: list[str] = []
        for repair_kind in obligation.allowed_repair_kinds:
            field_path = kind_paths.get(repair_kind)
            if field_path is None or any(
                _is_protected(object_id, field_path, protected)
                for object_id in subject_ids
            ):
                continue
            effective_kinds.append(repair_kind)
            for object_id in subject_ids:
                obligation_allowed[object_id].add(field_path)

        lock_protections = _matching_lock_protections(
            simulation.document,
            obligation_allowed,
        )
        protected.extend(lock_protections)
        matching_lock_ids.update(item.source_id for item in lock_protections)
        context_ids.update(item.source_id for item in lock_protections)
        if lock_protections:
            effective_kinds = [
                repair_kind
                for repair_kind in effective_kinds
                if not any(
                    _is_protected(
                        object_id,
                        kind_paths[repair_kind],
                        lock_protections,
                    )
                    for object_id in subject_ids
                )
            ]
            for object_id in subject_ids:
                obligation_allowed[object_id] = {
                    field_path
                    for field_path in obligation_allowed[object_id]
                    if not _is_protected(object_id, field_path, lock_protections)
                }

        if not effective_kinds or not any(obligation_allowed.values()):
            raise RepairScopeError("repair_requires_intent_revision")
        write_ids.update(subject_ids)
        for object_id, field_paths in obligation_allowed.items():
            allowed_by_object.setdefault(object_id, set()).update(field_paths)
        scoped_obligations.append(
            ScopedRepairObligation(
                obligation_key=obligation.obligation_key,
                source_finding_key=obligation.source_finding_key,
                rule_code=obligation.rule_code,
                subject_object_ids=subject_ids,
                effective_repair_kinds=tuple(effective_kinds),
                allowed_paths=_repair_object_paths(obligation_allowed),
            )
        )

    if (
        len(context_ids) > MAX_REPAIR_CONTEXT_OBJECTS
        or len(write_ids) > MAX_REPAIR_WRITE_OBJECTS
    ):
        raise RepairScopeError("repair_scope_too_large")
    operation_budget = min(
        MAX_REPAIR_OPERATIONS,
        sum(item.max_operations for item in assessment.obligations),
    )
    if operation_budget < len(scoped_obligations):
        raise RepairScopeError("repair_operation_budget_exceeded")
    return RepairScopeV1(
        scope_version=REPAIR_SCOPE_V1,
        closure_policy_version=simulation.closure_policy_version,
        repair_policy_version=assessment.obligations[0].repair_policy_version,
        base_draft_id=mutation_set.base_draft_id,
        base_revision=mutation_set.base_revision,
        candidate_hash=simulation.candidate_hash,
        obligations=tuple(scoped_obligations),
        read_write_object_ids=tuple(sorted(write_ids)),
        read_only_object_ids=tuple(sorted(context_ids - write_ids)),
        allowed_paths=_repair_object_paths(allowed_by_object),
        protected_paths=tuple(
            sorted(
                {item for item in protected if item.object_id in context_ids},
                key=lambda item: (
                    item.object_id,
                    item.field_path,
                    item.source,
                    item.source_id,
                ),
            )
        ),
        structure_lock_ids=tuple(sorted(matching_lock_ids)),
        max_operations=operation_budget,
        max_context_objects=MAX_REPAIR_CONTEXT_OBJECTS,
        max_write_objects=MAX_REPAIR_WRITE_OBJECTS,
    )


def _validate_inputs(
    mutation_set: MutationSet,
    simulation: MutationSimulation,
    assessment: ClosureRepairAssessment,
) -> None:
    if assessment.status != "eligible" or not assessment.agent_repair_allowed:
        raise RepairScopeError("repair_scope_assessment_ineligible")
    if not assessment.obligations:
        raise RepairScopeError("repair_scope_obligations_missing")
    if any(item.automation != "agent" for item in assessment.obligations):
        raise RepairScopeError("repair_scope_assessment_ineligible")
    if not simulation.valid or simulation.normalized_mutation is None:
        raise RepairScopeError("repair_scope_simulation_incomplete")
    if (
        simulation.closure_policy_version != ACTIVE_APPLY_POLICY
        or simulation.closure_policy_version != mutation_set.closure_policy_version
    ):
        raise RepairScopeError("repair_scope_policy_mismatch")
    for obligation in assessment.obligations:
        try:
            policy = repair_policy(
                obligation.rule_code,
                "repair_required",
                version=obligation.repair_policy_version,
            )
        except ValueError as error:
            raise RepairScopeError("repair_scope_policy_mismatch") from error
        if (
            obligation.base_draft_id != mutation_set.base_draft_id
            or obligation.base_revision != mutation_set.base_revision
            or obligation.candidate_hash != simulation.candidate_hash
            or obligation.closure_policy_version != simulation.closure_policy_version
            or obligation.repair_policy_version
            != REPAIR_POLICY_V1
            or obligation.allowed_repair_kinds != policy.allowed_repair_kinds
            or obligation.automation != policy.automation
            or obligation.max_operations != policy.max_operations
            or obligation.allow_create != policy.allow_create
            or obligation.allow_delete != policy.allow_delete
        ):
            raise RepairScopeError("repair_scope_binding_mismatch")


def _objects_by_id(document: Mapping[str, Any]) -> dict[str, _DocumentObject]:
    result: dict[str, _DocumentObject] = {}
    for object_type, collection in COLLECTION_BY_TYPE.items():
        values = document.get(collection, [])
        if not isinstance(values, Sequence) or isinstance(values, str | bytes):
            continue
        for value in values:
            if isinstance(value, Mapping) and isinstance(value.get("id"), str):
                result[str(value["id"])] = _DocumentObject(object_type, value)
    return result


def _primary_protections(mutation_set: MutationSet) -> list[ProtectedRepairPath]:
    result: list[ProtectedRepairPath] = []
    for operation in mutation_set.operations:
        field_path = operation.field_path if isinstance(operation, UpdateField) else ""
        result.append(
            ProtectedRepairPath(
                operation.object_id,
                field_path,
                "primary",
                operation.operation_id,
            )
        )
    return result


def _mechanical_protections(
    normalized_mutation: Mapping[str, Any] | None,
) -> list[ProtectedRepairPath]:
    if normalized_mutation is None:
        return []
    raw_operations = normalized_mutation.get("mechanical_operations", [])
    if not isinstance(raw_operations, Sequence) or isinstance(
        raw_operations, str | bytes
    ):
        raise RepairScopeError("repair_scope_mechanical_contract_invalid")
    result: list[ProtectedRepairPath] = []
    for raw in raw_operations:
        if not isinstance(raw, Mapping):
            raise RepairScopeError("repair_scope_mechanical_contract_invalid")
        object_id = raw.get("object_id")
        field_path = raw.get("field_path")
        operation_id = raw.get("operation_id")
        if not all(
            isinstance(value, str) for value in (object_id, field_path, operation_id)
        ):
            raise RepairScopeError("repair_scope_mechanical_contract_invalid")
        result.append(
            ProtectedRepairPath(
                str(object_id), str(field_path), "mechanical", str(operation_id)
            )
        )
    return result


def _matching_lock_protections(
    document: Mapping[str, Any],
    candidate_paths: Mapping[str, set[str]],
) -> list[ProtectedRepairPath]:
    result: list[ProtectedRepairPath] = []
    locks = document.get("structure_locks", [])
    if not isinstance(locks, Sequence) or isinstance(locks, str | bytes):
        return result
    for lock in locks:
        if not isinstance(lock, Mapping) or not isinstance(lock.get("id"), str):
            continue
        target = lock.get("object_ref")
        field_paths = lock.get("field_paths")
        if not isinstance(target, Mapping) or not isinstance(field_paths, Sequence):
            continue
        object_id = target.get("object_id")
        if not isinstance(object_id, str) or object_id not in candidate_paths:
            continue
        for locked_path in field_paths:
            if not isinstance(locked_path, str):
                continue
            if any(
                _paths_overlap(locked_path, candidate_path)
                for candidate_path in candidate_paths[object_id]
            ):
                result.append(
                    ProtectedRepairPath(
                        object_id,
                        locked_path,
                        "structure_lock",
                        str(lock["id"]),
                    )
                )
    return result


def _is_protected(
    object_id: str,
    field_path: str,
    protected: Sequence[ProtectedRepairPath],
) -> bool:
    return any(
        item.object_id == object_id and _paths_overlap(field_path, item.field_path)
        for item in protected
    )


def _paths_overlap(left: str, right: str) -> bool:
    if left == "" or right == "":
        return True
    left_parts = _pointer_parts(left)
    right_parts = _pointer_parts(right)
    shorter = min(len(left_parts), len(right_parts))
    return left_parts[:shorter] == right_parts[:shorter]


def _pointer_parts(path: str) -> tuple[str, ...]:
    if not path.startswith("/") or path == "/":
        raise RepairScopeError("repair_scope_field_path_invalid")
    return tuple(
        part.replace("~1", "/").replace("~0", "~")
        for part in path[1:].split("/")
    )


def _repair_object_paths(
    values: Mapping[str, set[str]],
) -> tuple[RepairObjectPaths, ...]:
    return tuple(
        RepairObjectPaths(object_id, tuple(sorted(field_paths)))
        for object_id, field_paths in sorted(values.items())
        if field_paths
    )
