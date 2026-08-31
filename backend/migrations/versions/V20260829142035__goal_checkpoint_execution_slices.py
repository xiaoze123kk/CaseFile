"""goal_checkpoint_execution_slices

Revision ID: 20260829142035
Revises: 20260829124753
Create Date: 2026-08-29 14:20:36.298099
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260829142035"
down_revision: str | None = "20260829124753"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_agent_goal_obligations_candidate_requires_mutation"),
        "agent_goal_obligations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_agent_goal_obligations_mutation_targets_baseline"),
        "agent_goal_obligations",
        "capability <> 'propose_mutation' OR target_state = 'baseline'",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_agent_goal_obligations_mutation_targets_baseline"),
        "agent_goal_obligations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_agent_goal_obligations_candidate_requires_mutation"),
        "agent_goal_obligations",
        "capability = 'propose_mutation' OR target_state = 'baseline'",
    )
