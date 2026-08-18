"""Append-only rolling Thread Memory states for casefile_chat compaction."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260817000000"
down_revision: str | None = "20260813160000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_thread_context_states",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("state_kind", sa.String(length=32), nullable=False),
        sa.Column("from_message_seq", sa.BigInteger(), nullable=True),
        sa.Column("to_message_seq", sa.BigInteger(), nullable=True),
        sa.Column("state_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "thread_id"],
            ["agent_threads.project_id", "agent_threads.id"],
            name="fk_agent_thread_context_states_project_thread_agent_threads",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_agent_thread_context_states_project_id_id",
        ),
        sa.CheckConstraint(
            "length(btrim(policy_version)) > 0",
            name="policy_version_not_blank",
        ),
        sa.CheckConstraint(
            "state_kind = 'thread_memory'",
            name="state_kind_allowed",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="input_hash_format",
        ),
        sa.CheckConstraint(
            "(from_message_seq IS NULL AND to_message_seq IS NULL) OR "
            "(from_message_seq >= 1 AND to_message_seq >= from_message_seq)",
            name="message_range_shape",
        ),
        sa.Index(
            "ix_agent_thread_context_states_thread_from_seq",
            "thread_id",
            "from_message_seq",
        ),
    )


def downgrade() -> None:
    op.drop_table("agent_thread_context_states")
