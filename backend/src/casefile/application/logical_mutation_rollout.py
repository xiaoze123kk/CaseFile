"""Shadow scanning and mechanical normalization for pre-M3 Current Drafts."""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from casefile.application.casefile_v1 import build_casefile_document, casefile_content_hash
from casefile.application.errors import ApplicationError, not_found
from casefile.application.v1_editing import V1EditingService
from casefile.data_postgres.repositories import ProjectRepository
from casefile.domain.logical_mutation import (
    ACTIVE_APPLY_POLICY,
    CLOSURE_POLICY_V2,
    SHADOW_POLICY,
    MutationSet,
    UpdateField,
)
from casefile.domain.verification_engine import VerificationEngine, VerificationFinding

_RECIPROCALS = (
    ("information_units", "supports_claim_refs", "claims", "support_refs"),
    ("information_units", "refutes_claim_refs", "claims", "refute_refs"),
)


class LogicalMutationRolloutService:
    """Keep migration diagnostics separate from the enforced write path."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

    def shadow_scan(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        owned = self.projects.get_owned(actor_user_id, project_id)
        if owned is None:
            raise not_found("Project")
        try:
            document = build_casefile_document(self.session, owned)
        except ApplicationError as error:
            if error.code != "brief_version_missing":
                raise
            return {
                "draft_id": owned.draft.id,
                "draft_revision": owned.draft.revision,
                "scan_status": "not_ready",
                "reason_code": error.code,
                "closure_policy_version": ACTIVE_APPLY_POLICY,
                "active_policy": ACTIVE_APPLY_POLICY,
                "shadow_policy": SHADOW_POLICY,
                "content_hash": None,
                "mechanical_mismatches": [],
                "mechanical_mismatch_count": 0,
                "finding_counts": {},
                "findings": [],
                "shadow_findings": [],
                "shadow_only_finding_keys": [],
                "shadow_new_finding_keys": [],
                "shadow_promoted_findings": [],
                "shadow_finding_counts": {},
                "blocking_enabled": ACTIVE_APPLY_POLICY == CLOSURE_POLICY_V2,
            }
        mismatches = _reciprocal_mismatches(document)
        active_engine = VerificationEngine(
            profile="fast",
            draft_revision=owned.draft.revision,
            closure_policy_version=ACTIVE_APPLY_POLICY,
        )
        result = active_engine.verify(document)
        findings = result.findings
        if result.structural_valid:
            findings = tuple(
                {
                    item.finding_key: item
                    for item in (
                        *findings,
                        *active_engine.evaluate_snapshot_closure(document),
                    )
                }.values()
            )
        shadow_engine = VerificationEngine(
            profile="fast",
            draft_revision=owned.draft.revision,
            closure_policy_version=SHADOW_POLICY,
        )
        shadow_result = shadow_engine.verify(document)
        shadow_findings = shadow_result.findings
        if shadow_result.structural_valid:
            shadow_findings = tuple(
                {
                    item.finding_key: item
                    for item in (
                        *shadow_findings,
                        *shadow_engine.evaluate_snapshot_closure(document),
                    )
                }.values()
            )
        by_level = Counter(
            str(item.payload.get("closure_level", "legacy")) for item in findings
        )
        shadow_by_level = Counter(
            str(item.payload.get("closure_level", "legacy"))
            for item in shadow_findings
        )
        active_keys = {item.finding_key for item in findings}
        shadow_only = tuple(
            sorted(
                item.finding_key
                for item in shadow_findings
                if item.finding_key not in active_keys
            )
        )
        active_by_identity = {_finding_identity(item): item for item in findings}
        shadow_new: list[str] = []
        shadow_promoted: list[dict[str, str]] = []
        for item in shadow_findings:
            active_item = active_by_identity.get(_finding_identity(item))
            if active_item is None:
                shadow_new.append(item.finding_key)
                continue
            active_level = str(active_item.payload.get("closure_level", "legacy"))
            shadow_level = str(item.payload.get("closure_level", "legacy"))
            if active_level != shadow_level:
                shadow_promoted.append(
                    {
                        "rule_code": item.rule_code,
                        "active_finding_key": active_item.finding_key,
                        "shadow_finding_key": item.finding_key,
                        "active_level": active_level,
                        "shadow_level": shadow_level,
                    }
                )
        return {
            "draft_id": owned.draft.id,
            "draft_revision": owned.draft.revision,
            "scan_status": "completed",
            "closure_policy_version": ACTIVE_APPLY_POLICY,
            "active_policy": ACTIVE_APPLY_POLICY,
            "shadow_policy": SHADOW_POLICY,
            "content_hash": casefile_content_hash(document),
            "mechanical_mismatches": mismatches,
            "mechanical_mismatch_count": len(mismatches),
            "finding_counts": dict(sorted(by_level.items())),
            "findings": [item.as_dict() for item in findings],
            "shadow_findings": [item.as_dict() for item in shadow_findings],
            "shadow_only_finding_keys": list(shadow_only),
            "shadow_new_finding_keys": sorted(shadow_new),
            "shadow_promoted_findings": sorted(
                shadow_promoted,
                key=lambda item: (
                    item["rule_code"],
                    item["active_finding_key"],
                    item["shadow_finding_key"],
                ),
            ),
            "shadow_finding_counts": dict(sorted(shadow_by_level.items())),
            "blocking_enabled": ACTIVE_APPLY_POLICY == CLOSURE_POLICY_V2,
        }
    def normalize_mechanical(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        expected_draft_id: int,
        expected_revision: int,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id, lock=True)
            if owned is None:
                raise not_found("Project")
            document = build_casefile_document(self.session, owned)
            mismatches = _reciprocal_mismatches(document)
            if not mismatches:
                return {
                    "draft_id": owned.draft.id,
                    "draft_revision": owned.draft.revision,
                    "normalized": False,
                    "mechanical_mismatch_count": 0,
                }
            operations: list[UpdateField] = []
            seen: set[tuple[str, str]] = set()
            objects = {
                str(item["id"]): item
                for collection in (
                    "information_units",
                    "claims",
                )
                for item in document[collection]
            }
            for mismatch in mismatches:
                key = (str(mismatch["source_object_id"]), str(mismatch["source_path"]))
                if key in seen:
                    continue
                seen.add(key)
                value = objects[key[0]][key[1][1:]]
                operations.append(
                    UpdateField(
                        operation_id=f"normalize_{len(operations) + 1:04d}",
                        object_id=key[0],
                        field_path=key[1],
                        old_value=value,
                        new_value=value,
                        expected_object_revision=int(objects[key[0]]["revision"]),
                    )
                )
            mutation = MutationSet(
                mutation_set_id=f"normalize_{owned.draft.id}_{owned.draft.revision}",
                base_draft_id=expected_draft_id,
                base_revision=expected_revision,
                operations=tuple(operations),
                actor="system",
            )
            revision, group_no, simulation = V1EditingService(
                self.session
            ).apply_mutation_set(
                owned,
                mutation_set=mutation,
                actor_user_id=None,
                draft_operation_type="logical_mutation_normalize",
            )
            return {
                "draft_id": owned.draft.id,
                "draft_revision": revision,
                "operation_group_no": group_no,
                "normalized": True,
                "mechanical_mismatch_count": len(mismatches),
                "before_hash": simulation.baseline_hash,
                "after_hash": simulation.candidate_hash,
            }


def _finding_identity(
    finding: VerificationFinding,
) -> tuple[str, tuple[tuple[str, str, str], ...]]:
    return (
        finding.rule_code,
        tuple(
            sorted((ref.ref_kind, ref.ref_key, ref.role) for ref in finding.refs)
        ),
    )


def _reciprocal_mismatches(document: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for source_collection, source_field, target_collection, target_field in _RECIPROCALS:
        targets = {str(item["id"]): item for item in document[target_collection]}
        for source in document[source_collection]:
            for reference in source[source_field]:
                target_id = str(reference["object_id"])
                target = targets.get(target_id)
                if target is None:
                    continue
                reciprocal_ids = {str(item["object_id"]) for item in target[target_field]}
                if str(source["id"]) not in reciprocal_ids:
                    result.append(
                        {
                            "rule_code": "reciprocal_projection_missing",
                            "source_object_id": str(source["id"]),
                            "source_path": f"/{source_field}",
                            "target_object_id": target_id,
                            "target_path": f"/{target_field}",
                        }
                    )
        sources = {str(item["id"]): item for item in document[source_collection]}
        for target in document[target_collection]:
            for reference in target[target_field]:
                source_id = str(reference["object_id"])
                source = sources.get(source_id)
                if source is None:
                    continue
                forward_ids = {str(item["object_id"]) for item in source[source_field]}
                if str(target["id"]) not in forward_ids:
                    result.append(
                        {
                            "rule_code": "reciprocal_projection_missing",
                            "source_object_id": source_id,
                            "source_path": f"/{source_field}",
                            "target_object_id": str(target["id"]),
                            "target_path": f"/{target_field}",
                        }
                    )
    return sorted(
        result,
        key=lambda item: (
            item["source_object_id"],
            item["source_path"],
            item["target_object_id"],
        ),
    )
