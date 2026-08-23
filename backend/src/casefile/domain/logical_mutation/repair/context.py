"""Frozen, JSON-safe context for a future closure-repair proposer."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast

import rfc8785

from casefile.domain.logical_mutation.graph import (
    COLLECTION_BY_TYPE,
    compile_logical_graph,
)
from casefile.domain.logical_mutation.models import (
    OLD_VALUE_UNSET,
    CreateObject,
    DeleteObject,
    MutationSet,
)
from casefile.domain.logical_mutation.repair.models import (
    ClosureRepairAssessment,
    ClosureRepairContextV1,
    RepairContextObject,
    RepairScopeV1,
)
from casefile.domain.logical_mutation.repair.scope import (
    REPAIR_SCOPE_V1,
    RepairScopeError,
    build_repair_scope,
)

if TYPE_CHECKING:
    from casefile.domain.verification_engine import MutationSimulation

REPAIR_CONTEXT_V1 = "closure-repair-context-v1"


class RepairContextError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def build_closure_repair_context(
    mutation_set: MutationSet,
    simulation: MutationSimulation,
    assessment: ClosureRepairAssessment,
    scope: RepairScopeV1,
    *,
    original_intent: str,
) -> ClosureRepairContextV1:
    intent = original_intent.strip()
    if not intent:
        raise RepairContextError("repair_context_intent_missing")
    _validate_inputs(mutation_set, simulation, assessment, scope)
    objects_by_id = _objects_by_id(simulation.document)
    ordered_ids = tuple(
        sorted({*scope.read_write_object_ids, *scope.read_only_object_ids})
    )
    missing = tuple(object_id for object_id in ordered_ids if object_id not in objects_by_id)
    if missing:
        raise RepairContextError("repair_context_object_missing")
    objects = tuple(
        RepairContextObject(
            object_id=object_id,
            object_type=objects_by_id[object_id][0],
            access=(
                "read_write"
                if object_id in scope.read_write_object_ids
                else "read_only"
            ),
            object_value=deepcopy(dict(objects_by_id[object_id][1])),
        )
        for object_id in ordered_ids
    )
    dependency_paths = tuple(
        sorted(
            {
                obligation.dependency_path
                for obligation in assessment.obligations
                if obligation.dependency_path
            }
        )
    )
    relevant_edges = _relevant_edges(simulation.document, set(ordered_ids))
    kwargs: dict[str, Any] = {
        "context_version": REPAIR_CONTEXT_V1,
        "scope_version": scope.scope_version,
        "closure_policy_version": scope.closure_policy_version,
        "repair_policy_version": scope.repair_policy_version,
        "base_draft_id": scope.base_draft_id,
        "base_revision": scope.base_revision,
        "baseline_hash": simulation.baseline_hash,
        "candidate_hash": scope.candidate_hash,
        "original_intent": intent,
        "primary_operations": _primary_operations(mutation_set),
        "obligations": scope.obligations,
        "objects": objects,
        "allowed_paths": scope.allowed_paths,
        "protected_paths": scope.protected_paths,
        "structure_lock_ids": scope.structure_lock_ids,
        "dependency_paths": dependency_paths,
        "relevant_edges": relevant_edges,
        "max_operations": scope.max_operations,
        "max_context_objects": scope.max_context_objects,
        "max_write_objects": scope.max_write_objects,
    }
    unhashed = ClosureRepairContextV1(**kwargs, context_hash="")
    context_hash = hashlib.sha256(
        rfc8785.dumps(cast(Any, unhashed.hash_payload()))
    ).hexdigest()
    return ClosureRepairContextV1(**kwargs, context_hash=context_hash)


def _validate_inputs(
    mutation_set: MutationSet,
    simulation: MutationSimulation,
    assessment: ClosureRepairAssessment,
    scope: RepairScopeV1,
) -> None:
    if assessment.status != "eligible" or not assessment.agent_repair_allowed:
        raise RepairContextError("repair_context_assessment_ineligible")
    if scope.scope_version != REPAIR_SCOPE_V1 or not scope.obligations:
        raise RepairContextError("repair_context_scope_invalid")
    try:
        expected_scope = build_repair_scope(mutation_set, simulation, assessment)
    except RepairScopeError as error:
        raise RepairContextError("repair_context_scope_invalid") from error
    if scope != expected_scope:
        raise RepairContextError("repair_context_scope_invalid")
    if (
        scope.base_draft_id != mutation_set.base_draft_id
        or scope.base_revision != mutation_set.base_revision
        or scope.candidate_hash != simulation.candidate_hash
        or scope.closure_policy_version != simulation.closure_policy_version
        or scope.closure_policy_version != mutation_set.closure_policy_version
    ):
        raise RepairContextError("repair_context_binding_mismatch")
    assessment_keys = tuple(item.obligation_key for item in assessment.obligations)
    scope_keys = tuple(item.obligation_key for item in scope.obligations)
    if assessment_keys != scope_keys or any(
        item.repair_policy_version != scope.repair_policy_version
        for item in assessment.obligations
    ):
        raise RepairContextError("repair_context_binding_mismatch")
    if len(scope.read_write_object_ids) > scope.max_write_objects or len(
        {*scope.read_write_object_ids, *scope.read_only_object_ids}
    ) > scope.max_context_objects:
        raise RepairContextError("repair_context_scope_too_large")
    if scope.max_operations < len(scope.obligations):
        raise RepairContextError("repair_context_operation_budget_invalid")


def _objects_by_id(
    document: Mapping[str, Any],
) -> dict[str, tuple[str, Mapping[str, Any]]]:
    result: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for object_type, collection in COLLECTION_BY_TYPE.items():
        values = document.get(collection, [])
        if not isinstance(values, Sequence) or isinstance(values, str | bytes):
            continue
        for value in values:
            if isinstance(value, Mapping) and isinstance(value.get("id"), str):
                result[str(value["id"])] = (object_type, value)
    return result


def _primary_operations(mutation_set: MutationSet) -> tuple[Mapping[str, Any], ...]:
    result: list[Mapping[str, Any]] = []
    for operation in mutation_set.operations:
        if isinstance(operation, CreateObject):
            result.append(
                {
                    "operation_id": operation.operation_id,
                    "operation_type": operation.operation_type,
                    "object_id": operation.object_id,
                    "collection": operation.collection,
                    "object_value": deepcopy(dict(operation.object_value)),
                }
            )
        elif isinstance(operation, DeleteObject):
            result.append(
                {
                    "operation_id": operation.operation_id,
                    "operation_type": operation.operation_type,
                    "object_id": operation.object_id,
                    "old_object_value": (
                        None
                        if operation.old_object_value is None
                        else deepcopy(dict(operation.old_object_value))
                    ),
                }
            )
        else:
            result.append(
                {
                    "operation_id": operation.operation_id,
                    "operation_type": operation.operation_type,
                    "object_id": operation.object_id,
                    "field_path": operation.field_path,
                    "new_value": deepcopy(operation.new_value),
                    **(
                        {}
                        if operation.old_value is OLD_VALUE_UNSET
                        else {"old_value": deepcopy(operation.old_value)}
                    ),
                }
            )
    return tuple(result)


def _relevant_edges(
    document: Mapping[str, Any],
    scoped_ids: set[str],
) -> tuple[Mapping[str, str], ...]:
    return tuple(
        edge.as_dict()
        for edge in compile_logical_graph(document).edges
        if edge.prerequisite_id in scoped_ids and edge.dependent_id in scoped_ids
    )
