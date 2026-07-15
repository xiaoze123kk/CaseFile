"""Project, editable CaseFile, object, reference, and operation tables."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from casefile.data_postgres.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The workspace-owned project root used by project APIs and imports."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("workspace_id", "public_id", name="uq_projects_workspace_public_id"),
        UniqueConstraint("workspace_id", "id", name="uq_projects_workspace_id"),
        CheckConstraint("public_id ~ '^project_[0-9a-z_]+$'", name="public_id_format"),
        CheckConstraint("status IN ('active', 'archived')", name="status_allowed"),
        Index("ix_projects_workspace_status_updated", "workspace_id", "status", "updated_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'")
    )
    profile_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_by_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaseFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A project's structured source of truth across Draft and Canon states."""

    __tablename__ = "casefiles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            ondelete="CASCADE",
            name="fk_casefiles_workspace_project",
        ),
        UniqueConstraint("workspace_id", "public_id", name="uq_casefiles_workspace_public_id"),
        UniqueConstraint("workspace_id", "project_id", name="uq_casefiles_workspace_project"),
        UniqueConstraint("workspace_id", "id", name="uq_casefiles_workspace_id"),
        CheckConstraint("public_id ~ '^case_[0-9a-z_]+$'", name="public_id_format"),
        CheckConstraint("status IN ('draft', 'canon', 'archived')", name="status_allowed"),
        Index("ix_casefiles_workspace_status_updated", "workspace_id", "status", "updated_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'draft'")
    )
    schema_version: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'1.0'")
    )
    created_by_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Draft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The single mutable working copy for one CaseFile."""

    __tablename__ = "drafts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "casefile_id"],
            ["casefiles.workspace_id", "casefiles.id"],
            ondelete="CASCADE",
            name="fk_drafts_workspace_casefile",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "base_canon_version_id", "casefile_id"],
            ["canon_versions.workspace_id", "canon_versions.id", "canon_versions.casefile_id"],
            ondelete="RESTRICT",
            name="fk_drafts_workspace_base_canon_casefile",
            use_alter=True,
        ),
        UniqueConstraint("workspace_id", "public_id", name="uq_drafts_workspace_public_id"),
        UniqueConstraint("workspace_id", "casefile_id", name="uq_drafts_workspace_casefile"),
        UniqueConstraint("workspace_id", "id", name="uq_drafts_workspace_id"),
        UniqueConstraint(
            "workspace_id", "id", "casefile_id", name="uq_drafts_workspace_id_casefile"
        ),
        CheckConstraint("public_id ~ '^draft_[0-9a-z_]+$'", name="public_id_format"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("status IN ('active', 'locked')", name="status_allowed"),
    )

    workspace_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    casefile_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    base_canon_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    schema_version: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'1.0'")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'")
    )
    created_by_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)


class CaseFileObject(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The current JSON content for one stable object in a Draft."""

    __tablename__ = "casefile_objects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "draft_id", "casefile_id"],
            ["drafts.workspace_id", "drafts.id", "drafts.casefile_id"],
            ondelete="CASCADE",
            name="fk_casefile_objects_workspace_draft_casefile",
        ),
        UniqueConstraint(
            "workspace_id", "casefile_id", "object_id", name="uq_casefile_objects_stable_id"
        ),
        UniqueConstraint(
            "workspace_id", "draft_id", "id", name="uq_casefile_objects_workspace_draft_id"
        ),
        CheckConstraint("length(object_id) > 0", name="object_id_not_empty"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint(
            "confirmation_status IN ('user_confirmed', 'ai_inferred', 'unresolved')",
            name="confirmation_status_allowed",
        ),
        Index(
            "ix_casefile_objects_workspace_casefile_type_deleted",
            "workspace_id",
            "casefile_id",
            "object_type",
            "deleted_at",
        ),
        Index("ix_casefile_objects_payload_gin", "payload_jsonb", postgresql_using="gin"),
    )

    workspace_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    casefile_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    draft_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    source_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    confirmation_status: Mapped[str] = mapped_column(String(24), nullable=False)
    created_by_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaseFileRef(UUIDPrimaryKeyMixin, Base):
    """A rebuildable directional reference index derived from object JSON."""

    __tablename__ = "casefile_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "draft_id", "casefile_id"],
            ["drafts.workspace_id", "drafts.id", "drafts.casefile_id"],
            ondelete="CASCADE",
            name="fk_casefile_refs_workspace_draft_casefile",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "draft_id", "from_object_id"],
            ["casefile_objects.workspace_id", "casefile_objects.draft_id", "casefile_objects.id"],
            ondelete="CASCADE",
            name="fk_casefile_refs_workspace_draft_from_object",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "draft_id", "to_object_id"],
            ["casefile_objects.workspace_id", "casefile_objects.draft_id", "casefile_objects.id"],
            ondelete="CASCADE",
            name="fk_casefile_refs_workspace_draft_to_object",
        ),
        UniqueConstraint(
            "workspace_id",
            "draft_id",
            "from_object_id",
            "field_path",
            "to_object_id",
            "ref_kind",
            name="uq_casefile_refs_edge",
        ),
        CheckConstraint("length(field_path) > 0", name="field_path_not_empty"),
        Index(
            "ix_casefile_refs_outgoing",
            "workspace_id",
            "casefile_id",
            "from_object_id",
        ),
        Index(
            "ix_casefile_refs_incoming", "workspace_id", "casefile_id", "to_object_id"
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    casefile_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    draft_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    from_object_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    to_object_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    field_path: Mapped[str] = mapped_column(String(512), nullable=False)
    ref_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class DraftOperation(UUIDPrimaryKeyMixin, Base):
    """An immutable ordered JSON-style operation used for undo and traceability."""

    __tablename__ = "draft_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "draft_id"],
            ["drafts.workspace_id", "drafts.id"],
            ondelete="CASCADE",
            name="fk_draft_operations_workspace_draft",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "draft_id", "object_id"],
            ["casefile_objects.workspace_id", "casefile_objects.draft_id", "casefile_objects.id"],
            ondelete="RESTRICT",
            name="fk_draft_operations_workspace_draft_object",
        ),
        UniqueConstraint(
            "workspace_id", "draft_id", "sequence_no", name="uq_draft_operations_sequence"
        ),
        CheckConstraint("sequence_no >= 1", name="sequence_positive"),
        CheckConstraint("operation_type IN ('add', 'remove', 'replace')", name="type_allowed"),
        CheckConstraint("base_revision >= 1", name="base_revision_positive"),
        CheckConstraint("result_revision = base_revision + 1", name="revision_step"),
        Index("ix_draft_operations_draft_created", "workspace_id", "draft_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    draft_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    object_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    field_path: Mapped[str] = mapped_column(String(512), nullable=False)
    old_value_jsonb: Mapped[Any | None] = mapped_column(JSONB)
    new_value_jsonb: Mapped[Any | None] = mapped_column(JSONB)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    result_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
