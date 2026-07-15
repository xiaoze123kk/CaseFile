"""创建用户与工作区地基。

Revision ID: 20260715145030
Revises: None
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260715145030"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCAL_USER_UUID = UUID("00000000-0000-0000-0000-000000000001")
LOCAL_WORKSPACE_UUID = UUID("00000000-0000-0000-0000-000000000010")
LOCAL_MEMBERSHIP_UUID = UUID("00000000-0000-0000-0000-000000000020")
LOCAL_SETTINGS_UUID = UUID("00000000-0000-0000-0000-000000000030")


def upgrade() -> None:
    users = op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
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
        sa.CheckConstraint("public_id ~ '^user_[0-9a-z_]+$'", name="ck_users_public_id_format"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status_allowed"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("public_id", name="uq_users_public_id"),
    )

    workspaces = op.create_table(
        "workspaces",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
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
        sa.CheckConstraint("public_id ~ '^ws_[0-9a-z_]+$'", name="ck_workspaces_public_id_format"),
        sa.CheckConstraint("status IN ('active', 'archived')", name="ck_workspaces_status_allowed"),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
        sa.UniqueConstraint("public_id", name="uq_workspaces_public_id"),
        sa.UniqueConstraint("slug", name="uq_workspaces_slug"),
    )

    memberships = op.create_table(
        "memberships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
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
            "public_id ~ '^membership_[0-9a-z_]+$'",
            name="ck_memberships_public_id_format",
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'author', 'reviewer')",
            name="ck_memberships_role_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_memberships_status_allowed"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships_user_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_memberships_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
        sa.UniqueConstraint(
            "workspace_id", "public_id", name="uq_memberships_workspace_public_id"
        ),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_memberships_workspace_user"),
    )
    op.create_index("ix_memberships_workspace_id", "memberships", ["workspace_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])

    op.create_table(
        "workspace_settings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column(
            "settings_jsonb",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "public_id ~ '^wsetting_[0-9a-z_]+$'",
            name="ck_workspace_settings_public_id_format",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_workspace_settings_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspace_settings"),
        sa.UniqueConstraint("workspace_id", name="uq_workspace_settings_workspace_id"),
        sa.UniqueConstraint(
            "workspace_id", "public_id", name="uq_workspace_settings_public_id"
        ),
    )

    op.bulk_insert(
        users,
        [
            {
                "id": LOCAL_USER_UUID,
                "public_id": "user_local_owner",
                "display_name": "Local Owner",
                "status": "active",
            }
        ],
    )
    op.bulk_insert(
        workspaces,
        [
            {
                "id": LOCAL_WORKSPACE_UUID,
                "public_id": "ws_local",
                "slug": "local",
                "name": "Local Workspace",
                "status": "active",
            }
        ],
    )
    op.bulk_insert(
        memberships,
        [
            {
                "id": LOCAL_MEMBERSHIP_UUID,
                "workspace_id": LOCAL_WORKSPACE_UUID,
                "user_id": LOCAL_USER_UUID,
                "public_id": "membership_local_owner",
                "role": "owner",
                "status": "active",
            }
        ],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO workspace_settings (id, workspace_id, public_id, settings_jsonb)
            VALUES (:id, :workspace_id, 'wsetting_local', '{}'::jsonb)
            """
        ).bindparams(id=LOCAL_SETTINGS_UUID, workspace_id=LOCAL_WORKSPACE_UUID)
    )


def downgrade() -> None:
    op.drop_table("workspace_settings")
    op.drop_index("ix_memberships_user_id", table_name="memberships")
    op.drop_index("ix_memberships_workspace_id", table_name="memberships")
    op.drop_table("memberships")
    op.drop_table("workspaces")
    op.drop_table("users")
