"""Persisted domain observations produced by the VerificationEngine."""

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

from casefile.data_postgres.base import Base, BigIntIdentityPrimaryKeyMixin


class VerificationRun(BigIntIdentityPrimaryKeyMixin, Base):
    """One domain verification observation over one frozen Draft revision."""

    __tablename__ = "verification_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_verification_runs_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "source_task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_verification_runs_project_source_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "patch_set_id"],
            ["agent_patch_sets.project_id", "agent_patch_sets.id"],
            name="fk_verification_runs_project_patch_set_agent_patch_sets",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("project_id", "id", name="uq_verification_runs_project_id_id"),
        CheckConstraint(
            "trigger IN ('chat', 'manual', 'pre_apply', 'post_apply')",
            name="trigger_allowed",
        ),
        CheckConstraint("profile IN ('fast', 'balanced', 'strict')", name="profile_allowed"),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="status_allowed",
        ),
        CheckConstraint("engine_version <> ''", name="engine_version_not_blank"),
        CheckConstraint("draft_revision >= 1", name="draft_revision_positive"),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="input_hash_format"),
        CheckConstraint("finding_count >= 0", name="finding_count_nonnegative"),
        CheckConstraint(
            "deterministic_finding_count >= 0 AND llm_finding_count >= 0",
            name="finding_counts_nonnegative",
        ),
        Index(
            "ix_verification_runs_project_draft_created_at",
            "project_id",
            "draft_id",
            "started_at",
        ),
        Index(
            "ix_verification_runs_source_task_run_id",
            "source_task_run_id",
        ),
        Index(
            "ix_verification_runs_patch_set_id",
            "patch_set_id",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_task_run_id: Mapped[int | None] = mapped_column(BigInteger)
    patch_set_id: Mapped[int | None] = mapped_column(BigInteger)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    profile: Mapped[str] = mapped_column(String(16), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    deterministic_finding_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    llm_finding_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )


class VerificationFinding(BigIntIdentityPrimaryKeyMixin, Base):
    """One finding observed in a verification run; never deleted."""

    __tablename__ = "verification_findings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_verification_findings_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "verification_run_id"],
            ["verification_runs.project_id", "verification_runs.id"],
            name="fk_verification_findings_project_run_verification_runs",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_verification_findings_project_id_id",
        ),
        UniqueConstraint(
            "verification_run_id",
            "finding_key",
            name="uq_verification_findings_run_key",
        ),
        CheckConstraint("kind IN ('deterministic', 'llm')", name="kind_allowed"),
        CheckConstraint(
            "severity IN ('info', 'warning', 'error', 'blocker')",
            name="severity_allowed",
        ),
        CheckConstraint(
            "status IN ('open', 'resolved', 'reopened', 'dismissed')",
            name="status_allowed",
        ),
        CheckConstraint("draft_revision >= 1", name="draft_revision_positive"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="confidence_range",
        ),
        CheckConstraint("jsonb_typeof(payload_jsonb) = 'object'", name="payload_is_object"),
        Index(
            "ix_verification_findings_project_draft_status",
            "project_id",
            "draft_id",
            "status",
        ),
        Index(
            "ix_verification_findings_project_rule_code",
            "project_id",
            "rule_code",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verification_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finding_key: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_fix: Mapped[str | None] = mapped_column(Text)
    rule_code: Mapped[str] = mapped_column(String(120), nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerificationFindingRef(BigIntIdentityPrimaryKeyMixin, Base):
    """Normalized finding evidence and target references."""

    __tablename__ = "verification_finding_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "finding_id"],
            ["verification_findings.project_id", "verification_findings.id"],
            name="fk_vf_refs_project_finding",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "finding_id",
            "ref_kind",
            "ref_key",
            "role",
            name="uq_verification_finding_refs_identity",
        ),
        CheckConstraint(
            "ref_kind IN ('object', 'event', 'validation_issue', 'patch_operation', 'related')",
            name="ref_kind_allowed",
        ),
        CheckConstraint("role IN ('evidence', 'target', 'related')", name="role_allowed"),
        Index("ix_verification_finding_refs_project_ref_key", "project_id", "ref_key"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finding_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ref_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    ref_key: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)


class VerificationFindingReview(BigIntIdentityPrimaryKeyMixin, Base):
    """Append-only author review decision for a finding."""

    __tablename__ = "verification_finding_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "finding_id"],
            ["verification_findings.project_id", "verification_findings.id"],
            name="fk_vf_reviews_project_finding",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision IN ('confirm', 'resolve', 'reopen', 'dismiss')",
            name="decision_allowed",
        ),
        Index(
            "ix_verification_finding_reviews_project_finding_created_at",
            "project_id",
            "finding_id",
            "created_at",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finding_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class VerificationFindingPatchOperation(BigIntIdentityPrimaryKeyMixin, Base):
    """Link findings to patch operations without embedding relationships in JSON."""

    __tablename__ = "verification_finding_patch_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "finding_id"],
            ["verification_findings.project_id", "verification_findings.id"],
            name="fk_vf_patch_project_finding",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "patch_operation_id"],
            ["agent_patch_operations.project_id", "agent_patch_operations.id"],
            name="fk_vf_patch_project_operation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "finding_id",
            "patch_operation_id",
            "relation_kind",
            name="uq_verification_finding_patch_relation",
        ),
        CheckConstraint(
            "relation_kind IN ('fixes', 'may_introduce', 'derived_from')",
            name="relation_kind_allowed",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finding_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    patch_operation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    relation_kind: Mapped[str] = mapped_column(String(20), nullable=False)


__all__ = [
    "VerificationFinding",
    "VerificationFindingPatchOperation",
    "VerificationFindingRef",
    "VerificationFindingReview",
    "VerificationRun",
]
