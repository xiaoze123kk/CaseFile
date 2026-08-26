"""exposure_plan_typed_obligations

Revision ID: 20260826115117
Revises: 20260824233834
Create Date: 2026-08-26 11:51:18.873757
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260826115117"
down_revision: str | None = "20260824233834"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "exposure_plan_revisions",
        sa.Column(
            "payload_schema_id",
            sa.String(length=80),
            server_default=sa.text("'casefile.exposure-plan.v1'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_exposure_plan_revisions_payload_schema_id_known"),
        "exposure_plan_revisions",
        "payload_schema_id IN ('casefile.exposure-plan.v1', "
        "'casefile.exposure-plan.v2')",
    )
    op.alter_column(
        "exposure_plan_revisions",
        "payload_schema_id",
        server_default=sa.text("'casefile.exposure-plan.v2'"),
    )
    op.create_unique_constraint(
        "uq_exposure_plan_entries_revision_lineage_id",
        "exposure_plan_entries",
        ["project_id", "casefile_id", "draft_id", "plan_revision_id", "id"],
    )
    op.create_table(
        "exposure_plan_obligations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_id", sa.BigInteger(), nullable=False),
        sa.Column("obligation_key", sa.String(length=160), nullable=False),
        sa.Column("obligation_kind", sa.String(length=40), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("min_distinct", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "obligation_kind IN ('participant_coverage', 'basis_ref_coverage', "
            "'hypothesis_coverage')",
            name=op.f("ck_exposure_plan_obligations_kind_known"),
        ),
        sa.CheckConstraint(
            "level IN ('hard', 'soft')",
            name=op.f("ck_exposure_plan_obligations_level_known"),
        ),
        sa.CheckConstraint(
            "obligation_key ~ '^obligation_[a-z0-9][a-z0-9_]{0,150}$'",
            name=op.f("ck_exposure_plan_obligations_obligation_key_format"),
        ),
        sa.CheckConstraint(
            "(obligation_kind = 'participant_coverage' AND min_distinct >= 1) OR "
            "(obligation_kind <> 'participant_coverage' AND min_distinct IS NULL)",
            name=op.f("ck_exposure_plan_obligations_min_distinct_matches_kind"),
        ),
        sa.ForeignKeyConstraint(
            [
                "project_id",
                "casefile_id",
                "draft_id",
                "plan_revision_id",
                "entry_id",
            ],
            [
                "exposure_plan_entries.project_id",
                "exposure_plan_entries.casefile_id",
                "exposure_plan_entries.draft_id",
                "exposure_plan_entries.plan_revision_id",
                "exposure_plan_entries.id",
            ],
            name="fk_exposure_plan_obligations_lineage_entry_entries",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exposure_plan_obligations")),
        sa.UniqueConstraint(
            "plan_revision_id",
            "obligation_key",
            name="uq_exposure_plan_obligations_revision_key",
        ),
        sa.UniqueConstraint(
            "project_id",
            "casefile_id",
            "draft_id",
            "plan_revision_id",
            "id",
            name="uq_exposure_plan_obligations_revision_lineage_id",
        ),
    )
    op.create_table(
        "exposure_plan_obligation_refs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("obligation_id", sa.BigInteger(), nullable=False),
        sa.Column("object_registry_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "ordinal >= 1",
            name=op.f("ck_exposure_plan_obligation_refs_ordinal_positive"),
        ),
        sa.ForeignKeyConstraint(
            [
                "project_id",
                "casefile_id",
                "draft_id",
                "plan_revision_id",
                "obligation_id",
            ],
            [
                "exposure_plan_obligations.project_id",
                "exposure_plan_obligations.casefile_id",
                "exposure_plan_obligations.draft_id",
                "exposure_plan_obligations.plan_revision_id",
                "exposure_plan_obligations.id",
            ],
            name=(
                "fk_exposure_plan_obligation_refs_lineage_obligation_obligations"
            ),
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
            name="fk_exposure_plan_obligation_refs_lineage_object_registry",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_exposure_plan_obligation_refs"),
        ),
        sa.UniqueConstraint(
            "obligation_id",
            "ordinal",
            name="uq_exposure_plan_obligation_refs_obligation_ordinal",
        ),
        sa.UniqueConstraint(
            "obligation_id",
            "object_registry_id",
            name="uq_exposure_plan_obligation_refs_obligation_object",
        ),
    )
    for table_name in (
        "exposure_plan_obligations",
        "exposure_plan_obligation_refs",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_immutable BEFORE UPDATE OR DELETE "
            f"ON {table_name} FOR EACH ROW EXECUTE FUNCTION "
            "casefile_reject_history_mutation()"
        )


def downgrade() -> None:
    for table_name in (
        "exposure_plan_obligation_refs",
        "exposure_plan_obligations",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    op.drop_table("exposure_plan_obligation_refs")
    op.drop_table("exposure_plan_obligations")
    op.drop_constraint(
        "uq_exposure_plan_entries_revision_lineage_id",
        "exposure_plan_entries",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_exposure_plan_revisions_payload_schema_id_known"),
        "exposure_plan_revisions",
        type_="check",
    )
    op.drop_column("exposure_plan_revisions", "payload_schema_id")
