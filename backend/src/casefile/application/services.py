"""Transactional application services for projects, Draft editing, and Snapshots."""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from casefile.application.commands import ProjectCreate
from casefile.application.errors import ApplicationError, not_found, revision_conflict
from casefile.application.snapshot import build_casefile_document, casefile_content_hash
from casefile.contracts import CASEFILE_SCHEMA_VERSION, ContractValidationError
from casefile.data_postgres.repositories import (
    OwnedDraft,
    ProjectRepository,
    SnapshotRepository,
)


class CaseFileService:
    """One-request service facade with explicit PostgreSQL transactions."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
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

    def unarchive_project(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            if owned.project.status == "archived":
                self.projects.unarchive(owned)
                self.session.flush()
            return _project_view(owned)

    def get_draft(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            if owned.draft.brief_version_id is None:
                return _draft_view(owned, None)
            return _draft_view(owned, self._document(owned))

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
                        "已有快照与当前草稿投影不一致。",
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

    def get_snapshot(self, actor_user_id: int, project_id: int, snapshot_id: int) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            snapshot = self.snapshots.get(owned.draft.id, snapshot_id)
            if snapshot is None:
                raise not_found("Snapshot")
            return _snapshot_view(snapshot, include_content=True)

    def _owned(self, actor_user_id: int, project_id: int, *, lock: bool = False) -> OwnedDraft:
        owned = self.projects.get_owned(actor_user_id, project_id, lock=lock)
        if owned is None:
            raise not_found("Project")
        return owned

    def _editable(self, actor_user_id: int, project_id: int, base_revision: int) -> OwnedDraft:
        owned = self._owned(actor_user_id, project_id, lock=True)
        if owned.project.status == "archived" or owned.casefile.status == "archived":
            raise ApplicationError(
                "project_archived",
                "已归档的项目不能修改。",
                status_code=409,
            )
        if owned.draft.status != "active":
            raise ApplicationError(
                "draft_locked",
                "已锁定的草稿不能修改。",
                status_code=409,
            )
        if owned.draft.revision != base_revision:
            raise revision_conflict(expected=owned.draft.revision, received=base_revision)
        return owned

    def _document(self, owned: OwnedDraft) -> dict[str, Any]:
        try:
            return build_casefile_document(self.session, owned)
        except ContractValidationError as error:
            raise ApplicationError(
                "casefile_contract_invalid",
                "当前草稿无法转换为 CaseFile 文档。",
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
        "created_at": owned.project.created_at,
        "updated_at": owned.project.updated_at,
        "casefile_id": owned.casefile.id,
        "draft": {
            "id": owned.draft.id,
            "revision": owned.draft.revision,
            "schema_version": owned.draft.schema_version,
            "status": owned.draft.status,
        },
    }


def _draft_view(owned: OwnedDraft, document: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "project_id": owned.project.id,
        "casefile_id": owned.casefile.id,
        "draft_id": owned.draft.id,
        "revision": owned.draft.revision,
        "schema_version": owned.draft.schema_version,
        "status": owned.draft.status,
        "content": document,
    }


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


def _integrity_error(error: IntegrityError) -> ApplicationError:
    constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
    return ApplicationError(
        "resource_conflict",
        "当前修改与已保存的数据冲突，请刷新后重试。",
        status_code=409,
        details={"constraint": constraint} if constraint else {},
    )
