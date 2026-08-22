"""Stable pure-domain contracts for logical CaseFile mutations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

CLOSURE_POLICY_VERSION = "logical-mutation-v1"
MutationMode = Literal["normal", "restructure"]
MutationActor = Literal["author", "agent", "import", "system"]
ClosureLevel = Literal["hard_invariant", "repair_required", "warning"]
OLD_VALUE_UNSET = object()


@dataclass(frozen=True, slots=True)
class CreateObject:
    operation_id: str
    collection: str
    object_value: Mapping[str, Any]
    operation_type: Literal["create_object"] = "create_object"

    @property
    def object_id(self) -> str:
        return str(self.object_value.get("id", ""))


@dataclass(frozen=True, slots=True)
class UpdateField:
    operation_id: str
    object_id: str
    field_path: str
    new_value: Any
    old_value: Any = field(default=OLD_VALUE_UNSET)
    expected_object_revision: int | None = None
    operation_type: Literal["update_field"] = "update_field"


@dataclass(frozen=True, slots=True)
class DeleteObject:
    operation_id: str
    object_id: str
    old_object_value: Mapping[str, Any] | None = None
    operation_type: Literal["delete_object"] = "delete_object"


type MutationOperation = CreateObject | UpdateField | DeleteObject


@dataclass(frozen=True, slots=True)
class MutationSet:
    mutation_set_id: str
    base_draft_id: int
    base_revision: int
    operations: tuple[MutationOperation, ...]
    actor: MutationActor = "author"
    mode: MutationMode = "normal"
    closure_policy_version: str = CLOSURE_POLICY_VERSION


@dataclass(frozen=True, slots=True)
class MechanicalOperation:
    operation_id: str
    reason_code: str
    object_id: str
    field_path: str
    old_value: Any
    new_value: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "operation_type": "update_field",
            "reason_code": self.reason_code,
            "object_id": self.object_id,
            "field_path": self.field_path,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }


@dataclass(frozen=True, slots=True)
class NormalizedMutation:
    mutation_set: MutationSet
    ordered_operations: tuple[MutationOperation, ...]
    mechanical_operations: tuple[MechanicalOperation, ...]
    candidate_document: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class LogicEdge:
    prerequisite_id: str
    dependent_id: str
    relation: str
    strength: Literal["hard", "conditional", "contextual"]
    source_path: str

    def as_dict(self) -> dict[str, str]:
        return {
            "prerequisite_id": self.prerequisite_id,
            "dependent_id": self.dependent_id,
            "relation": self.relation,
            "strength": self.strength,
            "source_path": self.source_path,
        }


@dataclass(frozen=True, slots=True)
class LogicCycle:
    relation: str
    object_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"relation": self.relation, "object_ids": list(self.object_ids)}


@dataclass(frozen=True, slots=True)
class ImpactCone:
    root_object_ids: tuple[str, ...]
    direct_object_ids: tuple[str, ...]
    transitive_object_ids: tuple[str, ...]
    affected_edges: tuple[LogicEdge, ...]
    affected_resolution_ids: tuple[str, ...]
    dependency_paths: tuple[tuple[str, ...], ...]
    cycles: tuple[LogicCycle, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "root_object_ids": list(self.root_object_ids),
            "direct_object_ids": list(self.direct_object_ids),
            "transitive_object_ids": list(self.transitive_object_ids),
            "affected_edges": [edge.as_dict() for edge in self.affected_edges],
            "affected_resolution_ids": list(self.affected_resolution_ids),
            "dependency_paths": [list(path) for path in self.dependency_paths],
            "cycles": [cycle.as_dict() for cycle in self.cycles],
        }


@dataclass(frozen=True, slots=True)
class ClosureIssue:
    rule_code: str
    level: ClosureLevel
    title: str
    message: str
    object_ids: tuple[str, ...]
    caused_by_operation_ids: tuple[str, ...] = ()
    dependency_path: tuple[str, ...] = ()
    repair_kinds: tuple[str, ...] = ()
