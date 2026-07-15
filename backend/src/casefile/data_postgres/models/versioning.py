"""Immutable snapshots, approvals, Canon versions, and audit events."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from casefile.data_postgres.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DraftSnapshot(UUIDPrimaryKeyMixin, Base):
    """An immutable full Draft snapshot consumed by tasks and approval."""

    __tablename__ = "draft_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "draft_id", "casefile_id"],
            ["drafts.workspace_id", "drafts.id", "drafts.casefile_id"],
            ondelete="CASCADE",
            name="fk_draft_snapshots_workspace_draft_casefile",
        ),
        UniqueConstraint(
            "workspace_id", "public_id", name="uq_draft_snapshots_workspace_public_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "draft_id",
            "snapshot_revision",
            name="uq_draft_snapshots_draft_revision",
        ),
        UniqueConstraint(
            "workspace_id", "id", "casefile_id", name="uq_draft_snapshots_workspace_id_casefile"
        ),
        CheckConstraint("public_id ~ '^snapshot_[0-9a-z_]+$'", name="public_id_format"),
        CheckConstraint("snapshot_revision >= 1", name="revision_positive"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
    )

    workspace_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    casefile_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    draft_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class Approval(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The explicit human decision that may authorize one Draft Snapshot."""

    __tablename__ = "approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "draft_snapshot_id", "casefile_id"],
            ["draft_snapshots.workspace_id", "draft_snapshots.id", "draft_snapshots.casefile_id"],
            ondelete="RESTRICT",
            name="fk_approvals_workspace_snapshot_casefile",
        ),
        UniqueConstraint("workspace_id", "public_id", name="uq_approvals_workspace_public_id"),
        UniqueConstraint(
            "workspace_id",
            "id",
            "casefile_id",
            "draft_snapshot_id",
            name="uq_approvals_workspace_id_casefile_snapshot",
        ),
        CheckConstraint("public_id ~ '^approval_[0-9a-z_]+$'", name="public_id_format"),
        CheckConstraint("approval_type = 'draft_to_canon'", name="type_allowed"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')", name="status_allowed"
        ),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "(status = 'pending' AND decided_by_actor_id IS NULL AND decided_at IS NULL) OR "
            "(status <> 'pending' AND decided_by_actor_id IS NOT NULL AND decided_at IS NOT NULL)",
            name="decision_fields_consistent",
        ),
        Index(
            "uq_approvals_one_approved_snapshot",
            "workspace_id",
            "draft_snapshot_id",
            unique=True,
            postgresql_where=text("status = 'approved'"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    casefile_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    draft_snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'draft_to_canon'")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'pending'")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    requested_by_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_by_actor_id: Mapped[str | None] = mapped_column(String(64))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CanonVersion(UUIDPrimaryKeyMixin, Base):
    """An immutable approved CaseFile version used by formal downstream runs."""

    __tablename__ = "canon_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "casefile_id"],
            ["casefiles.workspace_id", "casefiles.id"],
            ondelete="CASCADE",
            name="fk_canon_versions_workspace_casefile",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "parent_canon_version_id", "casefile_id"],
            ["canon_versions.workspace_id", "canon_versions.id", "canon_versions.casefile_id"],
            ondelete="RESTRICT",
            name="fk_canon_versions_workspace_parent_casefile",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "source_snapshot_id", "casefile_id"],
            ["draft_snapshots.workspace_id", "draft_snapshots.id", "draft_snapshots.casefile_id"],
            ondelete="RESTRICT",
            name="fk_canon_versions_workspace_snapshot_casefile",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "approval_id", "casefile_id", "source_snapshot_id"],
            [
                "approvals.workspace_id",
                "approvals.id",
                "approvals.casefile_id",
                "approvals.draft_snapshot_id",
            ],
            ondelete="RESTRICT",
            name="fk_canon_versions_workspace_approval_casefile_snapshot",
        ),
        UniqueConstraint(
            "workspace_id", "public_id", name="uq_canon_versions_workspace_public_id"
        ),
        UniqueConstraint(
            "workspace_id", "casefile_id", "version_no", name="uq_canon_versions_casefile_version"
        ),
        UniqueConstraint(
            "workspace_id", "id", "casefile_id", name="uq_canon_versions_workspace_id_casefile"
        ),
        UniqueConstraint("workspace_id", "approval_id", name="uq_canon_versions_approval"),
        UniqueConstraint(
            "workspace_id", "source_snapshot_id", name="uq_canon_versions_source_snapshot"
        ),
        CheckConstraint("public_id ~ '^cv_[0-9a-z_]+$'", name="public_id_format"),
        CheckConstraint("version_no >= 1", name="version_positive"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        CheckConstraint(
            "result_validity IN ('valid', 'possibly_invalid', 'invalid')",
            name="result_validity_allowed",
        ),
        Index(
            "ix_canon_versions_casefile_version_desc",
            "workspace_id",
            "casefile_id",
            text("version_no DESC"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    casefile_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    parent_canon_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    source_snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    approval_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    content_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_validity: Mapped[str] = mapped_column(String(24), nullable=False)
    approved_by_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    """An append-only record of a meaningful business or governance action."""

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("workspace_id", "public_id", name="uq_audit_events_workspace_public_id"),
        CheckConstraint("public_id ~ '^audit_[0-9a-z_]+$'", name="public_id_format"),
        Index("ix_audit_events_workspace_occurred", "workspace_id", "occurred_at"),
        Index(
            "ix_audit_events_entity",
            "workspace_id",
            "entity_type",
            "entity_public_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_public_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    trace_id: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
