"""agent_patch_repair_provenance

Revision ID: 20260823133155
Revises: 20260822193348
Create Date: 2026-08-23 13:31:55.769664
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260823133155"
down_revision: str | None = "20260822193348"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_patch_operations",
        sa.Column(
            "origin",
            sa.String(length=24),
            server_default=sa.text("'primary'"),
            nullable=False,
        ),
    )
    op.add_column(
        "agent_patch_operations",
        sa.Column("repair_round", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_patch_operations",
        sa.Column(
            "repair_obligation_keys",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_origin_allowed"),
        "agent_patch_operations",
        "origin IN ('primary', 'closure_repair')",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_repair_round_allowed"),
        "agent_patch_operations",
        "repair_round IS NULL OR repair_round BETWEEN 1 AND 2",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_repair_obligation_keys_array"),
        "agent_patch_operations",
        "jsonb_typeof(repair_obligation_keys) = 'array'",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_repair_obligation_keys_valid"),
        "agent_patch_operations",
        "NOT jsonb_path_exists(repair_obligation_keys, "
        '\'$[*] ? (@.type() != "string" || @ like_regex "^\\\\s*$")\')',
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_repair_provenance_shape"),
        "agent_patch_operations",
        "(origin = 'primary' AND repair_round IS NULL "
        "AND repair_obligation_keys = '[]'::jsonb) OR "
        "(origin = 'closure_repair' AND repair_round IS NOT NULL "
        "AND jsonb_array_length(repair_obligation_keys) > 0)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_agent_patch_operations_repair_provenance_shape"),
        "agent_patch_operations",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_patch_operations_repair_obligation_keys_valid"),
        "agent_patch_operations",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_patch_operations_repair_obligation_keys_array"),
        "agent_patch_operations",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_patch_operations_repair_round_allowed"),
        "agent_patch_operations",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_patch_operations_origin_allowed"),
        "agent_patch_operations",
        type_="check",
    )
    op.drop_column("agent_patch_operations", "repair_obligation_keys")
    op.drop_column("agent_patch_operations", "repair_round")
    op.drop_column("agent_patch_operations", "origin")
