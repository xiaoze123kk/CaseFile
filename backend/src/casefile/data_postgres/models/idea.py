"""Immutable creative idea candidates for Path B (帮我想一个)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from casefile.data_postgres.base import Base, BigIntIdentityPrimaryKeyMixin


class IdeaCandidate(BigIntIdentityPrimaryKeyMixin, Base):
    __tablename__ = "idea_candidates"
    __table_args__ = (
        UniqueConstraint("project_id", "batch_id", "ordinal", name="uq_idea_candidates_batch_ordinal"),
        UniqueConstraint("project_id", "id", name="uq_idea_candidates_project_id_id"),
        CheckConstraint("ordinal BETWEEN 1 AND 3", name="ck_idea_candidates_ordinal_range"),
        CheckConstraint(
            "status IN ('active', 'bookmarked', 'archived', 'selected')",
            name="ck_idea_candidates_status_allowed",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_idea_candidates_content_hash_format"),
        CheckConstraint("jsonb_typeof(content_jsonb) = 'object'", name="ck_idea_candidates_content_is_object"),
        CheckConstraint(
            "(bookmarked_at IS NULL AND bookmarked_by_user_id IS NULL) OR "
            "(bookmarked_at IS NOT NULL AND bookmarked_by_user_id IS NOT NULL)",
            name="ck_idea_candidates_bookmark_consistent",
        ),
        Index("ix_idea_candidates_project_batch", "project_id", "batch_id"),
        Index("ix_idea_candidates_project_status", "project_id", "status"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    content_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    bookmarked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bookmarked_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
