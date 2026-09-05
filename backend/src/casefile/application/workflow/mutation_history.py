"""Strict LIFO history actions for reviewable logical mutations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.application.agent_patch_mutation import (
    mutation_from_document_history,
    mutation_set_from_patch_operations,
)
from casefile.application.casefile_v1 import build_casefile_document, casefile_content_hash
from casefile.application.errors import ApplicationError
from casefile.application.v1_editing import V1EditingService
from casefile.application.workflow.patches import get_agent_patch_set, patch_set_view
from casefile.application.workflow_common import require_owned_project
from casefile.data_postgres.models import AgentPatchOperation, CaseFileObject, DraftOperation
from casefile.data_postgres.repositories import ProjectRepository
from casefile.domain.logical_mutation import ACTIVE_APPLY_POLICY
from casefile.domain.verification_engine import VerificationEngine


def redo_agent_patch_set(
    session: Session,
    projects: ProjectRepository,
    actor_user_id: int,
    project_id: int,
    patch_set_id: int,
    *,
    expected_draft_id: int,
    expected_revision: int,
) -> dict[str, Any]:
    with session.begin():
        owned = require_owned_project(projects, actor_user_id, project_id, lock=True)
        patch_set = get_agent_patch_set(
            session,
            owned, patch_set_id, lock=True
        )
        if patch_set.status != "undone":
            raise ApplicationError(
                "agent_patch_not_undone",
                "只有栈顶已撤销的修改批次才能重做。",
                status_code=409,
            )
        if patch_set.closure_policy_version != ACTIVE_APPLY_POLICY:
            raise ApplicationError(
                "agent_patch_redo_policy_stale",
                "该修改批次使用旧逻辑策略，不能直接重做，请重新生成 MutationSet。",
                status_code=409,
            )
        if (
            owned.draft.id != expected_draft_id
            or owned.draft.revision != expected_revision
            or patch_set.undone_to_revision != expected_revision
        ):
            raise ApplicationError(
                "agent_patch_redo_stale",
                "Redo 栈已失效，请改用新的 MutationSet 审阅。",
                status_code=409,
            )
        current_document = build_casefile_document(session, owned)
        if patch_set.baseline_hash and (
            casefile_content_hash(current_document) != patch_set.baseline_hash
        ):
            raise ApplicationError(
                "agent_patch_redo_hash_conflict",
                "当前 Draft 与 Redo 栈顶不一致。",
                status_code=409,
            )
        operations = list(
            session.scalars(
                select(AgentPatchOperation)
                .where(
                    AgentPatchOperation.patch_set_id == patch_set.id,
                    AgentPatchOperation.decision == "accepted",
                )
                .order_by(AgentPatchOperation.ordinal)
            )
        )
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
        original_apply = session.scalar(
            select(DraftOperation).where(
                DraftOperation.draft_id == owned.draft.id,
                DraftOperation.operation_group_no
                == patch_set.applied_operation_group_no,
            )
        )
        stored_payload = (
            original_apply.new_value_jsonb if original_apply is not None else None
        )
        original_payload = stored_payload if isinstance(stored_payload, dict) else {}
        if patch_set.review_mode == "atomic":
            target_document = original_payload.get("document")
            if not isinstance(target_document, dict):
                raise RuntimeError("Atomic patch history has no after document")
            mutation = mutation_from_document_history(
                current_document,
                target_document,
                mutation_set_id=f"agent_patch_redo_{patch_set.id}",
                draft_id=owned.draft.id,
                base_revision=owned.draft.revision,
            )
        else:
            mutation = mutation_set_from_patch_operations(
                owned,
                patch_set,
                operations,
                {operation.id for operation in operations},
                registries,
            )
        accepted_debt_keys = tuple(
            original_payload.get("accepted_debt_finding_keys", [])
        )
        if accepted_debt_keys and patch_set.review_mode == "atomic":
            preview = VerificationEngine(profile="fast").simulate_mutation_set(
                current_document,
                mutation,
            )
            accepted_debt_keys = tuple(
                preview.authorization_required_finding_keys
            )
        revision, group_no, simulation = V1EditingService(
            session
        ).apply_mutation_set(
            owned,
            mutation_set=mutation,
            actor_user_id=actor_user_id,
            draft_operation_type="logical_mutation_redo",
            source_patch_set_id=patch_set.id,
            accepted_debt_finding_keys=accepted_debt_keys,
            debt_acceptance_reason=original_payload.get("debt_acceptance_reason"),
        )
        now = datetime.now(UTC)
        patch_set.status = "applied"
        patch_set.applied_operation_group_no = group_no
        patch_set.applied_from_revision = expected_revision
        patch_set.applied_to_revision = revision
        patch_set.applied_at = now
        patch_set.undone_operation_group_no = None
        patch_set.undone_to_revision = None
        patch_set.undone_at = None
        patch_set.candidate_hash = simulation.candidate_hash
        session.flush()
        return {
            **patch_set_view(
                session,
                owned, patch_set, operations=operations
            ),
            "draft_revision": revision,
            "simulation": simulation.as_dict(),
        }



__all__ = ["redo_agent_patch_set"]
