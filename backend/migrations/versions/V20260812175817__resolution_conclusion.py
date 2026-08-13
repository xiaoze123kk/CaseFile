"""resolution_conclusion

Revision ID: 20260812175817
Revises: 20260811131444
Create Date: 2026-08-12 17:58:18.177847
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260812175817"
down_revision: str | None = "20260811131444"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("resolution_specs", sa.Column("conclusion_outcome", sa.String(20)))
    op.add_column("resolution_specs", sa.Column("conclusion_review_status", sa.String(20)))
    op.add_column("resolution_specs", sa.Column("conclusion_summary", sa.Text()))
    op.add_column("resolution_specs", sa.Column("conclusion_rationale", sa.Text()))
    op.add_column(
        "resolution_specs",
        sa.Column(
            "conclusion_unresolved_gaps_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column("resolution_specs", sa.Column("conclusion_confirmed_by_user_id", sa.BigInteger()))
    op.add_column(
        "resolution_specs", sa.Column("conclusion_confirmed_at", sa.DateTime(timezone=True))
    )
    op.create_foreign_key(
        op.f("fk_resolution_specs_conclusion_confirmed_by_user_id_users"),
        "resolution_specs",
        "users",
        ["conclusion_confirmed_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_resolution_specs_conclusion_outcome_allowed"),
        "resolution_specs",
        "conclusion_outcome IS NULL OR conclusion_outcome IN ('answer', 'undetermined')",
    )
    op.create_check_constraint(
        op.f("ck_resolution_specs_conclusion_review_status_allowed"),
        "resolution_specs",
        "conclusion_review_status IS NULL OR conclusion_review_status IN ('proposed', 'confirmed')",
    )
    op.create_check_constraint(
        op.f("ck_resolution_specs_conclusion_content_consistent"),
        "resolution_specs",
        "(conclusion_outcome IS NULL AND conclusion_review_status IS NULL "
        "AND conclusion_summary IS NULL AND conclusion_rationale IS NULL) OR "
        "(conclusion_outcome IS NOT NULL AND conclusion_review_status IS NOT NULL "
        "AND conclusion_summary IS NOT NULL AND conclusion_rationale IS NOT NULL "
        "AND length(btrim(conclusion_summary)) > 0 "
        "AND length(btrim(conclusion_rationale)) > 0)",
    )
    op.create_check_constraint(
        op.f("ck_resolution_specs_conclusion_confirmation_consistent"),
        "resolution_specs",
        "(conclusion_review_status = 'confirmed' "
        "AND conclusion_confirmed_by_user_id IS NOT NULL "
        "AND conclusion_confirmed_at IS NOT NULL) OR "
        "(conclusion_review_status IS DISTINCT FROM 'confirmed' "
        "AND conclusion_confirmed_by_user_id IS NULL "
        "AND conclusion_confirmed_at IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_resolution_specs_conclusion_unresolved_gaps_is_array"),
        "resolution_specs",
        "jsonb_typeof(conclusion_unresolved_gaps_jsonb) = 'array'",
    )


def downgrade() -> None:
    for name in (
        "conclusion_unresolved_gaps_is_array",
        "conclusion_confirmation_consistent",
        "conclusion_content_consistent",
        "conclusion_review_status_allowed",
        "conclusion_outcome_allowed",
    ):
        op.drop_constraint(op.f(f"ck_resolution_specs_{name}"), "resolution_specs", type_="check")
    op.drop_constraint(
        op.f("fk_resolution_specs_conclusion_confirmed_by_user_id_users"),
        "resolution_specs",
        type_="foreignkey",
    )
    for name in (
        "conclusion_confirmed_at",
        "conclusion_confirmed_by_user_id",
        "conclusion_unresolved_gaps_jsonb",
        "conclusion_rationale",
        "conclusion_summary",
        "conclusion_review_status",
        "conclusion_outcome",
    ):
        op.drop_column("resolution_specs", name)
