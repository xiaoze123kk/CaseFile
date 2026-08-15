"""Personal account and single-owner project persistence models."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
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
        CheckConstraint("status IN ('active', 'archived', 'cleared')", name="status_allowed"),
        CheckConstraint(
            "(status = 'active' AND archived_at IS NULL) OR "
            "(status = 'archived' AND archived_at IS NOT NULL) OR "
            "(status = 'cleared' AND archived_at IS NOT NULL)",
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


class UserProviderSetting(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One user's encrypted credential and default model settings for a provider."""

    __tablename__ = "user_provider_settings"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_provider_settings_user_provider"),
        UniqueConstraint("user_id", "id", name="uq_user_provider_settings_user_id_id"),
        CheckConstraint("provider ~ '^[a-z][a-z0-9_]*$'", name="provider_format"),
        CheckConstraint("length(btrim(model_id)) > 0", name="model_id_not_blank"),
        CheckConstraint("key_version >= 1", name="key_version_positive"),
        CheckConstraint("config_version >= 1", name="config_version_positive"),
        CheckConstraint("octet_length(secret_nonce) = 12", name="secret_nonce_length"),
        CheckConstraint("octet_length(secret_ciphertext) > 16", name="ciphertext_not_empty"),
        CheckConstraint("length(secret_last_four) = 4", name="last_four_length"),
        CheckConstraint(
            "credential_status IN ('unverified', 'valid', 'invalid', 'deleted')",
            name="credential_status_allowed",
        ),
        CheckConstraint(
            "(credential_status = 'deleted' "
            "AND credential_deleted_at IS NOT NULL "
            "AND secret_ciphertext IS NULL "
            "AND secret_nonce IS NULL "
            "AND key_version IS NULL "
            "AND secret_last_four IS NULL) "
            "OR (credential_status <> 'deleted' "
            "AND credential_deleted_at IS NULL "
            "AND secret_ciphertext IS NOT NULL "
            "AND secret_nonce IS NOT NULL "
            "AND key_version IS NOT NULL "
            "AND secret_last_four IS NOT NULL)",
            name="credential_material_consistent",
        ),
        CheckConstraint("jsonb_typeof(default_budget_jsonb) = 'object'", name="budget_is_object"),
        Index("ix_user_provider_settings_user_id_updated_at", "user_id", "updated_at"),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model_id: Mapped[str] = mapped_column(String(160), nullable=False)
    model_is_custom: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    config_version: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
    )
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int | None] = mapped_column(
        BigInteger,
        server_default=text("1"),
    )
    secret_last_four: Mapped[str | None] = mapped_column(String(4))
    credential_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'unverified'"),
    )
    default_budget_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_error_code: Mapped[str | None] = mapped_column(String(80))
    credential_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
