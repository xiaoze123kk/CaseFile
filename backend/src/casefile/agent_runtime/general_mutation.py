"""Runtime-private contract for planning general CaseFile mutations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GENERAL_MUTATION_PLAN_VERSION_V1: Literal["general-mutation-planner-v1"] = (
    "general-mutation-planner-v1"
)
GENERAL_MUTATION_PLAN_VERSION: Literal["general-mutation-planner-v2"] = (
    "general-mutation-planner-v2"
)
GENERAL_MUTATION_PROMPT_VERSION: Literal["general-mutation-planner-v6"] = (
    "general-mutation-planner-v6"
)
GENERAL_MUTATION_SCHEMA_ID_V1 = "general-mutation-plan-v1"
GENERAL_MUTATION_SCHEMA_ID = "general-mutation-plan-v2"
GENERAL_MUTATION_COMPONENT_ID = "general_mutation_planner"
GENERAL_MUTATION_POLICY_VERSION = "general-mutation-policy-v2"
GENERAL_MUTATION_BINDER_VERSION_V1 = "general-mutation-binder-v1"
GENERAL_MUTATION_BINDER_VERSION = "general-mutation-binder-v3"
GENERAL_MUTATION_TRANSPORT_VERSION = "general-mutation-json-object-v1"

ALLOWED_COLLECTIONS = frozenset(
    {
        "entities",
        "relationships",
        "locations",
        "events",
        "information_units",
        "claims",
        "hypotheses",
        "reasoning_paths",
    }
)
PROTECTED_COLLECTIONS = frozenset({"resolution_specs", "constraints", "structure_locks"})
SYSTEM_FIELDS = frozenset({"id", "revision", "confirmation_status", "created_by", "updated_at"})
MAX_OPERATIONS = 12
MAX_CREATES = 4
MAX_DELETES = 2


class StrictMutationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ExistingTarget(StrictMutationModel):
    ref_kind: Literal["existing"]
    object_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,59}$")


class LocalTarget(StrictMutationModel):
    ref_kind: Literal["local"]
    local_ref: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")


type MutationTarget = Annotated[ExistingTarget | LocalTarget, Field(discriminator="ref_kind")]


class MutationOperationBase(StrictMutationModel):
    operation_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,57}$")
    depends_on_operation_keys: list[str] = Field(default_factory=list, max_length=12)
    reason: str = Field(min_length=1, max_length=2_000)

    @field_validator("depends_on_operation_keys")
    @classmethod
    def dependencies_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("general_mutation_dependency_duplicate")
        return value


class CreateMutationCandidate(MutationOperationBase):
    operation_type: Literal["create_object"]
    local_ref: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    collection: str
    fields: dict[str, Any]

    @model_validator(mode="after")
    def create_contract_is_bounded(self) -> CreateMutationCandidate:
        if self.collection not in ALLOWED_COLLECTIONS:
            raise ValueError("general_mutation_collection_forbidden")
        if SYSTEM_FIELDS.intersection(self.fields):
            raise ValueError("general_mutation_model_system_field_forbidden")
        return self


class UpdateMutationCandidate(MutationOperationBase):
    operation_type: Literal["update_field"]
    target: MutationTarget
    field_path: str = Field(pattern=r"^/(?:[^/~]|~[01])+(?:/(?:[^/~]|~[01])+)*$")
    new_value: Any


class DeleteMutationCandidate(MutationOperationBase):
    operation_type: Literal["delete_object"]
    target: ExistingTarget


type MutationPlanOperation = Annotated[
    CreateMutationCandidate | UpdateMutationCandidate | DeleteMutationCandidate,
    Field(discriminator="operation_type"),
]


class MutationPlanV1(StrictMutationModel):
    plan_version: Literal["general-mutation-planner-v1"] = GENERAL_MUTATION_PLAN_VERSION_V1
    operations: list[MutationPlanOperation] = Field(min_length=1, max_length=MAX_OPERATIONS)

    @model_validator(mode="after")
    def plan_is_bounded_and_connected(self) -> MutationPlanV1:
        keys = [item.operation_key for item in self.operations]
        if len(keys) != len(set(keys)):
            raise ValueError("general_mutation_operation_key_duplicate")
        local_refs = [
            item.local_ref for item in self.operations if isinstance(item, CreateMutationCandidate)
        ]
        if len(local_refs) != len(set(local_refs)):
            raise ValueError("general_mutation_local_ref_duplicate")
        if sum(isinstance(item, CreateMutationCandidate) for item in self.operations) > MAX_CREATES:
            raise ValueError("general_mutation_create_budget_exceeded")
        if sum(isinstance(item, DeleteMutationCandidate) for item in self.operations) > MAX_DELETES:
            raise ValueError("general_mutation_delete_budget_exceeded")
        known_keys = set(keys)
        for item in self.operations:
            if item.operation_key in item.depends_on_operation_keys:
                raise ValueError("general_mutation_self_dependency")
            if not set(item.depends_on_operation_keys).issubset(known_keys):
                raise ValueError("general_mutation_dependency_unknown")
        _assert_dependency_dag(self.operations)
        return self


class MutationPlanV2(StrictMutationModel):
    plan_version: Literal["general-mutation-planner-v2"] = GENERAL_MUTATION_PLAN_VERSION
    operations: list[MutationPlanOperation] = Field(min_length=1, max_length=MAX_OPERATIONS)

    @model_validator(mode="after")
    def plan_is_bounded_connected_and_identity_only(self) -> MutationPlanV2:
        _validate_plan_operations(self.operations)
        for operation in self.operations:
            if isinstance(operation, CreateMutationCandidate):
                _assert_v2_planned_refs(operation.fields)
            elif isinstance(operation, UpdateMutationCandidate):
                _assert_v2_planned_refs(operation.new_value)
        return self


class GeneralMutationPromptInput(StrictMutationModel):
    message: str = Field(min_length=1, max_length=100_000)
    casefile: dict[str, Any]
    editable_fields_by_collection: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class GeneralMutationPlannerRequest:
    task_run_id: int
    model_id: str
    api_key: str | None
    casefile: dict[str, Any]
    message: str
    input_hash: str
    editable_fields_by_collection: dict[str, tuple[str, ...]]
    emit: Any
    network_retries: int = 2
    prompt_version: str = GENERAL_MUTATION_PROMPT_VERSION
    max_turns: int = 1


@dataclass(frozen=True, slots=True)
class GeneralMutationPlannerResult:
    candidate: MutationPlanV1 | MutationPlanV2
    usage: dict[str, Any]


def _assert_dependency_dag(operations: list[MutationPlanOperation]) -> None:
    dependencies = {item.operation_key: set(item.depends_on_operation_keys) for item in operations}
    pending = set(dependencies)
    while pending:
        ready = {key for key in pending if not (dependencies[key] & pending)}
        if not ready:
            raise ValueError("general_mutation_dependency_cycle")
        pending.difference_update(ready)


def _validate_plan_operations(operations: list[MutationPlanOperation]) -> None:
    keys = [item.operation_key for item in operations]
    if len(keys) != len(set(keys)):
        raise ValueError("general_mutation_operation_key_duplicate")
    local_refs = [
        item.local_ref for item in operations if isinstance(item, CreateMutationCandidate)
    ]
    if len(local_refs) != len(set(local_refs)):
        raise ValueError("general_mutation_local_ref_duplicate")
    if sum(isinstance(item, CreateMutationCandidate) for item in operations) > MAX_CREATES:
        raise ValueError("general_mutation_create_budget_exceeded")
    if sum(isinstance(item, DeleteMutationCandidate) for item in operations) > MAX_DELETES:
        raise ValueError("general_mutation_delete_budget_exceeded")
    known_keys = set(keys)
    for item in operations:
        if item.operation_key in item.depends_on_operation_keys:
            raise ValueError("general_mutation_self_dependency")
        if not set(item.depends_on_operation_keys).issubset(known_keys):
            raise ValueError("general_mutation_dependency_unknown")
    _assert_dependency_dag(operations)


def _assert_v2_planned_refs(value: Any) -> None:
    if isinstance(value, dict):
        if "object_type" in value:
            raise ValueError("general_mutation_ref_object_type_forbidden")
        ref_kind = value.get("ref_kind")
        if ref_kind in {"local", "existing"}:
            expected = (
                {"ref_kind", "local_ref"} if ref_kind == "local" else {"ref_kind", "object_id"}
            )
            if set(value) != expected:
                raise ValueError("general_mutation_ref_shape_invalid")
        for child in value.values():
            _assert_v2_planned_refs(child)
    elif isinstance(value, list):
        for child in value:
            _assert_v2_planned_refs(child)


__all__ = [
    "ALLOWED_COLLECTIONS",
    "GENERAL_MUTATION_BINDER_VERSION",
    "GENERAL_MUTATION_BINDER_VERSION_V1",
    "GENERAL_MUTATION_COMPONENT_ID",
    "GENERAL_MUTATION_PLAN_VERSION",
    "GENERAL_MUTATION_PLAN_VERSION_V1",
    "GENERAL_MUTATION_PROMPT_VERSION",
    "GENERAL_MUTATION_POLICY_VERSION",
    "GENERAL_MUTATION_SCHEMA_ID",
    "GENERAL_MUTATION_SCHEMA_ID_V1",
    "GENERAL_MUTATION_TRANSPORT_VERSION",
    "GeneralMutationPlannerRequest",
    "GeneralMutationPlannerResult",
    "GeneralMutationPromptInput",
    "MutationPlanV1",
    "MutationPlanV2",
    "PROTECTED_COLLECTIONS",
]
