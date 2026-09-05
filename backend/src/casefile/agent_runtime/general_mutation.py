"""Runtime-private contract for planning general CaseFile mutations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GENERAL_MUTATION_PLAN_VERSION_V1: Literal["general-mutation-planner-v1"] = (
    "general-mutation-planner-v1"
)
GENERAL_MUTATION_PLAN_VERSION: Literal["general-mutation-planner-v2"] = (
    "general-mutation-planner-v2"
)
GENERAL_MUTATION_PROMPT_VERSION: Literal["general-mutation-planner-v7"] = (
    "general-mutation-planner-v7"
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

_EXPLICIT_BATCH_CREATE_PATTERN = re.compile(
    r"(?:创建|新建)\s*"
    r"(?P<count>[0-9]{1,4})\s*"
    r"(?:个|名|位|条|处)\s*"
    r"(?:新\s*)?"
    r"(?:人物|实体|对象|事件|地点|关系|主张|假设|信息(?:单元)?|记录)"
)
_EXPLICIT_STABLE_TOKEN_PATTERN = re.compile(r"\b[a-z][a-z0-9_]{2,59}\b")
_EXPLICIT_DEPENDENCY_CYCLE_MARKERS = (
    "循环依赖",
    "互相依赖",
    "依赖环",
    "cyclic dependency",
    "dependency cycle",
)


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


def explicit_batch_create_count(message: str) -> int | None:
    """Return an explicitly requested object-create cardinality when unambiguous.

    This deliberately recognizes only Arabic numerals attached to a concrete
    CaseFile object noun. It must not reinterpret times, identifiers, tag
    counts, or other numbers as mutation cardinality.
    """

    counts = {
        int(match.group("count")) for match in _EXPLICIT_BATCH_CREATE_PATTERN.finditer(message)
    }
    if len(counts) != 1:
        return None
    return counts.pop()


def general_mutation_request_budget_reason(message: str) -> str | None:
    requested_creates = explicit_batch_create_count(message)
    if requested_creates is not None and requested_creates > MAX_CREATES:
        return "general_mutation_requested_create_budget_exceeded"
    return None


def general_mutation_request_dependency_reason(message: str) -> str | None:
    normalized = message.casefold()
    if any(marker in normalized for marker in _EXPLICIT_DEPENDENCY_CYCLE_MARKERS):
        return "general_mutation_requested_dependency_cycle"
    return None


def general_mutation_explicit_system_field_reason(message: str) -> str | None:
    """Reject an explicitly named server-owned field before model planning."""

    normalized = message.casefold()
    if any(
        re.search(rf"(?<![a-z0-9_]){re.escape(field)}(?![a-z0-9_])", normalized)
        for field in SYSTEM_FIELDS
    ):
        return "general_mutation_requested_system_field_forbidden"
    return None


def general_mutation_explicit_unknown_object_ids(
    message: str,
    casefile: dict[str, Any],
    editable_fields_by_collection: dict[str, tuple[str, ...]],
) -> tuple[str, ...]:
    """Find explicit stable-ID-shaped tokens that cannot name current objects.

    Only underscore-bearing tokens are considered. Known collection/field
    identifiers are excluded so ordinary contract vocabulary such as
    ``support_refs`` cannot be mistaken for an object ID.
    """

    known_object_ids = {
        str(item["id"])
        for collection in ALLOWED_COLLECTIONS | PROTECTED_COLLECTIONS
        for item in casefile.get(collection, [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    contract_tokens = set(editable_fields_by_collection) | set(SYSTEM_FIELDS)
    contract_tokens.update(
        field
        for fields in editable_fields_by_collection.values()
        for field in fields
    )
    known_object_prefixes = {
        object_id.split("_", 1)[0] + "_"
        for object_id in known_object_ids
        if "_" in object_id
    }
    candidates = {
        token
        for token in _EXPLICIT_STABLE_TOKEN_PATTERN.findall(message)
        if any(token.startswith(prefix) for prefix in known_object_prefixes)
    }
    return tuple(sorted(candidates - known_object_ids - contract_tokens))


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
    "explicit_batch_create_count",
    "general_mutation_explicit_system_field_reason",
    "general_mutation_explicit_unknown_object_ids",
    "general_mutation_request_dependency_reason",
    "general_mutation_request_budget_reason",
    "MutationPlanV1",
    "MutationPlanV2",
    "PROTECTED_COLLECTIONS",
]
