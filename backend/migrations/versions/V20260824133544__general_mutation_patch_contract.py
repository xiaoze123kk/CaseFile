"""general_mutation_patch_contract

Revision ID: 20260824133544
Revises: 20260823133155
Create Date: 2026-08-24 13:35:46.851638
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '20260824133544'
down_revision: str | None = '20260823133155'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_casefile_objects_confirmation_status_allowed"),
        "casefile_objects",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_casefile_objects_confirmation_status_allowed"),
        "casefile_objects",
        "confirmation_status IN "
        "('user_confirmed', 'ai_inferred', 'unresolved', 'proposed')",
    )
    op.drop_constraint(
        op.f("ck_agent_patch_operations_target_object_shape"),
        "agent_patch_operations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_target_object_shape"),
        "agent_patch_operations",
        "operation_type = 'update_field' OR "
        "(operation_type = 'create_object' AND target_object_id IS NULL) OR "
        "(operation_type NOT IN ('create_object', 'update_field') "
        "AND target_object_id IS NOT NULL)",
    )
    op.add_column("agent_patch_sets", sa.Column("plan_version", sa.String(80)))
    op.add_column(
        "agent_patch_sets", sa.Column("capability_policy_version", sa.String(80))
    )
    op.add_column("agent_patch_sets", sa.Column("binder_version", sa.String(80)))
    op.add_column(
        "agent_patch_sets",
        sa.Column(
            "review_mode",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'selective'"),
        ),
    )
    op.add_column("agent_patch_sets", sa.Column("plan_hash", sa.String(64)))
    op.add_column("agent_patch_sets", sa.Column("impact_hash", sa.String(64)))
    op.add_column(
        "agent_patch_sets",
        sa.Column(
            "contains_delete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_sets_review_mode_allowed"),
        "agent_patch_sets",
        "review_mode IN ('selective', 'atomic')",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_sets_general_mutation_lineage_complete"),
        "agent_patch_sets",
        "review_mode = 'selective' OR (plan_version IS NOT NULL "
        "AND capability_policy_version IS NOT NULL AND binder_version IS NOT NULL "
        "AND plan_hash IS NOT NULL AND impact_hash IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_sets_plan_hash_format"),
        "agent_patch_sets",
        "plan_hash IS NULL OR plan_hash ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_sets_impact_hash_format"),
        "agent_patch_sets",
        "impact_hash IS NULL OR impact_hash ~ '^[0-9a-f]{64}$'",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_agent_patch_sets_impact_hash_format"),
        "agent_patch_sets",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_patch_sets_plan_hash_format"),
        "agent_patch_sets",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_patch_sets_general_mutation_lineage_complete"),
        "agent_patch_sets",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_patch_sets_review_mode_allowed"),
        "agent_patch_sets",
        type_="check",
    )
    op.drop_column("agent_patch_sets", "contains_delete")
    op.drop_column("agent_patch_sets", "impact_hash")
    op.drop_column("agent_patch_sets", "plan_hash")
    op.drop_column("agent_patch_sets", "review_mode")
    op.drop_column("agent_patch_sets", "binder_version")
    op.drop_column("agent_patch_sets", "capability_policy_version")
    op.drop_column("agent_patch_sets", "plan_version")
    op.drop_constraint(
        op.f("ck_agent_patch_operations_target_object_shape"),
        "agent_patch_operations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_agent_patch_operations_target_object_shape"),
        "agent_patch_operations",
        "(operation_type = 'create_object' AND target_object_id IS NULL) OR "
        "(operation_type <> 'create_object' AND target_object_id IS NOT NULL)",
    )
    op.drop_constraint(
        op.f("ck_casefile_objects_confirmation_status_allowed"),
        "casefile_objects",
        type_="check",
    )
    op.execute(
        "UPDATE casefile_objects SET confirmation_status = 'ai_inferred' "
        "WHERE confirmation_status = 'proposed'"
    )
    op.create_check_constraint(
        op.f("ck_casefile_objects_confirmation_status_allowed"),
        "casefile_objects",
        "confirmation_status IN ('user_confirmed', 'ai_inferred', 'unresolved')",
    )
