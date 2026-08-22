"""Transactional Preview/Apply façade shared by all logical write adapters."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from casefile.application.casefile_v1 import build_casefile_document
from casefile.application.errors import ApplicationError, not_found
from casefile.application.v1_editing import V1EditingService
from casefile.data_postgres.repositories import ProjectRepository
from casefile.domain.logical_mutation import (
    CreateObject,
    DeleteObject,
    MutationSet,
    UpdateField,
)
from casefile.domain.logical_mutation.models import MutationOperation
from casefile.domain.verification_engine import VerificationEngine


class LogicalMutationService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

    def preview(
        self, actor_user_id: int, project_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id, lock=True)
            if owned is None:
                raise not_found("Project")
            mutation = _mutation_set(payload)
            if (
                mutation.base_draft_id != owned.draft.id
                or mutation.base_revision != owned.draft.revision
            ):
                raise ApplicationError(
                    "mutation_revision_conflict", "Draft 已变化，请重新预演。", status_code=409
                )
            simulation = VerificationEngine(
                profile="fast", draft_revision=owned.draft.revision
            ).simulate_mutation_set(
                build_casefile_document(self.session, owned),
                mutation,
                target_finding_keys=payload.get("target_finding_keys", []),
                accepted_debt_finding_keys=payload.get(
                    "accepted_debt_finding_keys", []
                ),
                debt_acceptance_reason=payload.get("debt_acceptance_reason"),
                allow_author_debt_acceptance=True,
            )
            return simulation.as_dict()

    def apply(
        self, actor_user_id: int, project_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id, lock=True)
            if owned is None:
                raise not_found("Project")
            revision, group_no, simulation = V1EditingService(
                self.session
            ).apply_mutation_set(
                owned,
                mutation_set=_mutation_set(payload),
                actor_user_id=actor_user_id,
                expected_candidate_hash=str(payload["expected_candidate_hash"]),
                accepted_debt_finding_keys=tuple(
                    payload.get("accepted_debt_finding_keys", [])
                ),
                debt_acceptance_reason=payload.get("debt_acceptance_reason"),
                target_finding_keys=tuple(payload.get("target_finding_keys", [])),
            )
            return {
                "draft_id": owned.draft.id,
                "draft_revision": revision,
                "operation_group_no": group_no,
                "simulation": simulation.as_dict(),
            }


def _mutation_set(payload: dict[str, Any]) -> MutationSet:
    operations: list[MutationOperation] = []
    for item in payload["operations"]:
        kind = item["operation_type"]
        if kind == "create_object":
            operations.append(
                CreateObject(item["operation_id"], item["collection"], item["object_value"])
            )
        elif kind == "update_field":
            operations.append(
                UpdateField(
                    item["operation_id"],
                    item["object_id"],
                    item["field_path"],
                    item["new_value"],
                    item["old_value"],
                    item.get("expected_object_revision"),
                )
            )
        elif kind == "delete_object":
            operations.append(
                DeleteObject(
                    item["operation_id"], item["object_id"], item.get("old_object_value")
                )
            )
        else:
            raise ApplicationError(
                "mutation_operation_type_invalid",
                "逻辑修改包含不支持的操作类型。",
                status_code=422,
                details={"operation_type": kind},
            )
    return MutationSet(
        mutation_set_id=payload["mutation_set_id"],
        base_draft_id=payload["base_draft_id"],
        base_revision=payload["base_revision"],
        operations=tuple(operations),
        actor="author",
        mode=payload["mode"],
        closure_policy_version=payload["closure_policy_version"],
    )
