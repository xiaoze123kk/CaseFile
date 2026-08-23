"""Pure contracts for bounded logical-closure repair."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from casefile.domain.logical_mutation.models import ClosureLevel, ClosureObjectRef

RepairAutomation = Literal["agent", "mechanical", "manual", "ineligible"]
RepairAssessmentStatus = Literal[
    "eligible", "manual_required", "blocked", "not_applicable"
]
RepairPathProtectionSource = Literal["primary", "mechanical", "structure_lock"]


@dataclass(frozen=True, slots=True)
class RepairPolicy:
    rule_code: str
    closure_level: ClosureLevel
    automation: RepairAutomation
    allowed_repair_kinds: tuple[str, ...]
    required_object_roles: tuple[str, ...]
    allowed_object_roles: tuple[str, ...]
    max_operations: int
    allow_create: bool = False
    allow_delete: bool = False

    def __post_init__(self) -> None:
        if not self.rule_code.strip():
            raise ValueError("repair_policy_rule_code_missing")
        if self.max_operations < 0:
            raise ValueError("repair_policy_operation_budget_invalid")
        if self.automation in {"agent", "mechanical"} and self.max_operations == 0:
            raise ValueError("repair_policy_operation_budget_missing")
        if not set(self.required_object_roles).issubset(self.allowed_object_roles):
            raise ValueError("repair_policy_role_contract_invalid")


@dataclass(frozen=True, slots=True)
class ClosureObligation:
    obligation_key: str
    source_finding_key: str
    rule_code: str
    closure_policy_version: str
    repair_policy_version: str
    base_draft_id: int
    base_revision: int
    candidate_hash: str
    object_refs: tuple[ClosureObjectRef, ...]
    caused_by_operation_ids: tuple[str, ...]
    dependency_path: tuple[str, ...]
    allowed_repair_kinds: tuple[str, ...]
    automation: RepairAutomation
    max_operations: int
    allow_create: bool
    allow_delete: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "obligation_key": self.obligation_key,
            "source_finding_key": self.source_finding_key,
            "rule_code": self.rule_code,
            "closure_policy_version": self.closure_policy_version,
            "repair_policy_version": self.repair_policy_version,
            "base_draft_id": self.base_draft_id,
            "base_revision": self.base_revision,
            "candidate_hash": self.candidate_hash,
            "object_refs": [ref.as_dict() for ref in self.object_refs],
            "caused_by_operation_ids": list(self.caused_by_operation_ids),
            "dependency_path": list(self.dependency_path),
            "allowed_repair_kinds": list(self.allowed_repair_kinds),
            "automation": self.automation,
            "max_operations": self.max_operations,
            "allow_create": self.allow_create,
            "allow_delete": self.allow_delete,
        }


@dataclass(frozen=True, slots=True)
class ClosureRepairAssessment:
    status: RepairAssessmentStatus
    reason_code: str
    obligations: tuple[ClosureObligation, ...] = ()
    blocking_finding_keys: tuple[str, ...] = ()
    manual_finding_keys: tuple[str, ...] = ()

    @property
    def agent_repair_allowed(self) -> bool:
        return self.status == "eligible"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "agent_repair_allowed": self.agent_repair_allowed,
            "obligations": [item.as_dict() for item in self.obligations],
            "blocking_finding_keys": list(self.blocking_finding_keys),
            "manual_finding_keys": list(self.manual_finding_keys),
        }


@dataclass(frozen=True, slots=True)
class ProtectedRepairPath:
    object_id: str
    field_path: str
    source: RepairPathProtectionSource
    source_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "object_id": self.object_id,
            "field_path": self.field_path,
            "source": self.source,
            "source_id": self.source_id,
        }


@dataclass(frozen=True, slots=True)
class RepairObjectPaths:
    object_id: str
    field_paths: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"object_id": self.object_id, "field_paths": list(self.field_paths)}


@dataclass(frozen=True, slots=True)
class ScopedRepairObligation:
    obligation_key: str
    source_finding_key: str
    rule_code: str
    subject_object_ids: tuple[str, ...]
    effective_repair_kinds: tuple[str, ...]
    allowed_paths: tuple[RepairObjectPaths, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "obligation_key": self.obligation_key,
            "source_finding_key": self.source_finding_key,
            "rule_code": self.rule_code,
            "subject_object_ids": list(self.subject_object_ids),
            "effective_repair_kinds": list(self.effective_repair_kinds),
            "allowed_paths": [item.as_dict() for item in self.allowed_paths],
        }


@dataclass(frozen=True, slots=True)
class RepairScopeV1:
    scope_version: str
    closure_policy_version: str
    repair_policy_version: str
    base_draft_id: int
    base_revision: int
    candidate_hash: str
    obligations: tuple[ScopedRepairObligation, ...]
    read_write_object_ids: tuple[str, ...]
    read_only_object_ids: tuple[str, ...]
    allowed_paths: tuple[RepairObjectPaths, ...]
    protected_paths: tuple[ProtectedRepairPath, ...]
    structure_lock_ids: tuple[str, ...]
    max_operations: int
    max_context_objects: int
    max_write_objects: int

    def allowed_paths_for(self, object_id: str) -> tuple[str, ...]:
        return next(
            (item.field_paths for item in self.allowed_paths if item.object_id == object_id),
            (),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "scope_version": self.scope_version,
            "closure_policy_version": self.closure_policy_version,
            "repair_policy_version": self.repair_policy_version,
            "base_draft_id": self.base_draft_id,
            "base_revision": self.base_revision,
            "candidate_hash": self.candidate_hash,
            "obligations": [item.as_dict() for item in self.obligations],
            "read_write_object_ids": list(self.read_write_object_ids),
            "read_only_object_ids": list(self.read_only_object_ids),
            "allowed_paths": [item.as_dict() for item in self.allowed_paths],
            "protected_paths": [item.as_dict() for item in self.protected_paths],
            "structure_lock_ids": list(self.structure_lock_ids),
            "max_operations": self.max_operations,
            "max_context_objects": self.max_context_objects,
            "max_write_objects": self.max_write_objects,
        }


@dataclass(frozen=True, slots=True)
class RepairContextObject:
    object_id: str
    object_type: str
    access: Literal["read_write", "read_only"]
    object_value: Mapping[str, Any]

    def as_dict(self) -> dict[str, object]:
        return {
            "object_id": self.object_id,
            "object_type": self.object_type,
            "access": self.access,
            "object_value": deepcopy(dict(self.object_value)),
        }


@dataclass(frozen=True, slots=True)
class ClosureRepairContextV1:
    context_version: str
    scope_version: str
    closure_policy_version: str
    repair_policy_version: str
    base_draft_id: int
    base_revision: int
    baseline_hash: str
    candidate_hash: str
    original_intent: str
    primary_operations: tuple[Mapping[str, Any], ...]
    obligations: tuple[ScopedRepairObligation, ...]
    objects: tuple[RepairContextObject, ...]
    allowed_paths: tuple[RepairObjectPaths, ...]
    protected_paths: tuple[ProtectedRepairPath, ...]
    structure_lock_ids: tuple[str, ...]
    dependency_paths: tuple[tuple[str, ...], ...]
    relevant_edges: tuple[Mapping[str, str], ...]
    max_operations: int
    max_context_objects: int
    max_write_objects: int
    context_hash: str

    def hash_payload(self) -> dict[str, object]:
        return {
            "context_version": self.context_version,
            "scope_version": self.scope_version,
            "closure_policy_version": self.closure_policy_version,
            "repair_policy_version": self.repair_policy_version,
            "base_draft_id": self.base_draft_id,
            "base_revision": self.base_revision,
            "baseline_hash": self.baseline_hash,
            "candidate_hash": self.candidate_hash,
            "original_intent": self.original_intent,
            "primary_operations": [deepcopy(dict(item)) for item in self.primary_operations],
            "obligations": [item.as_dict() for item in self.obligations],
            "objects": [item.as_dict() for item in self.objects],
            "allowed_paths": [item.as_dict() for item in self.allowed_paths],
            "protected_paths": [item.as_dict() for item in self.protected_paths],
            "structure_lock_ids": list(self.structure_lock_ids),
            "dependency_paths": [list(item) for item in self.dependency_paths],
            "relevant_edges": [dict(item) for item in self.relevant_edges],
            "max_operations": self.max_operations,
            "max_context_objects": self.max_context_objects,
            "max_write_objects": self.max_write_objects,
        }

    def as_dict(self) -> dict[str, object]:
        return {**self.hash_payload(), "context_hash": self.context_hash}
