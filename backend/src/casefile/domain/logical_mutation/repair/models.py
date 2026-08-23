"""Pure contracts for bounded logical-closure repair."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from casefile.domain.logical_mutation.models import ClosureLevel, ClosureObjectRef

if TYPE_CHECKING:
    from casefile.domain.logical_mutation.models import MutationSet
    from casefile.domain.verification_engine import MutationSimulation

RepairAutomation = Literal["agent", "mechanical", "manual", "ineligible"]
RepairAssessmentStatus = Literal[
    "eligible", "manual_required", "blocked", "not_applicable"
]
RepairPathProtectionSource = Literal["primary", "mechanical", "structure_lock"]
RepairRunStatus = Literal[
    "repaired",
    "not_applicable",
    "manual_required",
    "intent_revision_required",
    "blocked",
    "proposal_rejected",
    "no_progress",
    "cycle_detected",
    "exhausted",
    "rebase_mismatch",
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
class RepairAllowedWrite:
    """One server-authored writable field and its JSON value affordance."""

    object_id: str
    field_path: str
    obligation_keys: tuple[str, ...]
    current_value: Any
    value_schema: Mapping[str, Any]

    def as_dict(self) -> dict[str, object]:
        return {
            "object_id": self.object_id,
            "field_path": self.field_path,
            "obligation_keys": list(self.obligation_keys),
            "current_value": deepcopy(self.current_value),
            "value_schema": deepcopy(dict(self.value_schema)),
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


@dataclass(frozen=True, slots=True)
class ClosureRepairContextV2(ClosureRepairContextV1):
    """V2 context adds exact server-owned write affordances."""

    allowed_writes: tuple[RepairAllowedWrite, ...]

    def hash_payload(self) -> dict[str, object]:
        return {
            **ClosureRepairContextV1.hash_payload(self),
            "allowed_writes": [item.as_dict() for item in self.allowed_writes],
        }


@dataclass(frozen=True, slots=True)
class RepairUpdateOperation:
    obligation_keys: tuple[str, ...]
    object_id: str
    field_path: str
    new_value: Any
    reason: str

    def __post_init__(self) -> None:
        if not self.obligation_keys or any(
            not value.strip() for value in self.obligation_keys
        ):
            raise ValueError("repair_proposal_obligation_keys_missing")
        if len(self.obligation_keys) != len(set(self.obligation_keys)):
            raise ValueError("repair_proposal_obligation_keys_duplicate")
        if not self.object_id.strip():
            raise ValueError("repair_proposal_object_id_missing")
        if not self.field_path.startswith("/"):
            raise ValueError("repair_proposal_field_path_invalid")
        if not self.reason.strip():
            raise ValueError("repair_proposal_reason_missing")

    def as_dict(self) -> dict[str, object]:
        return {
            "obligation_keys": list(self.obligation_keys),
            "object_id": self.object_id,
            "field_path": self.field_path,
            "new_value": deepcopy(self.new_value),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class RepairProposal:
    context_hash: str
    operations: tuple[RepairUpdateOperation, ...]

    def __post_init__(self) -> None:
        if len(self.context_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.context_hash
        ):
            raise ValueError("repair_proposal_context_hash_invalid")
        if not self.operations:
            raise ValueError("repair_proposal_operations_missing")

    def as_dict(self) -> dict[str, object]:
        return {
            "context_hash": self.context_hash,
            "operations": [item.as_dict() for item in self.operations],
        }


@dataclass(frozen=True, slots=True)
class CompanionRepairOperation:
    repair_round: int
    obligation_keys: tuple[str, ...]
    object_id: str
    field_path: str
    new_value: Any
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "repair_round": self.repair_round,
            "obligation_keys": list(self.obligation_keys),
            "object_id": self.object_id,
            "field_path": self.field_path,
            "new_value": deepcopy(self.new_value),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ClosureRepairRound:
    round_no: int
    context_hash: str
    proposal: RepairProposal
    obligation_keys_before: tuple[str, ...]
    obligation_keys_after: tuple[str, ...]
    candidate_hash_before: str
    candidate_hash_after: str
    outcome: str

    def as_dict(self) -> dict[str, object]:
        return {
            "round_no": self.round_no,
            "context_hash": self.context_hash,
            "proposal": self.proposal.as_dict(),
            "obligation_keys_before": list(self.obligation_keys_before),
            "obligation_keys_after": list(self.obligation_keys_after),
            "candidate_hash_before": self.candidate_hash_before,
            "candidate_hash_after": self.candidate_hash_after,
            "outcome": self.outcome,
        }


@dataclass(frozen=True, slots=True)
class ClosureRepairResult:
    status: RepairRunStatus
    reason_code: str
    original_simulation: MutationSimulation
    rounds: tuple[ClosureRepairRound, ...] = ()
    companion_operations: tuple[CompanionRepairOperation, ...] = ()
    final_mutation_set: MutationSet | None = None
    final_simulation: MutationSimulation | None = None

    @property
    def repaired(self) -> bool:
        return self.status == "repaired"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "repaired": self.repaired,
            "rounds": [item.as_dict() for item in self.rounds],
            "companion_operations": [
                item.as_dict() for item in self.companion_operations
            ],
            "final_mutation_set": (
                None
                if self.final_mutation_set is None
                else {
                    "mutation_set_id": self.final_mutation_set.mutation_set_id,
                    "base_draft_id": self.final_mutation_set.base_draft_id,
                    "base_revision": self.final_mutation_set.base_revision,
                    "operation_ids": [
                        item.operation_id for item in self.final_mutation_set.operations
                    ],
                    "actor": self.final_mutation_set.actor,
                    "mode": self.final_mutation_set.mode,
                    "closure_policy_version": (
                        self.final_mutation_set.closure_policy_version
                    ),
                }
            ),
            "final_simulation": (
                None
                if self.final_simulation is None
                else self.final_simulation.as_dict()
            ),
        }
