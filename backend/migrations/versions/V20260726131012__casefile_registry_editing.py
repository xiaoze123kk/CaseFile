"""casefile_registry_editing

Revision ID: 20260726131012
Revises: 20260726131010
Create Date: 2026-07-26 13:10:12.998607
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260726131012"
down_revision: str | None = "20260726131010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "casefiles",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'draft'"), nullable=False
        ),
        sa.Column("current_canon_version_id", sa.BigInteger(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("length(btrim(title)) > 0", name=op.f("ck_casefiles_title_not_blank")),
        sa.CheckConstraint(
            "length(btrim(schema_version)) > 0", name=op.f("ck_casefiles_schema_version_not_blank")
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'canon', 'archived')", name=op.f("ck_casefiles_status_allowed")
        ),
        sa.CheckConstraint(
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status <> 'archived' AND archived_at IS NULL)",
            name=op.f("ck_casefiles_archive_state_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_casefiles_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_casefiles")),
        sa.UniqueConstraint("project_id", name="uq_casefiles_project_id"),
        sa.UniqueConstraint("project_id", "id", name="uq_casefiles_project_id_id"),
    )
    op.create_index(
        "ix_casefiles_project_id_status_updated_at",
        "casefiles",
        ["project_id", "status", "updated_at"],
    )

    op.create_table(
        "drafts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("base_canon_version_id", sa.BigInteger(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'active'"), nullable=False
        ),
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
        sa.CheckConstraint("revision >= 1", name=op.f("ck_drafts_revision_positive")),
        sa.CheckConstraint(
            "length(btrim(schema_version)) > 0", name=op.f("ck_drafts_schema_version_not_blank")
        ),
        sa.CheckConstraint("status IN ('active', 'locked')", name=op.f("ck_drafts_status_allowed")),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id"],
            ["casefiles.project_id", "casefiles.id"],
            name="fk_drafts_project_id_casefile_id_casefiles",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_drafts")),
        sa.UniqueConstraint("project_id", "casefile_id", name="uq_drafts_project_id_casefile_id"),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "id", name="uq_drafts_project_id_casefile_id_id"
        ),
    )

    op.create_table(
        "casefile_objects",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("object_id", sa.String(length=128), nullable=False),
        sa.Column("object_type", sa.String(length=40), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "source_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=True),
        sa.Column("confirmation_status", sa.String(length=24), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            "object_id ~ '^[a-z][a-z0-9_]{1,127}$'",
            name=op.f("ck_casefile_objects_object_id_format"),
        ),
        sa.CheckConstraint(
            "object_type IN ('narrative_phase', 'entity', 'event', 'information_unit', "
            "'claim', 'hypothesis', 'reasoning_path', 'resolution_spec', 'constraint', "
            "'knowledge_state')",
            name=op.f("ck_casefile_objects_object_type_allowed"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_casefile_objects_revision_positive")),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name=op.f("ck_casefile_objects_confidence_range"),
        ),
        sa.CheckConstraint(
            "confirmation_status IN ('user_confirmed', 'ai_inferred', 'unresolved')",
            name=op.f("ck_casefile_objects_confirmation_status_allowed"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_jsonb) = 'object'",
            name=op.f("ck_casefile_objects_source_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_casefile_objects_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_casefile_objects")),
        sa.UniqueConstraint(
            "project_id", "casefile_id", "draft_id", "id", name="uq_casefile_objects_lineage_id"
        ),
        sa.UniqueConstraint(
            "casefile_id", "object_id", name="uq_casefile_objects_casefile_id_object_id"
        ),
    )
    op.create_index(
        "ix_casefile_objects_casefile_id_object_type_deleted_at",
        "casefile_objects",
        ["casefile_id", "object_type", "deleted_at"],
    )

    op.create_table(
        "casefile_refs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("from_object_id", sa.BigInteger(), nullable=False),
        sa.Column("to_object_id", sa.BigInteger(), nullable=False),
        sa.Column("field_path", sa.String(length=512), nullable=False),
        sa.Column("ref_kind", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "metadata_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "field_path ~ '^/'", name=op.f("ck_casefile_refs_field_path_json_pointer")
        ),
        sa.CheckConstraint(
            "ref_kind ~ '^[a-z][a-z0-9_]*$'", name=op.f("ck_casefile_refs_ref_kind_format")
        ),
        sa.CheckConstraint("ordinal >= 1", name=op.f("ck_casefile_refs_ordinal_positive")),
        sa.CheckConstraint(
            "jsonb_typeof(metadata_jsonb) = 'object'",
            name=op.f("ck_casefile_refs_metadata_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_casefile_refs_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "from_object_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_casefile_refs_from_object",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "to_object_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_casefile_refs_to_object",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_casefile_refs")),
        sa.UniqueConstraint(
            "draft_id",
            "from_object_id",
            "field_path",
            "ref_kind",
            "ordinal",
            name="uq_casefile_refs_source_ordinal",
        ),
        sa.UniqueConstraint(
            "draft_id",
            "from_object_id",
            "field_path",
            "ref_kind",
            "to_object_id",
            name="uq_casefile_refs_target",
        ),
    )
    op.create_index(
        "ix_casefile_refs_draft_id_from_object_id", "casefile_refs", ["draft_id", "from_object_id"]
    )
    op.create_index(
        "ix_casefile_refs_draft_id_to_object_id", "casefile_refs", ["draft_id", "to_object_id"]
    )

    op.create_table(
        "draft_operations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_object_id", sa.BigInteger(), nullable=True),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("operation_group_no", sa.BigInteger(), nullable=False),
        sa.Column("operation_type", sa.String(length=16), nullable=False),
        sa.Column("field_path", sa.String(length=512), nullable=False),
        sa.Column("old_value_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_ref", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("sequence_no >= 1", name=op.f("ck_draft_operations_sequence_positive")),
        sa.CheckConstraint(
            "operation_group_no >= 1", name=op.f("ck_draft_operations_group_positive")
        ),
        sa.CheckConstraint(
            "operation_type IN ('add', 'remove', 'replace')",
            name=op.f("ck_draft_operations_type_allowed"),
        ),
        sa.CheckConstraint(
            "field_path = '' OR field_path ~ '^/'",
            name=op.f("ck_draft_operations_field_path_json_pointer"),
        ),
        sa.CheckConstraint(
            "base_revision >= 1", name=op.f("ck_draft_operations_base_revision_positive")
        ),
        sa.CheckConstraint(
            "result_revision = base_revision + 1", name=op.f("ck_draft_operations_revision_step")
        ),
        sa.CheckConstraint(
            "(actor_kind = 'user' AND actor_user_id IS NOT NULL AND actor_ref IS NULL) OR "
            "(actor_kind IN ('agent', 'system', 'import') AND actor_user_id IS NULL "
            "AND actor_ref IS NOT NULL AND length(btrim(actor_ref)) > 0)",
            name=op.f("ck_draft_operations_actor_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_draft_operations_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_draft_operations_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "casefile_object_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_draft_operations_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_draft_operations")),
        sa.UniqueConstraint(
            "draft_id", "sequence_no", name="uq_draft_operations_draft_id_sequence_no"
        ),
    )
    op.create_index(
        "ix_draft_operations_draft_id_created_at", "draft_operations", ["draft_id", "created_at"]
    )
    op.create_index(
        "ix_draft_operations_draft_id_operation_group_no_sequence_no",
        "draft_operations",
        ["draft_id", "operation_group_no", "sequence_no"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_draft_operations_draft_id_operation_group_no_sequence_no", table_name="draft_operations"
    )
    op.drop_index("ix_draft_operations_draft_id_created_at", table_name="draft_operations")
    op.drop_table("draft_operations")
    op.drop_index("ix_casefile_refs_draft_id_to_object_id", table_name="casefile_refs")
    op.drop_index("ix_casefile_refs_draft_id_from_object_id", table_name="casefile_refs")
    op.drop_table("casefile_refs")
    op.drop_index(
        "ix_casefile_objects_casefile_id_object_type_deleted_at", table_name="casefile_objects"
    )
    op.drop_table("casefile_objects")
    op.drop_table("drafts")
    op.drop_index("ix_casefiles_project_id_status_updated_at", table_name="casefiles")
    op.drop_table("casefiles")
