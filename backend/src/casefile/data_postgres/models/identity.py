"""Personal account and single-owner project persistence models."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from casefile.data_postgres.base import Base, BigIntIdentityPrimaryKeyMixin, TimestampMixin


class User(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """A personal CaseFile account identity; authentication is out of scope."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("length(btrim(display_name)) > 0", name="display_name_not_blank"),
        CheckConstraint("status IN ('active', 'disabled')", name="status_allowed"),
        Index("ix_users_status_updated_at", "status", "updated_at"),
    )

    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'active'"),
    )


class Project(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """The personal product's aggregate root, owned by exactly one user."""

    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("status IN ('active', 'archived')", name="status_allowed"),
        CheckConstraint(
            "(status = 'active' AND archived_at IS NULL) OR "
            "(status = 'archived' AND archived_at IS NOT NULL)",
            name="archive_state_consistent",
        ),
        CheckConstraint("jsonb_typeof(profile_jsonb) = 'object'", name="profile_is_object"),
        Index(
            "ix_projects_owner_user_id_status_updated_at",
            "owner_user_id",
            "status",
            "updated_at",
        ),
    )

    owner_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    profile_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'active'"),
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
