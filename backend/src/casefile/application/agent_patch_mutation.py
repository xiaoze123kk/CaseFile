"""Logical Mutation reconstruction helpers for persisted Agent PatchSets."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.application.closure_repair import (
    ValidatedClosureRepair,
    validate_closure_repair_envelope,
)
from casefile.application.errors import ApplicationError
from casefile.application.v1_editing import COLLECTIONS, casefile_semantically_equal
from casefile.data_postgres.models import (
    AgentPatchOperation,
    AgentPatchSet,
    CaseFileObject,
    VerificationFinding,
)
from casefile.data_postgres.repositories import OwnedDraft
from casefile.domain.logical_mutation import (
    CLOSURE_POLICY_VERSION,
    CreateObject,
    DeleteObject,
    MutationSet,
    UpdateField,
)
from casefile.domain.logical_mutation.repair import build_mutation_from_document_diff
from casefile.domain.verification_engine import MutationSimulation

EXACT_HISTORY_RESTORE_REASON = "撤销恢复已审核修改前的精确历史状态。"


def general_mutation_repair_validation(
    frozen_document: dict[str, Any],
    general_mutation_envelope: dict[str, Any],
    repair_envelope: dict[str, Any] | None,
    *,
    original_intent: str,
) -> tuple[ValidatedClosureRepair | None, int]:
    primary = general_mutation_envelope.get("primary_bound", general_mutation_envelope.get("bound"))
    if primary is None:
        return None, 0
    return (
        validate_closure_repair_envelope(
            frozen_document,
            primary.mutation_set,
            repair_envelope,
            original_intent=original_intent,
        ),
        len(primary.operations),
    )


def repair_provenance_by_target(
    validation: ValidatedClosureRepair | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (item["object_id"], item["field_path"]): item
        for item in (() if validation is None else validation.companion_operations)
    }


def general_mutation_patch_operation(
    *,
    task: Any,
    patch_set: AgentPatchSet,
    item: Any,
    registry: CaseFileObject | None,
    ordinal: int,
    companion: dict[str, Any] | None,
) -> AgentPatchOperation:
    return AgentPatchOperation(
        project_id=task.project_id,
        casefile_id=task.casefile_id,
        draft_id=task.draft_id,
        patch_set_id=patch_set.id,
        target_object_id=None if registry is None else registry.id,
        target_object_key=item.target_object_key,
        target_collection=item.target_collection,
        ordinal=ordinal,
        operation_id=item.operation_id,
        operation_type=item.operation_type,
        field_path=item.field_path,
        expected_object_revision=item.expected_object_revision,
        old_value_jsonb=deepcopy(item.old_value),
        new_value_jsonb=deepcopy(item.new_value),
        reason=item.reason,
        origin="closure_repair" if companion is not None else "primary",
        repair_round=None if companion is None else companion["repair_round"],
        repair_obligation_keys=([] if companion is None else list(companion["obligation_keys"])),
        decision="pending",
        reviewed_at=None,
    )


def patch_operation_count(
    general_mutation_envelope: dict[str, Any] | None,
    suggestions: list[dict[str, Any]],
) -> int:
    if general_mutation_envelope is None:
        return len(suggestions)
    bound = general_mutation_envelope.get("bound")
    return 0 if bound is None else len(bound.operations)


def mutation_reason_summary(reasons: list[str]) -> str:
    return reasons[0] if len(reasons) == 1 else f"Agent 建议原子执行 {len(reasons)} 项修改"


def logical_operations_from_patch(
    operations: list[AgentPatchOperation],
    selected: set[int],
    registries: dict[int, CaseFileObject],
) -> list[CreateObject | UpdateField | DeleteObject]:
    result: list[CreateObject | UpdateField | DeleteObject] = []
    created_keys = {
        item.target_object_key
        for item in operations
        if item.id in selected and item.operation_type == "create_object"
    }
    for operation in operations:
        if operation.id not in selected:
            continue
        registry = (
            None
            if operation.target_object_id is None
            else registries.get(operation.target_object_id)
        )
        if operation.operation_type == "create_object":
            if not isinstance(operation.new_value_jsonb, dict):
                raise RuntimeError("Create operation requires a complete object")
            result.append(
                CreateObject(
                    operation.operation_id,
                    operation.target_collection,
                    operation.new_value_jsonb,
                )
            )
        elif operation.operation_type == "delete_object":
            result.append(
                DeleteObject(
                    operation.operation_id,
                    operation.target_object_key,
                    operation.old_value_jsonb,
                )
            )
        else:
            if registry is None and operation.target_object_key not in created_keys:
                raise RuntimeError("Agent patch target object disappeared")
            result.append(
                UpdateField(
                    operation.operation_id,
                    operation.target_object_key,
                    operation.field_path,
                    operation.new_value_jsonb,
                    operation.old_value_jsonb,
                    (
                        registry.revision
                        if registry is not None
                        else operation.expected_object_revision or 1
                    ),
                )
            )
    return result


def inverse_logical_operations_from_patch(
    operations: list[AgentPatchOperation],
    registries: dict[int, CaseFileObject],
    document: dict[str, Any],
) -> list[CreateObject | UpdateField | DeleteObject]:
    current_objects = {
        str(item["id"]): item
        for collection in COLLECTIONS.values()
        for item in document[collection]
    }
    result: list[CreateObject | UpdateField | DeleteObject] = []
    for operation in operations:
        registry = (
            None
            if operation.target_object_id is None
            else registries.get(operation.target_object_id)
        )
        if operation.operation_type == "create_object":
            current = current_objects.get(operation.target_object_key)
            if current is None:
                raise RuntimeError("Created patch object disappeared")
            result.append(
                DeleteObject(
                    f"undo_{operation.operation_id}",
                    operation.target_object_key,
                    current,
                )
            )
        elif operation.operation_type == "delete_object":
            if not isinstance(operation.old_value_jsonb, dict):
                raise RuntimeError("Delete operation requires the complete old object")
            result.append(
                CreateObject(
                    f"undo_{operation.operation_id}",
                    operation.target_collection,
                    operation.old_value_jsonb,
                )
            )
        else:
            if registry is None:
                raise RuntimeError("Agent patch target object disappeared")
            result.append(
                UpdateField(
                    f"undo_{operation.operation_id}",
                    operation.target_object_key,
                    operation.field_path,
                    operation.old_value_jsonb,
                    operation.new_value_jsonb,
                    registry.revision,
                )
            )
    return result


def mutation_set_from_patch_operations(
    owned: OwnedDraft,
    patch_set: AgentPatchSet,
    operations: list[AgentPatchOperation],
    selected: set[int],
    registries: dict[int, CaseFileObject],
) -> MutationSet:
    return MutationSet(
        mutation_set_id=f"agent_patch_{patch_set.id}_{owned.draft.revision}",
        base_draft_id=owned.draft.id,
        base_revision=owned.draft.revision,
        operations=tuple(logical_operations_from_patch(operations, selected, registries)),
        actor="agent",
        mode=patch_set.mutation_mode,  # type: ignore[arg-type]
        closure_policy_version=patch_set.closure_policy_version,
    )


def mutation_from_document_history(
    current_document: dict[str, Any],
    target_document: dict[str, Any],
    *,
    mutation_set_id: str,
    draft_id: int,
    base_revision: int,
) -> MutationSet:
    seed = MutationSet(
        mutation_set_id=mutation_set_id,
        base_draft_id=draft_id,
        base_revision=base_revision,
        operations=(),
        actor="author",
        closure_policy_version=CLOSURE_POLICY_VERSION,
    )
    rebuilt = build_mutation_from_document_diff(current_document, target_document, seed)
    return MutationSet(
        mutation_set_id=mutation_set_id,
        base_draft_id=draft_id,
        base_revision=base_revision,
        operations=tuple(
            operation
            for operation in rebuilt.operations
            if not (isinstance(operation, UpdateField) and operation.field_path == "/updated_at")
        ),
        actor="author",
        closure_policy_version=CLOSURE_POLICY_VERSION,
    )


def exact_history_restore_authorization(
    simulation: MutationSimulation,
    target_document: Mapping[str, Any],
) -> tuple[tuple[str, ...], str | None]:
    """Authorize repair debt only for an exact atomic history restoration."""

    finding_keys = tuple(simulation.authorization_required_finding_keys)
    if (
        simulation.can_apply
        or simulation.reason_code != "repair_required"
        or not finding_keys
        or not casefile_semantically_equal(simulation.document, target_document)
    ):
        return (), None
    return finding_keys, EXACT_HISTORY_RESTORE_REASON


class AgentPatchMutationMixin:
    """Small shared seam used by Agent review and Redo workflows."""

    session: Session
    _logical_operations_from_patch = staticmethod(logical_operations_from_patch)
    _inverse_logical_operations_from_patch = staticmethod(inverse_logical_operations_from_patch)
    _mutation_set_from_patch_operations = staticmethod(mutation_set_from_patch_operations)
    _mutation_from_document_history = staticmethod(mutation_from_document_history)

    @staticmethod
    def _validate_patch_selection(
        patch_set: AgentPatchSet,
        operation_ids: list[int] | None,
    ) -> None:
        if patch_set.review_mode == "atomic" and operation_ids is not None and operation_ids:
            raise ApplicationError(
                "agent_patch_atomic_subset_forbidden",
                "原子修改批次只能整组接受或整组拒绝。",
                status_code=422,
            )

    def _target_finding_keys(
        self,
        project_id: int,
        draft_id: int,
        finding_ids: list[int] | None,
    ) -> list[str]:
        if not finding_ids:
            return []
        rows = list(
            self.session.scalars(
                select(VerificationFinding).where(
                    VerificationFinding.project_id == project_id,
                    VerificationFinding.draft_id == draft_id,
                    VerificationFinding.id.in_(finding_ids),
                )
            )
        )
        found = {row.id for row in rows}
        unknown = sorted(set(finding_ids) - found)
        if unknown:
            raise ApplicationError(
                "verification_finding_not_found",
                "目标验证问题不属于当前工作稿。",
                status_code=422,
                details={"finding_ids": unknown},
            )
        return [row.finding_key for row in rows]


__all__ = [
    "AgentPatchMutationMixin",
    "inverse_logical_operations_from_patch",
    "logical_operations_from_patch",
    "general_mutation_repair_validation",
    "general_mutation_patch_operation",
    "mutation_from_document_history",
    "exact_history_restore_authorization",
    "mutation_set_from_patch_operations",
    "mutation_reason_summary",
    "patch_operation_count",
    "repair_provenance_by_target",
]
