"""Shadow scanning and mechanical normalization for pre-M3 Current Drafts."""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from casefile.application.casefile_v1 import build_casefile_document, casefile_content_hash
from casefile.application.errors import not_found
from casefile.application.v1_editing import V1EditingService
from casefile.data_postgres.repositories import ProjectRepository
from casefile.domain.logical_mutation import CLOSURE_POLICY_VERSION, MutationSet, UpdateField
from casefile.domain.verification_engine import VerificationEngine

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
        document = build_casefile_document(self.session, owned)
        mismatches = _reciprocal_mismatches(document)
        result = VerificationEngine(
            profile="fast", draft_revision=owned.draft.revision
        ).verify(document)
        by_level = Counter(
            str(item.payload.get("closure_level", "legacy")) for item in result.findings
        )
        return {
            "draft_id": owned.draft.id,
            "draft_revision": owned.draft.revision,
            "closure_policy_version": CLOSURE_POLICY_VERSION,
            "content_hash": casefile_content_hash(document),
            "mechanical_mismatches": mismatches,
            "mechanical_mismatch_count": len(mismatches),
            "finding_counts": dict(sorted(by_level.items())),
            "findings": [item.as_dict() for item in result.findings],
            "blocking_enabled": False,
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
