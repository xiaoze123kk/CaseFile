"""Rebuild one replayable MutationSet from a repaired candidate document."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from casefile.domain.logical_mutation.graph import COLLECTION_BY_TYPE
from casefile.domain.logical_mutation.models import (
    CreateObject,
    DeleteObject,
    MutationSet,
    UpdateField,
)

_IGNORED_OBJECT_FIELDS = frozenset({"id", "revision"})


class RepairDocumentDiffError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def build_mutation_from_document_diff(
    baseline_document: Mapping[str, Any],
    repaired_document: Mapping[str, Any],
    primary_mutation: MutationSet,
    *,
    mechanical_paths: Sequence[tuple[str, str]] = (),
) -> MutationSet:
    """Create a deterministic top-level semantic diff for final proof.

    Normalizer-owned mechanical paths are omitted unless they overlap a path
    explicitly authored by the primary mutation. Replaying the returned
    MutationSet must regenerate those mechanical projections.
    """

    _validate_envelope(baseline_document, repaired_document)
    primary_paths = _primary_paths(primary_mutation)
    operations: list[CreateObject | UpdateField | DeleteObject] = []
    for collection in sorted(COLLECTION_BY_TYPE.values()):
        baseline = _objects(baseline_document, collection)
        repaired = _objects(repaired_document, collection)
        for object_id in baseline:
            if object_id in repaired:
                continue
            operations.append(
                DeleteObject(
                    _operation_id("delete", object_id, ""),
                    object_id,
                    deepcopy(dict(baseline[object_id])),
                )
            )
        for object_id in repaired:
            if object_id in baseline:
                continue
            operations.append(
                CreateObject(
                    _operation_id("create", object_id, ""),
                    collection,
                    deepcopy(dict(repaired[object_id])),
                )
            )
        for object_id in sorted(set(baseline) & set(repaired)):
            before = baseline[object_id]
            after = repaired[object_id]
            for field_name in sorted(
                (set(before) | set(after)) - _IGNORED_OBJECT_FIELDS
            ):
                before_value = before.get(field_name)
                after_value = after.get(field_name)
                if before_value == after_value:
                    continue
                field_path = f"/{_escape(field_name)}"
                if _mechanical_only(
                    object_id,
                    field_path,
                    primary_paths=primary_paths,
                    mechanical_paths=mechanical_paths,
                ):
                    continue
                if (
                    field_name not in before or field_name not in after
                ) and field_name != "description":
                    raise RepairDocumentDiffError("repair_rebase_field_shape_unsupported")
                revision = before.get("revision")
                operations.append(
                    UpdateField(
                        _operation_id("update", object_id, field_path),
                        object_id,
                        field_path,
                        deepcopy(after_value),
                        deepcopy(before_value),
                        (
                            revision
                            if isinstance(revision, int)
                            and not isinstance(revision, bool)
                            else None
                        ),
                    )
                )
    if not operations:
        raise RepairDocumentDiffError("repair_rebase_empty")
    return MutationSet(
        mutation_set_id=f"{primary_mutation.mutation_set_id}_closure_repaired",
        base_draft_id=primary_mutation.base_draft_id,
        base_revision=primary_mutation.base_revision,
        operations=tuple(operations),
        actor=primary_mutation.actor,
        mode=primary_mutation.mode,
        closure_policy_version=primary_mutation.closure_policy_version,
    )


def _validate_envelope(
    baseline_document: Mapping[str, Any], repaired_document: Mapping[str, Any]
) -> None:
    collections = set(COLLECTION_BY_TYPE.values())
    baseline_envelope = {
        key: value for key, value in baseline_document.items() if key not in collections
    }
    repaired_envelope = {
        key: value for key, value in repaired_document.items() if key not in collections
    }
    if baseline_envelope != repaired_envelope:
        raise RepairDocumentDiffError("repair_rebase_envelope_changed")


def _objects(
    document: Mapping[str, Any], collection: str
) -> dict[str, Mapping[str, Any]]:
    raw = document.get(collection, [])
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise RepairDocumentDiffError("repair_rebase_document_invalid")
    result: dict[str, Mapping[str, Any]] = {}
    for value in raw:
        if not isinstance(value, Mapping) or not isinstance(value.get("id"), str):
            raise RepairDocumentDiffError("repair_rebase_document_invalid")
        object_id = str(value["id"])
        if object_id in result:
            raise RepairDocumentDiffError("repair_rebase_object_duplicate")
        result[object_id] = value
    return result


def _primary_paths(mutation_set: MutationSet) -> tuple[tuple[str, str], ...]:
    return tuple(
        (
            operation.object_id,
            operation.field_path if isinstance(operation, UpdateField) else "",
        )
        for operation in mutation_set.operations
    )


def _mechanical_only(
    object_id: str,
    field_path: str,
    *,
    primary_paths: Sequence[tuple[str, str]],
    mechanical_paths: Sequence[tuple[str, str]],
) -> bool:
    mechanical = any(
        candidate_id == object_id and _paths_overlap(field_path, candidate_path)
        for candidate_id, candidate_path in mechanical_paths
    )
    primary = any(
        candidate_id == object_id and _paths_overlap(field_path, candidate_path)
        for candidate_id, candidate_path in primary_paths
    )
    return mechanical and not primary


def _paths_overlap(left: str, right: str) -> bool:
    if not left or not right:
        return True
    left_parts = left[1:].split("/")
    right_parts = right[1:].split("/")
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]


def _operation_id(kind: str, object_id: str, field_path: str) -> str:
    digest = hashlib.sha256(
        f"{kind}\0{object_id}\0{field_path}".encode()
    ).hexdigest()[:20]
    return f"repair_{kind}_{digest}"


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


__all__ = [
    "RepairDocumentDiffError",
    "build_mutation_from_document_diff",
]
