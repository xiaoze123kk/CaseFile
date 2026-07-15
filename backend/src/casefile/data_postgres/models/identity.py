"""Identity, workspace, membership, and workspace-setting tables."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from casefile.data_postgres.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A local user now and an account identity when authentication is enabled."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("public_id ~ '^user_[0-9a-z_]+$'", name="public_id_format"),
        CheckConstraint("status IN ('active', 'disabled')", name="status_allowed"),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'")
    )


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The root isolation boundary for all CaseFile business data."""

    __tablename__ = "workspaces"
    __table_args__ = (
        CheckConstraint("public_id ~ '^ws_[0-9a-z_]+$'", name="public_id_format"),
        CheckConstraint("status IN ('active', 'archived')", name="status_allowed"),
    )

    public_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'")
    )


class Membership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A user's role and lifecycle state inside one workspace."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("workspace_id", "public_id", name="uq_memberships_workspace_public_id"),
        UniqueConstraint("workspace_id", "user_id", name="uq_memberships_workspace_user"),
        CheckConstraint("public_id ~ '^membership_[0-9a-z_]+$'", name="public_id_format"),
        CheckConstraint("role IN ('owner', 'admin', 'author', 'reviewer')", name="role_allowed"),
        CheckConstraint("status IN ('active', 'inactive')", name="status_allowed"),
        Index("ix_memberships_workspace_id", "workspace_id"),
        Index("ix_memberships_user_id", "user_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'active'")
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class WorkspaceSetting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single JSON settings document owned by one workspace."""

    __tablename__ = "workspace_settings"
    __table_args__ = (
        UniqueConstraint("workspace_id", "public_id", name="uq_workspace_settings_public_id"),
        CheckConstraint("public_id ~ '^wsetting_[0-9a-z_]+$'", name="public_id_format"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    public_id: Mapped[str] = mapped_column(String(64), nullable=False)
    settings_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
