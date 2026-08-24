"""Deterministic server binding for runtime-private general mutation plans."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import networkx as nx
import rfc8785

from casefile.agent_runtime.general_mutation import (
    GENERAL_MUTATION_BINDER_VERSION,
    GENERAL_MUTATION_POLICY_VERSION,
    CreateMutationCandidate,
    DeleteMutationCandidate,
    ExistingTarget,
    LocalTarget,
    MutationPlanV1,
    UpdateMutationCandidate,
)
from casefile.application.v1_editing import COLLECTIONS, EDITABLE_FIELDS
from casefile.domain.logical_mutation import (
    CLOSURE_POLICY_VERSION,
    CreateObject,
    DeleteObject,
    MutationSet,
    UpdateField,
)
from casefile.domain.verification_engine import MutationSimulation

_OBJECT_TYPE_BY_COLLECTION = {collection: kind for kind, collection in COLLECTIONS.items()}
_ID_PREFIX_BY_COLLECTION = {
    "entities": "ent",
    "relationships": "rel",
    "locations": "loc",
    "events": "evt",
    "information_units": "info",
    "claims": "claim",
    "hypotheses": "hyp",
    "reasoning_paths": "path",
}
_COMMON_DEFAULTS: dict[str, Any] = {
    "tags": [],
    "source_refs": [],
    "confidence": None,
    "confirmation_status": "proposed",
    "created_by": {
        "actor_type": "agent",
        "actor_id": "agent_general_mutation",
    },
    "revision": 1,
}
_COLLECTION_DEFAULTS: dict[str, dict[str, Any]] = {
    "entities": {
        "aliases": [],
        "traits": [],
        "goals": [],
        "secrets": [],
        "capabilities": [],
        "knowledge_states": [],
    },
    "relationships": {},
    "locations": {
        "parent_ref": None,
        "adjacency_refs": [],
        "access_rules": [],
        "travel_times": [],
        "visibility_rules": [],
    },
    "events": {
        "participant_refs": [],
        "location_ref": None,
        "cause_refs": [],
        "effect_refs": [],
        "observed_by_refs": [],
    },
    "information_units": {
        "source_event_ref": None,
        "supports_claim_refs": [],
        "refutes_claim_refs": [],
        "availability": {
            "perspective_refs": [],
            "acquisition_conditions": [],
            "alternative_path_refs": [],
        },
    },
    "claims": {
        "support_refs": [],
        "refute_refs": [],
        "dependency_claim_refs": [],
    },
    "hypotheses": {
        "required_claim_refs": [],
        "falsifier_refs": [],
        "competing_hypothesis_refs": [],
        "evidence_assessments": [],
    },
    "reasoning_paths": {"alternative_path_refs": []},
}


class GeneralMutationBindingError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class BoundMutationOperation:
    operation_key: str
    operation_id: str
    operation_type: str
    target_collection: str
    target_object_key: str
    field_path: str
    expected_object_revision: int | None
    old_value: Any
    new_value: Any
    reason: str


@dataclass(frozen=True, slots=True)
class BoundMutationPlan:
    plan_version: str
    capability_policy_version: str
    binder_version: str
    plan_hash: str
    mutation_set: MutationSet
    operations: tuple[BoundMutationOperation, ...]
    contains_delete: bool


def append_repair_companions(
    bound: BoundMutationPlan,
    document: Mapping[str, Any],
    companions: Sequence[Mapping[str, Any]],
) -> BoundMutationPlan:
    """Append only replay-proved Closure Repair updates to a bound M3.4 plan."""

    if not companions:
        return bound
    existing = _objects_by_id(document)
    logical = list(bound.mutation_set.operations)
    operations = list(bound.operations)
    seen_ids = {item.operation_id for item in operations}
    for companion_index, companion in enumerate(companions, start=1):
        object_id = companion.get("object_id")
        field_path = companion.get("field_path")
        repair_round = companion.get("repair_round")
        if (
            not isinstance(object_id, str)
            or object_id not in existing
            or not isinstance(field_path, str)
            or isinstance(repair_round, bool)
            or not isinstance(repair_round, int)
        ):
            raise GeneralMutationBindingError("general_mutation_repair_companion_invalid")
        collection, value = existing[object_id]
        _assert_editable(collection, field_path)
        operation_id = f"op_repair_r{repair_round}_{companion_index:02d}"
        while operation_id in seen_ids:
            operation_id = f"{operation_id}_m34"
        seen_ids.add(operation_id)
        revision = value.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise GeneralMutationBindingError("general_mutation_object_revision_invalid")
        old_value = _pointer_value(value, field_path)
        new_value = deepcopy(companion.get("new_value"))
        logical.append(
            UpdateField(
                operation_id,
                object_id,
                field_path,
                new_value,
                old_value,
                revision,
            )
        )
        operations.append(
            BoundMutationOperation(
                operation_key=operation_id,
                operation_id=operation_id,
                operation_type="update_field",
                target_collection=collection,
                target_object_key=object_id,
                field_path=field_path,
                expected_object_revision=revision,
                old_value=old_value,
                new_value=new_value,
                reason=str(companion.get("reason") or "Closure Repair companion"),
            )
        )
    return BoundMutationPlan(
        plan_version=bound.plan_version,
        capability_policy_version=bound.capability_policy_version,
        binder_version=bound.binder_version,
        plan_hash=bound.plan_hash,
        mutation_set=MutationSet(
            mutation_set_id=f"{bound.mutation_set.mutation_set_id}_repair",
            base_draft_id=bound.mutation_set.base_draft_id,
            base_revision=bound.mutation_set.base_revision,
            operations=tuple(logical),
            actor=bound.mutation_set.actor,
            mode=bound.mutation_set.mode,
            closure_policy_version=bound.mutation_set.closure_policy_version,
        ),
        operations=tuple(operations),
        contains_delete=bound.contains_delete,
    )


def bind_general_mutation_plan(
    plan: MutationPlanV1,
    document: Mapping[str, Any],
    *,
    task_run_id: int,
    draft_id: int,
    base_revision: int,
    updated_at: datetime | str | None = None,
) -> BoundMutationPlan:
    timestamp = _timestamp(updated_at)
    plan_hash = _canonical_hash(plan.model_dump(mode="json"))
    existing = _objects_by_id(document)
    order = _ordered_plan_operations(plan)
    local_ids: dict[str, str] = {}
    for ordinal, operation in enumerate(
        (item for item in order if isinstance(item, CreateMutationCandidate)), start=1
    ):
        prefix = _ID_PREFIX_BY_COLLECTION[operation.collection]
        object_id = f"{prefix}_agent_t{task_run_id}_{ordinal:02d}"
        if object_id in existing or object_id in local_ids.values():
            raise GeneralMutationBindingError("general_mutation_object_id_conflict")
        local_ids[operation.local_ref] = object_id

    logical: list[CreateObject | UpdateField | DeleteObject] = []
    bound: list[BoundMutationOperation] = []
    created_objects: dict[str, dict[str, Any]] = {}
    for ordinal, operation in enumerate(order, start=1):
        operation_id = f"op_t{task_run_id}_{ordinal:02d}"
        if isinstance(operation, CreateMutationCandidate):
            _assert_create_fields(operation.collection, operation.fields)
            object_id = local_ids[operation.local_ref]
            fields = _resolve_refs(operation.fields, local_ids, existing)
            value = {
                **deepcopy(_COMMON_DEFAULTS),
                **deepcopy(_COLLECTION_DEFAULTS[operation.collection]),
                **fields,
                "id": object_id,
                "updated_at": timestamp,
            }
            created_objects[object_id] = value
            logical.append(CreateObject(operation_id, operation.collection, value))
            bound.append(
                BoundMutationOperation(
                    operation.operation_key,
                    operation_id,
                    "create_object",
                    operation.collection,
                    object_id,
                    "",
                    None,
                    None,
                    deepcopy(value),
                    operation.reason,
                )
            )
            continue

        object_id = _target_id(operation.target, local_ids)
        collection, current = _bound_object(object_id, existing, created_objects)
        if isinstance(operation, DeleteMutationCandidate):
            if object_id in created_objects:
                raise GeneralMutationBindingError("general_mutation_create_delete_same_object")
            logical.append(DeleteObject(operation_id, object_id, deepcopy(current)))
            bound.append(
                BoundMutationOperation(
                    operation.operation_key,
                    operation_id,
                    "delete_object",
                    collection,
                    object_id,
                    "",
                    int(current["revision"]),
                    deepcopy(current),
                    None,
                    operation.reason,
                )
            )
            continue

        _assert_editable(collection, operation.field_path)
        old_value = _pointer_value(current, operation.field_path)
        new_value = _resolve_refs(operation.new_value, local_ids, existing)
        logical.append(
            UpdateField(
                operation_id,
                object_id,
                operation.field_path,
                deepcopy(new_value),
                deepcopy(old_value),
                int(current["revision"]),
            )
        )
        _pointer_set(current, operation.field_path, deepcopy(new_value))
        bound.append(
            BoundMutationOperation(
                operation.operation_key,
                operation_id,
                "update_field",
                collection,
                object_id,
                operation.field_path,
                int(current["revision"]),
                deepcopy(old_value),
                deepcopy(new_value),
                operation.reason,
            )
        )

    mutation_set = MutationSet(
        mutation_set_id=f"general_mutation_t{task_run_id}",
        base_draft_id=draft_id,
        base_revision=base_revision,
        operations=tuple(logical),
        actor="agent",
        mode="normal",
        closure_policy_version=CLOSURE_POLICY_VERSION,
    )
    return BoundMutationPlan(
        plan.plan_version,
        GENERAL_MUTATION_POLICY_VERSION,
        GENERAL_MUTATION_BINDER_VERSION,
        plan_hash,
        mutation_set,
        tuple(bound),
        any(item.operation_type == "delete_object" for item in bound),
    )


def general_mutation_impact_hash(simulation: MutationSimulation) -> str:
    normalized = simulation.normalized_mutation or {}
    ordered = normalized.get("ordered_operations", [])
    delete_targets = sorted(
        str(item.get("object_id"))
        for item in ordered
        if isinstance(item, Mapping) and item.get("operation_type") == "delete_object"
    )
    payload = {
        "candidate_hash": simulation.candidate_hash,
        "delete_object_ids": delete_targets,
        "impact_cone": (
            None if simulation.impact_cone is None else simulation.impact_cone.as_dict()
        ),
        "mechanical_operations": normalized.get("mechanical_operations", []),
        "finding_delta": {
            "fixed": list(simulation.fixed_finding_keys),
            "introduced": list(simulation.introduced_finding_keys),
            "worsened": list(simulation.worsened_finding_keys),
            "authorization_required": list(
                simulation.authorization_required_finding_keys
            ),
        },
        "closure_policy_version": simulation.closure_policy_version,
    }
    return _canonical_hash(payload)


def _ordered_plan_operations(plan: MutationPlanV1) -> tuple[Any, ...]:
    by_key = {item.operation_key: item for item in plan.operations}
    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_nodes_from(by_key)
    local_creator = {
        item.local_ref: item.operation_key
        for item in plan.operations
        if isinstance(item, CreateMutationCandidate)
    }
    for item in plan.operations:
        for dependency in item.depends_on_operation_keys:
            graph.add_edge(dependency, item.operation_key)
        for local_ref in _local_refs_in_operation(item):
            creator = local_creator.get(local_ref)
            if creator is None:
                raise GeneralMutationBindingError("general_mutation_local_ref_unknown")
            if creator != item.operation_key:
                graph.add_edge(creator, item.operation_key)
    try:
        keys = nx.lexicographical_topological_sort(graph, key=str)
        return tuple(by_key[key] for key in keys)
    except nx.NetworkXUnfeasible as error:
        raise GeneralMutationBindingError("general_mutation_dependency_cycle") from error


def _local_refs_in_operation(operation: Any) -> set[str]:
    values: list[Any] = []
    if isinstance(operation, CreateMutationCandidate):
        values.append(operation.fields)
    elif isinstance(operation, UpdateMutationCandidate):
        values.append(operation.new_value)
        if isinstance(operation.target, LocalTarget):
            values.append({"ref_kind": "local", "local_ref": operation.target.local_ref})
    result: set[str] = set()
    for value in values:
        _collect_local_refs(value, result)
    return result


def _collect_local_refs(value: Any, result: set[str]) -> None:
    if isinstance(value, Mapping):
        if value.get("ref_kind") == "local" and isinstance(value.get("local_ref"), str):
            result.add(str(value["local_ref"]))
        for child in value.values():
            _collect_local_refs(child, result)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for child in value:
            _collect_local_refs(child, result)


def _resolve_refs(
    value: Any,
    local_ids: Mapping[str, str],
    existing: Mapping[str, tuple[str, dict[str, Any]]],
) -> Any:
    if isinstance(value, Mapping):
        ref_kind = value.get("ref_kind")
        if ref_kind in {"local", "existing"}:
            if set(value) != (
                {"ref_kind", "local_ref", "object_type"}
                if ref_kind == "local"
                else {"ref_kind", "object_id", "object_type"}
            ):
                raise GeneralMutationBindingError("general_mutation_ref_shape_invalid")
            object_type = value.get("object_type")
            if not isinstance(object_type, str) or object_type not in COLLECTIONS:
                raise GeneralMutationBindingError("general_mutation_ref_type_invalid")
            if ref_kind == "local":
                local_ref = value.get("local_ref")
                if not isinstance(local_ref, str) or local_ref not in local_ids:
                    raise GeneralMutationBindingError("general_mutation_local_ref_unknown")
                object_id = local_ids[local_ref]
            else:
                existing_object_id = value.get("object_id")
                if not isinstance(existing_object_id, str) or existing_object_id not in existing:
                    raise GeneralMutationBindingError("general_mutation_object_unknown")
                if existing[existing_object_id][0] != COLLECTIONS[object_type]:
                    raise GeneralMutationBindingError("general_mutation_ref_type_mismatch")
                object_id = existing_object_id
            return {"object_type": object_type, "object_id": object_id}
        return {key: _resolve_refs(child, local_ids, existing) for key, child in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(child, local_ids, existing) for child in value]
    return deepcopy(value)


def _objects_by_id(
    document: Mapping[str, Any],
) -> dict[str, tuple[str, dict[str, Any]]]:
    result: dict[str, tuple[str, dict[str, Any]]] = {}
    for collection in COLLECTIONS.values():
        values = document.get(collection, [])
        if not isinstance(values, Sequence) or isinstance(values, str | bytes):
            continue
        for item in values:
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                result[str(item["id"])] = (collection, deepcopy(dict(item)))
    return result


def _target_id(target: ExistingTarget | LocalTarget, local_ids: Mapping[str, str]) -> str:
    if isinstance(target, ExistingTarget):
        return target.object_id
    try:
        return local_ids[target.local_ref]
    except KeyError as error:
        raise GeneralMutationBindingError("general_mutation_local_ref_unknown") from error


def _assert_create_fields(collection: str, fields: Mapping[str, Any]) -> None:
    object_type = _OBJECT_TYPE_BY_COLLECTION.get(collection)
    allowed = set() if object_type is None else EDITABLE_FIELDS.get(object_type, set())
    forbidden = sorted(set(fields) - allowed)
    if forbidden:
        raise GeneralMutationBindingError("general_mutation_field_forbidden")


def _bound_object(
    object_id: str,
    existing: Mapping[str, tuple[str, dict[str, Any]]],
    created: Mapping[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    if object_id in created:
        value = created[object_id]
        return _collection_for_id(object_id), value
    try:
        collection, value = existing[object_id]
        return collection, value
    except KeyError as error:
        raise GeneralMutationBindingError("general_mutation_object_unknown") from error


def _collection_for_id(object_id: str) -> str:
    prefix = object_id.split("_", 1)[0]
    for collection, expected_prefix in _ID_PREFIX_BY_COLLECTION.items():
        if prefix == expected_prefix:
            return collection
    raise GeneralMutationBindingError("general_mutation_object_id_invalid")


def _assert_editable(collection: str, path: str) -> None:
    object_type = _OBJECT_TYPE_BY_COLLECTION.get(collection)
    top = path[1:].split("/", 1)[0].replace("~1", "/").replace("~0", "~")
    if object_type is None or top not in EDITABLE_FIELDS.get(object_type, set()):
        raise GeneralMutationBindingError("general_mutation_field_forbidden")


def _pointer_parts(path: str) -> list[str]:
    if not path.startswith("/") or path == "/":
        raise GeneralMutationBindingError("general_mutation_pointer_invalid")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _pointer_value(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in _pointer_parts(path):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        elif path == "/description":
            return None
        else:
            raise GeneralMutationBindingError("general_mutation_path_missing")
    return deepcopy(current)


def _pointer_set(value: dict[str, Any], path: str, new_value: Any) -> None:
    parts = _pointer_parts(path)
    current: Any = value
    for part in parts[:-1]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise GeneralMutationBindingError("general_mutation_path_missing")
    last = parts[-1]
    if isinstance(current, dict):
        current[last] = new_value
    elif isinstance(current, list) and last.isdigit() and int(last) < len(current):
        current[int(last)] = new_value
    else:
        raise GeneralMutationBindingError("general_mutation_path_missing")


def _timestamp(value: datetime | str | None) -> str:
    if value is None:
        value = datetime.now(UTC)
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


__all__ = [
    "BoundMutationOperation",
    "BoundMutationPlan",
    "GeneralMutationBindingError",
    "append_repair_companions",
    "bind_general_mutation_plan",
    "general_mutation_impact_hash",
]
