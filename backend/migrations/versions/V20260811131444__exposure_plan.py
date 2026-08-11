"""Add one versioned linear Exposure Plan per Draft.

Revision ID: 20260811131444
Revises: 20260809224245
Create Date: 2026-08-11 13:14:45.454256
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811131444"
down_revision: str | None = "20260809224245"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exposure_plans",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "revision",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("current_revision_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
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
            "revision >= 0",
            name=op.f("ck_exposure_plans_revision_non_negative"),
        ),
        sa.CheckConstraint(
            "(revision = 0 AND current_revision_id IS NULL) OR "
            "(revision >= 1 AND current_revision_id IS NOT NULL)",
            name=op.f("ck_exposure_plans_revision_pointer_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_exposure_plans_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_exposure_plans_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exposure_plans")),
        sa.UniqueConstraint("draft_id", name="uq_exposure_plans_draft_id"),
        sa.UniqueConstraint(
            "project_id",
            "casefile_id",
            "draft_id",
            "id",
            name="uq_exposure_plans_lineage_id",
        ),
    )
    op.create_table(
        "exposure_plan_revisions",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision_no >= 1",
            name=op.f("ck_exposure_plan_revisions_revision_no_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_exposure_plan_revisions_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "plan_id"],
            [
                "exposure_plans.project_id",
                "exposure_plans.casefile_id",
                "exposure_plans.draft_id",
                "exposure_plans.id",
            ],
            name="fk_exposure_plan_revisions_lineage_plan_exposure_plans",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_exposure_plan_revisions"),
        ),
        sa.UniqueConstraint(
            "plan_id",
            "revision_no",
            name="uq_exposure_plan_revisions_plan_id_revision_no",
        ),
        sa.UniqueConstraint(
            "project_id",
            "casefile_id",
            "draft_id",
            "id",
            name="uq_exposure_plan_revisions_lineage_id",
        ),
        sa.UniqueConstraint(
            "project_id",
            "casefile_id",
            "draft_id",
            "plan_id",
            "id",
            name="uq_exposure_plan_revisions_plan_lineage_id",
        ),
    )
    op.create_table(
        "exposure_plan_entries",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_key", sa.String(length=160), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "sequence_no >= 1",
            name=op.f("ck_exposure_plan_entries_sequence_no_positive"),
        ),
        sa.CheckConstraint(
            "entry_key ~ '^exposure_[a-z0-9][a-z0-9_]{0,150}$'",
            name=op.f("ck_exposure_plan_entries_entry_key_format"),
        ),
        sa.CheckConstraint(
            "length(btrim(title)) > 0",
            name=op.f("ck_exposure_plan_entries_title_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "plan_revision_id"],
            [
                "exposure_plan_revisions.project_id",
                "exposure_plan_revisions.casefile_id",
                "exposure_plan_revisions.draft_id",
                "exposure_plan_revisions.id",
            ],
            name="fk_exposure_plan_entries_lineage_revision_revisions",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exposure_plan_entries")),
        sa.UniqueConstraint(
            "plan_revision_id",
            "sequence_no",
            name="uq_exposure_plan_entries_revision_sequence_no",
        ),
        sa.UniqueConstraint(
            "plan_revision_id",
            "entry_key",
            name="uq_exposure_plan_entries_revision_entry_key",
        ),
        sa.UniqueConstraint(
            "project_id",
            "casefile_id",
            "draft_id",
            "id",
            name="uq_exposure_plan_entries_lineage_id",
        ),
    )
    op.create_table(
        "exposure_plan_entry_refs",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_id", sa.BigInteger(), nullable=False),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "ordinal >= 1",
            name=op.f("ck_exposure_plan_entry_refs_ordinal_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "entry_id"],
            [
                "exposure_plan_entries.project_id",
                "exposure_plan_entries.casefile_id",
                "exposure_plan_entries.draft_id",
                "exposure_plan_entries.id",
            ],
            name="fk_exposure_plan_entry_refs_lineage_entry_entries",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "object_registry_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_exposure_plan_entry_refs_lineage_object_casefile_objects",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_exposure_plan_entry_refs"),
        ),
        sa.UniqueConstraint(
            "entry_id",
            "ordinal",
            name="uq_exposure_plan_entry_refs_entry_id_ordinal",
        ),
        sa.UniqueConstraint(
            "entry_id",
            "object_registry_id",
            name="uq_exposure_plan_entry_refs_entry_object_registry",
        ),
    )
    op.create_foreign_key(
        "fk_exposure_plans_lineage_current_revision_revisions",
        "exposure_plans",
        "exposure_plan_revisions",
        [
            "project_id",
            "casefile_id",
            "draft_id",
            "id",
            "current_revision_id",
        ],
        ["project_id", "casefile_id", "draft_id", "plan_id", "id"],
        ondelete="RESTRICT",
        use_alter=True,
        deferrable=True,
        initially="DEFERRED",
    )

    op.execute(
        """
        INSERT INTO exposure_plans (
            project_id,
            casefile_id,
            draft_id,
            revision,
            current_revision_id,
            created_by_user_id
        )
        SELECT draft.project_id,
               draft.casefile_id,
               draft.id,
               0,
               NULL,
               project.owner_user_id
          FROM drafts AS draft
          JOIN projects AS project ON project.id = draft.project_id
        """
    )
    op.execute(
        """
        CREATE FUNCTION casefile_create_exposure_plan_for_draft()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            owner_id bigint;
        BEGIN
            SELECT owner_user_id INTO owner_id
              FROM projects
             WHERE id = NEW.project_id;
            IF owner_id IS NULL THEN
                RAISE EXCEPTION 'Exposure Plan Draft owner does not exist';
            END IF;
            INSERT INTO exposure_plans (
                project_id,
                casefile_id,
                draft_id,
                revision,
                current_revision_id,
                created_by_user_id
            ) VALUES (
                NEW.project_id,
                NEW.casefile_id,
                NEW.id,
                0,
                NULL,
                owner_id
            );
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_drafts_create_exposure_plan
        AFTER INSERT ON drafts
        FOR EACH ROW EXECUTE FUNCTION casefile_create_exposure_plan_for_draft()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_exposure_plans_updated_at
        BEFORE UPDATE ON exposure_plans
        FOR EACH ROW EXECUTE FUNCTION casefile_set_updated_at()
        """
    )
    for table_name in (
        "exposure_plan_revisions",
        "exposure_plan_entries",
        "exposure_plan_entry_refs",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION casefile_reject_history_mutation()
            """
        )


def downgrade() -> None:
    for table_name in (
        "exposure_plan_entry_refs",
        "exposure_plan_entries",
        "exposure_plan_revisions",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP TRIGGER trg_exposure_plans_updated_at ON exposure_plans")
    op.execute("DROP TRIGGER trg_drafts_create_exposure_plan ON drafts")
    op.execute("DROP FUNCTION casefile_create_exposure_plan_for_draft()")
    op.drop_constraint(
        "fk_exposure_plans_lineage_current_revision_revisions",
        "exposure_plans",
        type_="foreignkey",
    )
    op.drop_table("exposure_plan_entry_refs")
    op.drop_table("exposure_plan_entries")
    op.drop_table("exposure_plan_revisions")
    op.drop_table("exposure_plans")
