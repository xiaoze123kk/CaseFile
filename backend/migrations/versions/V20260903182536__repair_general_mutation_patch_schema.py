"""repair_general_mutation_patch_schema

Revision ID: 20260903182536
Revises: 20260829142035
Create Date: 2026-09-03 18:25:37.127036
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903182536"
down_revision: str | None = "20260829142035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    object_checks = {check["name"] for check in inspector.get_check_constraints("casefile_objects")}
    object_confirmation_check = op.f("ck_casefile_objects_confirmation_status_allowed")
    if object_confirmation_check in object_checks:
        op.drop_constraint(
            object_confirmation_check,
            "casefile_objects",
            type_="check",
        )
    op.create_check_constraint(
        object_confirmation_check,
        "casefile_objects",
        "confirmation_status IN ('user_confirmed', 'ai_inferred', 'unresolved', 'proposed')",
    )

    operation_checks = {
        check["name"] for check in inspector.get_check_constraints("agent_patch_operations")
    }
    target_shape_check = op.f("ck_agent_patch_operations_target_object_shape")
    if target_shape_check in operation_checks:
        op.drop_constraint(
            target_shape_check,
            "agent_patch_operations",
            type_="check",
        )
    op.create_check_constraint(
        target_shape_check,
        "agent_patch_operations",
        "operation_type = 'update_field' OR "
        "(operation_type = 'create_object' AND target_object_id IS NULL) OR "
        "(operation_type NOT IN ('create_object', 'update_field') "
        "AND target_object_id IS NOT NULL)",
    )

    patch_columns = {column["name"] for column in inspector.get_columns("agent_patch_sets")}
    missing_columns = {
        "plan_version": sa.Column("plan_version", sa.String(80)),
        "capability_policy_version": sa.Column("capability_policy_version", sa.String(80)),
        "binder_version": sa.Column("binder_version", sa.String(80)),
        "review_mode": sa.Column(
            "review_mode",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'selective'"),
        ),
        "plan_hash": sa.Column("plan_hash", sa.String(64)),
        "impact_hash": sa.Column("impact_hash", sa.String(64)),
        "contains_delete": sa.Column(
            "contains_delete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    }
    for name, column in missing_columns.items():
        if name not in patch_columns:
            op.add_column("agent_patch_sets", column)

    patch_checks = {check["name"] for check in inspector.get_check_constraints("agent_patch_sets")}
    checks = {
        op.f("ck_agent_patch_sets_review_mode_allowed"): "review_mode IN ('selective', 'atomic')",
        op.f(
            "ck_agent_patch_sets_general_mutation_lineage_complete"
        ): "review_mode = 'selective' OR (plan_version IS NOT NULL "
        "AND capability_policy_version IS NOT NULL AND binder_version IS NOT NULL "
        "AND plan_hash IS NOT NULL AND impact_hash IS NOT NULL)",
        op.f(
            "ck_agent_patch_sets_plan_hash_format"
        ): "plan_hash IS NULL OR plan_hash ~ '^[0-9a-f]{64}$'",
        op.f(
            "ck_agent_patch_sets_impact_hash_format"
        ): "impact_hash IS NULL OR impact_hash ~ '^[0-9a-f]{64}$'",
    }
    for name, condition in checks.items():
        if name not in patch_checks:
            op.create_check_constraint(name, "agent_patch_sets", condition)


def downgrade() -> None:
    # The previous revision already declares this schema. This migration only
    # repairs databases whose recorded revision drifted from their real DDL.
    pass
