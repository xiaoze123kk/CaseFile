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
    Text,
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


class SourceRecord(BigIntIdentityPrimaryKeyMixin, Base):
    """An immutable author or Agent source document in one Project."""

    __tablename__ = "source_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "parent_source_record_id"],
            ["source_records.project_id", "source_records.id"],
            name="fk_source_records_project_parent_source_records",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "generated_by_task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_source_records_project_generated_task_task_runs",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint("project_id", "id", name="uq_source_records_project_id_id"),
        CheckConstraint(
            "source_kind IN "
            "('human_original', 'agent_polish_proposal', 'human_revision')",
            name="source_kind_allowed",
        ),
        CheckConstraint("btrim(content_text) <> ''", name="content_not_blank"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        CheckConstraint(
            "("
            "source_kind = 'human_original' "
            "AND parent_source_record_id IS NULL "
            "AND generated_by_task_run_id IS NULL"
            ") OR ("
            "source_kind = 'human_revision' "
            "AND parent_source_record_id IS NOT NULL "
            "AND generated_by_task_run_id IS NULL"
            ") OR ("
            "source_kind = 'agent_polish_proposal' "
            "AND parent_source_record_id IS NOT NULL "
            "AND generated_by_task_run_id IS NOT NULL"
            ")",
            name="provenance_matches_kind",
        ),
        Index("ix_source_records_project_id_created_at", "project_id", "created_at"),
        Index("ix_source_records_parent_source_record_id", "parent_source_record_id"),
    )

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_source_record_id: Mapped[int | None] = mapped_column(BigInteger)
    generated_by_task_run_id: Mapped[int | None] = mapped_column(BigInteger)
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
            ["project_id", "input_source_record_id"],
            ["source_records.project_id", "source_records.id"],
            name="fk_task_runs_project_input_source_source_records",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "brief_intake_id"],
            ["brief_intakes.project_id", "brief_intakes.id"],
            name="fk_task_runs_project_brief_intake_brief_intakes",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["project_id", "brief_intake_id", "base_brief_intake_candidate_id"],
            [
                "brief_intake_candidates.project_id",
                "brief_intake_candidates.intake_id",
                "brief_intake_candidates.id",
            ],
            name="fk_task_runs_base_intake_candidate_candidates",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "provider_setting_id"],
            ["user_provider_settings.user_id", "user_provider_settings.id"],
            name="fk_task_runs_actor_provider_setting_user_provider_settings",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "agent_thread_id"],
            ["agent_threads.project_id", "agent_threads.id"],
            name="fk_task_runs_project_agent_thread_agent_threads",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "input_message_id"],
            ["agent_messages.project_id", "agent_messages.id"],
            name="fk_task_runs_project_input_message_agent_messages",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "output_message_id"],
            ["agent_messages.project_id", "agent_messages.id"],
            name="fk_task_runs_project_output_message_agent_messages",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("project_id", "id", name="uq_task_runs_project_id_id"),
        UniqueConstraint(
            "project_id", "brief_intake_id", "id", name="uq_task_runs_intake_lineage_id"
        ),
        CheckConstraint(
            "task_type IN "
            "('brief_polish', 'brief_anchor_extract', 'brief_intake_questions', "
            "'brief_intake_synthesize', 'brief_strategy_options', "
            "'brief_to_draft', 'casefile_chat')",
            name="task_type_allowed",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'cancelling', 'succeeded', 'failed', 'cancelled')",
            name="status_allowed",
        ),
        CheckConstraint("input_draft_revision >= 1", name="input_revision_positive"),
        CheckConstraint(
            "input_brief_revision IS NULL OR input_brief_revision >= 1",
            name="input_brief_revision_positive",
        ),
        CheckConstraint(
            "input_brief_intake_revision IS NULL OR input_brief_intake_revision >= 1",
            name="input_brief_intake_revision_positive",
        ),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="input_hash_format"),
        CheckConstraint("provider_config_version >= 1", name="provider_version_positive"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("jsonb_typeof(input_jsonb) = 'object'", name="input_is_object"),
        CheckConstraint("jsonb_typeof(budget_jsonb) = 'object'", name="budget_is_object"),
        CheckConstraint("jsonb_typeof(usage_jsonb) = 'object'", name="usage_is_object"),
        CheckConstraint(
            "result_jsonb IS NULL OR jsonb_typeof(result_jsonb) = 'object'",
            name="result_is_object",
        ),
        CheckConstraint(
            "jsonb_typeof(error_details_jsonb) = 'object'", name="error_details_is_object"
        ),
        CheckConstraint(
            "("
            "task_type = 'brief_polish' "
            "AND brief_version_id IS NULL "
            "AND input_source_record_id IS NOT NULL "
            "AND input_brief_revision IS NULL "
            "AND brief_intake_id IS NULL "
            "AND input_brief_intake_revision IS NULL "
            "AND base_brief_intake_candidate_id IS NULL "
            "AND agent_thread_id IS NULL "
            "AND input_message_id IS NULL "
            "AND output_message_id IS NULL"
            ") OR ("
            "task_type = 'brief_anchor_extract' "
            "AND brief_version_id IS NULL "
            "AND input_source_record_id IS NULL "
            "AND input_brief_revision IS NOT NULL "
            "AND brief_intake_id IS NULL "
            "AND input_brief_intake_revision IS NULL "
            "AND base_brief_intake_candidate_id IS NULL "
            "AND agent_thread_id IS NULL "
            "AND input_message_id IS NULL "
            "AND output_message_id IS NULL"
            ") OR ("
            "task_type = 'brief_intake_questions' "
            "AND brief_version_id IS NULL "
            "AND input_source_record_id IS NOT NULL "
            "AND input_brief_revision IS NULL "
            "AND brief_intake_id IS NOT NULL "
            "AND input_brief_intake_revision IS NOT NULL "
            "AND base_brief_intake_candidate_id IS NULL "
            "AND agent_thread_id IS NULL "
            "AND input_message_id IS NULL "
            "AND output_message_id IS NULL"
            ") OR ("
            "task_type = 'brief_intake_synthesize' "
            "AND brief_version_id IS NULL "
            "AND input_source_record_id IS NOT NULL "
            "AND input_brief_revision IS NULL "
            "AND brief_intake_id IS NOT NULL "
            "AND input_brief_intake_revision IS NOT NULL "
            "AND agent_thread_id IS NULL "
            "AND input_message_id IS NULL "
            "AND output_message_id IS NULL"
            ") OR ("
            "task_type = 'brief_strategy_options' "
            "AND brief_version_id IS NOT NULL "
            "AND input_source_record_id IS NULL "
            "AND input_brief_revision IS NOT NULL "
            "AND brief_intake_id IS NULL "
            "AND input_brief_intake_revision IS NULL "
            "AND base_brief_intake_candidate_id IS NULL "
            "AND agent_thread_id IS NULL "
            "AND input_message_id IS NULL "
            "AND output_message_id IS NULL"
            ") OR ("
            "task_type = 'brief_to_draft' "
            "AND brief_version_id IS NOT NULL "
            "AND input_source_record_id IS NULL "
            "AND input_brief_revision IS NOT NULL "
            "AND brief_intake_id IS NULL "
            "AND input_brief_intake_revision IS NULL "
            "AND base_brief_intake_candidate_id IS NULL "
            "AND agent_thread_id IS NULL "
            "AND input_message_id IS NULL "
            "AND output_message_id IS NULL"
            ") OR ("
            "task_type = 'casefile_chat' "
            "AND brief_version_id IS NULL "
            "AND input_source_record_id IS NULL "
            "AND input_brief_revision IS NULL "
            "AND brief_intake_id IS NULL "
            "AND input_brief_intake_revision IS NULL "
            "AND base_brief_intake_candidate_id IS NULL "
            "AND agent_thread_id IS NOT NULL "
            "AND input_message_id IS NOT NULL "
            "AND output_message_id IS NOT NULL"
            ")",
            name="input_matches_task_type",
        ),
        CheckConstraint(
            "(task_type = 'brief_to_draft') OR result_snapshot_id IS NULL",
            name="snapshot_matches_task_type",
        ),
        Index("ix_task_runs_status_created_at", "status", "created_at"),
        Index("ix_task_runs_project_id_updated_at", "project_id", "updated_at"),
        Index(
            "ix_task_runs_project_type_created_at",
            "project_id",
            "task_type",
            "created_at",
        ),
        Index("ix_task_runs_lease_expires_at", "lease_expires_at"),
        Index(
            "uq_task_runs_agent_thread_active",
            "agent_thread_id",
            unique=True,
            postgresql_where=text(
                "agent_thread_id IS NOT NULL "
                "AND status IN ('queued', 'running', 'cancelling')"
            ),
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    brief_version_id: Mapped[int | None] = mapped_column(BigInteger)
    input_source_record_id: Mapped[int | None] = mapped_column(BigInteger)
    input_brief_revision: Mapped[int | None] = mapped_column(Integer)
    brief_intake_id: Mapped[int | None] = mapped_column(BigInteger)
    input_brief_intake_revision: Mapped[int | None] = mapped_column(Integer)
    base_brief_intake_candidate_id: Mapped[int | None] = mapped_column(BigInteger)
    agent_thread_id: Mapped[int | None] = mapped_column(BigInteger)
    input_message_id: Mapped[int | None] = mapped_column(BigInteger)
    output_message_id: Mapped[int | None] = mapped_column(BigInteger)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
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
    result_jsonb: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
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
