"""Durable Narrative Compiler profiles, runs, and immutable artifacts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from casefile.data_postgres.base import Base, BigIntIdentityPrimaryKeyMixin, TimestampMixin


class CompilerProfile(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """A reusable profile identity with one mutable immutable-version pointer."""

    __tablename__ = "compiler_profiles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "id", "current_version_id"],
            [
                "compiler_profile_versions.project_id",
                "compiler_profile_versions.compiler_profile_id",
                "compiler_profile_versions.id",
            ],
            name="fk_compiler_profiles_current_version_profile_versions",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("project_id", "profile_key", name="uq_compiler_profiles_project_key"),
        UniqueConstraint("project_id", "id", name="uq_compiler_profiles_project_id_id"),
        CheckConstraint(
            "profile_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'",
            name="profile_key_format",
        ),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
    )

    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    profile_key: Mapped[str] = mapped_column(String(160), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    current_version_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class CompilerProfileVersion(BigIntIdentityPrimaryKeyMixin, Base):
    """One immutable canonical JSON profile payload."""

    __tablename__ = "compiler_profile_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "compiler_profile_id"],
            ["compiler_profiles.project_id", "compiler_profiles.id"],
            name="fk_compiler_profile_versions_project_profile_profiles",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint(
            "compiler_profile_id", "version_no", name="uq_compiler_profile_versions_version"
        ),
        UniqueConstraint("project_id", "id", name="uq_compiler_profile_versions_project_id_id"),
        UniqueConstraint(
            "project_id",
            "compiler_profile_id",
            "id",
            name="uq_compiler_profile_versions_profile_lineage_id",
        ),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint("jsonb_typeof(payload_jsonb) = 'object'", name="payload_is_object"),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        CheckConstraint("length(btrim(schema_id)) > 0", name="schema_id_not_blank"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    compiler_profile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_id: Mapped[str] = mapped_column(String(160), nullable=False)
    payload_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class CompileRun(BigIntIdentityPrimaryKeyMixin, Base):
    """Immutable domain identity and exact lineage for one narrative build."""

    __tablename__ = "compile_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_compile_runs_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.casefile_id", "task_runs.draft_id", "task_runs.id"],
            name="fk_compile_runs_lineage_task_task_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "source_snapshot_id"],
            [
                "draft_snapshots.project_id",
                "draft_snapshots.casefile_id",
                "draft_snapshots.draft_id",
                "draft_snapshots.id",
            ],
            name="fk_compile_runs_lineage_snapshot_snapshots",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "project_id",
                "casefile_id",
                "source_canon_version_id",
                "source_snapshot_id",
            ],
            [
                "canon_versions.project_id",
                "canon_versions.casefile_id",
                "canon_versions.id",
                "canon_versions.source_snapshot_id",
            ],
            name="fk_compile_runs_canon_snapshot_canon_versions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "exposure_plan_revision_id"],
            [
                "exposure_plan_revisions.project_id",
                "exposure_plan_revisions.casefile_id",
                "exposure_plan_revisions.draft_id",
                "exposure_plan_revisions.id",
            ],
            name="fk_compile_runs_lineage_exposure_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "compiler_profile_version_id"],
            ["compiler_profile_versions.project_id", "compiler_profile_versions.id"],
            name="fk_compile_runs_project_profile_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_run_id", name="uq_compile_runs_task_run_id"),
        UniqueConstraint("id", "task_run_id", name="uq_compile_runs_id_task_run_id"),
        UniqueConstraint("project_id", "id", name="uq_compile_runs_project_id_id"),
        UniqueConstraint(
            "project_id",
            "casefile_id",
            "id",
            name="uq_compile_runs_project_casefile_id",
        ),
        CheckConstraint("target_kind = 'novel'", name="target_kind_novel"),
        CheckConstraint("compile_mode IN ('preview', 'canonical')", name="compile_mode_allowed"),
        CheckConstraint(
            "(compile_mode = 'preview' AND source_canon_version_id IS NULL) OR "
            "(compile_mode = 'canonical' AND source_canon_version_id IS NOT NULL)",
            name="canon_binding_matches_mode",
        ),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="input_hash_format"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    compile_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    source_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_canon_version_id: Mapped[int | None] = mapped_column(BigInteger)
    exposure_plan_revision_id: Mapped[int | None] = mapped_column(BigInteger)
    compiler_profile_version_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    compiler_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class CompileArtifact(BigIntIdentityPrimaryKeyMixin, Base):
    """One immutable JSON artifact produced by a Compiler component step."""

    __tablename__ = "compile_artifacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "compile_run_id"],
            ["compile_runs.project_id", "compile_runs.casefile_id", "compile_runs.id"],
            name="fk_compile_artifacts_project_casefile_run_compile_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["compile_run_id", "task_run_id"],
            ["compile_runs.id", "compile_runs.task_run_id"],
            name="fk_compile_artifacts_run_task_compile_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["agent_step_run_id", "task_run_id"],
            ["agent_step_runs.id", "agent_step_runs.task_run_id"],
            name="fk_compile_artifacts_step_task_agent_step_runs",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "compile_run_id", "artifact_key", name="uq_compile_artifacts_run_artifact_key"
        ),
        CheckConstraint(
            "(artifact_kind = 'input_manifest' AND "
            "artifact_key = 'compiler.input_manifest' AND "
            "schema_id = 'compiler.input-manifest.v1') OR "
            "(artifact_kind = 'narrative_ir' AND "
            "artifact_key = 'compiler.narrative_ir' AND "
            "schema_id = 'compiler.narrative-ir.v1') OR "
            "(artifact_kind = 'novel_plan' AND "
            "artifact_key = 'compiler.novel_plan' AND "
            "schema_id = 'compiler.novel-plan.v1')",
            name="identity_allowed",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        CheckConstraint("jsonb_typeof(content_jsonb) = 'object'", name="content_is_object"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    compile_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    agent_step_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    artifact_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    artifact_key: Mapped[str] = mapped_column(String(160), nullable=False)
    schema_id: Mapped[str] = mapped_column(String(160), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


__all__ = ["CompileArtifact", "CompileRun", "CompilerProfile", "CompilerProfileVersion"]
