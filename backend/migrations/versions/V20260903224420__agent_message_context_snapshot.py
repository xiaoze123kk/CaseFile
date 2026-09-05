"""agent_message_context_snapshot

Revision ID: 20260903224420
Revises: 20260903182536
Create Date: 2026-09-03 22:44:21.773287
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903224420"
down_revision: str | None = "20260903182536"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_message_contexts",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("view", sa.String(length=64), nullable=True),
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
            "draft_revision >= 1",
            name=op.f("ck_agent_message_contexts_draft_revision_positive"),
        ),
        sa.CheckConstraint(
            "view IS NULL OR length(btrim(view)) > 0",
            name=op.f("ck_agent_message_contexts_view_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "thread_id", "message_id"],
            [
                "agent_messages.project_id",
                "agent_messages.thread_id",
                "agent_messages.id",
            ],
            name="fk_agent_message_contexts_message_agent_messages",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_agent_message_contexts_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_message_contexts")),
        sa.UniqueConstraint(
            "message_id",
            name="uq_agent_message_contexts_message_id",
        ),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_agent_message_contexts_project_id_id",
        ),
    )
    op.create_table(
        "agent_message_context_refs",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("context_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("ref_kind", sa.String(length=24), nullable=False),
        sa.Column("ref_id", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "ordinal >= 1",
            name=op.f("ck_agent_message_context_refs_ordinal_positive"),
        ),
        sa.CheckConstraint(
            "ref_kind IN ('object', 'event', 'validation_issue')",
            name=op.f("ck_agent_message_context_refs_ref_kind_allowed"),
        ),
        sa.CheckConstraint(
            "length(btrim(ref_id)) > 0",
            name=op.f("ck_agent_message_context_refs_ref_id_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "context_id"],
            ["agent_message_contexts.project_id", "agent_message_contexts.id"],
            name="fk_agent_message_context_refs_context_agent_message_contexts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_message_context_refs")),
        sa.UniqueConstraint(
            "context_id",
            "ref_kind",
            "ref_id",
            name="uq_agent_message_context_refs_context_kind_ref",
        ),
        sa.UniqueConstraint(
            "context_id",
            "ordinal",
            name="uq_agent_message_context_refs_context_ordinal",
        ),
    )
    op.create_index(
        "ix_agent_message_context_refs_context_ordinal",
        "agent_message_context_refs",
        ["context_id", "ordinal"],
        unique=False,
    )

    op.execute(
        """
        INSERT INTO agent_message_contexts (
            project_id,
            casefile_id,
            draft_id,
            thread_id,
            message_id,
            draft_revision,
            view
        )
        SELECT
            task.project_id,
            task.casefile_id,
            task.draft_id,
            task.agent_thread_id,
            task.input_message_id,
            task.input_draft_revision,
            NULLIF(btrim(task.input_jsonb -> 'focus' ->> 'view'), '')
        FROM task_runs AS task
        JOIN agent_messages AS message
          ON message.project_id = task.project_id
         AND message.thread_id = task.agent_thread_id
         AND message.id = task.input_message_id
        WHERE task.task_type = 'casefile_chat'
          AND task.input_message_id IS NOT NULL
          AND task.agent_thread_id IS NOT NULL
          AND message.role = 'user'
          AND jsonb_typeof(task.input_jsonb -> 'focus') = 'object'
          AND NOT EXISTS (
              SELECT 1 FROM task_runs AS other
              WHERE other.input_message_id = task.input_message_id
                AND other.id <> task.id
                AND (other.draft_id, other.input_draft_revision, other.input_jsonb -> 'focus')
                    IS DISTINCT FROM
                    (task.draft_id, task.input_draft_revision, task.input_jsonb -> 'focus')
          )
        ON CONFLICT (message_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO agent_message_context_refs (
            project_id,
            context_id,
            ordinal,
            ref_kind,
            ref_id
        )
        SELECT
            context.project_id,
            context.id,
            refs.kind_offset + refs.item_ordinal,
            refs.ref_kind,
            refs.ref_id
        FROM task_runs AS task
        JOIN agent_message_contexts AS context
          ON context.project_id = task.project_id
         AND context.message_id = task.input_message_id
        CROSS JOIN LATERAL (
            SELECT 0 AS kind_offset, 'object' AS ref_kind, value AS ref_id,
                   ordinal::integer AS item_ordinal
            FROM jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(task.input_jsonb -> 'focus' -> 'object_ids') = 'array'
                     THEN task.input_jsonb -> 'focus' -> 'object_ids' ELSE '[]'::jsonb END
            ) WITH ORDINALITY AS object_ref(value, ordinal)
            UNION ALL
            SELECT 50, 'event', value, ordinal::integer
            FROM jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(task.input_jsonb -> 'focus' -> 'event_ids') = 'array'
                     THEN task.input_jsonb -> 'focus' -> 'event_ids' ELSE '[]'::jsonb END
            ) WITH ORDINALITY AS event_ref(value, ordinal)
            UNION ALL
            SELECT 100, 'validation_issue', value, ordinal::integer
            FROM jsonb_array_elements_text(
                CASE WHEN jsonb_typeof(
                    task.input_jsonb -> 'focus' -> 'validation_issue_ids') = 'array'
                     THEN task.input_jsonb -> 'focus' -> 'validation_issue_ids' ELSE '[]'::jsonb END
            ) WITH ORDINALITY AS issue_ref(value, ordinal)
        ) AS refs
        WHERE task.task_type = 'casefile_chat'
          AND length(refs.ref_id) <= 128 AND length(btrim(refs.ref_id)) > 0
          AND refs.item_ordinal <= 50
        ON CONFLICT (context_id, ref_kind, ref_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_message_context_refs_context_ordinal",
        table_name="agent_message_context_refs",
    )
    op.drop_table("agent_message_context_refs")
    op.drop_table("agent_message_contexts")
