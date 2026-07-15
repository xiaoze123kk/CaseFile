"""创建项目与 CaseFile 工作态地基。

Revision ID: 20260715145031
Revises: 20260715145030
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260715145031"
down_revision: str | None = "20260715145030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column(
            "profile_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by_actor_id", sa.String(length=64), nullable=False),
        sa.Column("updated_by_actor_id", sa.String(length=64), nullable=False),
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
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("public_id ~ '^project_[0-9a-z_]+$'", name="ck_projects_public_id_format"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_projects_status_allowed"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_projects_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_projects_workspace_id"),
        sa.UniqueConstraint("workspace_id", "public_id", name="uq_projects_workspace_public_id"),
    )
    op.create_index(
        "ix_projects_workspace_status_updated",
        "projects",
        ["workspace_id", "status", "updated_at"],
    )

    op.create_table(
        "casefiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("schema_version", sa.String(length=32), server_default="1.0", nullable=False),
        sa.Column("created_by_actor_id", sa.String(length=64), nullable=False),
        sa.Column("updated_by_actor_id", sa.String(length=64), nullable=False),
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
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("public_id ~ '^case_[0-9a-z_]+$'", name="ck_casefiles_public_id_format"),
        sa.CheckConstraint(
            "status IN ('draft', 'canon', 'archived')", name="ck_casefiles_status_allowed"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "project_id"],
            ["projects.workspace_id", "projects.id"],
            name="fk_casefiles_workspace_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_casefiles"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_casefiles_workspace_id"),
        sa.UniqueConstraint("workspace_id", "project_id", name="uq_casefiles_workspace_project"),
        sa.UniqueConstraint("workspace_id", "public_id", name="uq_casefiles_workspace_public_id"),
    )
    op.create_index(
        "ix_casefiles_workspace_status_updated",
        "casefiles",
        ["workspace_id", "status", "updated_at"],
    )

    op.create_table(
        "drafts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("casefile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("schema_version", sa.String(length=32), server_default="1.0", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("created_by_actor_id", sa.String(length=64), nullable=False),
        sa.Column("updated_by_actor_id", sa.String(length=64), nullable=False),
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
        sa.CheckConstraint("public_id ~ '^draft_[0-9a-z_]+$'", name="ck_drafts_public_id_format"),
        sa.CheckConstraint("revision >= 1", name="ck_drafts_revision_positive"),
        sa.CheckConstraint("status IN ('active', 'locked')", name="ck_drafts_status_allowed"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "casefile_id"],
            ["casefiles.workspace_id", "casefiles.id"],
            name="fk_drafts_workspace_casefile",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_drafts"),
        sa.UniqueConstraint("workspace_id", "casefile_id", name="uq_drafts_workspace_casefile"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_drafts_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "id", "casefile_id", name="uq_drafts_workspace_id_casefile"
        ),
        sa.UniqueConstraint("workspace_id", "public_id", name="uq_drafts_workspace_public_id"),
    )

    op.create_table(
        "casefile_objects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("casefile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_id", sa.String(length=128), nullable=False),
        sa.Column("object_type", sa.String(length=40), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "payload_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "source_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("confirmation_status", sa.String(length=24), nullable=False),
        sa.Column("created_by_actor_id", sa.String(length=64), nullable=False),
        sa.Column("updated_by_actor_id", sa.String(length=64), nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(object_id) > 0", name="ck_casefile_objects_object_id_not_empty"),
        sa.CheckConstraint("revision >= 1", name="ck_casefile_objects_revision_positive"),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name="ck_casefile_objects_confidence_range",
        ),
        sa.CheckConstraint(
            "confirmation_status IN ('user_confirmed', 'ai_inferred', 'unresolved')",
            name="ck_casefile_objects_confirmation_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "draft_id", "casefile_id"],
            ["drafts.workspace_id", "drafts.id", "drafts.casefile_id"],
            name="fk_casefile_objects_workspace_draft_casefile",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_casefile_objects"),
        sa.UniqueConstraint(
            "workspace_id", "draft_id", "id", name="uq_casefile_objects_workspace_draft_id"
        ),
        sa.UniqueConstraint(
            "workspace_id", "casefile_id", "object_id", name="uq_casefile_objects_stable_id"
        ),
    )
    op.create_index(
        "ix_casefile_objects_workspace_casefile_type_deleted",
        "casefile_objects",
        ["workspace_id", "casefile_id", "object_type", "deleted_at"],
    )
    op.create_index(
        "ix_casefile_objects_payload_gin",
        "casefile_objects",
        ["payload_jsonb"],
        postgresql_using="gin",
    )

    op.create_table(
        "casefile_refs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("casefile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_path", sa.String(length=512), nullable=False),
        sa.Column("ref_kind", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("length(field_path) > 0", name="ck_casefile_refs_field_path_not_empty"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "draft_id", "casefile_id"],
            ["drafts.workspace_id", "drafts.id", "drafts.casefile_id"],
            name="fk_casefile_refs_workspace_draft_casefile",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "draft_id", "from_object_id"],
            ["casefile_objects.workspace_id", "casefile_objects.draft_id", "casefile_objects.id"],
            name="fk_casefile_refs_workspace_draft_from_object",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "draft_id", "to_object_id"],
            ["casefile_objects.workspace_id", "casefile_objects.draft_id", "casefile_objects.id"],
            name="fk_casefile_refs_workspace_draft_to_object",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_casefile_refs"),
        sa.UniqueConstraint(
            "workspace_id",
            "draft_id",
            "from_object_id",
            "field_path",
            "to_object_id",
            "ref_kind",
            name="uq_casefile_refs_edge",
        ),
    )
    op.create_index(
        "ix_casefile_refs_outgoing",
        "casefile_refs",
        ["workspace_id", "casefile_id", "from_object_id"],
    )
    op.create_index(
        "ix_casefile_refs_incoming",
        "casefile_refs",
        ["workspace_id", "casefile_id", "to_object_id"],
    )

    op.create_table(
        "draft_operations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("operation_type", sa.String(length=16), nullable=False),
        sa.Column("field_path", sa.String(length=512), nullable=False),
        sa.Column("old_value_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("sequence_no >= 1", name="ck_draft_operations_sequence_positive"),
        sa.CheckConstraint(
            "operation_type IN ('add', 'remove', 'replace')",
            name="ck_draft_operations_type_allowed",
        ),
        sa.CheckConstraint(
            "base_revision >= 1", name="ck_draft_operations_base_revision_positive"
        ),
        sa.CheckConstraint(
            "result_revision = base_revision + 1", name="ck_draft_operations_revision_step"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "draft_id"],
            ["drafts.workspace_id", "drafts.id"],
            name="fk_draft_operations_workspace_draft",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "draft_id", "object_id"],
            ["casefile_objects.workspace_id", "casefile_objects.draft_id", "casefile_objects.id"],
            name="fk_draft_operations_workspace_draft_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_draft_operations"),
        sa.UniqueConstraint(
            "workspace_id", "draft_id", "sequence_no", name="uq_draft_operations_sequence"
        ),
    )
    op.create_index(
        "ix_draft_operations_draft_created",
        "draft_operations",
        ["workspace_id", "draft_id", "created_at"],
    )

    op.execute(
        """
        CREATE FUNCTION casefile_reject_row_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable', TG_TABLE_NAME USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_draft_operations_reject_update
        BEFORE UPDATE ON draft_operations
        FOR EACH ROW EXECUTE FUNCTION casefile_reject_row_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_draft_operations_reject_update ON draft_operations")
    op.execute("DROP FUNCTION IF EXISTS casefile_reject_row_update()")
    op.drop_index("ix_draft_operations_draft_created", table_name="draft_operations")
    op.drop_table("draft_operations")
    op.drop_index("ix_casefile_refs_incoming", table_name="casefile_refs")
    op.drop_index("ix_casefile_refs_outgoing", table_name="casefile_refs")
    op.drop_table("casefile_refs")
    op.drop_index("ix_casefile_objects_payload_gin", table_name="casefile_objects")
    op.drop_index(
        "ix_casefile_objects_workspace_casefile_type_deleted", table_name="casefile_objects"
    )
    op.drop_table("casefile_objects")
    op.drop_table("drafts")
    op.drop_index("ix_casefiles_workspace_status_updated", table_name="casefiles")
    op.drop_table("casefiles")
    op.drop_index("ix_projects_workspace_status_updated", table_name="projects")
    op.drop_table("projects")
