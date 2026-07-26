"""personal_identity_project

Revision ID: 20260726131010
Revises:
Create Date: 2026-07-26 13:10:10.791977
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260726131010"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
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
        sa.CheckConstraint(
            "length(btrim(display_name)) > 0", name=op.f("ck_users_display_name_not_blank")
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name=op.f("ck_users_status_allowed")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index("ix_users_status_updated_at", "users", ["status", "updated_at"])

    op.create_table(
        "projects",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "profile_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'active'"), nullable=False
        ),
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
        sa.CheckConstraint("length(btrim(title)) > 0", name=op.f("ck_projects_title_not_blank")),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name=op.f("ck_projects_status_allowed")
        ),
        sa.CheckConstraint(
            "(status = 'active' AND archived_at IS NULL) OR "
            "(status = 'archived' AND archived_at IS NOT NULL)",
            name=op.f("ck_projects_archive_state_consistent"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(profile_jsonb) = 'object'", name=op.f("ck_projects_profile_is_object")
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_projects_owner_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
    )
    op.create_index(
        "ix_projects_owner_user_id_status_updated_at",
        "projects",
        ["owner_user_id", "status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_projects_owner_user_id_status_updated_at", table_name="projects")
    op.drop_table("projects")
    op.drop_index("ix_users_status_updated_at", table_name="users")
    op.drop_table("users")
