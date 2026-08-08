"""Durable, revisioned pre-Brief intake state and immutable candidate history."""

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


class BriefIntake(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One project's recoverable intake aggregate before formal Brief review."""

    __tablename__ = "brief_intakes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "current_source_record_id"],
            ["source_records.project_id", "source_records.id"],
            name="fk_brief_intakes_project_current_source_source_records",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "id", "current_questions_task_run_id"],
            ["task_runs.project_id", "task_runs.brief_intake_id", "task_runs.id"],
            name="fk_brief_intakes_current_questions_task_task_runs",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["project_id", "id", "current_candidate_id"],
            [
                "brief_intake_candidates.project_id",
                "brief_intake_candidates.intake_id",
                "brief_intake_candidates.id",
            ],
            name="fk_brief_intakes_current_candidate_brief_intake_candidates",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["project_id", "id", "adopted_candidate_id"],
            [
                "brief_intake_candidates.project_id",
                "brief_intake_candidates.intake_id",
                "brief_intake_candidates.id",
            ],
            name="fk_brief_intakes_adopted_candidate_brief_intake_candidates",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint("project_id", name="uq_brief_intakes_project_id"),
        UniqueConstraint("project_id", "id", name="uq_brief_intakes_project_id_id"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint(
            "stage IN ('idea', 'questions', 'confirmation', 'brief_review')",
            name="stage_allowed",
        ),
        CheckConstraint(
            "stage = 'idea' OR current_source_record_id IS NOT NULL",
            name="source_required_after_idea",
        ),
    )

    project_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("projects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    stage: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'idea'"))
    current_source_record_id: Mapped[int | None] = mapped_column(BigInteger)
    current_questions_task_run_id: Mapped[int | None] = mapped_column(BigInteger)
    current_candidate_id: Mapped[int | None] = mapped_column(BigInteger)
    adopted_candidate_id: Mapped[int | None] = mapped_column(BigInteger)


class BriefIntakeQuestion(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One generated question plus the author's mutable resolution state."""

    __tablename__ = "brief_intake_questions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "intake_id"],
            ["brief_intakes.project_id", "brief_intakes.id"],
            name="fk_brief_intake_questions_project_intake_brief_intakes",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "intake_id", "generated_by_task_run_id"],
            ["task_runs.project_id", "task_runs.brief_intake_id", "task_runs.id"],
            name="fk_brief_intake_questions_generated_task_task_runs",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint(
            "project_id", "intake_id", "id", name="uq_brief_intake_questions_lineage_id"
        ),
        UniqueConstraint(
            "generated_by_task_run_id",
            "question_key",
            name="uq_brief_intake_questions_task_question_key",
        ),
        UniqueConstraint(
            "generated_by_task_run_id",
            "ordinal",
            name="uq_brief_intake_questions_task_ordinal",
        ),
        CheckConstraint("ordinal BETWEEN 1 AND 2", name="ordinal_range"),
        CheckConstraint(
            "question_key ~ '^question_[a-z0-9][a-z0-9_]{0,53}$'",
            name="question_key_format",
        ),
        CheckConstraint("btrim(prompt) <> ''", name="prompt_not_blank"),
        CheckConstraint("btrim(impact) <> ''", name="impact_not_blank"),
        CheckConstraint("jsonb_typeof(suggestions_jsonb) = 'array'", name="suggestions_is_array"),
        CheckConstraint(
            "answer_status IN ('unanswered', 'user_answered', 'suggestion_accepted', 'pending')",
            name="answer_status_allowed",
        ),
        CheckConstraint(
            "(answer_status = 'unanswered' "
            "AND answer_text IS NULL AND answer_source IS NULL) OR "
            "(answer_status = 'user_answered' "
            "AND btrim(answer_text) <> '' AND answer_source = 'user_confirmed') OR "
            "(answer_status = 'suggestion_accepted' "
            "AND btrim(answer_text) <> '' AND answer_source = 'agent_suggestion') OR "
            "(answer_status = 'pending' AND is_required = false "
            "AND answer_text IS NULL AND answer_source = 'unresolved')",
            name="answer_matches_status",
        ),
        Index("ix_brief_intake_questions_intake_id", "intake_id"),
        Index(
            "uq_brief_intake_questions_task_required",
            "generated_by_task_run_id",
            unique=True,
            postgresql_where=text("is_required = true"),
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    intake_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    generated_by_task_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    question_key: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str] = mapped_column(Text, nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    suggestions_jsonb: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    answer_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'unanswered'")
    )
    answer_text: Mapped[str | None] = mapped_column(Text)
    answer_source: Mapped[str | None] = mapped_column(String(32))


class BriefIntakeCandidate(BigIntIdentityPrimaryKeyMixin, Base):
    """Immutable candidate content and lineage with mutable save-bookmark metadata."""

    __tablename__ = "brief_intake_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "intake_id"],
            ["brief_intakes.project_id", "brief_intakes.id"],
            name="fk_brief_intake_candidates_project_intake_brief_intakes",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "intake_id", "parent_candidate_id"],
            [
                "brief_intake_candidates.project_id",
                "brief_intake_candidates.intake_id",
                "brief_intake_candidates.id",
            ],
            name="fk_intake_candidates_project_parent_candidates",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "intake_id", "generated_by_task_run_id"],
            ["task_runs.project_id", "task_runs.brief_intake_id", "task_runs.id"],
            name="fk_brief_intake_candidates_generated_task_task_runs",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint(
            "project_id", "intake_id", "id", name="uq_brief_intake_candidates_lineage_id"
        ),
        UniqueConstraint(
            "generated_by_task_run_id", name="uq_brief_intake_candidates_generated_task"
        ),
        CheckConstraint(
            "origin IN ('agent_synthesis', 'dialogue_revision', 'manual_edit', 'legacy_import')",
            name="origin_allowed",
        ),
        CheckConstraint("basis_input_hash ~ '^[0-9a-f]{64}$'", name="basis_input_hash_format"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        CheckConstraint("jsonb_typeof(content_jsonb) = 'object'", name="content_is_object"),
        CheckConstraint(
            "((origin IN ('agent_synthesis', 'dialogue_revision')) "
            "AND generated_by_task_run_id IS NOT NULL) OR "
            "((origin IN ('manual_edit', 'legacy_import')) "
            "AND generated_by_task_run_id IS NULL)",
            name="generator_matches_origin",
        ),
        CheckConstraint(
            "origin <> 'dialogue_revision' OR parent_candidate_id IS NOT NULL",
            name="dialogue_revision_has_parent",
        ),
        CheckConstraint(
            "(saved_at IS NULL AND saved_by_user_id IS NULL) OR "
            "(saved_at IS NOT NULL AND saved_by_user_id IS NOT NULL)",
            name="save_bookmark_consistent",
        ),
        Index("ix_brief_intake_candidates_intake_id_created_at", "intake_id", "created_at"),
        Index("ix_brief_intake_candidates_parent_candidate_id", "parent_candidate_id"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    intake_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parent_candidate_id: Mapped[int | None] = mapped_column(BigInteger)
    generated_by_task_run_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    basis_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    saved_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
