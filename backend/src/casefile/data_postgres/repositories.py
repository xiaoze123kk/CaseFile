"""Aggregate-oriented repositories for the first personal-product write slice."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.data_postgres.models import (
    Brief,
    CaseFile,
    CaseFileObject,
    Draft,
    DraftOperation,
    DraftSnapshot,
    Project,
    User,
)


@dataclass(frozen=True, slots=True)
class OwnedDraft:
    """The single-owner aggregate roots required for a Draft transaction."""

    project: Project
    casefile: CaseFile
    draft: Draft


class ProjectRepository:
    """Persistence for the User → Project → CaseFile → Draft aggregate."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_active_user(self, user_id: int) -> User | None:
        return self.session.scalar(select(User).where(User.id == user_id, User.status == "active"))

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
            object_id=f"case_{secrets.token_hex(12)}",
            title=title,
            schema_version=schema_version,
            status="draft",
            # The deferred current-Draft foreign key permits the aggregate's two
            # rows to be inserted in one transaction before this pointer is fixed.
            current_draft_id=0,
        )
        self.session.add(casefile)
        self.session.flush()
        draft = Draft(
            project_id=project.id,
            casefile_id=casefile.id,
            revision=1,
            title=title,
            document_status="draft",
            version_id=f"draft_{secrets.token_hex(12)}",
            version_no=1,
            parent_version_id=None,
            schema_version=schema_version,
            status="active",
            content_notices_jsonb=[],
            extensions_jsonb={},
        )
        self.session.add(draft)
        self.session.flush()
        casefile.current_draft_id = draft.id
        self.session.add(
            Brief(
                project_id=project.id,
                public_id=f"brief_{secrets.token_hex(12)}",
                draft_revision=1,
                draft_jsonb={},
                current_version_id=None,
            )
        )
        self.session.flush()
        return OwnedDraft(project, casefile, draft)

    def list_owned(self, owner_user_id: int) -> list[OwnedDraft]:
        statement = (
            select(Project, CaseFile, Draft)
            .join(CaseFile, CaseFile.project_id == Project.id)
            .join(
                Draft,
                (Draft.project_id == Project.id)
                & (Draft.casefile_id == CaseFile.id)
                & (Draft.id == CaseFile.current_draft_id),
            )
            .where(Project.owner_user_id == owner_user_id)
            .order_by(Project.updated_at.desc(), Project.id.desc())
        )
        return [OwnedDraft(*row) for row in self.session.execute(statement).all()]

    def get_owned(
        self, owner_user_id: int, project_id: int, *, lock: bool = False
    ) -> OwnedDraft | None:
        if lock:
            aggregate_row = self.session.execute(
                select(Project, CaseFile)
                .join(CaseFile, CaseFile.project_id == Project.id)
                .where(Project.id == project_id, Project.owner_user_id == owner_user_id)
                .with_for_update(of=(Project, CaseFile))
            ).one_or_none()
            if aggregate_row is None:
                return None
            project, casefile = aggregate_row
            draft = self.session.scalar(
                select(Draft)
                .where(
                    Draft.project_id == project.id,
                    Draft.casefile_id == casefile.id,
                    Draft.id == casefile.current_draft_id,
                )
                .with_for_update()
            )
            return None if draft is None else OwnedDraft(project, casefile, draft)

        statement = (
            select(Project, CaseFile, Draft)
            .join(CaseFile, CaseFile.project_id == Project.id)
            .join(
                Draft,
                (Draft.project_id == Project.id)
                & (Draft.casefile_id == CaseFile.id)
                & (Draft.id == CaseFile.current_draft_id),
            )
            .where(Project.id == project_id, Project.owner_user_id == owner_user_id)
        )
        row = self.session.execute(statement).one_or_none()
        return None if row is None else OwnedDraft(*row)

    def get_owned_draft(
        self,
        owner_user_id: int,
        project_id: int,
        draft_id: int,
        *,
        lock: bool = False,
    ) -> OwnedDraft | None:
        statement = (
            select(Project, CaseFile, Draft)
            .join(CaseFile, CaseFile.project_id == Project.id)
            .join(
                Draft,
                (Draft.project_id == Project.id)
                & (Draft.casefile_id == CaseFile.id),
            )
            .where(
                Project.id == project_id,
                Project.owner_user_id == owner_user_id,
                Draft.id == draft_id,
            )
        )
        if lock:
            statement = statement.with_for_update(of=(Project, CaseFile, Draft))
        row = self.session.execute(statement).one_or_none()
        return None if row is None else OwnedDraft(*row)

    def list_drafts(self, owned: OwnedDraft) -> list[Draft]:
        return list(
            self.session.scalars(
                select(Draft)
                .where(
                    Draft.project_id == owned.project.id,
                    Draft.casefile_id == owned.casefile.id,
                )
                .order_by(
                    (Draft.id == owned.casefile.current_draft_id).desc(),
                    Draft.updated_at.desc(),
                    Draft.id.desc(),
                )
            )
        )

    def archive(self, owned: OwnedDraft) -> None:
        now = datetime.now(UTC)
        owned.project.status = "archived"
        owned.project.archived_at = now
        owned.casefile.status = "archived"
        owned.casefile.archived_at = now
        owned.draft.document_status = "archived"

    def unarchive(self, owned: OwnedDraft) -> None:
        owned.project.status = "active"
        owned.project.archived_at = None
        owned.casefile.status = "draft"
        owned.casefile.archived_at = None
        owned.draft.document_status = "draft"


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
