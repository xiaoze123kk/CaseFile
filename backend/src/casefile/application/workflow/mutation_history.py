"""Strict LIFO history actions for reviewable logical mutations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.application.casefile_v1 import build_casefile_document, casefile_content_hash
from casefile.application.errors import ApplicationError
from casefile.application.v1_editing import V1EditingService
from casefile.data_postgres.models import AgentPatchOperation, CaseFileObject, DraftOperation


class AgentMutationHistoryMixin:
    session: Session

    def redo_agent_patch_set(
        self,
        actor_user_id: int,
        project_id: int,
        patch_set_id: int,
        *,
        expected_draft_id: int,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)  # type: ignore[attr-defined]
            patch_set = self._agent_patch_set(  # type: ignore[attr-defined]
                owned, patch_set_id, lock=True
            )
            if patch_set.status != "undone":
                raise ApplicationError(
                    "agent_patch_not_undone",
                    "只有栈顶已撤销的修改批次才能重做。",
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
            current_document = build_casefile_document(self.session, owned)
            if patch_set.baseline_hash and (
                casefile_content_hash(current_document) != patch_set.baseline_hash
            ):
                raise ApplicationError(
                    "agent_patch_redo_hash_conflict",
                    "当前 Draft 与 Redo 栈顶不一致。",
                    status_code=409,
                )
            operations = list(
                self.session.scalars(
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
                for row in self.session.scalars(
                    select(CaseFileObject).where(
                        CaseFileObject.id.in_(
                            operation.target_object_id
                            for operation in operations
                            if operation.target_object_id is not None
                        )
                    )
                )
            }
            mutation = self._mutation_set_from_patch_operations(  # type: ignore[attr-defined]
                owned,
                patch_set,
                operations,
                {operation.id for operation in operations},
                registries,
            )
            original_apply = self.session.scalar(
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
            revision, group_no, simulation = V1EditingService(
                self.session
            ).apply_mutation_set(
                owned,
                mutation_set=mutation,
                actor_user_id=actor_user_id,
                draft_operation_type="logical_mutation_redo",
                source_patch_set_id=patch_set.id,
                accepted_debt_finding_keys=tuple(
                    original_payload.get("accepted_debt_finding_keys", [])
                ),
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
            self.session.flush()
            return {
                **self._patch_set_view(  # type: ignore[attr-defined]
                    owned, patch_set, operations=operations
                ),
                "draft_revision": revision,
                "simulation": simulation.as_dict(),
            }


__all__ = ["AgentMutationHistoryMixin"]
