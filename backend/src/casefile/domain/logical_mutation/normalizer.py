"""Normalize logical mutations and construct an in-memory candidate document."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

import networkx as nx
import rfc8785

from casefile.domain.logical_mutation.graph import COLLECTION_BY_TYPE
from casefile.domain.logical_mutation.models import (
    CLOSURE_POLICY_VERSION,
    OLD_VALUE_UNSET,
    CreateObject,
    DeleteObject,
    MechanicalOperation,
    MutationOperation,
    MutationSet,
    NormalizedMutation,
    UpdateField,
)

_TYPE_BY_COLLECTION = {
    collection: object_type for object_type, collection in COLLECTION_BY_TYPE.items()
}
_RECIPROCAL_FIELDS = {
    ("information_unit", "supports_claim_refs"): ("claim", "support_refs"),
    ("information_unit", "refutes_claim_refs"): ("claim", "refute_refs"),
    ("claim", "support_refs"): ("information_unit", "supports_claim_refs"),
    ("claim", "refute_refs"): ("information_unit", "refutes_claim_refs"),
}
_IMMUTABLE_OBJECT_FIELDS = {"id", "revision", "created_by", "updated_at"}


class MutationNormalizationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def normalize_mutation(
    document: Mapping[str, Any], mutation_set: MutationSet
) -> NormalizedMutation:
    if mutation_set.closure_policy_version != CLOSURE_POLICY_VERSION:
        raise MutationNormalizationError("closure_policy_version_stale")
    if mutation_set.base_draft_id < 1 or mutation_set.base_revision < 1:
        raise MutationNormalizationError("mutation_base_invalid")
    operation_ids = [operation.operation_id for operation in mutation_set.operations]
    if any(not value.strip() for value in operation_ids):
        raise MutationNormalizationError("operation_id_missing")
    if len(operation_ids) != len(set(operation_ids)):
        raise MutationNormalizationError("operation_id_duplicate")

    working = deepcopy(dict(document))
    ordered = _order_operations(working, mutation_set.operations)
    mechanical: list[MechanicalOperation] = []
    deleted_ids: set[str] = set()
    existing_changed_ids: set[str] = set()
    touched_relations: list[tuple[str, str]] = []

    for operation in ordered:
        if isinstance(operation, CreateObject):
            if operation.collection not in _TYPE_BY_COLLECTION:
                raise MutationNormalizationError("collection_invalid")
            if not operation.object_id:
                raise MutationNormalizationError("object_id_missing")
            if _find_object(working, operation.object_id) is not None:
                raise MutationNormalizationError("object_id_conflict")
            working[operation.collection].append(deepcopy(dict(operation.object_value)))
            touched_relations.append((operation.object_id, ""))
        elif isinstance(operation, UpdateField):
            found = _find_object(working, operation.object_id)
            if found is None:
                raise MutationNormalizationError("object_not_found")
            if operation.object_id in deleted_ids:
                raise MutationNormalizationError("update_after_delete")
            _collection, _object_type, item = found
            if _pointer_parts(operation.field_path)[0] in _IMMUTABLE_OBJECT_FIELDS:
                raise MutationNormalizationError("immutable_field_update")
            if (
                operation.expected_object_revision is not None
                and item.get("revision") != operation.expected_object_revision
            ):
                raise MutationNormalizationError("object_revision_conflict")
            old_value = _pointer_get(item, operation.field_path)
            if old_value is _MISSING and operation.field_path == "/description":
                old_value = None
            if old_value is _MISSING:
                raise MutationNormalizationError("path_not_found")
            if operation.old_value is not OLD_VALUE_UNSET and old_value != operation.old_value:
                raise MutationNormalizationError("old_value_conflict")
            if operation.field_path == "/description" and operation.new_value is None:
                item.pop("description", None)
            else:
                _pointer_set(item, operation.field_path, deepcopy(operation.new_value))
            existing_changed_ids.add(operation.object_id)
            touched_relations.append((operation.object_id, operation.field_path))
        else:
            found = _find_object(working, operation.object_id)
            if found is None:
                raise MutationNormalizationError("object_not_found")
            collection, _object_type, item = found
            if operation.old_object_value is not None and dict(operation.old_object_value) != item:
                raise MutationNormalizationError("old_object_value_conflict")
            working[collection] = [
                value for value in working[collection] if value.get("id") != operation.object_id
            ]
            deleted_ids.add(operation.object_id)

    for deleted_id in sorted(deleted_ids):
        _remove_inbound_refs(working, deleted_id, mechanical)
    for object_id, field_path in touched_relations:
        _synchronize_relation(working, object_id, field_path, mechanical)
    _stable_sort_relations(working)
    existing_changed_ids.update(operation.object_id for operation in mechanical)
    for object_id in sorted(existing_changed_ids - deleted_ids):
        found = _find_object(working, object_id)
        if found is not None:
            item = found[2]
            revision = item.get("revision")
            if isinstance(revision, int) and not isinstance(revision, bool):
                item["revision"] = revision + 1

    return NormalizedMutation(mutation_set, ordered, tuple(mechanical), working)


def _order_operations(
    document: Mapping[str, Any], operations: tuple[MutationOperation, ...]
) -> tuple[MutationOperation, ...]:
    del document
    graph: nx.DiGraph[int] = nx.DiGraph()
    graph.add_nodes_from(range(len(operations)))
    created_by = {
        operation.object_id: index
        for index, operation in enumerate(operations)
        if isinstance(operation, CreateObject)
    }
    deleted_by = {
        operation.object_id: index
        for index, operation in enumerate(operations)
        if isinstance(operation, DeleteObject)
    }
    last_for_object: dict[str, int] = {}
    for index, operation in enumerate(operations):
        object_id = operation.object_id
        if object_id in last_for_object:
            graph.add_edge(last_for_object[object_id], index)
        last_for_object[object_id] = index
        for referenced_id in _referenced_ids(operation):
            create_index = created_by.get(referenced_id)
            if create_index is not None and create_index != index:
                graph.add_edge(create_index, index)
        delete_index = deleted_by.get(object_id)
        if delete_index is not None and not isinstance(operation, DeleteObject):
            graph.add_edge(index, delete_index)
    try:
        order = tuple(nx.lexicographical_topological_sort(graph, key=lambda value: value))
    except nx.NetworkXUnfeasible as error:
        raise MutationNormalizationError("operation_dependency_cycle") from error
    return tuple(operations[index] for index in order)


def _referenced_ids(operation: MutationOperation) -> set[str]:
    value: Any
    if isinstance(operation, CreateObject):
        value = operation.object_value
    elif isinstance(operation, UpdateField):
        value = operation.new_value
    else:
        return set()
    result: set[str] = set()
    _collect_ref_ids(value, result)
    return result


def _collect_ref_ids(value: Any, result: set[str]) -> None:
    if isinstance(value, Mapping):
        if isinstance(value.get("object_id"), str):
            result.add(str(value["object_id"]))
        for child in value.values():
            _collect_ref_ids(child, result)
    elif isinstance(value, list | tuple):
        for child in value:
            _collect_ref_ids(child, result)


def _remove_inbound_refs(
    document: dict[str, Any], deleted_id: str, mechanical: list[MechanicalOperation]
) -> None:
    for collection in COLLECTION_BY_TYPE.values():
        for item in document.get(collection, []):
            _remove_refs_from_value(item, deleted_id, item["id"], "", mechanical)


def _remove_refs_from_value(
    value: Any,
    deleted_id: str,
    owner_id: str,
    path: str,
    mechanical: list[MechanicalOperation],
) -> None:
    if isinstance(value, dict):
        for key, child in list(value.items()):
            child_path = f"{path}/{_escape(key)}"
            if isinstance(child, dict) and child.get("object_id") == deleted_id:
                old = deepcopy(child)
                value[key] = None
                mechanical.append(
                    _mechanical(owner_id, child_path, old, None, "deleted_reference_removed")
                )
            else:
                _remove_refs_from_value(child, deleted_id, owner_id, child_path, mechanical)
    elif isinstance(value, list):
        old_list: list[Any] = deepcopy(value)
        value[:] = [
            item
            for item in value
            if not (isinstance(item, dict) and item.get("object_id") == deleted_id)
        ]
        if value != old_list:
            mechanical.append(
                _mechanical(
                    owner_id,
                    path,
                    old_list,
                    deepcopy(value),
                    "deleted_reference_removed",
                )
            )
        for index, child in enumerate(value):
            _remove_refs_from_value(child, deleted_id, owner_id, f"{path}/{index}", mechanical)


def _synchronize_relation(
    document: dict[str, Any], object_id: str, field_path: str, mechanical: list[MechanicalOperation]
) -> None:
    found = _find_object(document, object_id)
    if found is None:
        return
    _collection, object_type, item = found
    fields = (
        [field_path.split("/")[1]]
        if field_path.startswith("/") and len(field_path.split("/")) > 1
        else [
            field for candidate_type, field in _RECIPROCAL_FIELDS if candidate_type == object_type
        ]
    )
    for field in fields:
        reciprocal = _RECIPROCAL_FIELDS.get((object_type, field))
        if reciprocal is None:
            continue
        target_type, target_field = reciprocal
        desired = {
            str(ref["object_id"]): dict(ref)
            for ref in item.get(field, [])
            if isinstance(ref, Mapping)
        }
        for target_id, _ref in sorted(desired.items()):
            target = _find_object(document, target_id)
            if target is None or target[1] != target_type:
                continue
            target_item = target[2]
            old = deepcopy(target_item.get(target_field, []))
            if object_id not in {entry.get("object_id") for entry in old}:
                target_item[target_field] = [
                    *old,
                    {"object_type": object_type, "object_id": object_id},
                ]
                mechanical.append(
                    _mechanical(
                        target_id,
                        f"/{target_field}",
                        old,
                        deepcopy(target_item[target_field]),
                        "reciprocal_relation_added",
                    )
                )
        for target_item in _objects_of_type(document, target_type):
            if target_item["id"] in desired:
                continue
            old = deepcopy(target_item.get(target_field, []))
            new = [entry for entry in old if entry.get("object_id") != object_id]
            if new != old:
                target_item[target_field] = new
                mechanical.append(
                    _mechanical(
                        target_item["id"],
                        f"/{target_field}",
                        old,
                        deepcopy(new),
                        "reciprocal_relation_removed",
                    )
                )


def _stable_sort_relations(document: dict[str, Any]) -> None:
    for collection in COLLECTION_BY_TYPE.values():
        for item in document.get(collection, []):
            _sort_ref_lists(item)


def _sort_ref_lists(value: Any) -> None:
    if isinstance(value, dict):
        for child in value.values():
            _sort_ref_lists(child)
    elif isinstance(value, list):
        if all(isinstance(item, dict) and "object_id" in item for item in value):
            unique = {(item.get("object_type"), item.get("object_id")): item for item in value}
            value[:] = [unique[key] for key in sorted(unique)]
        else:
            for child in value:
                _sort_ref_lists(child)


def _objects_of_type(document: Mapping[str, Any], object_type: str) -> list[dict[str, Any]]:
    return list(document.get(COLLECTION_BY_TYPE[object_type], []))


def _find_object(
    document: Mapping[str, Any], object_id: str
) -> tuple[str, str, dict[str, Any]] | None:
    for object_type, collection in COLLECTION_BY_TYPE.items():
        for item in document.get(collection, []):
            if item.get("id") == object_id:
                return collection, object_type, item
    return None


_MISSING = object()


def _pointer_parts(path: str) -> list[str]:
    if not path.startswith("/") or path == "/":
        raise MutationNormalizationError("field_path_invalid")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _pointer_get(value: Any, path: str) -> Any:
    current = value
    for part in _pointer_parts(path):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (IndexError, ValueError):
                return _MISSING
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return _MISSING
    return current


def _pointer_set(value: Any, path: str, new_value: Any) -> None:
    parts = _pointer_parts(path)
    current = value
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    if isinstance(current, list):
        current[int(parts[-1])] = new_value
    else:
        current[parts[-1]] = new_value


def _mechanical(
    object_id: str, field_path: str, old: Any, new: Any, reason: str
) -> MechanicalOperation:
    digest = hashlib.sha256(
        f"{reason}\0{object_id}\0{field_path}\0".encode()
        + rfc8785.dumps({"old": old, "new": new})
    ).hexdigest()[:20]
    return MechanicalOperation(
        operation_id=f"sys_{digest}",
        reason_code=reason,
        object_id=object_id,
        field_path=field_path,
        old_value=old,
        new_value=new,
    )


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
