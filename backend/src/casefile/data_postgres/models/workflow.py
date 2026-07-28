"""Versioned Brief and durable background-task persistence models."""

from __future__ import annotations

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

from casefile.data_postgres.base import Base, BigIntIdentityPrimaryKeyMixin, TimestampMixin


class Brief(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One project's mutable Brief draft and current confirmed-version pointer."""

    __tablename__ = "briefs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "id", "current_version_id"],
            ["brief_versions.project_id", "brief_versions.brief_id", "brief_versions.id"],
            name="fk_briefs_project_brief_current_version_brief_versions",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint("project_id", name="uq_briefs_project_id"),
        UniqueConstraint("project_id", "id", name="uq_briefs_project_id_id"),
        UniqueConstraint("public_id", name="uq_briefs_public_id"),
        CheckConstraint("draft_revision >= 1", name="draft_revision_positive"),
        CheckConstraint(
            "public_id ~ '^brief_[a-z0-9][a-z0-9_]{0,54}$'",
            name="public_id_format",
        ),
        CheckConstraint("jsonb_typeof(draft_jsonb) = 'object'", name="draft_is_object"),
    )

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_revision: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
    )
    draft_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    current_version_id: Mapped[int | None] = mapped_column(BigInteger)


class BriefVersion(BigIntIdentityPrimaryKeyMixin, Base):
    """An immutable user-confirmed Brief document."""

    __tablename__ = "brief_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "brief_id"],
            ["briefs.project_id", "briefs.id"],
            name="fk_brief_versions_project_brief_briefs",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("project_id", "id", name="uq_brief_versions_project_id_id"),
        UniqueConstraint("project_id", "brief_id", "id", name="uq_brief_versions_lineage_id"),
        UniqueConstraint("brief_id", "version_no", name="uq_brief_versions_brief_version_no"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint("jsonb_typeof(content_jsonb) = 'object'", name="content_is_object"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        Index("ix_brief_versions_brief_id_confirmed_at", "brief_id", "confirmed_at"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    brief_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
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


class TaskRun(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """A durable user intent whose attempts share frozen task configuration."""

    __tablename__ = "task_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_task_runs_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "brief_version_id"],
            ["brief_versions.project_id", "brief_versions.id"],
            name="fk_task_runs_project_brief_version_brief_versions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "provider_setting_id"],
            ["user_provider_settings.user_id", "user_provider_settings.id"],
            name="fk_task_runs_actor_provider_setting_user_provider_settings",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("project_id", "id", name="uq_task_runs_project_id_id"),
        CheckConstraint("task_type ~ '^[a-z][a-z0-9_]*$'", name="task_type_format"),
        CheckConstraint(
            "status IN ('queued', 'running', 'cancelling', 'succeeded', 'failed', 'cancelled')",
            name="status_allowed",
        ),
        CheckConstraint("input_draft_revision >= 1", name="input_revision_positive"),
        CheckConstraint("provider_config_version >= 1", name="provider_version_positive"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("jsonb_typeof(budget_jsonb) = 'object'", name="budget_is_object"),
        CheckConstraint("jsonb_typeof(usage_jsonb) = 'object'", name="usage_is_object"),
        CheckConstraint(
            "jsonb_typeof(error_details_jsonb) = 'object'", name="error_details_is_object"
        ),
        Index("ix_task_runs_status_created_at", "status", "created_at"),
        Index("ix_task_runs_project_id_updated_at", "project_id", "updated_at"),
        Index("ix_task_runs_lease_expires_at", "lease_expires_at"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    brief_version_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider_setting_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'queued'"),
    )
    stage: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'queued'"),
    )
    input_draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model_id: Mapped[str] = mapped_column(String(160), nullable=False)
    provider_config_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    toolset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    usage_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    leased_by: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("draft_snapshots.id", ondelete="RESTRICT"),
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_details_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


class TaskAttempt(BigIntIdentityPrimaryKeyMixin, Base):
    """One automatic or user-triggered execution attempt for a TaskRun."""

    __tablename__ = "task_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_task_attempts_project_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_run_id", "attempt_no", name="uq_task_attempts_run_attempt_no"),
        CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'cancelled')",
            name="status_allowed",
        ),
        CheckConstraint(
            "candidate_jsonb IS NULL OR jsonb_typeof(candidate_jsonb) = 'object'",
            name="candidate_is_object",
        ),
        CheckConstraint(
            "jsonb_typeof(validation_errors_jsonb) = 'array'",
            name="validation_errors_is_array",
        ),
        CheckConstraint("jsonb_typeof(usage_jsonb) = 'object'", name="usage_is_object"),
        CheckConstraint(
            "jsonb_typeof(error_details_jsonb) = 'object'", name="error_details_is_object"
        ),
        Index("ix_task_attempts_task_run_id_started_at", "task_run_id", "started_at"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    candidate_jsonb: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    validation_errors_jsonb: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    usage_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_details_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskEvent(BigIntIdentityPrimaryKeyMixin, Base):
    """An append-only, replayable event in a TaskRun's SSE stream."""

    __tablename__ = "task_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_task_events_project_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_run_id", "sequence_no", name="uq_task_events_run_sequence_no"),
        CheckConstraint("sequence_no >= 1", name="sequence_no_positive"),
        CheckConstraint("event_type ~ '^[a-z][a-z0-9_.]*$'", name="event_type_format"),
        CheckConstraint("jsonb_typeof(payload_jsonb) = 'object'", name="payload_is_object"),
        Index("ix_task_events_task_run_id_sequence_no", "task_run_id", "sequence_no"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(
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
