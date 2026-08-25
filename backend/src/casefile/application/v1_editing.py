"""Validated single-object and atomic batch editing for CaseFile v1."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from casefile.application.casefile_v1 import (
    build_casefile_document,
    casefile_content_hash,
    create_casefile_objects,
    iter_contract_object_refs,
)
from casefile.application.errors import ApplicationError, not_found, revision_conflict
from casefile.contracts.validation import resolution_conclusion_target_ids
from casefile.data_postgres.models import (
    AuditEvent,
    CaseFileConstraint,
    CaseFileContractRef,
    CaseFileObject,
    Claim,
    DraftOperation,
    Entity,
    Event,
    Hypothesis,
    InformationUnit,
    Location,
    ReasoningNode,
    ReasoningPath,
    Relationship,
    ResolutionSlot,
    ResolutionSpec,
    StructureLock,
)
from casefile.data_postgres.repositories import OwnedDraft, ProjectRepository
from casefile.domain.logical_mutation import (
    CLOSURE_POLICY_VERSION,
    CreateObject,
    MutationSet,
    UpdateField,
)
from casefile.domain.verification_engine import MutationSimulation, VerificationEngine

COMMON_EDITABLE_FIELDS = {"description", "tags"}
CONCLUSION_INVALIDATING_RESOLUTION_FIELDS = {
    "accepted_answers",
    "conclusion",
    "conclusion_mode",
    "required_claim_refs",
    "required_slots",
}
EDITABLE_FIELDS = {
    "resolution_spec": COMMON_EDITABLE_FIELDS
    | {
        "title",
        "question_type",
        "reasoning_question",
        "conclusion_mode",
        "required_slots",
        "accepted_answers",
        "required_claim_refs",
        "conclusion",
    },
    "entity": COMMON_EDITABLE_FIELDS
    | {
        "entity_type",
        "name",
        "aliases",
        "traits",
        "goals",
        "secrets",
        "capabilities",
    },
    "relationship": COMMON_EDITABLE_FIELDS
    | {
        "title",
        "from_ref",
        "to_ref",
        "relationship_type",
        "direction",
        "truth_status",
        "visibility",
    },
    "location": COMMON_EDITABLE_FIELDS
    | {
        "name",
        "spatial_position",
        "parent_ref",
        "adjacency_refs",
        "access_rules",
        "travel_times",
        "visibility_rules",
    },
    "event": COMMON_EDITABLE_FIELDS
    | {
        "title",
        "truth_status",
        "time",
        "participant_refs",
        "location_ref",
        "cause_refs",
        "effect_refs",
        "observed_by_refs",
    },
    "information_unit": COMMON_EDITABLE_FIELDS
    | {
        "information_type",
        "title",
        "content",
        "source_event_ref",
        "reliability",
        "truth_status",
        "supports_claim_refs",
        "refutes_claim_refs",
        "availability",
        "classification",
    },
    "claim": COMMON_EDITABLE_FIELDS
    | {
        "title",
        "statement",
        "claim_type",
        "support_refs",
        "refute_refs",
        "dependency_claim_refs",
        "status",
        "materiality",
    },
    "hypothesis": COMMON_EDITABLE_FIELDS
    | {
        "title",
        "proposition",
        "target_resolution_ref",
        "required_claim_refs",
        "falsifier_refs",
        "competing_hypothesis_refs",
        "status",
        "score",
    },
    "reasoning_path": COMMON_EDITABLE_FIELDS
    | {
        "title",
        "path_type",
        "target_ref",
        "steps",
        "required_for_resolution",
        "alternative_path_refs",
    },
    "constraint": COMMON_EDITABLE_FIELDS
    | {
        "title",
        "level",
        "scope_refs",
        "statement",
        "rule_expression",
        "conflict_refs",
    },
    "structure_lock": COMMON_EDITABLE_FIELDS
    | {
        "title",
        "lock_type",
        "object_ref",
        "field_paths",
        "reason",
    },
}
COLLECTIONS = {
    "resolution_spec": "resolution_specs",
    "entity": "entities",
    "relationship": "relationships",
    "location": "locations",
    "event": "events",
    "information_unit": "information_units",
    "claim": "claims",
    "hypothesis": "hypotheses",
    "reasoning_path": "reasoning_paths",
    "constraint": "constraints",
    "structure_lock": "structure_locks",
}


def editable_fields_by_collection() -> dict[str, tuple[str, ...]]:
    """Expose the exact Agent-editable top-level fields by CaseFile collection."""

    return {
        collection: tuple(sorted(EDITABLE_FIELDS[object_type]))
        for object_type, collection in COLLECTIONS.items()
    }


class V1EditingService:
    """Apply optimistic, contract-validated Draft changes."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

    def patch_object(
        self,
        actor_user_id: int,
        project_id: int,
        object_id: str,
        *,
        expected_draft_id: int,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id, lock=True)
            if owned is None:
                raise not_found("Project")
            if owned.draft.id != expected_draft_id or owned.draft.revision != expected_revision:
                raise revision_conflict(
                    expected=owned.draft.revision,
                    received=expected_revision,
                )
            registry = self._registry(owned, object_id)
            self._require_editable_fields(registry.object_type, set(changes))
            collection = COLLECTIONS[registry.object_type]
            before_document = build_casefile_document(self.session, owned)
            before = _find(before_document[collection], object_id)
            proposed = deepcopy(before)
            proposed.update(deepcopy(changes))
            if registry.object_type == "resolution_spec" and (
                set(changes) & CONCLUSION_INVALIDATING_RESOLUTION_FIELDS
            ):
                conclusion = proposed.get("conclusion")
                if conclusion is not None:
                    conclusion["review_status"] = "proposed"
            operations = [
                UpdateField(
                    operation_id=f"manual_{index:02d}",
                    object_id=object_id,
                    field_path=f"/{field}",
                    old_value=deepcopy(before.get(field)),
                    new_value=deepcopy(proposed.get(field)),
                    expected_object_revision=registry.revision,
                )
                for index, field in enumerate(sorted(changes), start=1)
            ]
            invalidated_resolution_ids: list[str] = []
            if (
                registry.object_type == "resolution_spec"
                and (before.get("conclusion") or {}).get("review_status") == "confirmed"
                and (proposed.get("conclusion") or {}).get("review_status") == "proposed"
            ):
                operations.append(
                    UpdateField(
                        operation_id="manual_conclusion_invalidation",
                        object_id=object_id,
                        field_path="/conclusion/review_status",
                        old_value="confirmed",
                        new_value="proposed",
                        expected_object_revision=registry.revision,
                    )
                )
                invalidated_resolution_ids.append(object_id)
            if registry.object_type != "resolution_spec":
                for resolution in before_document["resolution_specs"]:
                    conclusion = resolution.get("conclusion")
                    if (
                        conclusion is not None
                        and conclusion.get("review_status") == "confirmed"
                        and _conclusion_references_object(
                            before_document, resolution, object_id
                        )
                    ):
                        resolution_registry = self._registry(owned, resolution["id"])
                        operations.append(
                            UpdateField(
                                operation_id=(
                                    f"manual_invalidate_{resolution['id']}"
                                ),
                                object_id=resolution["id"],
                                field_path="/conclusion/review_status",
                                old_value="confirmed",
                                new_value="proposed",
                                expected_object_revision=resolution_registry.revision,
                            )
                        )
                        invalidated_resolution_ids.append(resolution["id"])
            mutation = MutationSet(
                mutation_set_id=f"manual_{owned.draft.id}_{expected_revision}_{object_id}",
                base_draft_id=owned.draft.id,
                base_revision=expected_revision,
                operations=tuple(operations),
                actor="author",
            )
            revision, _group_no, simulation = self.apply_mutation_set(
                owned,
                mutation_set=mutation,
                actor_user_id=actor_user_id,
            )
            for invalidated_id in invalidated_resolution_ids:
                invalidated_registry = self._registry(owned, invalidated_id)
                self.session.add(
                    AuditEvent(
                        project_id=owned.project.id,
                        casefile_id=owned.casefile.id,
                        actor_kind="user",
                        actor_user_id=actor_user_id,
                        actor_ref=None,
                        action="resolution.conclusion_invalidated",
                        target_type="resolution_spec",
                        target_id=invalidated_registry.id,
                        trace_id=None,
                        details_jsonb={
                            "basis_object_id": object_id,
                            "draft_revision": revision,
                        },
                    )
                )
            return _find(dict(simulation.document)[collection], object_id), revision

    def _invalidate_dependent_conclusions(
        self,
        owned: OwnedDraft,
        object_id: str,
        *,
        actor_user_id: int,
        dependency_document: dict[str, Any],
    ) -> list[CaseFileObject]:
        """Return confirmed conclusions to review when their reasoning basis changes."""

        resolution_by_id = {
            item["id"]: item for item in dependency_document["resolution_specs"]
        }
        rows = list(
            self.session.scalars(
                select(ResolutionSpec).where(
                    ResolutionSpec.draft_id == owned.draft.id,
                    ResolutionSpec.conclusion_review_status == "confirmed",
                )
            )
        )
        invalidated: list[CaseFileObject] = []
        for row in rows:
            registry = self.session.scalar(
                select(CaseFileObject).where(CaseFileObject.id == row.object_registry_id)
            )
            resolution = resolution_by_id.get(registry.object_id) if registry else None
            if resolution is None or not _conclusion_references_object(
                dependency_document, resolution, object_id
            ):
                continue
            row.conclusion_review_status = "proposed"
            row.conclusion_confirmed_by_user_id = None
            row.conclusion_confirmed_at = None
            self.session.add(
                AuditEvent(
                    project_id=owned.project.id,
                    casefile_id=owned.casefile.id,
                    actor_kind="user",
                    actor_user_id=actor_user_id,
                    actor_ref=None,
                    action="resolution.conclusion_invalidated",
                    target_type="resolution_spec",
                    target_id=row.object_registry_id,
                    trace_id=None,
                    details_jsonb={
                        "changed_object_id": object_id,
                        "old_status": "confirmed",
                        "new_status": "proposed",
                    },
                )
            )
            if registry is not None:
                invalidated.append(registry)
        return invalidated

    def confirm_conclusion(
        self,
        actor_user_id: int,
        project_id: int,
        resolution_id: str,
        *,
        expected_draft_id: int,
        expected_revision: int,
    ) -> tuple[dict[str, Any], int]:
        return self._transition_conclusion(
            actor_user_id,
            project_id,
            resolution_id,
            expected_draft_id=expected_draft_id,
            expected_revision=expected_revision,
            target_status="confirmed",
        )

    def withdraw_conclusion(
        self,
        actor_user_id: int,
        project_id: int,
        resolution_id: str,
        *,
        expected_draft_id: int,
        expected_revision: int,
    ) -> tuple[dict[str, Any], int]:
        return self._transition_conclusion(
            actor_user_id,
            project_id,
            resolution_id,
            expected_draft_id=expected_draft_id,
            expected_revision=expected_revision,
            target_status="proposed",
        )

    def _transition_conclusion(
        self,
        actor_user_id: int,
        project_id: int,
        resolution_id: str,
        *,
        expected_draft_id: int,
        expected_revision: int,
        target_status: str,
    ) -> tuple[dict[str, Any], int]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id, lock=True)
            if owned is None:
                raise not_found("Project")
            if owned.draft.id != expected_draft_id or owned.draft.revision != expected_revision:
                raise revision_conflict(expected=owned.draft.revision, received=expected_revision)
            registry = self._registry(owned, resolution_id)
            if registry.object_type != "resolution_spec":
                raise ApplicationError(
                    "object_type_mismatch", "目标对象不是核心问题。", status_code=422
                )
            before_document = build_casefile_document(self.session, owned)
            collection = COLLECTIONS[registry.object_type]
            before = _find(before_document[collection], resolution_id)
            conclusion = before.get("conclusion")
            if conclusion is None:
                raise ApplicationError(
                    "conclusion_missing", "当前核心问题尚未形成可确认的结论。", status_code=422
                )
            current_status = conclusion["review_status"]
            if target_status == "confirmed":
                if current_status != "proposed":
                    raise ApplicationError(
                        "conclusion_transition_invalid", "只有待确认结论可以确认。", status_code=409
                    )
                self._validate_confirmable_conclusion(before_document, before)
            elif current_status != "confirmed":
                raise ApplicationError(
                    "conclusion_transition_invalid", "只有已确认结论可以撤回。", status_code=409
                )
            mutation = MutationSet(
                mutation_set_id=(
                    f"conclusion_{target_status}_{owned.draft.id}_{expected_revision}"
                ),
                base_draft_id=owned.draft.id,
                base_revision=expected_revision,
                operations=(
                    UpdateField(
                        operation_id="conclusion_status",
                        object_id=resolution_id,
                        field_path="/conclusion/review_status",
                        old_value=current_status,
                        new_value=target_status,
                        expected_object_revision=registry.revision,
                    ),
                ),
                actor="author",
            )
            row = self._content_row(ResolutionSpec, registry, "ResolutionSpec")
            if target_status == "confirmed":
                # Keep the database confirmation constraint true at every flush:
                # _apply_resolution writes the confirmed status during canonical
                # materialization, so its author metadata must already be present.
                row.conclusion_confirmed_by_user_id = actor_user_id
                row.conclusion_confirmed_at = datetime.now(UTC)
            revision, _group_no, simulation = self.apply_mutation_set(
                owned,
                mutation_set=mutation,
                actor_user_id=actor_user_id,
            )
            after = _find(dict(simulation.document)[collection], resolution_id)
            self.session.add(
                AuditEvent(
                    project_id=owned.project.id,
                    casefile_id=owned.casefile.id,
                    actor_kind="user",
                    actor_user_id=actor_user_id,
                    actor_ref=None,
                    action=(
                        "resolution.conclusion_confirmed"
                        if target_status == "confirmed"
                        else "resolution.conclusion_withdrawn"
                    ),
                    target_type="resolution_spec",
                    target_id=registry.id,
                    trace_id=None,
                    details_jsonb={
                        "old_status": current_status,
                        "new_status": target_status,
                        "draft_revision": expected_revision,
                    },
                )
            )
            self.session.refresh(owned.draft)
            return after, revision

    @staticmethod
    def _validate_confirmable_conclusion(
        document: dict[str, Any], resolution: dict[str, Any]
    ) -> None:
        conclusion = resolution["conclusion"]
        resolution_id = resolution["id"]
        hypotheses = {
            item["id"]: item
            for item in document["hypotheses"]
            if item["target_resolution_ref"]["object_id"] == resolution_id
        }
        if not conclusion["selected_hypothesis_refs"]:
            raise ApplicationError(
                "conclusion_hypotheses_missing",
                "确认结论至少需要关联一个同题假设。",
                status_code=422,
            )
        if any(
            ref["object_id"] not in hypotheses for ref in conclusion["selected_hypothesis_refs"]
        ):
            raise ApplicationError(
                "conclusion_hypothesis_scope_invalid",
                "结论引用了其他核心问题的假设。",
                status_code=422,
            )
        if conclusion["outcome"] == "answer":
            values = {item["slot_id"] for item in conclusion["values"]}
            missing = [
                slot["slot_id"]
                for slot in resolution["required_slots"]
                if slot["required"] and slot["slot_id"] not in values
            ]
            if missing:
                raise ApplicationError(
                    "conclusion_required_slot_missing",
                    "答案结论缺少必填答案槽位。",
                    status_code=422,
                    details={"slot_ids": missing},
                )
        else:
            if not conclusion["unresolved_gaps"]:
                raise ApplicationError(
                    "conclusion_gaps_missing", "未定论必须说明证据缺口。", status_code=422
                )
        selected_hypothesis_ids = {
            ref["object_id"] for ref in conclusion["selected_hypothesis_refs"]
        }
        valid_target_ids = resolution_conclusion_target_ids(
            document,
            resolution,
            selected_hypothesis_ids,
        )
        path_ids = {
            item["id"]
            for item in document["reasoning_paths"]
            if item["required_for_resolution"]
            and item["target_ref"]["object_id"] in valid_target_ids
        }
        supporting_path_ids = {
            ref["object_id"] for ref in conclusion["supporting_reasoning_path_refs"]
        }
        if not supporting_path_ids or not supporting_path_ids.issubset(path_ids):
            raise ApplicationError(
                "conclusion_reasoning_path_scope_invalid",
                "结论依据路径必须属于当前问题的必要推理链。",
                status_code=422,
                details={"reasoning_path_ids": sorted(supporting_path_ids - path_ids)},
            )

    def apply_operation_batch(
        self,
        owned: OwnedDraft,
        *,
        operations: list[dict[str, Any]],
        actor_user_id: int,
        operation_type: str,
        patch_set_id: int,
    ) -> tuple[int, int, list[dict[str, Any]]]:
        """Compatibility adapter for legacy Agent field replacements."""

        if operation_type not in {"agent_patch_apply", "agent_patch_undo"}:
            raise ValueError(f"Unsupported batch operation type: {operation_type}")
        if not operations:
            raise ApplicationError(
                "patch_operation_empty",
                "至少需要一项已接受的修改操作。",
                status_code=422,
            )

        logical_operations: list[UpdateField] = []
        for index, operation in enumerate(operations, start=1):
            if operation.get("operation_type", "replace") != "replace":
                raise ApplicationError(
                    "patch_operation_not_supported",
                    "当前版本仅支持替换字段值。",
                    status_code=422,
                    details={"operation_id": operation.get("operation_id")},
                )
            object_id = str(operation["object_id"])
            registry = self._registry(owned, object_id)
            field_path = str(operation["field_path"])
            new_value = deepcopy(operation.get("new_value"))
            if (
                registry.object_type == "resolution_spec"
                and _top_level_field(field_path) in CONCLUSION_INVALIDATING_RESOLUTION_FIELDS
            ):
                if field_path == "/conclusion" and isinstance(new_value, dict):
                    new_value["review_status"] = "proposed"
                elif field_path == "/conclusion/review_status" and new_value == "confirmed":
                    new_value = "proposed"
            logical_operations.append(
                UpdateField(
                    operation_id=str(operation.get("operation_id") or f"legacy_{index}"),
                    object_id=object_id,
                    field_path=field_path,
                    old_value=deepcopy(operation.get("old_value")),
                    new_value=new_value,
                    expected_object_revision=operation.get("expected_object_revision"),
                )
            )
        mutation = MutationSet(
            mutation_set_id=f"agent_patch_{patch_set_id}_{owned.draft.revision}",
            base_draft_id=owned.draft.id,
            base_revision=owned.draft.revision,
            operations=tuple(logical_operations),
            actor="author" if operation_type == "agent_patch_undo" else "agent",
        )
        revision, group_no, simulation = self.apply_mutation_set(
            owned,
            mutation_set=mutation,
            actor_user_id=actor_user_id,
            draft_operation_type=operation_type,
            source_patch_set_id=patch_set_id,
        )
        applied = [
            {
                "operation_id": operation.operation_id,
                "object_id": operation.object_id,
                "object_type": self._registry(owned, operation.object_id).object_type,
                "field_path": operation.field_path,
                "old_value": deepcopy(operation.old_value),
                "new_value": deepcopy(operation.new_value),
            }
            for operation in logical_operations
        ]
        return revision, group_no, applied

    def apply_mutation_set(
        self,
        owned: OwnedDraft,
        *,
        mutation_set: MutationSet,
        actor_user_id: int | None,
        draft_operation_type: str = "logical_mutation_apply",
        expected_candidate_hash: str | None = None,
        accepted_debt_finding_keys: tuple[str, ...] = (),
        debt_acceptance_reason: str | None = None,
        target_finding_keys: tuple[str, ...] = (),
        source_patch_set_id: int | None = None,
        source_closure_policy_version: str | None = None,
    ) -> tuple[int, int, MutationSimulation]:
        """Re-simulate and atomically project one canonical MutationSet."""

        if mutation_set.closure_policy_version != CLOSURE_POLICY_VERSION:
            raise ApplicationError(
                "closure_policy_version_stale",
                "逻辑门禁策略已更新，请重新预演。",
                status_code=409,
            )
        if (
            mutation_set.base_draft_id != owned.draft.id
            or mutation_set.base_revision != owned.draft.revision
        ):
            raise ApplicationError(
                "mutation_revision_conflict",
                "Draft 已变化，请重新预演。",
                status_code=409,
            )
        if accepted_debt_finding_keys and actor_user_id is None:
            raise ApplicationError(
                "logical_debt_requires_human_authorization",
                "逻辑债务必须由当前用户明确授权。",
                status_code=403,
            )
        before = build_casefile_document(self.session, owned)
        engine = VerificationEngine(
            profile="fast",
            draft_revision=owned.draft.revision,
            closure_policy_version=CLOSURE_POLICY_VERSION,
        )
        simulation = engine.simulate_mutation_set(
            before,
            mutation_set,
            accepted_debt_finding_keys=accepted_debt_finding_keys,
            debt_acceptance_reason=debt_acceptance_reason,
            target_finding_keys=target_finding_keys,
            allow_author_debt_acceptance=True,
            allow_existing_hard_invariants=(
                draft_operation_type == "logical_mutation_normalize"
            ),
        )
        if not simulation.can_apply:
            raise ApplicationError(
                simulation.reason_code or "logical_mutation_blocked",
                "逻辑修改未通过统一门禁，Draft 未发生变化。",
                status_code=409,
                details={"simulation": simulation.as_dict()},
            )
        if expected_candidate_hash and expected_candidate_hash != simulation.candidate_hash:
            raise ApplicationError(
                "mutation_candidate_hash_stale",
                "预演候选已失效，请重新预演。",
                status_code=409,
            )

        candidate = dict(simulation.document)
        before_by_id = _document_objects(before)
        candidate_by_id = _document_objects(candidate)
        created_ids = set(candidate_by_id) - set(before_by_id)
        deleted_ids = set(before_by_id) - set(candidate_by_id)
        changed_ids = {
            object_id
            for object_id in set(before_by_id) & set(candidate_by_id)
            if before_by_id[object_id][1] != candidate_by_id[object_id][1]
        }
        creates: dict[str, list[dict[str, Any]]] = {}
        restored: list[tuple[CaseFileObject, dict[str, Any]]] = []
        for object_id in sorted(created_ids):
            collection, value = candidate_by_id[object_id]
            deleted_registry = self.session.scalar(
                select(CaseFileObject).where(
                    CaseFileObject.draft_id == owned.draft.id,
                    CaseFileObject.object_id == object_id,
                    CaseFileObject.deleted_at.is_not(None),
                )
            )
            if deleted_registry is None:
                creates.setdefault(collection, []).append(deepcopy(value))
            else:
                if draft_operation_type not in {
                    "logical_mutation_undo",
                    "logical_mutation_redo",
                }:
                    raise ApplicationError(
                        "deleted_object_id_reuse",
                        "已删除对象的稳定 ID 不能用于创建新对象。",
                        status_code=409,
                        details={"object_id": object_id},
                    )
                restored.append((deleted_registry, value))
        if creates:
            create_casefile_objects(self.session, owned, creates)
        for registry, value in restored:
            registry.deleted_at = None
            self._apply_object(owned, registry, value)
            registry.revision = int(value["revision"])
            registry.contract_updated_at = str(value["updated_at"])
        for object_id in sorted(changed_ids):
            registry = self._registry(owned, object_id)
            value = candidate_by_id[object_id][1]
            self._apply_object(owned, registry, value)
            registry.revision = int(value["revision"])
            registry.contract_updated_at = str(value["updated_at"])
        now = datetime.now(UTC)
        for object_id in sorted(deleted_ids):
            self._registry(owned, object_id).deleted_at = now
        self.session.flush()

        projected = build_casefile_document(self.session, owned)
        projected_hash = casefile_content_hash(projected)
        if projected != candidate or projected_hash != simulation.candidate_hash:
            raise ApplicationError(
                "mutation_projection_hash_mismatch",
                "数据库投影与预演候选不一致，修改已整体回滚。",
                status_code=500,
                details={
                    "candidate_hash": simulation.candidate_hash,
                    "projected_hash": projected_hash,
                },
            )
        sequence_no = int(
            self.session.scalar(
                select(func.coalesce(func.max(DraftOperation.sequence_no), 0) + 1).where(
                    DraftOperation.draft_id == owned.draft.id
                )
            )
            or 1
        )
        payload = {
            "mutation_set": _mutation_set_payload(mutation_set),
            "mechanical_operations": (
                simulation.normalized_mutation or {}
            ).get("mechanical_operations", []),
            "closure_policy_version": mutation_set.closure_policy_version,
            "mode": mutation_set.mode,
            "before_hash": simulation.baseline_hash,
            "after_hash": simulation.candidate_hash,
            "accepted_debt_finding_keys": list(accepted_debt_finding_keys),
            "debt_acceptance_reason": debt_acceptance_reason,
            "source_patch_set_id": source_patch_set_id,
            "source_closure_policy_version": source_closure_policy_version,
        }
        is_user_actor = mutation_set.actor == "author"
        if is_user_actor and actor_user_id is None:
            raise ValueError("Author mutations require actor_user_id")
        self.session.add(
            DraftOperation(
                project_id=owned.project.id,
                casefile_id=owned.casefile.id,
                draft_id=owned.draft.id,
                casefile_object_id=None,
                sequence_no=sequence_no,
                operation_group_no=sequence_no,
                operation_type=draft_operation_type,
                field_path="",
                old_value_jsonb={**payload, "document": before},
                new_value_jsonb={**payload, "document": candidate},
                base_revision=owned.draft.revision,
                result_revision=owned.draft.revision + 1,
                actor_kind="user" if is_user_actor else mutation_set.actor,
                actor_user_id=actor_user_id if is_user_actor else None,
                actor_ref=None if is_user_actor else f"logical-mutation:{mutation_set.actor}",
            )
        )
        audit_action = {
            "logical_mutation_apply": "logical_mutation.applied",
            "logical_mutation_undo": "logical_mutation.undone",
            "logical_mutation_redo": "logical_mutation.redone",
        }.get(draft_operation_type, "logical_mutation.applied")
        self.session.add(
            AuditEvent(
                project_id=owned.project.id,
                casefile_id=owned.casefile.id,
                actor_kind="user" if is_user_actor else mutation_set.actor,
                actor_user_id=actor_user_id if is_user_actor else None,
                actor_ref=(
                    None
                    if is_user_actor
                    else f"logical-mutation:{mutation_set.actor}"
                ),
                action=audit_action,
                target_type="draft",
                target_id=owned.draft.id,
                trace_id=None,
                details_jsonb={
                    "mutation_set_id": mutation_set.mutation_set_id,
                    "operation_group_no": sequence_no,
                    "base_revision": owned.draft.revision,
                    "result_revision": owned.draft.revision + 1,
                    "before_hash": simulation.baseline_hash,
                    "after_hash": simulation.candidate_hash,
                    "source_patch_set_id": source_patch_set_id,
                },
            )
        )
        if accepted_debt_finding_keys and actor_user_id is not None:
            accepted_at = datetime.now(UTC)
            self.session.add(
                AuditEvent(
                    project_id=owned.project.id,
                    casefile_id=owned.casefile.id,
                    actor_kind="user",
                    actor_user_id=actor_user_id,
                    actor_ref=None,
                    action="logical_mutation.debt_accepted",
                    target_type="draft",
                    target_id=owned.draft.id,
                    trace_id=None,
                    details_jsonb={
                        "finding_keys": list(accepted_debt_finding_keys),
                        "reason": debt_acceptance_reason,
                        "closure_policy_version": mutation_set.closure_policy_version,
                        "candidate_hash": simulation.candidate_hash,
                        "accepted_at": accepted_at.isoformat(),
                    },
                )
            )
        self.session.flush()
        self.session.refresh(owned.draft)
        return owned.draft.revision, sequence_no, simulation

    def _registry(self, owned: OwnedDraft, object_id: str) -> CaseFileObject:
        registry = self.session.scalar(
            select(CaseFileObject).where(
                CaseFileObject.draft_id == owned.draft.id,
                CaseFileObject.object_id == object_id,
                CaseFileObject.deleted_at.is_(None),
            )
        )
        if registry is None:
            raise not_found("CaseFileObject")
        return registry

    def _require_editable_fields(self, object_type: str, fields: set[str]) -> None:
        allowed = EDITABLE_FIELDS.get(object_type)
        if allowed is None:
            raise self._object_read_only(object_type)
        unknown = sorted(fields - allowed)
        if unknown:
            raise ApplicationError(
                "field_read_only",
                "修改内容包含不可变或不受支持的字段。",
                status_code=422,
                details={"fields": unknown, "object_type": object_type},
            )

    @staticmethod
    def _object_read_only(object_type: str) -> ApplicationError:
        return ApplicationError(
            "object_read_only",
            "当前 CaseFile 契约中的该对象类型不可编辑。",
            status_code=409,
            details={"object_type": object_type},
        )

    @staticmethod
    def _advance_object_revision(registry: CaseFileObject) -> None:
        registry.revision += 1
        registry.contract_updated_at = datetime.now(UTC).isoformat()

    def _apply_object(
        self,
        owned: OwnedDraft,
        registry: CaseFileObject,
        value: dict[str, Any],
    ) -> None:
        registry.description = value.get("description")
        registry.tags_jsonb = value["tags"]
        object_type = registry.object_type
        if object_type == "resolution_spec":
            self._apply_resolution(registry, value)
        elif object_type == "entity":
            self._apply_entity(registry, value)
        elif object_type == "relationship":
            self._apply_relationship(registry, value)
        elif object_type == "location":
            self._apply_location(registry, value)
        elif object_type == "event":
            self._apply_event(registry, value)
        elif object_type == "information_unit":
            self._apply_information_unit(registry, value)
        elif object_type == "claim":
            self._apply_claim(registry, value)
        elif object_type == "hypothesis":
            self._apply_hypothesis(registry, value)
        elif object_type == "reasoning_path":
            self._apply_reasoning_path(registry, value)
        elif object_type == "constraint":
            self._apply_constraint(registry, value)
        elif object_type == "structure_lock":
            self._apply_structure_lock(registry, value)
        else:
            raise self._object_read_only(object_type)
        self._replace_contract_refs(owned, registry, value)

    def _apply_resolution(self, registry: CaseFileObject, value: dict[str, Any]) -> None:
        row = self._content_row(ResolutionSpec, registry, "ResolutionSpec")
        row.title = value["title"]
        row.question_type = value["question_type"]
        row.target_question = value["reasoning_question"]
        row.conclusion_mode = value["conclusion_mode"]
        row.accepted_answer_texts_jsonb = {
            str(index): answer
            for index, answer in enumerate(value["accepted_answers"], start=1)
            if isinstance(answer, str)
        }
        conclusion = value.get("conclusion")
        row.conclusion_outcome = conclusion["outcome"] if conclusion else None
        row.conclusion_review_status = conclusion["review_status"] if conclusion else None
        row.conclusion_summary = conclusion["summary"] if conclusion else None
        row.conclusion_rationale = conclusion["rationale"] if conclusion else None
        row.conclusion_unresolved_gaps_jsonb = conclusion["unresolved_gaps"] if conclusion else []
        if not conclusion or conclusion["review_status"] != "confirmed":
            row.conclusion_confirmed_by_user_id = None
            row.conclusion_confirmed_at = None
        conclusion_values = (
            {
                item["slot_id"]: item["value"]
                for item in conclusion["values"]
                if not isinstance(item["value"], dict)
            }
            if conclusion
            else {}
        )
        existing = {
            slot.slot_key: slot
            for slot in self.session.scalars(
                select(ResolutionSlot).where(ResolutionSlot.resolution_spec_id == row.id)
            )
        }
        retained: set[int] = set()
        for ordinal, slot_value in enumerate(value["required_slots"], start=1):
            slot = existing.get(slot_value["slot_id"])
            if slot is None:
                slot = ResolutionSlot(
                    project_id=registry.project_id,
                    casefile_id=registry.casefile_id,
                    draft_id=registry.draft_id,
                    resolution_spec_id=row.id,
                    slot_key=slot_value["slot_id"],
                    value_type=slot_value["value_type"],
                    label=slot_value["slot_id"],
                    is_required=slot_value["required"],
                    ordinal=ordinal,
                    value_jsonb=conclusion_values.get(slot_value["slot_id"]),
                )
                self.session.add(slot)
            else:
                slot.value_type = slot_value["value_type"]
                slot.label = slot_value["slot_id"]
                slot.is_required = slot_value["required"]
                slot.ordinal = ordinal
                slot.value_jsonb = conclusion_values.get(slot_value["slot_id"])
                retained.add(slot.id)
        for slot in existing.values():
            if slot.id not in retained and slot.slot_key not in {
                item["slot_id"] for item in value["required_slots"]
            }:
                self.session.delete(slot)

    def _apply_entity(self, registry: CaseFileObject, value: dict[str, Any]) -> None:
        row = self._content_row(Entity, registry, "Entity")
        row.entity_kind = value["entity_type"]
        row.name = value["name"]
        row.description = value.get("description")
        row.aliases_jsonb = value["aliases"]
        row.traits_jsonb = value["traits"]
        row.goals_jsonb = value["goals"]
        row.secrets_jsonb = value["secrets"]
        row.capabilities_jsonb = value["capabilities"]

    def _apply_relationship(self, registry: CaseFileObject, value: dict[str, Any]) -> None:
        row = self._content_row(Relationship, registry, "Relationship")
        row.title = value["title"]
        row.relationship_type = value["relationship_type"]
        row.direction = value["direction"]
        row.truth_status = value["truth_status"]
        row.visibility = value["visibility"]

    def _apply_location(self, registry: CaseFileObject, value: dict[str, Any]) -> None:
        row = self._content_row(Location, registry, "Location")
        row.name = value["name"]
        row.geo_jsonb = value.get("spatial_position", {})
        row.access_rules_jsonb = value["access_rules"]
        row.visibility_rules_jsonb = value["visibility_rules"]

    def _apply_event(self, registry: CaseFileObject, value: dict[str, Any]) -> None:
        row = self._content_row(Event, registry, "Event")
        row.title = value["title"]
        row.summary = value.get("description")
        row.truth_status = value["truth_status"]
        row.time_jsonb = value["time"]

    def _apply_information_unit(
        self,
        registry: CaseFileObject,
        value: dict[str, Any],
    ) -> None:
        row = self._content_row(InformationUnit, registry, "InformationUnit")
        row.information_kind = value["information_type"]
        row.title = value["title"]
        row.body_text = value["content"]
        row.reliability = value["reliability"]
        row.truth_status = value["truth_status"]
        row.classification = value["classification"]
        row.acquisition_conditions_jsonb = value["availability"]["acquisition_conditions"]
        row.is_misleading = value["classification"] == "misleading"

    def _apply_claim(self, registry: CaseFileObject, value: dict[str, Any]) -> None:
        row = self._content_row(Claim, registry, "Claim")
        row.title = value["title"]
        row.statement = value["statement"]
        row.claim_type = value["claim_type"]
        row.status = value["status"]
        row.materiality = value["materiality"]

    def _apply_hypothesis(self, registry: CaseFileObject, value: dict[str, Any]) -> None:
        row = self._content_row(Hypothesis, registry, "Hypothesis")
        row.title = value["title"]
        row.summary = value["proposition"]
        row.status = value["status"]
        score = value["score"]
        row.score = None if score is None else Decimal(str(score))

    def _apply_reasoning_path(
        self,
        registry: CaseFileObject,
        value: dict[str, Any],
    ) -> None:
        row = self._content_row(ReasoningPath, registry, "ReasoningPath")
        row.name = value["title"]
        row.reasoning_type = value["path_type"]
        row.summary = value.get("description")
        row.required_for_resolution = value["required_for_resolution"]
        existing = {
            node.node_key: node
            for node in self.session.scalars(
                select(ReasoningNode).where(ReasoningNode.reasoning_path_id == row.id)
            )
        }
        retained: set[int] = set()
        for ordinal, step in enumerate(value["steps"], start=1):
            node = existing.get(step["step_id"])
            if node is None:
                node = ReasoningNode(
                    project_id=registry.project_id,
                    casefile_id=registry.casefile_id,
                    draft_id=registry.draft_id,
                    reasoning_path_id=row.id,
                    node_key=step["step_id"],
                    ordinal=ordinal,
                    source_object_id=None,
                    node_type=step["operation"],
                    statement=step["operation"],
                    attributes_jsonb={},
                )
                self.session.add(node)
            else:
                node.ordinal = ordinal
                node.node_type = step["operation"]
                node.statement = step["operation"]
                retained.add(node.id)
        desired_keys = {step["step_id"] for step in value["steps"]}
        for node in existing.values():
            if node.id not in retained and node.node_key not in desired_keys:
                self.session.delete(node)

    def _apply_constraint(self, registry: CaseFileObject, value: dict[str, Any]) -> None:
        row = self._content_row(CaseFileConstraint, registry, "CaseFileConstraint")
        row.title = value["title"]
        row.constraint_level = value["level"]
        row.statement = value["statement"]
        row.rule_expression = value["rule_expression"]

    def _apply_structure_lock(
        self,
        registry: CaseFileObject,
        value: dict[str, Any],
    ) -> None:
        row = self._content_row(StructureLock, registry, "StructureLock")
        row.title = value["title"]
        row.lock_type = value["lock_type"]
        row.field_paths_jsonb = value["field_paths"]
        row.reason = value["reason"]

    def _replace_contract_refs(
        self,
        owned: OwnedDraft,
        registry: CaseFileObject,
        value: dict[str, Any],
    ) -> None:
        self.session.execute(
            delete(CaseFileContractRef).where(
                CaseFileContractRef.draft_id == owned.draft.id,
                CaseFileContractRef.from_object_id == registry.id,
            )
        )
        for path, ordinal, reference, metadata in iter_contract_object_refs(value):
            self.session.add(
                CaseFileContractRef(
                    project_id=owned.project.id,
                    casefile_id=owned.casefile.id,
                    draft_id=owned.draft.id,
                    from_object_id=registry.id,
                    field_path=path,
                    object_type=reference["object_type"],
                    object_id=reference["object_id"],
                    ordinal=ordinal,
                    metadata_jsonb=metadata,
                )
            )

    def _content_row(
        self,
        model: type[Any],
        registry: CaseFileObject,
        resource_name: str,
    ) -> Any:
        row = self.session.scalar(select(model).where(model.object_registry_id == registry.id))
        if row is None:
            raise not_found(resource_name)
        return row


def _find(values: list[dict[str, Any]], object_id: str) -> dict[str, Any]:
    for value in values:
        if value["id"] == object_id:
            return value
    raise not_found("CaseFileObject")


def _document_objects(
    document: dict[str, Any],
) -> dict[str, tuple[str, dict[str, Any]]]:
    return {
        str(value["id"]): (collection, value)
        for collection in COLLECTIONS.values()
        for value in document[collection]
    }


def _mutation_set_payload(mutation_set: MutationSet) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    for operation in mutation_set.operations:
        if isinstance(operation, CreateObject):
            operations.append(
                {
                    "operation_id": operation.operation_id,
                    "operation_type": operation.operation_type,
                    "collection": operation.collection,
                    "object_value": deepcopy(dict(operation.object_value)),
                }
            )
        elif isinstance(operation, UpdateField):
            operations.append(
                {
                    "operation_id": operation.operation_id,
                    "operation_type": operation.operation_type,
                    "object_id": operation.object_id,
                    "field_path": operation.field_path,
                    "old_value": deepcopy(operation.old_value),
                    "new_value": deepcopy(operation.new_value),
                    "expected_object_revision": operation.expected_object_revision,
                }
            )
        else:
            operations.append(
                {
                    "operation_id": operation.operation_id,
                    "operation_type": operation.operation_type,
                    "object_id": operation.object_id,
                    "old_object_value": (
                        None
                        if operation.old_object_value is None
                        else deepcopy(dict(operation.old_object_value))
                    ),
                }
            )
    return {
        "mutation_set_id": mutation_set.mutation_set_id,
        "base_draft_id": mutation_set.base_draft_id,
        "base_revision": mutation_set.base_revision,
        "actor": mutation_set.actor,
        "mode": mutation_set.mode,
        "closure_policy_version": mutation_set.closure_policy_version,
        "operations": operations,
    }


def _conclusion_references_object(
    document: dict[str, Any], resolution: dict[str, Any], object_id: str
) -> bool:
    conclusion = resolution.get("conclusion")
    if conclusion is None:
        return False
    direct_refs = [
        *resolution["required_claim_refs"],
        *conclusion["selected_hypothesis_refs"],
        *conclusion["supporting_reasoning_path_refs"],
        *(
            item["value"]
            for item in conclusion["values"]
            if isinstance(item["value"], dict)
        ),
    ]
    if any(ref["object_id"] == object_id for ref in direct_refs):
        return True

    selected_hypothesis_ids = {
        ref["object_id"] for ref in conclusion["selected_hypothesis_refs"]
    }
    supporting_path_ids = {
        ref["object_id"] for ref in conclusion["supporting_reasoning_path_refs"]
    }
    dependency_ids = {ref["object_id"] for ref in resolution["required_claim_refs"]}
    for hypothesis in document["hypotheses"]:
        if hypothesis["id"] not in selected_hypothesis_ids:
            continue
        dependency_ids.update(
            ref["object_id"]
            for ref in [
                *hypothesis["required_claim_refs"],
                *hypothesis["falsifier_refs"],
            ]
        )
        dependency_ids.update(
            assessment["information_ref"]["object_id"]
            for assessment in hypothesis.get("evidence_assessments", [])
        )
    for path in document["reasoning_paths"]:
        if path["id"] not in supporting_path_ids:
            continue
        for step in path["steps"]:
            dependency_ids.update(ref["object_id"] for ref in step["input_refs"])
            dependency_ids.add(step["output_ref"]["object_id"])
    return object_id in dependency_ids


def _pointer_parts(path: str) -> list[str]:
    if not path.startswith("/") or path == "/":
        raise ApplicationError(
            "patch_path_invalid",
            "修改路径必须指向一个业务字段。",
            status_code=422,
            details={"field_path": path},
        )
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _top_level_field(path: str) -> str:
    return _pointer_parts(path)[0]


def _pointer_value(value: Any, path: str) -> Any:
    current = value
    for part in _pointer_parts(path):
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise ApplicationError(
                "patch_path_missing",
                "建议字段不存在于当前对象中。",
                status_code=409,
                details={"field_path": path},
            ) from error
    return deepcopy(current)


def _replace_pointer(value: Any, path: str, replacement: Any) -> None:
    parts = _pointer_parts(path)
    parent = value
    for part in parts[:-1]:
        try:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        except (IndexError, KeyError, TypeError, ValueError) as error:
            raise ApplicationError(
                "patch_path_missing",
                "建议字段不存在于当前对象中。",
                status_code=409,
                details={"field_path": path},
            ) from error
    final = parts[-1]
    try:
        if isinstance(parent, list):
            parent[int(final)] = replacement
        else:
            parent[final] = replacement
    except (IndexError, KeyError, TypeError, ValueError) as error:
        raise ApplicationError(
            "patch_path_missing",
            "建议字段不存在于当前对象中。",
            status_code=409,
            details={"field_path": path},
        ) from error


def iter_editable_fields(object_type: str) -> Iterator[str]:
    """Expose stable editable business-field names for API/UI capability checks."""

    yield from sorted(EDITABLE_FIELDS.get(object_type, set()))


__all__ = [
    "EDITABLE_FIELDS",
    "V1EditingService",
    "editable_fields_by_collection",
    "iter_editable_fields",
]
