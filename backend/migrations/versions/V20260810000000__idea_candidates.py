"""Create idea_candidates table for Path B (帮我想一个) creative directions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810000000"
down_revision: str | None = "20260809224245"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idea_candidates",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("batch_id", sa.String(36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("content_jsonb", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("bookmarked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bookmarked_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idea_candidates")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name=op.f("fk_idea_candidates_created_by_user_id_users"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bookmarked_by_user_id"], ["users.id"],
            name=op.f("fk_idea_candidates_bookmarked_by_user_id_users"), ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("project_id", "batch_id", "ordinal",
                           name=op.f("uq_idea_candidates_batch_ordinal")),
        sa.UniqueConstraint("project_id", "id",
                           name=op.f("uq_idea_candidates_project_id_id")),
        sa.CheckConstraint("ordinal BETWEEN 1 AND 3",
                           name=op.f("ck_idea_candidates_ordinal_range")),
        sa.CheckConstraint("status IN ('active', 'bookmarked', 'archived', 'selected')",
                           name=op.f("ck_idea_candidates_status_allowed")),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'",
                           name=op.f("ck_idea_candidates_content_hash_format")),
        sa.CheckConstraint("jsonb_typeof(content_jsonb) = 'object'",
                           name=op.f("ck_idea_candidates_content_is_object")),
        sa.CheckConstraint(
            "(bookmarked_at IS NULL AND bookmarked_by_user_id IS NULL) OR "
            "(bookmarked_at IS NOT NULL AND bookmarked_by_user_id IS NOT NULL)",
            name=op.f("ck_idea_candidates_bookmark_consistent")),
    )
    op.create_index("ix_idea_candidates_project_batch", "idea_candidates",
                    ["project_id", "batch_id"])
    op.create_index("ix_idea_candidates_project_status", "idea_candidates",
                    ["project_id", "status"])


def downgrade() -> None:
    op.drop_table("idea_candidates")
