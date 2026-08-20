"""verification_domain_results

Revision ID: 20260820151005
Revises: 20260817000000
Create Date: 2026-08-20 15:10:06.111284
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '20260820151005'
down_revision: str | None = '20260817000000'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_agent_patch_operations_project_id_id",
        "agent_patch_operations",
        ["project_id", "id"],
    )
    _create_verification_runs()
    _create_verification_findings()
    _create_finding_refs()
    _create_finding_reviews()
    _create_finding_patch_operations()
    _create_append_only_triggers()


def downgrade() -> None:
    _drop_append_only_triggers()
    op.drop_table("verification_finding_patch_operations")
    op.drop_index(
        "ix_verification_finding_reviews_project_finding_created_at",
        table_name="verification_finding_reviews",
    )
    op.drop_table("verification_finding_reviews")
    op.drop_index(
        "ix_verification_finding_refs_project_ref_key",
        table_name="verification_finding_refs",
    )
    op.drop_table("verification_finding_refs")
    op.drop_index(
        "ix_verification_findings_project_rule_code",
        table_name="verification_findings",
    )
    op.drop_index(
        "ix_verification_findings_project_draft_status",
        table_name="verification_findings",
    )
    op.drop_table("verification_findings")
    op.drop_index("ix_verification_runs_patch_set_id", table_name="verification_runs")
    op.drop_index("ix_verification_runs_source_task_run_id", table_name="verification_runs")
    op.drop_index(
        "ix_verification_runs_project_draft_created_at",
        table_name="verification_runs",
    )
    op.drop_table("verification_runs")
    op.drop_constraint(
        "uq_agent_patch_operations_project_id_id",
        "agent_patch_operations",
        type_="unique",
    )


def _create_verification_runs() -> None:
    op.create_table(
        "verification_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("source_task_run_id", sa.BigInteger(), nullable=True),
        sa.Column("patch_set_id", sa.BigInteger(), nullable=True),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("profile", sa.String(16), nullable=False),
        sa.Column("engine_version", sa.String(64), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finding_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "deterministic_finding_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "llm_finding_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "trigger IN ('chat', 'manual', 'pre_apply', 'post_apply')",
            name=op.f("ck_verification_runs_trigger_allowed"),
        ),
        sa.CheckConstraint(
            "profile IN ('fast', 'balanced', 'strict')",
            name=op.f("ck_verification_runs_profile_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name=op.f("ck_verification_runs_status_allowed"),
        ),
        sa.CheckConstraint(
            "engine_version <> ''",
            name=op.f("ck_verification_runs_engine_version_not_blank"),
        ),
        sa.CheckConstraint(
            "draft_revision >= 1",
            name=op.f("ck_verification_runs_draft_revision_positive"),
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_verification_runs_input_hash_format"),
        ),
        sa.CheckConstraint(
            "finding_count >= 0",
            name=op.f("ck_verification_runs_finding_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "deterministic_finding_count >= 0 AND llm_finding_count >= 0",
            name=op.f("ck_verification_runs_finding_counts_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_verification_runs_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "source_task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_verification_runs_project_source_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "patch_set_id"],
            ["agent_patch_sets.project_id", "agent_patch_sets.id"],
            name="fk_verification_runs_project_patch_set_agent_patch_sets",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_runs")),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_verification_runs_project_id_id"
        ),
    )
    op.create_index(
        "ix_verification_runs_project_draft_created_at",
        "verification_runs",
        ["project_id", "draft_id", "started_at"],
    )
    op.create_index(
        "ix_verification_runs_source_task_run_id",
        "verification_runs",
        ["source_task_run_id"],
    )
    op.create_index(
        "ix_verification_runs_patch_set_id",
        "verification_runs",
        ["patch_set_id"],
    )


def _create_verification_findings() -> None:
    op.create_table(
        "verification_findings",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("verification_run_id", sa.BigInteger(), nullable=False),
        sa.Column("finding_key", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'open'"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("suggested_fix", sa.Text(), nullable=True),
        sa.Column("rule_code", sa.String(120), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column(
            "payload_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('deterministic', 'llm')",
            name=op.f("ck_verification_findings_kind_allowed"),
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warning', 'error', 'blocker')",
            name=op.f("ck_verification_findings_severity_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'reopened', 'dismissed')",
            name=op.f("ck_verification_findings_status_allowed"),
        ),
        sa.CheckConstraint(
            "draft_revision >= 1",
            name=op.f("ck_verification_findings_draft_revision_positive"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name=op.f("ck_verification_findings_confidence_range"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload_jsonb) = 'object'",
            name=op.f("ck_verification_findings_payload_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_verification_findings_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "verification_run_id"],
            ["verification_runs.project_id", "verification_runs.id"],
            name="fk_verification_findings_project_run_verification_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_findings")),
        sa.UniqueConstraint(
            "project_id", "id", name="uq_verification_findings_project_id_id"
        ),
        sa.UniqueConstraint(
            "verification_run_id", "finding_key", name="uq_verification_findings_run_key"
        ),
    )
    op.create_index(
        "ix_verification_findings_project_draft_status",
        "verification_findings",
        ["project_id", "draft_id", "status"],
    )
    op.create_index(
        "ix_verification_findings_project_rule_code",
        "verification_findings",
        ["project_id", "rule_code"],
    )


def _create_finding_refs() -> None:
    op.create_table(
        "verification_finding_refs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("finding_id", sa.BigInteger(), nullable=False),
        sa.Column("ref_kind", sa.String(32), nullable=False),
        sa.Column("ref_key", sa.String(512), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "ref_kind IN ('object', 'event', 'validation_issue', 'patch_operation', 'related')",
            name=op.f("ck_verification_finding_refs_ref_kind_allowed"),
        ),
        sa.CheckConstraint(
            "role IN ('evidence', 'target', 'related')",
            name=op.f("ck_verification_finding_refs_role_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "finding_id"],
            ["verification_findings.project_id", "verification_findings.id"],
            name="fk_vf_refs_project_finding",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_finding_refs")),
        sa.UniqueConstraint(
            "finding_id",
            "ref_kind",
            "ref_key",
            "role",
            name="uq_verification_finding_refs_identity",
        ),
    )
    op.create_index(
        "ix_verification_finding_refs_project_ref_key",
        "verification_finding_refs",
        ["project_id", "ref_key"],
    )


def _create_finding_reviews() -> None:
    op.create_table(
        "verification_finding_reviews",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("finding_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('confirm', 'resolve', 'reopen', 'dismiss')",
            name=op.f("ck_verification_finding_reviews_decision_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "finding_id"],
            ["verification_findings.project_id", "verification_findings.id"],
            name="fk_vf_reviews_project_finding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_verification_finding_reviews_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_finding_reviews")),
    )
    op.create_index(
        "ix_verification_finding_reviews_project_finding_created_at",
        "verification_finding_reviews",
        ["project_id", "finding_id", "created_at"],
    )


def _create_finding_patch_operations() -> None:
    op.create_table(
        "verification_finding_patch_operations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("finding_id", sa.BigInteger(), nullable=False),
        sa.Column("patch_operation_id", sa.BigInteger(), nullable=False),
        sa.Column("relation_kind", sa.String(20), nullable=False),
        sa.CheckConstraint(
            "relation_kind IN ('fixes', 'may_introduce', 'derived_from')",
            name=op.f("ck_verification_finding_patch_operations_relation_kind_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "finding_id"],
            ["verification_findings.project_id", "verification_findings.id"],
            name="fk_vf_patch_project_finding",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "patch_operation_id"],
            ["agent_patch_operations.project_id", "agent_patch_operations.id"],
            name="fk_vf_patch_project_operation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id", name=op.f("pk_verification_finding_patch_operations")
        ),
        sa.UniqueConstraint(
            "finding_id",
            "patch_operation_id",
            "relation_kind",
            name="uq_verification_finding_patch_relation",
        ),
    )


def _create_append_only_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION casefile_prevent_verification_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_TABLE_NAME = 'verification_findings' AND TG_OP = 'UPDATE' THEN
                IF NEW.project_id IS DISTINCT FROM OLD.project_id
                   OR NEW.casefile_id IS DISTINCT FROM OLD.casefile_id
                   OR NEW.draft_id IS DISTINCT FROM OLD.draft_id
                   OR NEW.verification_run_id IS DISTINCT FROM OLD.verification_run_id
                   OR NEW.finding_key IS DISTINCT FROM OLD.finding_key
                   OR NEW.kind IS DISTINCT FROM OLD.kind
                   OR NEW.severity IS DISTINCT FROM OLD.severity
                   OR NEW.title IS DISTINCT FROM OLD.title
                   OR NEW.message IS DISTINCT FROM OLD.message
                   OR NEW.suggested_fix IS DISTINCT FROM OLD.suggested_fix
                   OR NEW.rule_code IS DISTINCT FROM OLD.rule_code
                   OR NEW.confidence IS DISTINCT FROM OLD.confidence
                   OR NEW.draft_revision IS DISTINCT FROM OLD.draft_revision
                   OR NEW.payload_jsonb IS DISTINCT FROM OLD.payload_jsonb
                   OR NEW.first_seen_at IS DISTINCT FROM OLD.first_seen_at
                THEN
                    RAISE EXCEPTION 'verification finding facts are append-only';
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'verification facts are append-only';
        END;
        $$
        """
    )
    for table, events in (
        ("verification_runs", "UPDATE OR DELETE"),
        ("verification_findings", "UPDATE OR DELETE"),
        ("verification_finding_refs", "UPDATE OR DELETE"),
        ("verification_finding_reviews", "UPDATE OR DELETE"),
        ("verification_finding_patch_operations", "UPDATE OR DELETE"),
    ):
        op.execute(
            f"CREATE TRIGGER {table}_append_only "
            f"BEFORE {events} ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION casefile_prevent_verification_mutation()"
        )


def _drop_append_only_triggers() -> None:
    for table in (
        "verification_runs",
        "verification_findings",
        "verification_finding_refs",
        "verification_finding_reviews",
        "verification_finding_patch_operations",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS casefile_prevent_verification_mutation()")
