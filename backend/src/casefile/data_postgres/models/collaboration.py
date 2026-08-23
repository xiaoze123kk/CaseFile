"""Persistent Agent threads, messages, and reviewable Draft patch batches."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class AgentThread(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One resumable Agent conversation over the latest state of a CaseFile."""

    __tablename__ = "agent_threads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_agent_threads_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("project_id", "id", name="uq_agent_threads_project_id_id"),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("title_source IN ('auto', 'user')", name="title_source_allowed"),
        CheckConstraint("status IN ('active', 'archived')", name="status_allowed"),
        CheckConstraint(
            "(status = 'active' AND archived_at IS NULL) OR "
            "(status = 'archived' AND archived_at IS NOT NULL)",
            name="archive_shape",
        ),
        Index(
            "ix_agent_threads_project_status_updated_at",
            "project_id",
            "status",
            "updated_at",
        ),
        Index(
            "ix_agent_threads_project_pinned_updated_at",
            "project_id",
            "is_pinned",
            "updated_at",
        ),
    )

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    title_source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'auto'"),
    )
    is_pinned: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'active'"),
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentMessage(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One ordered user, assistant, or system entry in an Agent thread."""

    __tablename__ = "agent_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "thread_id"],
            ["agent_threads.project_id", "agent_threads.id"],
            name="fk_agent_messages_project_thread_agent_threads",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("project_id", "id", name="uq_agent_messages_project_id_id"),
        UniqueConstraint(
            "thread_id",
            "sequence_no",
            name="uq_agent_messages_thread_sequence_no",
        ),
        CheckConstraint("sequence_no >= 1", name="sequence_no_positive"),
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="role_allowed"),
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="status_allowed",
        ),
        CheckConstraint(
            "(role = 'user' AND created_by_user_id IS NOT NULL) OR "
            "(role IN ('assistant', 'system') AND created_by_user_id IS NULL)",
            name="actor_shape",
        ),
        CheckConstraint(
            "(status = 'pending' AND role = 'assistant' AND content_text IS NULL) OR "
            "(status = 'failed' AND role = 'assistant') OR "
            "(status = 'completed' AND content_text IS NOT NULL "
            "AND length(btrim(content_text)) > 0)",
            name="content_shape",
        ),
        Index(
            "ix_agent_messages_thread_sequence_no",
            "thread_id",
            "sequence_no",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
    )


class AgentPatchSet(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One assistant-produced suggestion batch reviewed and applied atomically."""

    __tablename__ = "agent_patch_sets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_agent_patch_sets_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "thread_id"],
            ["agent_threads.project_id", "agent_threads.id"],
            name="fk_agent_patch_sets_project_thread_agent_threads",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "source_message_id"],
            ["agent_messages.project_id", "agent_messages.id"],
            name="fk_agent_patch_sets_project_source_message_agent_messages",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_agent_patch_sets_project_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("project_id", "id", name="uq_agent_patch_sets_project_id_id"),
        UniqueConstraint("task_run_id", name="uq_agent_patch_sets_task_run_id"),
        CheckConstraint("base_draft_revision >= 1", name="base_revision_positive"),
        CheckConstraint(
            "mutation_mode IN ('normal', 'restructure')",
            name="mutation_mode_allowed",
        ),
        CheckConstraint(
            "length(btrim(closure_policy_version)) > 0",
            name="closure_policy_version_not_blank",
        ),
        CheckConstraint(
            "baseline_hash IS NULL OR baseline_hash ~ '^[0-9a-f]{64}$'",
            name="baseline_hash_format",
        ),
        CheckConstraint(
            "candidate_hash IS NULL OR candidate_hash ~ '^[0-9a-f]{64}$'",
            name="candidate_hash_format",
        ),
        CheckConstraint(
            "status IN ('pending', 'stale', 'applied', 'undone', 'rejected')",
            name="status_allowed",
        ),
        CheckConstraint("length(btrim(reason_summary)) > 0", name="reason_not_blank"),
        CheckConstraint(
            "applied_from_revision IS NULL OR applied_from_revision >= 1",
            name="applied_from_revision_positive",
        ),
        CheckConstraint(
            "applied_to_revision IS NULL OR applied_to_revision = applied_from_revision + 1",
            name="applied_revision_step",
        ),
        CheckConstraint(
            "undone_to_revision IS NULL OR undone_to_revision = applied_to_revision + 1",
            name="undone_revision_step",
        ),
        CheckConstraint(
            "(status IN ('pending', 'stale', 'rejected') "
            "AND applied_operation_group_no IS NULL "
            "AND applied_from_revision IS NULL AND applied_to_revision IS NULL "
            "AND applied_at IS NULL AND undone_operation_group_no IS NULL "
            "AND undone_to_revision IS NULL AND undone_at IS NULL) OR "
            "(status = 'applied' AND applied_operation_group_no IS NOT NULL "
            "AND applied_from_revision IS NOT NULL AND applied_to_revision IS NOT NULL "
            "AND applied_at IS NOT NULL AND undone_operation_group_no IS NULL "
            "AND undone_to_revision IS NULL AND undone_at IS NULL) OR "
            "(status = 'undone' AND applied_operation_group_no IS NOT NULL "
            "AND applied_from_revision IS NOT NULL AND applied_to_revision IS NOT NULL "
            "AND applied_at IS NOT NULL AND undone_operation_group_no IS NOT NULL "
            "AND undone_to_revision IS NOT NULL AND undone_at IS NOT NULL)",
            name="lifecycle_shape",
        ),
        Index(
            "ix_agent_patch_sets_project_status_created_at",
            "project_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_agent_patch_sets_source_message_id",
            "source_message_id",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    closure_policy_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=text("'logical-mutation-v1'"),
    )
    mutation_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'normal'"),
    )
    baseline_hash: Mapped[str | None] = mapped_column(String(64))
    candidate_hash: Mapped[str | None] = mapped_column(String(64))
    reason_summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
    )
    applied_operation_group_no: Mapped[int | None] = mapped_column(BigInteger)
    applied_from_revision: Mapped[int | None] = mapped_column(Integer)
    applied_to_revision: Mapped[int | None] = mapped_column(Integer)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    undone_operation_group_no: Mapped[int | None] = mapped_column(BigInteger)
    undone_to_revision: Mapped[int | None] = mapped_column(Integer)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentPatchOperation(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One field-level operation inside a reviewable Agent patch set."""

    __tablename__ = "agent_patch_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "patch_set_id"],
            ["agent_patch_sets.project_id", "agent_patch_sets.id"],
            name="fk_agent_patch_operations_project_patch_set_agent_patch_sets",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "target_object_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_agent_patch_operations_target_object",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "patch_set_id",
            "ordinal",
            name="uq_agent_patch_operations_patch_set_ordinal",
        ),
        UniqueConstraint(
            "patch_set_id",
            "operation_id",
            name="uq_agent_patch_operations_patch_set_operation_id",
        ),
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_agent_patch_operations_project_id_id",
        ),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint(
            "operation_id ~ '^op_[a-z0-9][a-z0-9_]{0,57}$'",
            name="operation_id_format",
        ),
        CheckConstraint(
            "operation_type IN "
            "('add', 'remove', 'replace', 'create_object', 'update_field', 'delete_object')",
            name="operation_type_allowed",
        ),
        CheckConstraint(
            "(operation_type IN ('create_object', 'delete_object') AND field_path = '') OR "
            "(operation_type NOT IN ('create_object', 'delete_object') AND field_path ~ '^/')",
            name="field_path_shape",
        ),
        CheckConstraint(
            "(operation_type = 'create_object' AND target_object_id IS NULL) OR "
            "(operation_type <> 'create_object' AND target_object_id IS NOT NULL)",
            name="target_object_shape",
        ),
        CheckConstraint(
            "length(btrim(target_object_key)) > 0",
            name="target_object_key_not_blank",
        ),
        CheckConstraint(
            "target_collection IN "
            "('resolution_specs', 'entities', 'relationships', 'locations', 'events', "
            "'information_units', 'claims', 'hypotheses', 'reasoning_paths', "
            "'constraints', 'structure_locks')",
            name="target_collection_allowed",
        ),
        CheckConstraint(
            "expected_object_revision IS NULL OR expected_object_revision >= 1",
            name="expected_revision_positive",
        ),
        CheckConstraint(
            "decision IN ('pending', 'accepted', 'rejected')",
            name="decision_allowed",
        ),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_not_blank"),
        CheckConstraint(
            "origin IN ('primary', 'closure_repair')",
            name="origin_allowed",
        ),
        CheckConstraint(
            "repair_round IS NULL OR repair_round BETWEEN 1 AND 2",
            name="repair_round_allowed",
        ),
        CheckConstraint(
            "jsonb_typeof(repair_obligation_keys) = 'array'",
            name="repair_obligation_keys_array",
        ),
        CheckConstraint(
            "NOT jsonb_path_exists(repair_obligation_keys, "
            '\'$[*] ? (@.type() != "string" || @ like_regex "^\\\\s*$")\')',
            name="repair_obligation_keys_valid",
        ),
        CheckConstraint(
            "(origin = 'primary' AND repair_round IS NULL "
            "AND repair_obligation_keys = '[]'::jsonb) OR "
            "(origin = 'closure_repair' AND repair_round IS NOT NULL "
            "AND jsonb_array_length(repair_obligation_keys) > 0)",
            name="repair_provenance_shape",
        ),
        CheckConstraint(
            "(decision = 'pending' AND reviewed_at IS NULL) OR "
            "(decision IN ('accepted', 'rejected') AND reviewed_at IS NOT NULL)",
            name="review_shape",
        ),
        Index(
            "ix_agent_patch_operations_patch_set_ordinal",
            "patch_set_id",
            "ordinal",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    patch_set_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_object_id: Mapped[int | None] = mapped_column(BigInteger)
    target_object_key: Mapped[str] = mapped_column(String(128), nullable=False)
    target_collection: Mapped[str] = mapped_column(String(40), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'replace'"),
    )
    field_path: Mapped[str] = mapped_column(String(512), nullable=False)
    expected_object_revision: Mapped[int | None] = mapped_column(Integer)
    old_value_jsonb: Mapped[Any | None] = mapped_column(JSONB)
    new_value_jsonb: Mapped[Any | None] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'primary'")
    )
    repair_round: Mapped[int | None] = mapped_column(Integer)
    repair_obligation_keys: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    decision: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = [
    "AgentMessage",
    "AgentPatchOperation",
    "AgentPatchSet",
    "AgentThread",
]
