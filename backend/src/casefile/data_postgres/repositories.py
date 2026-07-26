"""Aggregate-oriented repositories for the first personal-product write slice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, aliased

from casefile.data_postgres.models import (
    CaseFile,
    CaseFileConstraint,
    CaseFileObject,
    CaseFileRef,
    Draft,
    DraftOperation,
    DraftSnapshot,
    Entity,
    Event,
    EvidenceItem,
    InformationUnit,
    KnowledgeState,
    KnowledgeStateEntry,
    Location,
    Person,
    Project,
    ReasoningNode,
    ReasoningPath,
    Testimony,
    User,
)


@dataclass(frozen=True, slots=True)
class OwnedDraft:
    """The single-owner aggregate roots required for a Draft transaction."""

    project: Project
    casefile: CaseFile
    draft: Draft


@dataclass(frozen=True, slots=True)
class EntityRows:
    registry: CaseFileObject
    entity: Entity
    person: Person | None
    location: Location | None


@dataclass(frozen=True, slots=True)
class EventRows:
    registry: CaseFileObject
    event: Event


class ProjectRepository:
    """Persistence for the User → Project → CaseFile → Draft aggregate."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_active_user(self, user_id: int) -> User | None:
        return self.session.scalar(
            select(User).where(User.id == user_id, User.status == "active")
        )

    def create(
        self,
        *,
        owner_user_id: int,
        title: str,
        description: str | None,
        profile: dict[str, Any],
        schema_version: str,
    ) -> OwnedDraft:
        project = Project(
            owner_user_id=owner_user_id,
            title=title,
            description=description,
            profile_jsonb=profile,
            status="active",
        )
        self.session.add(project)
        self.session.flush()
        casefile = CaseFile(
            project_id=project.id,
            title=title,
            schema_version=schema_version,
            status="draft",
        )
        self.session.add(casefile)
        self.session.flush()
        draft = Draft(
            project_id=project.id,
            casefile_id=casefile.id,
            revision=1,
            schema_version=schema_version,
            status="active",
        )
        self.session.add(draft)
        self.session.flush()
        return OwnedDraft(project, casefile, draft)

    def list_owned(self, owner_user_id: int) -> list[OwnedDraft]:
        statement = (
            select(Project, CaseFile, Draft)
            .join(CaseFile, CaseFile.project_id == Project.id)
            .join(
                Draft,
                (Draft.project_id == Project.id) & (Draft.casefile_id == CaseFile.id),
            )
            .where(Project.owner_user_id == owner_user_id)
            .order_by(Project.updated_at.desc(), Project.id.desc())
        )
        return [OwnedDraft(*row) for row in self.session.execute(statement).all()]

    def get_owned(
        self, owner_user_id: int, project_id: int, *, lock: bool = False
    ) -> OwnedDraft | None:
        statement = (
            select(Project, CaseFile, Draft)
            .join(CaseFile, CaseFile.project_id == Project.id)
            .join(
                Draft,
                (Draft.project_id == Project.id) & (Draft.casefile_id == CaseFile.id),
            )
            .where(Project.id == project_id, Project.owner_user_id == owner_user_id)
        )
        if lock:
            statement = statement.with_for_update(of=(Project, CaseFile, Draft))
        row = self.session.execute(statement).one_or_none()
        return None if row is None else OwnedDraft(*row)

    def archive(self, owned: OwnedDraft) -> None:
        now = datetime.now(UTC)
        owned.project.status = "archived"
        owned.project.archived_at = now
        owned.casefile.status = "archived"
        owned.casefile.archived_at = now


class DraftRepository:
    """Current-state object, semantic-reference, and edit-log persistence."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_registry(
        self, owned: OwnedDraft, object_id: str, *, include_deleted: bool = False
    ) -> CaseFileObject | None:
        conditions = [
            CaseFileObject.project_id == owned.project.id,
            CaseFileObject.casefile_id == owned.casefile.id,
            CaseFileObject.draft_id == owned.draft.id,
            CaseFileObject.object_id == object_id,
        ]
        if not include_deleted:
            conditions.append(CaseFileObject.deleted_at.is_(None))
        return self.session.scalar(select(CaseFileObject).where(*conditions))

    def get_entity(self, owned: OwnedDraft, object_id: str) -> EntityRows | None:
        registry = self.get_registry(owned, object_id)
        if registry is None or registry.object_type != "entity":
            return None
        entity = self.session.scalar(
            select(Entity).where(
                Entity.draft_id == owned.draft.id,
                Entity.object_registry_id == registry.id,
            )
        )
        if entity is None:
            return None
        person = self.session.scalar(select(Person).where(Person.entity_id == entity.id))
        location = self.session.scalar(select(Location).where(Location.entity_id == entity.id))
        return EntityRows(registry, entity, person, location)

    def list_entities(self, owned: OwnedDraft) -> list[EntityRows]:
        rows = self.session.execute(
            select(CaseFileObject, Entity)
            .join(Entity, Entity.object_registry_id == CaseFileObject.id)
            .where(
                CaseFileObject.draft_id == owned.draft.id,
                CaseFileObject.deleted_at.is_(None),
            )
            .order_by(CaseFileObject.object_id)
        ).all()
        result: list[EntityRows] = []
        for registry, entity in rows:
            person = self.session.scalar(select(Person).where(Person.entity_id == entity.id))
            location = self.session.scalar(select(Location).where(Location.entity_id == entity.id))
            result.append(EntityRows(registry, entity, person, location))
        return result

    def get_event(self, owned: OwnedDraft, object_id: str) -> EventRows | None:
        registry = self.get_registry(owned, object_id)
        if registry is None or registry.object_type != "event":
            return None
        event = self.session.scalar(
            select(Event).where(
                Event.draft_id == owned.draft.id,
                Event.object_registry_id == registry.id,
            )
        )
        return None if event is None else EventRows(registry, event)

    def list_events(self, owned: OwnedDraft) -> list[EventRows]:
        rows = self.session.execute(
            select(CaseFileObject, Event)
            .join(Event, Event.object_registry_id == CaseFileObject.id)
            .where(
                CaseFileObject.draft_id == owned.draft.id,
                CaseFileObject.deleted_at.is_(None),
            )
            .order_by(Event.narrative_order, CaseFileObject.object_id)
        ).all()
        return [EventRows(registry, event) for registry, event in rows]

    def add_registry(
        self,
        owned: OwnedDraft,
        *,
        object_id: str,
        object_type: str,
        confidence: float | None,
    ) -> CaseFileObject:
        registry = CaseFileObject(
            project_id=owned.project.id,
            casefile_id=owned.casefile.id,
            draft_id=owned.draft.id,
            object_id=object_id,
            object_type=object_type,
            revision=1,
            source_jsonb={"kind": "user"},
            confidence=confidence,
            confirmation_status="user_confirmed",
        )
        self.session.add(registry)
        self.session.flush()
        return registry

    def add_operation(
        self,
        owned: OwnedDraft,
        *,
        registry: CaseFileObject,
        operation_type: str,
        field_path: str,
        old_value: Any,
        new_value: Any,
        base_revision: int,
        actor_user_id: int,
    ) -> int:
        sequence = int(
            self.session.scalar(
                select(func.coalesce(func.max(DraftOperation.sequence_no), 0) + 1).where(
                    DraftOperation.draft_id == owned.draft.id
                )
            )
            or 1
        )
        operation = DraftOperation(
            project_id=owned.project.id,
            casefile_id=owned.casefile.id,
            draft_id=owned.draft.id,
            casefile_object_id=registry.id,
            sequence_no=sequence,
            operation_group_no=sequence,
            operation_type=operation_type,
            field_path=field_path,
            old_value_jsonb=old_value,
            new_value_jsonb=new_value,
            base_revision=base_revision,
            result_revision=base_revision + 1,
            actor_kind="user",
            actor_user_id=actor_user_id,
        )
        self.session.add(operation)
        self.session.flush()
        return base_revision + 1

    def replace_references(
        self,
        owned: OwnedDraft,
        *,
        source: CaseFileObject,
        target_object_ids: list[str],
        field_path: str,
        ref_kind: str,
        target_type: str,
        target_subtype: tuple[str, str] | None = None,
    ) -> None:
        targets: list[CaseFileObject] = []
        for object_id in target_object_ids:
            target = self.get_registry(owned, object_id)
            if target is None or target.object_type != target_type:
                raise LookupError(object_id)
            if target_subtype is not None:
                entity = self.session.scalar(
                    select(Entity).where(Entity.object_registry_id == target.id)
                )
                if entity is None or getattr(entity, target_subtype[0]) != target_subtype[1]:
                    raise LookupError(object_id)
            targets.append(target)

        self.session.execute(
            delete(CaseFileRef).where(
                CaseFileRef.draft_id == owned.draft.id,
                CaseFileRef.from_object_id == source.id,
                CaseFileRef.field_path == field_path,
                CaseFileRef.ref_kind == ref_kind,
            )
        )
        for ordinal, target in enumerate(targets, start=1):
            self.session.add(
                CaseFileRef(
                    project_id=owned.project.id,
                    casefile_id=owned.casefile.id,
                    draft_id=owned.draft.id,
                    from_object_id=source.id,
                    to_object_id=target.id,
                    field_path=field_path,
                    ref_kind=ref_kind,
                    ordinal=ordinal,
                    metadata_jsonb={},
                )
            )

    def reference_ids(
        self, owned: OwnedDraft, source: CaseFileObject, *, field_path: str, ref_kind: str
    ) -> list[str]:
        target = aliased(CaseFileObject)
        return list(
            self.session.scalars(
                select(target.object_id)
                .join(CaseFileRef, CaseFileRef.to_object_id == target.id)
                .where(
                    CaseFileRef.draft_id == owned.draft.id,
                    CaseFileRef.from_object_id == source.id,
                    CaseFileRef.field_path == field_path,
                    CaseFileRef.ref_kind == ref_kind,
                    target.deleted_at.is_(None),
                )
                .order_by(CaseFileRef.ordinal)
            )
        )

    def resolve_phase_id(self, owned: OwnedDraft, object_id: str | None) -> int | None:
        if object_id is None:
            return None
        registry = self.get_registry(owned, object_id)
        if registry is None or registry.object_type != "narrative_phase":
            raise LookupError(object_id)
        from casefile.data_postgres.models import NarrativePhase

        phase_id = self.session.scalar(
            select(NarrativePhase.id).where(NarrativePhase.object_registry_id == registry.id)
        )
        if phase_id is None:
            raise LookupError(object_id)
        return phase_id

    def resolve_location_id(self, owned: OwnedDraft, object_id: str | None) -> int | None:
        if object_id is None:
            return None
        rows = self.get_entity(owned, object_id)
        if rows is None or rows.entity.entity_kind != "location" or rows.location is None:
            raise LookupError(object_id)
        return rows.location.id

    def inbound_references(self, owned: OwnedDraft, target: CaseFileObject) -> list[dict[str, str]]:
        references: list[dict[str, str]] = []
        source = aliased(CaseFileObject)
        for source_id, field_path in self.session.execute(
            select(source.object_id, CaseFileRef.field_path)
            .join(CaseFileRef, CaseFileRef.from_object_id == source.id)
            .where(
                CaseFileRef.draft_id == owned.draft.id,
                CaseFileRef.to_object_id == target.id,
                source.deleted_at.is_(None),
            )
        ):
            references.append({"source_object_id": source_id, "field_path": field_path})

        references.extend(self._single_value_inbound(owned, target))
        return sorted(references, key=lambda item: (item["source_object_id"], item["field_path"]))

    def _single_value_inbound(
        self, owned: OwnedDraft, target: CaseFileObject
    ) -> list[dict[str, str]]:
        references: list[dict[str, str]] = []
        if target.object_type == "entity":
            entity = self.session.scalar(
                select(Entity).where(Entity.object_registry_id == target.id)
            )
            if entity is not None:
                references.extend(self._entity_inbound(owned, entity))
        elif target.object_type == "event":
            event = self.session.scalar(select(Event).where(Event.object_registry_id == target.id))
            if event is not None:
                references.extend(self._event_inbound(owned, event))

        path_registry = aliased(CaseFileObject)
        for source_id in self.session.scalars(
            select(path_registry.object_id)
            .join(ReasoningPath, ReasoningPath.object_registry_id == path_registry.id)
            .join(ReasoningNode, ReasoningNode.reasoning_path_id == ReasoningPath.id)
            .where(
                ReasoningNode.draft_id == owned.draft.id,
                ReasoningNode.source_object_id == target.id,
                path_registry.deleted_at.is_(None),
            )
        ):
            references.append(
                {"source_object_id": source_id, "field_path": "/nodes/source_object_id"}
            )

        constraint_registry = aliased(CaseFileObject)
        for source_id in self.session.scalars(
            select(constraint_registry.object_id)
            .join(
                CaseFileConstraint,
                CaseFileConstraint.object_registry_id == constraint_registry.id,
            )
            .where(
                CaseFileConstraint.draft_id == owned.draft.id,
                CaseFileConstraint.target_object_id == target.id,
                constraint_registry.deleted_at.is_(None),
            )
        ):
            references.append({"source_object_id": source_id, "field_path": "/target_object_id"})

        state_registry = aliased(CaseFileObject)
        for source_id in self.session.scalars(
            select(state_registry.object_id)
            .join(KnowledgeState, KnowledgeState.object_registry_id == state_registry.id)
            .join(KnowledgeStateEntry, KnowledgeStateEntry.knowledge_state_id == KnowledgeState.id)
            .where(
                KnowledgeStateEntry.draft_id == owned.draft.id,
                KnowledgeStateEntry.acquired_from_object_id == target.id,
                state_registry.deleted_at.is_(None),
            )
        ):
            references.append(
                {"source_object_id": source_id, "field_path": "/entries/acquired_from_object_id"}
            )
        return references

    def _entity_inbound(self, owned: OwnedDraft, entity: Entity) -> list[dict[str, str]]:
        references: list[dict[str, str]] = []
        state_registry = aliased(CaseFileObject)
        for source_id in self.session.scalars(
            select(state_registry.object_id)
            .join(KnowledgeState, KnowledgeState.object_registry_id == state_registry.id)
            .where(
                KnowledgeState.draft_id == owned.draft.id,
                KnowledgeState.entity_id == entity.id,
                state_registry.deleted_at.is_(None),
            )
        ):
            references.append({"source_object_id": source_id, "field_path": "/entity_object_id"})

        if entity.entity_kind == "location":
            location = self.session.scalar(select(Location).where(Location.entity_id == entity.id))
            if location is not None:
                event_registry = aliased(CaseFileObject)
                for source_id in self.session.scalars(
                    select(event_registry.object_id)
                    .join(Event, Event.object_registry_id == event_registry.id)
                    .where(
                        Event.draft_id == owned.draft.id,
                        Event.location_id == location.id,
                        event_registry.deleted_at.is_(None),
                    )
                ):
                    references.append(
                        {"source_object_id": source_id, "field_path": "/location_object_id"}
                    )
        if entity.entity_kind == "person":
            person = self.session.scalar(select(Person).where(Person.entity_id == entity.id))
            if person is not None:
                info_registry = aliased(CaseFileObject)
                for source_id in self.session.scalars(
                    select(info_registry.object_id)
                    .join(InformationUnit, InformationUnit.object_registry_id == info_registry.id)
                    .join(Testimony, Testimony.information_unit_id == InformationUnit.id)
                    .where(
                        Testimony.draft_id == owned.draft.id,
                        Testimony.speaker_person_id == person.id,
                        info_registry.deleted_at.is_(None),
                    )
                ):
                    references.append(
                        {
                            "source_object_id": source_id,
                            "field_path": "/testimony/speaker_person_object_id",
                        }
                    )
        return references

    def _event_inbound(self, owned: OwnedDraft, event: Event) -> list[dict[str, str]]:
        info_registry = aliased(CaseFileObject)
        return [
            {"source_object_id": source_id, "field_path": "/evidence/source_event_object_id"}
            for source_id in self.session.scalars(
                select(info_registry.object_id)
                .join(InformationUnit, InformationUnit.object_registry_id == info_registry.id)
                .join(EvidenceItem, EvidenceItem.information_unit_id == InformationUnit.id)
                .where(
                    EvidenceItem.draft_id == owned.draft.id,
                    EvidenceItem.source_event_id == event.id,
                    info_registry.deleted_at.is_(None),
                )
            )
        ]

    def soft_delete(self, owned: OwnedDraft, target: CaseFileObject) -> None:
        self.session.execute(
            delete(CaseFileRef).where(
                CaseFileRef.draft_id == owned.draft.id,
                CaseFileRef.from_object_id == target.id,
            )
        )
        if target.object_type == "event":
            event = self.session.scalar(
                select(Event).where(Event.object_registry_id == target.id)
            )
            if event is not None:
                event.narrative_phase_id = None
                event.location_id = None
        target.deleted_at = datetime.now(UTC)
        target.revision += 1


class SnapshotRepository:
    """Append-only Snapshot persistence scoped through an owned Draft."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def find_revision(self, draft_id: int, revision: int) -> DraftSnapshot | None:
        return self.session.scalar(
            select(DraftSnapshot).where(
                DraftSnapshot.draft_id == draft_id,
                DraftSnapshot.snapshot_revision == revision,
            )
        )

    def create(
        self,
        owned: OwnedDraft,
        *,
        document: dict[str, Any],
        content_hash: str,
        actor_user_id: int,
    ) -> DraftSnapshot:
        snapshot = DraftSnapshot(
            project_id=owned.project.id,
            casefile_id=owned.casefile.id,
            draft_id=owned.draft.id,
            snapshot_revision=owned.draft.revision,
            schema_version=owned.draft.schema_version,
            snapshot_jsonb=document,
            content_hash=content_hash,
            created_by_user_id=actor_user_id,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def list(self, draft_id: int) -> list[DraftSnapshot]:
        return list(
            self.session.scalars(
                select(DraftSnapshot)
                .where(DraftSnapshot.draft_id == draft_id)
                .order_by(DraftSnapshot.snapshot_revision.desc())
            )
        )

    def get(self, draft_id: int, snapshot_id: int) -> DraftSnapshot | None:
        return self.session.scalar(
            select(DraftSnapshot).where(
                DraftSnapshot.draft_id == draft_id,
                DraftSnapshot.id == snapshot_id,
            )
        )
