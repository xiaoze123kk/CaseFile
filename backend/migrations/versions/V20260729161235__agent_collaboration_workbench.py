"""agent_collaboration_workbench

Revision ID: 20260729161235
Revises: 20260728171649
Create Date: 2026-07-29 16:12:40.736711
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260729161235"
down_revision: str | None = "20260728171649"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _create_agent_threads()
    _create_agent_messages()
    _extend_task_runs()
    _create_agent_patch_sets()
    _create_agent_patch_operations()
    _extend_draft_operation_types()
    _allow_v1_discriminator_edits()
    _create_updated_at_triggers()


def downgrade() -> None:
    _drop_updated_at_triggers()
    _restore_immutable_discriminators()
    _restore_draft_operation_types()
    op.drop_index(
        "ix_agent_patch_operations_patch_set_ordinal",
        table_name="agent_patch_operations",
    )
    op.drop_table("agent_patch_operations")
    op.drop_index("ix_agent_patch_sets_source_message_id", table_name="agent_patch_sets")
    op.drop_index(
        "ix_agent_patch_sets_project_status_created_at",
        table_name="agent_patch_sets",
    )
    op.drop_table("agent_patch_sets")
    _restore_task_runs()
    op.drop_index("ix_agent_messages_thread_sequence_no", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_index(
        "ix_agent_threads_project_pinned_updated_at",
        table_name="agent_threads",
    )
    op.drop_index(
        "ix_agent_threads_project_status_updated_at",
        table_name="agent_threads",
    )
    op.drop_table("agent_threads")


def _create_agent_threads() -> None:
    op.create_table(
        "agent_threads",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column(
            "title_source",
            sa.String(16),
            server_default=sa.text("'auto'"),
            nullable=False,
        ),
        sa.Column(
            "is_pinned",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
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
            "length(btrim(title)) > 0",
            name=op.f("ck_agent_threads_title_not_blank"),
        ),
        sa.CheckConstraint(
            "title_source IN ('auto', 'user')",
            name=op.f("ck_agent_threads_title_source_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name=op.f("ck_agent_threads_status_allowed"),
        ),
        sa.CheckConstraint(
            "(status = 'active' AND archived_at IS NULL) OR "
            "(status = 'archived' AND archived_at IS NOT NULL)",
            name=op.f("ck_agent_threads_archive_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_agent_threads_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_agent_threads_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_agent_threads_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_threads")),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_agent_threads_project_id_id",
        ),
    )
    op.create_index(
        "ix_agent_threads_project_status_updated_at",
        "agent_threads",
        ["project_id", "status", "updated_at"],
    )
    op.create_index(
        "ix_agent_threads_project_pinned_updated_at",
        "agent_threads",
        ["project_id", "is_pinned", "updated_at"],
    )


def _create_agent_messages() -> None:
    op.create_table(
        "agent_messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
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
            "sequence_no >= 1",
            name=op.f("ck_agent_messages_sequence_no_positive"),
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name=op.f("ck_agent_messages_role_allowed"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name=op.f("ck_agent_messages_status_allowed"),
        ),
        sa.CheckConstraint(
            "(role = 'user' AND created_by_user_id IS NOT NULL) OR "
            "(role IN ('assistant', 'system') AND created_by_user_id IS NULL)",
            name=op.f("ck_agent_messages_actor_shape"),
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND role = 'assistant' AND content_text IS NULL) OR "
            "(status = 'failed' AND role = 'assistant') OR "
            "(status = 'completed' AND content_text IS NOT NULL "
            "AND length(btrim(content_text)) > 0)",
            name=op.f("ck_agent_messages_content_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_agent_messages_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "thread_id"],
            ["agent_threads.project_id", "agent_threads.id"],
            name="fk_agent_messages_project_thread_agent_threads",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_messages")),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_agent_messages_project_id_id",
        ),
        sa.UniqueConstraint(
            "thread_id",
            "sequence_no",
            name="uq_agent_messages_thread_sequence_no",
        ),
    )
    op.create_index(
        "ix_agent_messages_thread_sequence_no",
        "agent_messages",
        ["thread_id", "sequence_no"],
    )


def _extend_task_runs() -> None:
    op.add_column("task_runs", sa.Column("agent_thread_id", sa.BigInteger(), nullable=True))
    op.add_column("task_runs", sa.Column("input_message_id", sa.BigInteger(), nullable=True))
    op.add_column("task_runs", sa.Column("output_message_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_task_runs_project_agent_thread_agent_threads",
        "task_runs",
        "agent_threads",
        ["project_id", "agent_thread_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_task_runs_project_input_message_agent_messages",
        "task_runs",
        "agent_messages",
        ["project_id", "input_message_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_task_runs_project_output_message_agent_messages",
        "task_runs",
        "agent_messages",
        ["project_id", "output_message_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        op.f("ck_task_runs_task_type_allowed"),
        "task_runs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_task_runs_input_matches_task_type"),
        "task_runs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_task_type_allowed"),
        "task_runs",
        "task_type IN "
        "('brief_polish', 'brief_anchor_extract', 'brief_to_draft', 'casefile_chat')",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_input_matches_task_type"),
        "task_runs",
        "("
        "task_type = 'brief_polish' "
        "AND brief_version_id IS NULL "
        "AND input_source_record_id IS NOT NULL "
        "AND input_brief_revision IS NULL "
        "AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL "
        "AND output_message_id IS NULL"
        ") OR ("
        "task_type = 'brief_anchor_extract' "
        "AND brief_version_id IS NULL "
        "AND input_source_record_id IS NULL "
        "AND input_brief_revision IS NOT NULL "
        "AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL "
        "AND output_message_id IS NULL"
        ") OR ("
        "task_type = 'brief_to_draft' "
        "AND brief_version_id IS NOT NULL "
        "AND input_source_record_id IS NULL "
        "AND input_brief_revision IS NOT NULL "
        "AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL "
        "AND output_message_id IS NULL"
        ") OR ("
        "task_type = 'casefile_chat' "
        "AND brief_version_id IS NULL "
        "AND input_source_record_id IS NULL "
        "AND input_brief_revision IS NULL "
        "AND agent_thread_id IS NOT NULL "
        "AND input_message_id IS NOT NULL "
        "AND output_message_id IS NOT NULL"
        ")",
    )
    op.create_index(
        "uq_task_runs_agent_thread_active",
        "task_runs",
        ["agent_thread_id"],
        unique=True,
        postgresql_where=sa.text(
            "agent_thread_id IS NOT NULL "
            "AND status IN ('queued', 'running', 'cancelling')"
        ),
    )


def _create_agent_patch_sets() -> None:
    op.create_table(
        "agent_patch_sets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("task_run_id", sa.BigInteger(), nullable=False),
        sa.Column("base_draft_revision", sa.Integer(), nullable=False),
        sa.Column("reason_summary", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("applied_operation_group_no", sa.BigInteger(), nullable=True),
        sa.Column("applied_from_revision", sa.Integer(), nullable=True),
        sa.Column("applied_to_revision", sa.Integer(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undone_operation_group_no", sa.BigInteger(), nullable=True),
        sa.Column("undone_to_revision", sa.Integer(), nullable=True),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
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
            "base_draft_revision >= 1",
            name=op.f("ck_agent_patch_sets_base_revision_positive"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'stale', 'applied', 'undone', 'rejected')",
            name=op.f("ck_agent_patch_sets_status_allowed"),
        ),
        sa.CheckConstraint(
            "length(btrim(reason_summary)) > 0",
            name=op.f("ck_agent_patch_sets_reason_not_blank"),
        ),
        sa.CheckConstraint(
            "applied_from_revision IS NULL OR applied_from_revision >= 1",
            name=op.f("ck_agent_patch_sets_applied_from_revision_positive"),
        ),
        sa.CheckConstraint(
            "applied_to_revision IS NULL OR applied_to_revision = applied_from_revision + 1",
            name=op.f("ck_agent_patch_sets_applied_revision_step"),
        ),
        sa.CheckConstraint(
            "undone_to_revision IS NULL OR undone_to_revision = applied_to_revision + 1",
            name=op.f("ck_agent_patch_sets_undone_revision_step"),
        ),
        sa.CheckConstraint(
            "(status IN ('pending', 'stale', 'rejected') "
            "AND applied_operation_group_no IS NULL "
            "AND applied_from_revision IS NULL AND applied_to_revision IS NULL "
            "AND applied_at IS NULL AND undone_operation_group_no IS NULL "
            "AND undone_to_revision IS NULL AND undone_at IS NULL) OR "
            "(status = 'applied' AND applied_operation_group_no IS NOT NULL "
            "AND applied_from_revision IS NOT NULL AND applied_to_revision IS NOT NULL "
            "AND applied_at IS NOT NULL AND undone_operation_group_no IS NULL "
            "AND undone_to_revision IS NULL AND undone_at IS NULL) OR "
            "(status = 'undone' AND applied_operation_group_no IS NOT NULL "
            "AND applied_from_revision IS NOT NULL AND applied_to_revision IS NOT NULL "
            "AND applied_at IS NOT NULL AND undone_operation_group_no IS NOT NULL "
            "AND undone_to_revision IS NOT NULL AND undone_at IS NOT NULL)",
            name=op.f("ck_agent_patch_sets_lifecycle_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_agent_patch_sets_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "thread_id"],
            ["agent_threads.project_id", "agent_threads.id"],
            name="fk_agent_patch_sets_project_thread_agent_threads",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "source_message_id"],
            ["agent_messages.project_id", "agent_messages.id"],
            name="fk_agent_patch_sets_project_source_message_agent_messages",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_agent_patch_sets_project_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_patch_sets")),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_agent_patch_sets_project_id_id",
        ),
        sa.UniqueConstraint(
            "task_run_id",
            name="uq_agent_patch_sets_task_run_id",
        ),
    )
    op.create_index(
        "ix_agent_patch_sets_project_status_created_at",
        "agent_patch_sets",
        ["project_id", "status", "created_at"],
    )
    op.create_index(
        "ix_agent_patch_sets_source_message_id",
        "agent_patch_sets",
        ["source_message_id"],
    )


def _create_agent_patch_operations() -> None:
    op.create_table(
        "agent_patch_operations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("patch_set_id", sa.BigInteger(), nullable=False),
        sa.Column("target_object_id", sa.BigInteger(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.String(64), nullable=False),
        sa.Column(
            "operation_type",
            sa.String(16),
            server_default=sa.text("'replace'"),
            nullable=False,
        ),
        sa.Column("field_path", sa.String(512), nullable=False),
        sa.Column("expected_object_revision", sa.Integer(), nullable=True),
        sa.Column(
            "old_value_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "new_value_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "decision",
            sa.String(16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
            "ordinal >= 1",
            name=op.f("ck_agent_patch_operations_ordinal_positive"),
        ),
        sa.CheckConstraint(
            "operation_id ~ '^op_[a-z0-9][a-z0-9_]{0,57}$'",
            name=op.f("ck_agent_patch_operations_operation_id_format"),
        ),
        sa.CheckConstraint(
            "operation_type IN ('add', 'remove', 'replace')",
            name=op.f("ck_agent_patch_operations_operation_type_allowed"),
        ),
        sa.CheckConstraint(
            "field_path ~ '^/'",
            name=op.f("ck_agent_patch_operations_field_path_json_pointer"),
        ),
        sa.CheckConstraint(
            "expected_object_revision IS NULL OR expected_object_revision >= 1",
            name=op.f("ck_agent_patch_operations_expected_revision_positive"),
        ),
        sa.CheckConstraint(
            "decision IN ('pending', 'accepted', 'rejected')",
            name=op.f("ck_agent_patch_operations_decision_allowed"),
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0",
            name=op.f("ck_agent_patch_operations_reason_not_blank"),
        ),
        sa.CheckConstraint(
            "(decision = 'pending' AND reviewed_at IS NULL) OR "
            "(decision IN ('accepted', 'rejected') AND reviewed_at IS NOT NULL)",
            name=op.f("ck_agent_patch_operations_review_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "patch_set_id"],
            ["agent_patch_sets.project_id", "agent_patch_sets.id"],
            name="fk_agent_patch_operations_project_patch_set_agent_patch_sets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "target_object_id"],
            [
                "casefile_objects.project_id",
                "casefile_objects.casefile_id",
                "casefile_objects.draft_id",
                "casefile_objects.id",
            ],
            name="fk_agent_patch_operations_target_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_patch_operations")),
        sa.UniqueConstraint(
            "patch_set_id",
            "ordinal",
            name="uq_agent_patch_operations_patch_set_ordinal",
        ),
        sa.UniqueConstraint(
            "patch_set_id",
            "operation_id",
            name="uq_agent_patch_operations_patch_set_operation_id",
        ),
    )
    op.create_index(
        "ix_agent_patch_operations_patch_set_ordinal",
        "agent_patch_operations",
        ["patch_set_id", "ordinal"],
    )


def _extend_draft_operation_types() -> None:
    op.drop_constraint(
        op.f("ck_draft_operations_type_allowed"),
        "draft_operations",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_draft_operations_type_allowed"),
        "draft_operations",
        "operation_type IN "
        "('add', 'remove', 'replace', 'agent_generate_from_brief', "
        "'agent_patch_apply', 'agent_patch_undo')",
    )


def _create_updated_at_triggers() -> None:
    for table_name in (
        "agent_threads",
        "agent_messages",
        "agent_patch_sets",
        "agent_patch_operations",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_updated_at
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION casefile_set_updated_at()
            """
        )


def _allow_v1_discriminator_edits() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION casefile_reject_discriminator_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_TABLE_NAME = 'entities'
               AND NEW.entity_kind IS DISTINCT FROM OLD.entity_kind
               AND (
                   EXISTS (SELECT 1 FROM people WHERE entity_id = OLD.id)
                   OR EXISTS (SELECT 1 FROM locations WHERE entity_id = OLD.id)
               ) THEN
                RAISE EXCEPTION 'entity_kind is immutable while a legacy subtype row exists';
            ELSIF TG_TABLE_NAME = 'information_units'
                  AND NEW.information_kind IS DISTINCT FROM OLD.information_kind
                  AND (
                      EXISTS (
                          SELECT 1 FROM evidence_items
                           WHERE information_unit_id = OLD.id
                      )
                      OR EXISTS (
                          SELECT 1 FROM testimonies
                           WHERE information_unit_id = OLD.id
                      )
                  ) THEN
                RAISE EXCEPTION
                    'information_kind is immutable while a legacy subtype row exists';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )


def _restore_immutable_discriminators() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION casefile_reject_discriminator_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_TABLE_NAME = 'entities'
               AND NEW.entity_kind IS DISTINCT FROM OLD.entity_kind THEN
                RAISE EXCEPTION 'entity_kind is immutable';
            ELSIF TG_TABLE_NAME = 'information_units'
                  AND NEW.information_kind IS DISTINCT FROM OLD.information_kind THEN
                RAISE EXCEPTION 'information_kind is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )


def _drop_updated_at_triggers() -> None:
    for table_name in (
        "agent_patch_operations",
        "agent_patch_sets",
        "agent_messages",
        "agent_threads",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_updated_at ON {table_name}")


def _restore_draft_operation_types() -> None:
    op.drop_constraint(
        op.f("ck_draft_operations_type_allowed"),
        "draft_operations",
        type_="check",
    )
    op.execute("ALTER TABLE draft_operations DISABLE TRIGGER trg_draft_operations_immutable")
    op.execute(
        """
        DELETE FROM draft_operations
         WHERE operation_type IN ('agent_patch_apply', 'agent_patch_undo')
        """
    )
    op.execute("ALTER TABLE draft_operations ENABLE TRIGGER trg_draft_operations_immutable")
    op.create_check_constraint(
        op.f("ck_draft_operations_type_allowed"),
        "draft_operations",
        "operation_type IN ('add', 'remove', 'replace', 'agent_generate_from_brief')",
    )


def _restore_task_runs() -> None:
    op.drop_index("uq_task_runs_agent_thread_active", table_name="task_runs")
    op.drop_constraint(
        op.f("ck_task_runs_input_matches_task_type"),
        "task_runs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_task_runs_task_type_allowed"),
        "task_runs",
        type_="check",
    )
    op.drop_constraint(
        "fk_task_runs_project_output_message_agent_messages",
        "task_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_task_runs_project_input_message_agent_messages",
        "task_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_task_runs_project_agent_thread_agent_threads",
        "task_runs",
        type_="foreignkey",
    )
    op.execute("ALTER TABLE task_events DISABLE TRIGGER trg_task_events_immutable")
    op.execute(
        """
        DELETE FROM task_events
         WHERE task_run_id IN (
             SELECT id FROM task_runs WHERE task_type = 'casefile_chat'
         )
        """
    )
    op.execute("ALTER TABLE task_events ENABLE TRIGGER trg_task_events_immutable")
    op.execute(
        """
        DELETE FROM task_attempts
         WHERE task_run_id IN (
             SELECT id FROM task_runs WHERE task_type = 'casefile_chat'
         )
        """
    )
    op.execute("DELETE FROM task_runs WHERE task_type = 'casefile_chat'")
    op.drop_column("task_runs", "output_message_id")
    op.drop_column("task_runs", "input_message_id")
    op.drop_column("task_runs", "agent_thread_id")
    op.create_check_constraint(
        op.f("ck_task_runs_task_type_allowed"),
        "task_runs",
        "task_type IN ('brief_polish', 'brief_anchor_extract', 'brief_to_draft')",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_input_matches_task_type"),
        "task_runs",
        "("
        "task_type = 'brief_polish' "
        "AND brief_version_id IS NULL "
        "AND input_source_record_id IS NOT NULL "
        "AND input_brief_revision IS NULL"
        ") OR ("
        "task_type = 'brief_anchor_extract' "
        "AND brief_version_id IS NULL "
        "AND input_source_record_id IS NULL "
        "AND input_brief_revision IS NOT NULL"
        ") OR ("
        "task_type = 'brief_to_draft' "
        "AND brief_version_id IS NOT NULL "
        "AND input_source_record_id IS NULL "
        "AND input_brief_revision IS NOT NULL"
        ")",
    )
