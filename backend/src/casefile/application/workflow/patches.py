"""Shared PatchSet lookup and read projection within the caller's transaction."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.application.agent_collaboration import (
    nonblocking_validator_issues as _nonblocking_validator_issues,
)
from casefile.application.casefile_v1 import build_casefile_document
from casefile.application.errors import not_found
from casefile.application.v1_editing import COLLECTIONS
from casefile.application.workflow_common import _time
from casefile.data_postgres.models import (
    AgentPatchOperation,
    AgentPatchSet,
    CaseFileObject,
    TaskRun,
    VerificationFindingPatchOperation,
)
from casefile.data_postgres.repositories import OwnedDraft


def get_agent_patch_set(
    session: Session,
    owned: OwnedDraft,
    patch_set_id: int,
    *,
    lock: bool = False,
) -> AgentPatchSet:
    statement = select(AgentPatchSet).where(
        AgentPatchSet.id == patch_set_id,
        AgentPatchSet.project_id == owned.project.id,
        AgentPatchSet.draft_id == owned.draft.id,
    )
    if lock:
        statement = statement.with_for_update()
    patch_set = session.scalar(statement)
    if patch_set is None:
        raise not_found("AgentPatchSet")
    return patch_set


def patch_set_view(
    session: Session,
    owned: OwnedDraft,
    patch_set: AgentPatchSet,
    *,
    operations: list[AgentPatchOperation] | None = None,
    current_document: dict[str, Any] | None = None,
    validator_issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if operations is None:
        operations = list(
            session.scalars(
                select(AgentPatchOperation)
                .where(AgentPatchOperation.patch_set_id == patch_set.id)
                .order_by(AgentPatchOperation.ordinal)
            )
        )
    projection_document = current_document or build_casefile_document(session, owned)
    object_labels = _patch_object_labels(projection_document)
    registries = {
        row.id: row
        for row in session.scalars(
            select(CaseFileObject).where(
                CaseFileObject.id.in_(
                    operation.target_object_id
                    for operation in operations
                    if operation.target_object_id is not None
                )
            )
        )
    }
    finding_ids_by_operation: dict[int, list[int]] = {}
    operation_ids = [operation.id for operation in operations]
    if operation_ids:
        links = list(
            session.scalars(
                select(VerificationFindingPatchOperation).where(
                    VerificationFindingPatchOperation.project_id == owned.project.id,
                    VerificationFindingPatchOperation.patch_operation_id.in_(operation_ids),
                )
            )
        )
        for link in links:
            finding_ids_by_operation.setdefault(link.patch_operation_id, []).append(
                link.finding_id
            )
    if validator_issues is None:
        validator_issues = []
        if patch_set.status == "applied":
            accepted = [
                {
                    "object_id": registries[operation.target_object_id].object_id,
                    "field_path": operation.field_path,
                    "old_value": operation.old_value_jsonb,
                    "new_value": operation.new_value_jsonb,
                }
                for operation in operations
                if operation.decision == "accepted" and operation.target_object_id in registries
            ]
            validator_issues = _nonblocking_validator_issues(projection_document, accepted)
    source_task = session.get(TaskRun, patch_set.task_run_id)
    goal_value = None if source_task is None else source_task.input_jsonb.get("goal_session")
    goal = goal_value if isinstance(goal_value, dict) else {}
    return {
        "patch_set_id": patch_set.id,
        "goal_id": goal.get("goal_id"),
        "goal_revision": goal.get("goal_revision"),
        "thread_id": patch_set.thread_id,
        "source_message_id": patch_set.source_message_id,
        "task_run_id": patch_set.task_run_id,
        "base_draft_revision": patch_set.base_draft_revision,
        "closure_policy_version": patch_set.closure_policy_version,
        "mutation_mode": patch_set.mutation_mode,
        "review_mode": patch_set.review_mode,
        "plan_version": patch_set.plan_version,
        "capability_policy_version": patch_set.capability_policy_version,
        "binder_version": patch_set.binder_version,
        "plan_hash": patch_set.plan_hash,
        "impact_hash": patch_set.impact_hash,
        "contains_delete": patch_set.contains_delete,
        "baseline_hash": patch_set.baseline_hash,
        "candidate_hash": patch_set.candidate_hash,
        "reason_summary": patch_set.reason_summary,
        "status": patch_set.status,
        "is_stale": (
            patch_set.status == "stale"
            or (
                patch_set.status == "pending"
                and owned.draft.revision != patch_set.base_draft_revision
            )
        ),
        "applied_from_revision": patch_set.applied_from_revision,
        "applied_to_revision": patch_set.applied_to_revision,
        "undone_to_revision": patch_set.undone_to_revision,
        "operations": [
            {
                "operation_id": operation.id,
                "operation_key": operation.operation_id,
                "ordinal": operation.ordinal,
                "object_id": (
                    None
                    if (
                        registry := (
                            None
                            if operation.target_object_id is None
                            else registries.get(operation.target_object_id)
                        )
                    )
                    is None
                    else registry.object_id
                ),
                "object_type": (None if registry is None else registry.object_type),
                "target_collection": operation.target_collection,
                "target_object_key": operation.target_object_key,
                "operation_type": operation.operation_type,
                "field_path": operation.field_path,
                "expected_object_revision": operation.expected_object_revision,
                "old_value": operation.old_value_jsonb,
                "new_value": operation.new_value_jsonb,
                "reason": operation.reason,
                "origin": operation.origin,
                "decision": operation.decision,
                "reviewed_at": _time(operation.reviewed_at),
                "finding_ids": finding_ids_by_operation.get(operation.id, []),
            }
            for operation in operations
        ],
        "object_labels": object_labels,
        "validation_warning": bool(validator_issues),
        "validator_issues": validator_issues,
        "created_at": _time(patch_set.created_at),
        "updated_at": _time(patch_set.updated_at),
    }


def _patch_object_labels(document: dict[str, Any]) -> dict[str, dict[str, str | None]]:
    labels: dict[str, dict[str, str | None]] = {}
    for object_type, collection in COLLECTIONS.items():
        values = document.get(collection)
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                continue
            name = next(
                (
                    candidate.strip()[:240]
                    for key in ("name", "title")
                    if isinstance((candidate := value.get(key)), str) and candidate.strip()
                ),
                None,
            )
            labels[str(value["id"])] = {
                "object_type": object_type,
                "name": name,
            }
    return labels
