"""Imported documents and their confirmed item-by-item reverse-parse results."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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


class ImportedDocument(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """One uploaded source document plus its extracted text and parse state."""

    __tablename__ = "imported_documents"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_imported_documents_project_id_id"),
        CheckConstraint(
            "parse_status IN ('queued', 'running', 'succeeded', 'failed')",
            name="parse_status_allowed",
        ),
        CheckConstraint("btrim(filename) <> ''", name="filename_not_blank"),
        CheckConstraint("btrim(media_type) <> ''", name="media_type_not_blank"),
        CheckConstraint("btrim(extracted_text) <> ''", name="extracted_text_not_blank"),
        CheckConstraint("jsonb_typeof(blocks_jsonb) = 'array'", name="blocks_is_array"),
        CheckConstraint(
            "(parse_status = 'succeeded' AND current_task_run_id IS NOT NULL) OR "
            "(parse_status <> 'succeeded' )",
            name="succeeded_has_task",
        ),
        Index("ix_imported_documents_project_created", "project_id", "created_at"),
    )

    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    original_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    blocks_jsonb: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    parse_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'queued'")
    )
    current_task_run_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class ParseItem(BigIntIdentityPrimaryKeyMixin, Base):
    """One extracted item awaiting author confirmation."""

    __tablename__ = "parse_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "document_id"],
            ["imported_documents.project_id", "imported_documents.id"],
            name="fk_parse_items_project_document_imported_documents",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("project_id", "document_id", "id", name="uq_parse_items_lineage_id"),
        CheckConstraint(
            "item_type IN ('entity_alias', 'event', 'information_unit', 'knowledge_state', "
            "'relationship_causality', 'candidate_question', 'candidate_conclusion')",
            name="item_type_allowed",
        ),
        CheckConstraint(
            "grading IN ('explicit', 'inferred', 'needs_confirmation', 'conflicting', "
            "'missing_important')",
            name="grading_allowed",
        ),
        CheckConstraint(
            "confirm_status IN ('unconfirmed', 'confirmed', 'rejected')",
            name="confirm_status_allowed",
        ),
        CheckConstraint(
            "(confirm_status = 'unconfirmed' AND confirmed_by_user_id IS NULL "
            "AND confirmed_at IS NULL) OR "
            "(confirm_status <> 'unconfirmed' AND confirmed_by_user_id IS NOT NULL "
            "AND confirmed_at IS NOT NULL)",
            name="confirm_consistent",
        ),
        CheckConstraint("jsonb_typeof(content_jsonb) = 'object'", name="content_is_object"),
        CheckConstraint("jsonb_typeof(source_block_refs) = 'array'", name="refs_is_array"),
        CheckConstraint("btrim(source_quote) <> ''", name="quote_not_blank"),
        Index("ix_parse_items_document_id", "document_id"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    item_type: Mapped[str] = mapped_column(String(48), nullable=False)
    content_jsonb: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    grading: Mapped[str] = mapped_column(String(32), nullable=False)
    source_block_refs: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    source_quote: Mapped[str] = mapped_column(Text, nullable=False)
    confirm_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'unconfirmed'")
    )
    confirmed_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
