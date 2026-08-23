"""Pure contracts for bounded logical-closure repair."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from casefile.domain.logical_mutation.models import ClosureLevel, ClosureObjectRef

RepairAutomation = Literal["agent", "mechanical", "manual", "ineligible"]
RepairAssessmentStatus = Literal[
    "eligible", "manual_required", "blocked", "not_applicable"
]


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
