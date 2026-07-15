"""创建版本、批准与审计地基。

Revision ID: 20260715145032
Revises: 20260715145031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260715145032"
down_revision: str | None = "20260715145031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "draft_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("casefile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_revision", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("snapshot_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_actor_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "public_id ~ '^snapshot_[0-9a-z_]+$'",
            name="ck_draft_snapshots_public_id_format",
        ),
        sa.CheckConstraint(
            "snapshot_revision >= 1", name="ck_draft_snapshots_revision_positive"
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_draft_snapshots_content_hash_format",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "draft_id", "casefile_id"],
            ["drafts.workspace_id", "drafts.id", "drafts.casefile_id"],
            name="fk_draft_snapshots_workspace_draft_casefile",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_draft_snapshots"),
        sa.UniqueConstraint(
            "workspace_id",
            "draft_id",
            "snapshot_revision",
            name="uq_draft_snapshots_draft_revision",
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", "casefile_id", name="uq_draft_snapshots_workspace_id_casefile"
        ),
        sa.UniqueConstraint(
            "workspace_id", "public_id", name="uq_draft_snapshots_workspace_public_id"
        ),
    )

    op.create_table(
        "approvals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("casefile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column(
            "approval_type",
            sa.String(length=32),
            server_default="draft_to_canon",
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("requested_by_actor_id", sa.String(length=64), nullable=False),
        sa.Column("decided_by_actor_id", sa.String(length=64), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "public_id ~ '^approval_[0-9a-z_]+$'", name="ck_approvals_public_id_format"
        ),
        sa.CheckConstraint(
            "approval_type = 'draft_to_canon'", name="ck_approvals_type_allowed"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'cancelled')",
            name="ck_approvals_status_allowed",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_approvals_revision_positive"),
        sa.CheckConstraint(
            "(status = 'pending' AND decided_by_actor_id IS NULL AND decided_at IS NULL) OR "
            "(status <> 'pending' AND decided_by_actor_id IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_approvals_decision_fields_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "draft_snapshot_id", "casefile_id"],
            ["draft_snapshots.workspace_id", "draft_snapshots.id", "draft_snapshots.casefile_id"],
            name="fk_approvals_workspace_snapshot_casefile",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_approvals"),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            "casefile_id",
            "draft_snapshot_id",
            name="uq_approvals_workspace_id_casefile_snapshot",
        ),
        sa.UniqueConstraint("workspace_id", "public_id", name="uq_approvals_workspace_public_id"),
    )
    op.create_index(
        "uq_approvals_one_approved_snapshot",
        "approvals",
        ["workspace_id", "draft_snapshot_id"],
        unique=True,
        postgresql_where=sa.text("status = 'approved'"),
    )

    op.create_table(
        "canon_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("casefile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_canon_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("content_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("result_validity", sa.String(length=24), nullable=False),
        sa.Column("approved_by_actor_id", sa.String(length=64), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "public_id ~ '^cv_[0-9a-z_]+$'", name="ck_canon_versions_public_id_format"
        ),
        sa.CheckConstraint("version_no >= 1", name="ck_canon_versions_version_positive"),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_canon_versions_content_hash_format",
        ),
        sa.CheckConstraint(
            "result_validity IN ('valid', 'possibly_invalid', 'invalid')",
            name="ck_canon_versions_result_validity_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "casefile_id"],
            ["casefiles.workspace_id", "casefiles.id"],
            name="fk_canon_versions_workspace_casefile",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "parent_canon_version_id", "casefile_id"],
            ["canon_versions.workspace_id", "canon_versions.id", "canon_versions.casefile_id"],
            name="fk_canon_versions_workspace_parent_casefile",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_snapshot_id", "casefile_id"],
            ["draft_snapshots.workspace_id", "draft_snapshots.id", "draft_snapshots.casefile_id"],
            name="fk_canon_versions_workspace_snapshot_casefile",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "approval_id", "casefile_id", "source_snapshot_id"],
            [
                "approvals.workspace_id",
                "approvals.id",
                "approvals.casefile_id",
                "approvals.draft_snapshot_id",
            ],
            name="fk_canon_versions_workspace_approval_casefile_snapshot",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_canon_versions"),
        sa.UniqueConstraint(
            "workspace_id", "approval_id", name="uq_canon_versions_approval"
        ),
        sa.UniqueConstraint(
            "workspace_id", "casefile_id", "version_no", name="uq_canon_versions_casefile_version"
        ),
        sa.UniqueConstraint(
            "workspace_id", "id", "casefile_id", name="uq_canon_versions_workspace_id_casefile"
        ),
        sa.UniqueConstraint(
            "workspace_id", "public_id", name="uq_canon_versions_workspace_public_id"
        ),
        sa.UniqueConstraint(
            "workspace_id", "source_snapshot_id", name="uq_canon_versions_source_snapshot"
        ),
    )
    op.create_index(
        "ix_canon_versions_casefile_version_desc",
        "canon_versions",
        ["workspace_id", "casefile_id", sa.text("version_no DESC")],
    )

    op.create_table(
        "audit_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_public_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column(
            "payload_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "public_id ~ '^audit_[0-9a-z_]+$'", name="ck_audit_events_public_id_format"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_audit_events_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.UniqueConstraint(
            "workspace_id", "public_id", name="uq_audit_events_workspace_public_id"
        ),
    )
    op.create_index(
        "ix_audit_events_workspace_occurred",
        "audit_events",
        ["workspace_id", "occurred_at"],
    )
    op.create_index(
        "ix_audit_events_entity",
        "audit_events",
        ["workspace_id", "entity_type", "entity_public_id", "occurred_at"],
    )

    op.add_column(
        "drafts",
        sa.Column("base_canon_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_drafts_workspace_base_canon_casefile",
        "drafts",
        "canon_versions",
        ["workspace_id", "base_canon_version_id", "casefile_id"],
        ["workspace_id", "id", "casefile_id"],
        ondelete="RESTRICT",
    )

    for table_name in ("draft_snapshots", "canon_versions", "audit_events"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_reject_update
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION casefile_reject_row_update()
            """
        )

    op.execute(
        """
        CREATE FUNCTION casefile_guard_approval_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.status <> 'pending' THEN
                RAISE EXCEPTION 'terminal approval cannot be changed' USING ERRCODE = '55000';
            END IF;
            IF NEW.workspace_id <> OLD.workspace_id
               OR NEW.casefile_id <> OLD.casefile_id
               OR NEW.draft_snapshot_id <> OLD.draft_snapshot_id
               OR NEW.public_id <> OLD.public_id
               OR NEW.approval_type <> OLD.approval_type
               OR NEW.requested_by_actor_id <> OLD.requested_by_actor_id
               OR NEW.created_at <> OLD.created_at THEN
                RAISE EXCEPTION 'approval identity fields are immutable' USING ERRCODE = '55000';
            END IF;
            IF NEW.revision <> OLD.revision + 1 THEN
                RAISE EXCEPTION 'approval revision must increment by one' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_approvals_guard_update
        BEFORE UPDATE ON approvals
        FOR EACH ROW EXECUTE FUNCTION casefile_guard_approval_update()
        """
    )

    op.execute(
        """
        CREATE FUNCTION casefile_validate_canon_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            approval_status varchar(20);
            decision_actor varchar(64);
            snapshot_hash varchar(64);
        BEGIN
            SELECT a.status, a.decided_by_actor_id, s.content_hash
            INTO approval_status, decision_actor, snapshot_hash
            FROM approvals a
            JOIN draft_snapshots s ON s.id = a.draft_snapshot_id
            WHERE a.id = NEW.approval_id
              AND a.workspace_id = NEW.workspace_id
              AND a.casefile_id = NEW.casefile_id
              AND a.draft_snapshot_id = NEW.source_snapshot_id;

            IF approval_status IS DISTINCT FROM 'approved' THEN
                RAISE EXCEPTION 'Canon requires an approved snapshot' USING ERRCODE = '23514';
            END IF;
            IF decision_actor IS DISTINCT FROM NEW.approved_by_actor_id THEN
                RAISE EXCEPTION 'Canon approver must match approval decision actor'
                    USING ERRCODE = '23514';
            END IF;
            IF snapshot_hash IS DISTINCT FROM NEW.content_hash THEN
                RAISE EXCEPTION 'Canon content hash must match approved snapshot'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_canon_versions_validate_insert
        BEFORE INSERT ON canon_versions
        FOR EACH ROW EXECUTE FUNCTION casefile_validate_canon_insert()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_canon_versions_validate_insert ON canon_versions")
    op.execute("DROP FUNCTION IF EXISTS casefile_validate_canon_insert()")
    op.execute("DROP TRIGGER IF EXISTS trg_approvals_guard_update ON approvals")
    op.execute("DROP FUNCTION IF EXISTS casefile_guard_approval_update()")

    op.drop_constraint(
        "fk_drafts_workspace_base_canon_casefile", "drafts", type_="foreignkey"
    )
    op.drop_column("drafts", "base_canon_version_id")

    op.execute("DROP TRIGGER IF EXISTS trg_audit_events_reject_update ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS trg_canon_versions_reject_update ON canon_versions")
    op.execute("DROP TRIGGER IF EXISTS trg_draft_snapshots_reject_update ON draft_snapshots")

    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_index("ix_audit_events_workspace_occurred", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_canon_versions_casefile_version_desc", table_name="canon_versions")
    op.drop_table("canon_versions")
    op.drop_index("uq_approvals_one_approved_snapshot", table_name="approvals")
    op.drop_table("approvals")
    op.drop_table("draft_snapshots")
