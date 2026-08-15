"""Add cleared project status for one-click archive clearing."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260813160000"
down_revision: str | None = "20260813120000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_projects_status_allowed"), "projects", type_="check")
    op.drop_constraint(
        op.f("ck_projects_archive_state_consistent"), "projects", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_projects_status_allowed"),
        "projects",
        "status IN ('active', 'archived', 'cleared')",
    )
    op.create_check_constraint(
        op.f("ck_projects_archive_state_consistent"),
        "projects",
        "(status = 'active' AND archived_at IS NULL) OR "
        "(status = 'archived' AND archived_at IS NOT NULL) OR "
        "(status = 'cleared' AND archived_at IS NOT NULL)",
    )


def downgrade() -> None:
    # 回退前把已清空的卷宗还原为归档，避免违反旧约束。
    op.execute("UPDATE projects SET status = 'archived' WHERE status = 'cleared'")
    op.drop_constraint(op.f("ck_projects_status_allowed"), "projects", type_="check")
    op.drop_constraint(
        op.f("ck_projects_archive_state_consistent"), "projects", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_projects_status_allowed"),
        "projects",
        "status IN ('active', 'archived')",
    )
    op.create_check_constraint(
        op.f("ck_projects_archive_state_consistent"),
        "projects",
        "(status = 'active' AND archived_at IS NULL) OR "
        "(status = 'archived' AND archived_at IS NOT NULL)",
    )
