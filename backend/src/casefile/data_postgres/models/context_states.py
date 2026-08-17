"""Append-only rolling Thread Memory states for Agent context compaction."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from casefile.data_postgres.base import Base, BigIntIdentityPrimaryKeyMixin


class AgentThreadContextState(BigIntIdentityPrimaryKeyMixin, Base):
    """One immutable, append-only Thread Memory snapshot for a conversation.

    Each row records the raw message range that was compacted
    (``from_message_seq``/``to_message_seq``), the input hash for replay, and
    the validated structured state. Compression never deletes evidence: raw
    messages remain in ``agent_messages`` and pointers stay resolvable.
    """

    __tablename__ = "agent_thread_context_states"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "thread_id"],
            ["agent_threads.project_id", "agent_threads.id"],
            name="fk_agent_thread_context_states_project_thread_agent_threads",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_agent_thread_context_states_project_id_id",
        ),
        CheckConstraint(
            "length(btrim(policy_version)) > 0",
            name="policy_version_not_blank",
        ),
        CheckConstraint("state_kind = 'thread_memory'", name="state_kind_allowed"),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="input_hash_format",
        ),
        CheckConstraint(
            "(from_message_seq IS NULL AND to_message_seq IS NULL) OR "
            "(from_message_seq >= 1 AND to_message_seq >= from_message_seq)",
            name="message_range_shape",
        ),
        Index(
            "ix_agent_thread_context_states_thread_from_seq",
            "thread_id",
            "from_message_seq",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    state_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    from_message_seq: Mapped[int | None] = mapped_column(BigInteger)
    to_message_seq: Mapped[int | None] = mapped_column(BigInteger)
    state_jsonb: Mapped[Any] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


__all__ = ["AgentThreadContextState"]
