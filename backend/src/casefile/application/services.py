"""Transactional application services for projects, Draft editing, and Snapshots."""

from __future__ import annotations

import secrets
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from casefile.application.commands import EntityWrite, EventWrite, ProjectCreate
from casefile.application.errors import ApplicationError, not_found, revision_conflict
from casefile.application.snapshot import build_casefile_document, casefile_content_hash
from casefile.contracts import CASEFILE_SCHEMA_VERSION, ContractValidationError
from casefile.data_postgres.models import Entity, Event, Location, Person
from casefile.data_postgres.repositories import (
    DraftRepository,
    EntityRows,
    EventRows,
    OwnedDraft,
    ProjectRepository,
    SnapshotRepository,
)


class CaseFileService:
    """One-request service facade with explicit PostgreSQL transactions."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.drafts = DraftRepository(session)
        self.snapshots = SnapshotRepository(session)

    def create_project(self, actor_user_id: int, command: ProjectCreate) -> dict[str, Any]:
        try:
            with self.session.begin():
                owned = self.projects.create(
                    owner_user_id=actor_user_id,
                    title=command.title,
                    description=command.description,
                    profile=command.profile,
                    schema_version=CASEFILE_SCHEMA_VERSION,
                )
                result = _project_view(owned)
            return result
        except IntegrityError as error:
            raise _integrity_error(error) from error

    def list_projects(self, actor_user_id: int) -> list[dict[str, Any]]:
        with self.session.begin():
            return [_project_view(item) for item in self.projects.list_owned(actor_user_id)]

    def get_project(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        with self.session.begin():
            return _project_view(self._owned(actor_user_id, project_id))

    def update_project(
        self, actor_user_id: int, project_id: int, changes: dict[str, Any]
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            if "title" in changes:
                owned.project.title = changes["title"]
                owned.casefile.title = changes["title"]
            if "description" in changes:
                owned.project.description = changes["description"]
            if "profile" in changes:
                owned.project.profile_jsonb = changes["profile"]
            self.session.flush()
            return _project_view(owned)

    def archive_project(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            if owned.project.status != "archived":
                self.projects.archive(owned)
                self.session.flush()
            return _project_view(owned)

    def get_draft(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            return _draft_view(owned, self._document(owned))

    def list_entities(self, actor_user_id: int, project_id: int) -> list[dict[str, Any]]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            document = self._document(owned)
            return list(document["entities"])

    def get_entity(
        self, actor_user_id: int, project_id: int, object_id: str
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            self._entity(owned, object_id)
            return _find_document_object(self._document(owned)["entities"], object_id)

    def create_entity(
        self,
        actor_user_id: int,
        project_id: int,
        base_revision: int,
        command: EntityWrite,
    ) -> tuple[dict[str, Any], int]:
        object_id = f"entity_{secrets.token_hex(12)}"
        try:
            with self.session.begin():
                owned = self._editable(actor_user_id, project_id, base_revision)
                registry = self.drafts.add_registry(
                    owned,
                    object_id=object_id,
                    object_type="entity",
                    confidence=command.confidence,
                )
                new_value = _entity_command_document(object_id, command)
                revision = self.drafts.add_operation(
                    owned,
                    registry=registry,
                    operation_type="add",
                    field_path=f"/entities/{object_id}",
                    old_value=None,
                    new_value=new_value,
                    base_revision=base_revision,
                    actor_user_id=actor_user_id,
                )
                entity = Entity(
                    project_id=owned.project.id,
                    casefile_id=owned.casefile.id,
                    draft_id=owned.draft.id,
                    object_registry_id=registry.id,
                    entity_kind=command.entity_kind,
                    name=command.name,
                    description=command.description,
                    traits_jsonb=command.traits,
                    attributes_jsonb=command.attributes,
                )
                self.session.add(entity)
                self.session.flush()
                self._write_entity_extension(owned, entity, command)
                self.session.flush()
                self.session.refresh(owned.draft)
                result = _find_document_object(self._document(owned)["entities"], object_id)
            return result, revision
        except IntegrityError as error:
            raise _integrity_error(error) from error

    def update_entity(
        self,
        actor_user_id: int,
        project_id: int,
        object_id: str,
        base_revision: int,
        command: EntityWrite,
    ) -> tuple[dict[str, Any], int]:
        try:
            with self.session.begin():
                owned = self._editable(actor_user_id, project_id, base_revision)
                rows = self._entity(owned, object_id)
                if rows.entity.entity_kind != command.entity_kind:
                    raise ApplicationError(
                        "immutable_field",
                        "entity_kind cannot be changed",
                        status_code=409,
                        details={"field": "entity_kind"},
                    )
                old_value = _find_document_object(self._document(owned)["entities"], object_id)
                new_value = _entity_command_document(
                    object_id,
                    command,
                    adjacent_ids=old_value.get("location", {}).get(
                        "adjacent_location_object_ids", []
                    ),
                )
                revision = self.drafts.add_operation(
                    owned,
                    registry=rows.registry,
                    operation_type="replace",
                    field_path=f"/entities/{object_id}",
                    old_value=old_value,
                    new_value=new_value,
                    base_revision=base_revision,
                    actor_user_id=actor_user_id,
                )
                rows.entity.name = command.name
                rows.entity.description = command.description
                rows.entity.traits_jsonb = command.traits
                rows.entity.attributes_jsonb = command.attributes
                rows.registry.confidence = _decimal(command.confidence)
                rows.registry.revision += 1
                self._update_entity_extension(rows, command)
                self.session.flush()
                self.session.refresh(owned.draft)
                result = _find_document_object(self._document(owned)["entities"], object_id)
            return result, revision
        except IntegrityError as error:
            raise _integrity_error(error) from error

    def set_adjacent_locations(
        self,
        actor_user_id: int,
        project_id: int,
        object_id: str,
        base_revision: int,
        target_object_ids: list[str],
    ) -> tuple[dict[str, Any], int]:
        _reject_duplicate_ids(target_object_ids)
        if object_id in target_object_ids:
            raise ApplicationError(
                "self_reference",
                "A location cannot be adjacent to itself",
                status_code=422,
            )
        with self.session.begin():
            owned = self._editable(actor_user_id, project_id, base_revision)
            rows = self._entity(owned, object_id)
            if rows.entity.entity_kind != "location" or rows.location is None:
                raise ApplicationError(
                    "reference_type_mismatch",
                    "Only a location can have adjacent locations",
                    status_code=422,
                )
            old_ids = self.drafts.reference_ids(
                owned,
                rows.registry,
                field_path="/location/adjacent_location_object_ids",
                ref_kind="location_adjacent_to",
            )
            revision = self.drafts.add_operation(
                owned,
                registry=rows.registry,
                operation_type="replace",
                field_path=f"/entities/{object_id}/location/adjacent_location_object_ids",
                old_value=old_ids,
                new_value=target_object_ids,
                base_revision=base_revision,
                actor_user_id=actor_user_id,
            )
            try:
                self.drafts.replace_references(
                    owned,
                    source=rows.registry,
                    target_object_ids=target_object_ids,
                    field_path="/location/adjacent_location_object_ids",
                    ref_kind="location_adjacent_to",
                    target_type="entity",
                    target_subtype=("entity_kind", "location"),
                )
            except LookupError as error:
                raise _invalid_reference(str(error.args[0]), "location") from error
            rows.registry.revision += 1
            self.session.flush()
            self.session.refresh(owned.draft)
            result = _find_document_object(self._document(owned)["entities"], object_id)
        return result, revision

    def list_events(self, actor_user_id: int, project_id: int) -> list[dict[str, Any]]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            return list(self._document(owned)["events"])

    def get_event(
        self, actor_user_id: int, project_id: int, object_id: str
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            self._event(owned, object_id)
            return _find_document_object(self._document(owned)["events"], object_id)

    def create_event(
        self,
        actor_user_id: int,
        project_id: int,
        base_revision: int,
        command: EventWrite,
    ) -> tuple[dict[str, Any], int]:
        object_id = f"event_{secrets.token_hex(12)}"
        try:
            with self.session.begin():
                owned = self._editable(actor_user_id, project_id, base_revision)
                phase_id, location_id = self._resolve_event_relations(owned, command)
                registry = self.drafts.add_registry(
                    owned,
                    object_id=object_id,
                    object_type="event",
                    confidence=command.confidence,
                )
                revision = self.drafts.add_operation(
                    owned,
                    registry=registry,
                    operation_type="add",
                    field_path=f"/events/{object_id}",
                    old_value=None,
                    new_value=_event_command_document(object_id, command, []),
                    base_revision=base_revision,
                    actor_user_id=actor_user_id,
                )
                self.session.add(
                    Event(
                        project_id=owned.project.id,
                        casefile_id=owned.casefile.id,
                        draft_id=owned.draft.id,
                        object_registry_id=registry.id,
                        title=command.title,
                        summary=command.summary,
                        start_time_jsonb=command.start_time,
                        end_time_jsonb=command.end_time,
                        narrative_order=command.narrative_order,
                        narrative_phase_id=phase_id,
                        location_id=location_id,
                        visibility=command.visibility,
                        truth_status=command.truth_status,
                    )
                )
                self.session.flush()
                self.session.refresh(owned.draft)
                result = _find_document_object(self._document(owned)["events"], object_id)
            return result, revision
        except IntegrityError as error:
            raise _integrity_error(error) from error

    def update_event(
        self,
        actor_user_id: int,
        project_id: int,
        object_id: str,
        base_revision: int,
        command: EventWrite,
    ) -> tuple[dict[str, Any], int]:
        try:
            with self.session.begin():
                owned = self._editable(actor_user_id, project_id, base_revision)
                rows = self._event(owned, object_id)
                phase_id, location_id = self._resolve_event_relations(owned, command)
                old_value = _find_document_object(self._document(owned)["events"], object_id)
                actor_ids = list(old_value["actor_object_ids"])
                revision = self.drafts.add_operation(
                    owned,
                    registry=rows.registry,
                    operation_type="replace",
                    field_path=f"/events/{object_id}",
                    old_value=old_value,
                    new_value=_event_command_document(object_id, command, actor_ids),
                    base_revision=base_revision,
                    actor_user_id=actor_user_id,
                )
                rows.event.title = command.title
                rows.event.summary = command.summary
                rows.event.start_time_jsonb = command.start_time
                rows.event.end_time_jsonb = command.end_time
                rows.event.narrative_order = command.narrative_order
                rows.event.narrative_phase_id = phase_id
                rows.event.location_id = location_id
                rows.event.visibility = command.visibility
                rows.event.truth_status = command.truth_status
                rows.registry.confidence = _decimal(command.confidence)
                rows.registry.revision += 1
                self.session.flush()
                self.session.refresh(owned.draft)
                result = _find_document_object(self._document(owned)["events"], object_id)
            return result, revision
        except IntegrityError as error:
            raise _integrity_error(error) from error

    def set_event_actors(
        self,
        actor_user_id: int,
        project_id: int,
        object_id: str,
        base_revision: int,
        target_object_ids: list[str],
    ) -> tuple[dict[str, Any], int]:
        _reject_duplicate_ids(target_object_ids)
        with self.session.begin():
            owned = self._editable(actor_user_id, project_id, base_revision)
            rows = self._event(owned, object_id)
            old_ids = self.drafts.reference_ids(
                owned,
                rows.registry,
                field_path="/actor_object_ids",
                ref_kind="event_actor",
            )
            revision = self.drafts.add_operation(
                owned,
                registry=rows.registry,
                operation_type="replace",
                field_path=f"/events/{object_id}/actor_object_ids",
                old_value=old_ids,
                new_value=target_object_ids,
                base_revision=base_revision,
                actor_user_id=actor_user_id,
            )
            try:
                self.drafts.replace_references(
                    owned,
                    source=rows.registry,
                    target_object_ids=target_object_ids,
                    field_path="/actor_object_ids",
                    ref_kind="event_actor",
                    target_type="entity",
                )
            except LookupError as error:
                raise _invalid_reference(str(error.args[0]), "entity") from error
            rows.registry.revision += 1
            self.session.flush()
            self.session.refresh(owned.draft)
            result = _find_document_object(self._document(owned)["events"], object_id)
        return result, revision

    def delete_entity(
        self, actor_user_id: int, project_id: int, object_id: str, base_revision: int
    ) -> int:
        return self._delete_object(
            actor_user_id, project_id, object_id, base_revision, "entity", "entities"
        )

    def delete_event(
        self, actor_user_id: int, project_id: int, object_id: str, base_revision: int
    ) -> int:
        return self._delete_object(
            actor_user_id, project_id, object_id, base_revision, "event", "events"
        )

    def create_snapshot(
        self, actor_user_id: int, project_id: int, base_revision: int
    ) -> tuple[dict[str, Any], bool]:
        with self.session.begin():
            owned = self._editable(actor_user_id, project_id, base_revision)
            document = self._document(owned)
            content_hash = casefile_content_hash(document)
            existing = self.snapshots.find_revision(owned.draft.id, owned.draft.revision)
            if existing is not None:
                if existing.content_hash != content_hash or existing.snapshot_jsonb != document:
                    raise ApplicationError(
                        "snapshot_content_mismatch",
                        "The existing Snapshot differs from the current Draft projection",
                        status_code=409,
                    )
                return _snapshot_view(existing, include_content=True), False
            snapshot = self.snapshots.create(
                owned,
                document=document,
                content_hash=content_hash,
                actor_user_id=actor_user_id,
            )
            return _snapshot_view(snapshot, include_content=True), True

    def list_snapshots(self, actor_user_id: int, project_id: int) -> list[dict[str, Any]]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            return [
                _snapshot_view(snapshot, include_content=False)
                for snapshot in self.snapshots.list(owned.draft.id)
            ]

    def get_snapshot(
        self, actor_user_id: int, project_id: int, snapshot_id: int
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            snapshot = self.snapshots.get(owned.draft.id, snapshot_id)
            if snapshot is None:
                raise not_found("Snapshot")
            return _snapshot_view(snapshot, include_content=True)

    def _delete_object(
        self,
        actor_user_id: int,
        project_id: int,
        object_id: str,
        base_revision: int,
        expected_type: str,
        collection: str,
    ) -> int:
        with self.session.begin():
            owned = self._editable(actor_user_id, project_id, base_revision)
            target = self.drafts.get_registry(owned, object_id)
            if target is None or target.object_type != expected_type:
                raise not_found(expected_type.title())
            inbound = self.drafts.inbound_references(owned, target)
            if inbound:
                raise ApplicationError(
                    "object_in_use",
                    "The object is still referenced by active objects",
                    status_code=409,
                    details={"references": inbound},
                )
            old_value = _find_document_object(self._document(owned)[collection], object_id)
            revision = self.drafts.add_operation(
                owned,
                registry=target,
                operation_type="remove",
                field_path=f"/{collection}/{object_id}",
                old_value=old_value,
                new_value=None,
                base_revision=base_revision,
                actor_user_id=actor_user_id,
            )
            self.drafts.soft_delete(owned, target)
            self.session.flush()
            return revision

    def _owned(
        self, actor_user_id: int, project_id: int, *, lock: bool = False
    ) -> OwnedDraft:
        owned = self.projects.get_owned(actor_user_id, project_id, lock=lock)
        if owned is None:
            raise not_found("Project")
        return owned

    def _editable(self, actor_user_id: int, project_id: int, base_revision: int) -> OwnedDraft:
        owned = self._owned(actor_user_id, project_id, lock=True)
        if owned.project.status == "archived" or owned.casefile.status == "archived":
            raise ApplicationError(
                "project_archived",
                "Archived projects cannot be modified",
                status_code=409,
            )
        if owned.draft.status != "active":
            raise ApplicationError(
                "draft_locked",
                "Locked Drafts cannot be modified",
                status_code=409,
            )
        if owned.draft.revision != base_revision:
            raise revision_conflict(expected=owned.draft.revision, received=base_revision)
        return owned

    def _entity(self, owned: OwnedDraft, object_id: str) -> EntityRows:
        rows = self.drafts.get_entity(owned, object_id)
        if rows is None:
            raise not_found("Entity")
        return rows

    def _event(self, owned: OwnedDraft, object_id: str) -> EventRows:
        rows = self.drafts.get_event(owned, object_id)
        if rows is None:
            raise not_found("Event")
        return rows

    def _write_entity_extension(
        self, owned: OwnedDraft, entity: Entity, command: EntityWrite
    ) -> None:
        if command.entity_kind == "person":
            self.session.add(
                Person(
                    project_id=owned.project.id,
                    casefile_id=owned.casefile.id,
                    draft_id=owned.draft.id,
                    entity_id=entity.id,
                    role=command.role,
                    background=command.background,
                )
            )
        else:
            self.session.add(
                Location(
                    project_id=owned.project.id,
                    casefile_id=owned.casefile.id,
                    draft_id=owned.draft.id,
                    entity_id=entity.id,
                    geo_jsonb=command.geo,
                    movement_rules_jsonb=command.movement_rules,
                )
            )

    def _update_entity_extension(self, rows: EntityRows, command: EntityWrite) -> None:
        if command.entity_kind == "person" and rows.person is not None:
            rows.person.role = command.role
            rows.person.background = command.background
        elif command.entity_kind == "location" and rows.location is not None:
            rows.location.geo_jsonb = command.geo
            rows.location.movement_rules_jsonb = command.movement_rules
        else:
            raise ApplicationError(
                "entity_extension_missing",
                "The persisted Entity subtype extension is missing",
                status_code=409,
            )

    def _resolve_event_relations(
        self, owned: OwnedDraft, command: EventWrite
    ) -> tuple[int | None, int | None]:
        try:
            phase_id = self.drafts.resolve_phase_id(
                owned, command.narrative_phase_object_id
            )
            location_id = self.drafts.resolve_location_id(owned, command.location_object_id)
        except LookupError as error:
            raise _invalid_reference(str(error.args[0]), "event relation") from error
        return phase_id, location_id

    def _document(self, owned: OwnedDraft) -> dict[str, Any]:
        try:
            return build_casefile_document(self.session, owned)
        except ContractValidationError as error:
            raise ApplicationError(
                "casefile_contract_invalid",
                "The current Draft cannot be projected to CaseFile 0.1.0",
                status_code=409,
                details={"errors": error.errors},
            ) from error


def _project_view(owned: OwnedDraft) -> dict[str, Any]:
    return {
        "id": owned.project.id,
        "title": owned.project.title,
        "description": owned.project.description,
        "profile": owned.project.profile_jsonb,
        "status": owned.project.status,
        "archived_at": owned.project.archived_at,
        "casefile_id": owned.casefile.id,
        "draft": {
            "id": owned.draft.id,
            "revision": owned.draft.revision,
            "schema_version": owned.draft.schema_version,
            "status": owned.draft.status,
        },
    }


def _draft_view(owned: OwnedDraft, document: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": owned.project.id,
        "casefile_id": owned.casefile.id,
        "draft_id": owned.draft.id,
        "revision": owned.draft.revision,
        "schema_version": owned.draft.schema_version,
        "status": owned.draft.status,
        "content": document,
    }


def _entity_command_document(
    object_id: str, command: EntityWrite, *, adjacent_ids: list[str] | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "object_id": object_id,
        "entity_kind": command.entity_kind,
        "name": command.name,
        "description": command.description,
        "traits": command.traits,
        "attributes": command.attributes,
        "source": {"kind": "user"},
        "confidence": command.confidence,
        "confirmation_status": "user_confirmed",
    }
    if command.entity_kind == "person":
        result["person"] = {"role": command.role, "background": command.background}
    else:
        result["location"] = {
            "geo": command.geo,
            "movement_rules": command.movement_rules,
            "adjacent_location_object_ids": adjacent_ids or [],
        }
    return result


def _event_command_document(
    object_id: str, command: EventWrite, actor_ids: list[str]
) -> dict[str, Any]:
    return {
        "object_id": object_id,
        "title": command.title,
        "summary": command.summary,
        "start_time": command.start_time,
        "end_time": command.end_time,
        "narrative_order": command.narrative_order,
        "narrative_phase_object_id": command.narrative_phase_object_id,
        "location_object_id": command.location_object_id,
        "actor_object_ids": actor_ids,
        "visibility": command.visibility,
        "truth_status": command.truth_status,
        "source": {"kind": "user"},
        "confidence": command.confidence,
        "confirmation_status": "user_confirmed",
    }


def _find_document_object(items: list[dict[str, Any]], object_id: str) -> dict[str, Any]:
    for item in items:
        if item["object_id"] == object_id:
            return item
    raise not_found("Object")


def _snapshot_view(snapshot: Any, *, include_content: bool) -> dict[str, Any]:
    result = {
        "id": snapshot.id,
        "draft_id": snapshot.draft_id,
        "revision": snapshot.snapshot_revision,
        "schema_version": snapshot.schema_version,
        "content_hash": snapshot.content_hash,
        "created_by_user_id": snapshot.created_by_user_id,
        "created_at": snapshot.created_at,
    }
    if include_content:
        result["content"] = snapshot.snapshot_jsonb
    return result


def _reject_duplicate_ids(object_ids: list[str]) -> None:
    if len(object_ids) != len(set(object_ids)):
        raise ApplicationError(
            "duplicate_reference",
            "Reference target IDs must be unique",
            status_code=422,
        )


def _invalid_reference(object_id: str, expected: str) -> ApplicationError:
    return ApplicationError(
        "invalid_reference",
        "A referenced object does not exist or has the wrong type",
        status_code=422,
        details={"object_id": object_id, "expected": expected},
    )


def _integrity_error(error: IntegrityError) -> ApplicationError:
    constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    if constraint == "uq_events_draft_narrative_order":
        return ApplicationError(
            "narrative_order_conflict",
            "Another Event already uses this narrative order",
            status_code=409,
        )
    return ApplicationError(
        "resource_conflict",
        "The requested change conflicts with current persisted state",
        status_code=409,
    )


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))
