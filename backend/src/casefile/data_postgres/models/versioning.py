"""Immutable Draft snapshot, Canon version, and audit-event models."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from casefile.data_postgres.base import Base, BigIntIdentityPrimaryKeyMixin


class DraftSnapshot(BigIntIdentityPrimaryKeyMixin, Base):
    """An immutable complete Draft document at one revision."""

    __tablename__ = "draft_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_draft_snapshots_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "draft_id",
            "snapshot_revision",
            name="uq_draft_snapshots_draft_id_snapshot_revision",
        ),
        UniqueConstraint(
            "project_id",
            "casefile_id",
            "id",
            name="uq_draft_snapshots_project_id_casefile_id_id",
        ),
        CheckConstraint("snapshot_revision >= 1", name="revision_positive"),
        CheckConstraint("length(btrim(schema_version)) > 0", name="schema_version_not_blank"),
        CheckConstraint("jsonb_typeof(snapshot_jsonb) = 'object'", name="content_is_object"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        Index(
            "ix_draft_snapshots_casefile_id_created_at",
            "casefile_id",
            "created_at",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    snapshot_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class CanonVersion(BigIntIdentityPrimaryKeyMixin, Base):
    """An immutable personal confirmation of one complete Draft snapshot."""

    __tablename__ = "canon_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id"],
            ["casefiles.project_id", "casefiles.id"],
            name="fk_canon_versions_project_id_casefile_id_casefiles",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "parent_canon_version_id"],
            [
                "canon_versions.project_id",
                "canon_versions.casefile_id",
                "canon_versions.id",
            ],
            name="fk_canon_versions_project_casefile_parent_canon_versions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "source_snapshot_id"],
            [
                "draft_snapshots.project_id",
                "draft_snapshots.casefile_id",
                "draft_snapshots.id",
            ],
            name="fk_canon_versions_project_casefile_source_snapshot_snapshots",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "casefile_id",
            "version_no",
            name="uq_canon_versions_casefile_id_version_no",
        ),
        UniqueConstraint(
            "project_id",
            "casefile_id",
            "id",
            name="uq_canon_versions_project_id_casefile_id_id",
        ),
        UniqueConstraint(
            "source_snapshot_id",
            name="uq_canon_versions_source_snapshot_id",
        ),
        CheckConstraint("version_no >= 1", name="version_positive"),
        CheckConstraint("length(btrim(schema_version)) > 0", name="schema_version_not_blank"),
        CheckConstraint("jsonb_typeof(content_jsonb) = 'object'", name="content_is_object"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        CheckConstraint(
            "(version_no = 1 AND parent_canon_version_id IS NULL) OR "
            "(version_no > 1 AND parent_canon_version_id IS NOT NULL)",
            name="parent_fields_consistent",
        ),
        Index(
            "ix_canon_versions_casefile_id_created_at",
            "casefile_id",
            "created_at",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parent_canon_version_id: Mapped[int | None] = mapped_column(BigInteger)
    source_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    content_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_by_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class AuditEvent(BigIntIdentityPrimaryKeyMixin, Base):
    """An append-only record of a meaningful action in a personal project."""

    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id"],
            ["casefiles.project_id", "casefiles.id"],
            name="fk_audit_events_project_id_casefile_id_casefiles",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(actor_kind = 'user' AND actor_user_id IS NOT NULL AND actor_ref IS NULL) OR "
            "(actor_kind IN ('agent', 'system', 'import') AND actor_user_id IS NULL "
            "AND actor_ref IS NOT NULL AND length(btrim(actor_ref)) > 0)",
            name="actor_shape",
        ),
        CheckConstraint("action ~ '^[a-z][a-z0-9_.]*$'", name="action_format"),
        CheckConstraint(
            "target_type ~ '^[a-z][a-z0-9_]*$'",
            name="target_type_format",
        ),
        CheckConstraint("target_id >= 1", name="target_id_positive"),
        CheckConstraint("jsonb_typeof(details_jsonb) = 'object'", name="details_is_object"),
        Index("ix_audit_events_project_id_occurred_at", "project_id", "occurred_at"),
        Index(
            "ix_audit_events_casefile_id_occurred_at",
            "casefile_id",
            "occurred_at",
        ),
        Index(
            "ix_audit_events_trace_id",
            "trace_id",
            postgresql_where=text("trace_id IS NOT NULL"),
        ),
    )

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    casefile_id: Mapped[int | None] = mapped_column(BigInteger)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
    )
    actor_ref: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(128))
    details_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
