"""Focused v1 editing for generated Entity, Location, and Event objects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.application.casefile_v1 import build_casefile_document
from casefile.application.errors import ApplicationError, not_found, revision_conflict
from casefile.data_postgres.models import CaseFileObject, Entity, Event, Location
from casefile.data_postgres.repositories import DraftRepository, ProjectRepository

EDITABLE_FIELDS = {
    "entity": {
        "name",
        "description",
        "traits",
        "aliases",
        "goals",
        "secrets",
        "capabilities",
    },
    "location": {"name", "description", "access_rules", "visibility_rules"},
    "event": {"title", "description", "truth_status", "time"},
}
COLLECTIONS = {"entity": "entities", "location": "locations", "event": "events"}


class V1EditingService:
    """Apply one optimistic, validated object patch in a single transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.drafts = DraftRepository(session)

    def patch_object(
        self,
        actor_user_id: int,
        project_id: int,
        object_id: str,
        *,
        expected_revision: int,
        changes: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id, lock=True)
            if owned is None:
                raise not_found("Project")
            if owned.draft.revision != expected_revision:
                raise revision_conflict(
                    expected=owned.draft.revision,
                    received=expected_revision,
                )
            registry = self.session.scalar(
                select(CaseFileObject).where(
                    CaseFileObject.draft_id == owned.draft.id,
                    CaseFileObject.object_id == object_id,
                    CaseFileObject.deleted_at.is_(None),
                )
            )
            if registry is None:
                raise not_found("CaseFileObject")
            if registry.object_type not in EDITABLE_FIELDS:
                raise ApplicationError(
                    "object_read_only",
                    "Only Entity, Location, and Event objects are editable in this release",
                    status_code=409,
                    details={"object_type": registry.object_type},
                )
            unknown = sorted(set(changes) - EDITABLE_FIELDS[registry.object_type])
            if unknown:
                raise ApplicationError(
                    "field_read_only",
                    "The patch contains immutable or unsupported fields",
                    status_code=422,
                    details={"fields": unknown},
                )

            collection = COLLECTIONS[registry.object_type]
            before_document = build_casefile_document(self.session, owned)
            before = _find(before_document[collection], object_id)
            self._apply(registry, changes)
            registry.revision += 1
            registry.contract_updated_at = datetime.now(UTC).isoformat()
            self.session.flush()
            after_document = build_casefile_document(self.session, owned)
            after = _find(after_document[collection], object_id)
            revision = self.drafts.add_operation(
                owned,
                registry=registry,
                operation_type="replace",
                field_path=f"/{collection}/{object_id}",
                old_value=before,
                new_value=after,
                base_revision=expected_revision,
                actor_user_id=actor_user_id,
            )
            self.session.refresh(owned.draft)
            validated_document = build_casefile_document(self.session, owned)
            return _find(validated_document[collection], object_id), revision

    def _apply(self, registry: CaseFileObject, changes: dict[str, Any]) -> None:
        if "description" in changes:
            registry.description = changes["description"]
        row: Entity | Location | Event | None
        mappings: dict[str, str]
        if registry.object_type == "entity":
            row = self.session.scalar(
                select(Entity).where(Entity.object_registry_id == registry.id)
            )
            if row is None:
                raise not_found("Entity")
            mappings = {
                "name": "name",
                "traits": "traits_jsonb",
                "aliases": "aliases_jsonb",
                "goals": "goals_jsonb",
                "secrets": "secrets_jsonb",
                "capabilities": "capabilities_jsonb",
            }
        elif registry.object_type == "location":
            row = self.session.scalar(
                select(Location).where(Location.object_registry_id == registry.id)
            )
            if row is None:
                raise not_found("Location")
            mappings = {
                "name": "name",
                "access_rules": "access_rules_jsonb",
                "visibility_rules": "visibility_rules_jsonb",
            }
        else:
            row = self.session.scalar(select(Event).where(Event.object_registry_id == registry.id))
            if row is None:
                raise not_found("Event")
            mappings = {"title": "title", "truth_status": "truth_status", "time": "time_jsonb"}
        for public_name, column_name in mappings.items():
            if public_name in changes:
                setattr(row, column_name, changes[public_name])


def _find(values: list[dict[str, Any]], object_id: str) -> dict[str, Any]:
    for value in values:
        if value["id"] == object_id:
            return value
    raise not_found("CaseFileObject")


__all__ = ["V1EditingService"]
