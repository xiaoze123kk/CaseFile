"""Create imported_documents/parse_items and allow reverse_parse task type."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260813120000"
down_revision: Union[str, None] = "20260810000000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "imported_documents",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("original_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("blocks_jsonb", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("parse_status", sa.String(32), nullable=False,
                  server_default=sa.text("'queued'")),
        sa.Column("current_task_run_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_imported_documents")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"],
                                name=op.f("fk_imported_documents_project_id_projects"),
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                name=op.f("fk_imported_documents_created_by_user_id_users"),
                                ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "id",
                            name=op.f("uq_imported_documents_project_id_id")),
        sa.CheckConstraint("parse_status IN ('queued', 'running', 'succeeded', 'failed')",
                           name=op.f("ck_imported_documents_parse_status_allowed")),
        sa.CheckConstraint("btrim(filename) <> ''",
                           name=op.f("ck_imported_documents_filename_not_blank")),
        sa.CheckConstraint("btrim(media_type) <> ''",
                           name=op.f("ck_imported_documents_media_type_not_blank")),
        sa.CheckConstraint("btrim(extracted_text) <> ''",
                           name=op.f("ck_imported_documents_extracted_text_not_blank")),
        sa.CheckConstraint("jsonb_typeof(blocks_jsonb) = 'array'",
                           name=op.f("ck_imported_documents_blocks_is_array")),
        sa.CheckConstraint(
            "(parse_status = 'succeeded' AND current_task_run_id IS NOT NULL) OR "
            "(parse_status <> 'succeeded' )",
            name=op.f("ck_imported_documents_succeeded_has_task")),
    )
    op.create_index("ix_imported_documents_project_created", "imported_documents",
                    ["project_id", "created_at"])

    op.create_table(
        "parse_items",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("item_type", sa.String(48), nullable=False),
        sa.Column("content_jsonb", postgresql.JSONB(), nullable=False),
        sa.Column("grading", sa.String(32), nullable=False),
        sa.Column("source_block_refs", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("confirm_status", sa.String(32), nullable=False,
                  server_default=sa.text("'unconfirmed'")),
        sa.Column("confirmed_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_parse_items")),
        sa.ForeignKeyConstraint(
            ["project_id", "document_id"],
            ["imported_documents.project_id", "imported_documents.id"],
            name=op.f("fk_parse_items_project_document_imported_documents"),
            ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["confirmed_by_user_id"], ["users.id"],
                                name=op.f("fk_parse_items_confirmed_by_user_id_users"),
                                ondelete="RESTRICT"),
        sa.UniqueConstraint("project_id", "document_id", "id",
                            name=op.f("uq_parse_items_lineage_id")),
        sa.CheckConstraint(
            "item_type IN ('entity_alias', 'event', 'information_unit', 'knowledge_state', "
            "'relationship_causality', 'candidate_question', 'candidate_conclusion')",
            name=op.f("ck_parse_items_item_type_allowed")),
        sa.CheckConstraint(
            "grading IN ('explicit', 'inferred', 'needs_confirmation', 'conflicting', "
            "'missing_important')",
            name=op.f("ck_parse_items_grading_allowed")),
        sa.CheckConstraint("confirm_status IN ('unconfirmed', 'confirmed', 'rejected')",
                           name=op.f("ck_parse_items_confirm_status_allowed")),
        sa.CheckConstraint(
            "(confirm_status = 'unconfirmed' AND confirmed_by_user_id IS NULL "
            "AND confirmed_at IS NULL) OR "
            "(confirm_status <> 'unconfirmed' AND confirmed_by_user_id IS NOT NULL "
            "AND confirmed_at IS NOT NULL)",
            name=op.f("ck_parse_items_confirm_consistent")),
        sa.CheckConstraint("jsonb_typeof(content_jsonb) = 'object'",
                           name=op.f("ck_parse_items_content_is_object")),
        sa.CheckConstraint("jsonb_typeof(source_block_refs) = 'array'",
                           name=op.f("ck_parse_items_refs_is_array")),
        sa.CheckConstraint("btrim(source_quote) <> ''",
                           name=op.f("ck_parse_items_quote_not_blank")),
    )
    op.create_index("ix_parse_items_document_id", "parse_items", ["document_id"])

    # Allow the new task type on task_runs.
    op.drop_constraint(op.f("ck_task_runs_task_type_allowed"), "task_runs", type_="check")
    op.create_check_constraint(
        "task_type_allowed", "task_runs",
        "task_type IN ('brief_polish', 'brief_anchor_extract', 'brief_intake_questions', "
        "'brief_intake_synthesize', 'brief_strategy_options', 'brief_to_draft', "
        "'casefile_chat', 'reverse_parse')",
    )
    op.drop_constraint(op.f("ck_task_runs_input_matches_task_type"), "task_runs", type_="check")
    op.create_check_constraint(
        "input_matches_task_type", "task_runs",
        "(task_type = 'brief_polish' AND brief_version_id IS NULL "
        "AND input_source_record_id IS NOT NULL AND input_brief_revision IS NULL "
        "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
        "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL AND output_message_id IS NULL) "
        "OR (task_type = 'brief_anchor_extract' AND brief_version_id IS NULL "
        "AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL "
        "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
        "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL AND output_message_id IS NULL) "
        "OR (task_type = 'brief_intake_questions' AND brief_version_id IS NULL "
        "AND input_source_record_id IS NOT NULL AND input_brief_revision IS NULL "
        "AND brief_intake_id IS NOT NULL AND input_brief_intake_revision IS NOT NULL "
        "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL AND output_message_id IS NULL) "
        "OR (task_type = 'brief_intake_synthesize' AND brief_version_id IS NULL "
        "AND input_source_record_id IS NOT NULL AND input_brief_revision IS NULL "
        "AND brief_intake_id IS NOT NULL AND input_brief_intake_revision IS NOT NULL "
        "AND agent_thread_id IS NULL AND input_message_id IS NULL "
        "AND output_message_id IS NULL) "
        "OR (task_type = 'brief_strategy_options' AND brief_version_id IS NOT NULL "
        "AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL "
        "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
        "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL AND output_message_id IS NULL) "
        "OR (task_type = 'brief_to_draft' AND brief_version_id IS NOT NULL "
        "AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL "
        "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
        "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL AND output_message_id IS NULL) "
        "OR (task_type = 'casefile_chat' AND brief_version_id IS NULL "
        "AND input_source_record_id IS NULL AND input_brief_revision IS NULL "
        "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
        "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NOT NULL "
        "AND input_message_id IS NOT NULL AND output_message_id IS NOT NULL) "
        "OR (task_type = 'reverse_parse' AND brief_version_id IS NULL "
        "AND input_source_record_id IS NULL AND input_brief_revision IS NULL "
        "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
        "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL AND output_message_id IS NULL)",
    )


def downgrade() -> None:
    op.execute("ALTER TABLE task_events DISABLE TRIGGER trg_task_events_immutable")
    op.execute(
        "ALTER TABLE task_attempts DISABLE TRIGGER trg_task_attempt_candidate_immutable"
    )
    op.execute(
        "DELETE FROM task_events WHERE task_run_id IN "
        "(SELECT id FROM task_runs WHERE task_type = 'reverse_parse')"
    )
    op.execute(
        "DELETE FROM task_attempts WHERE task_run_id IN "
        "(SELECT id FROM task_runs WHERE task_type = 'reverse_parse')"
    )
    op.execute("DELETE FROM task_runs WHERE task_type = 'reverse_parse'")
    op.execute(
        "ALTER TABLE task_attempts ENABLE TRIGGER trg_task_attempt_candidate_immutable"
    )
    op.execute("ALTER TABLE task_events ENABLE TRIGGER trg_task_events_immutable")
    op.drop_constraint(op.f("ck_task_runs_input_matches_task_type"), "task_runs", type_="check")
    op.create_check_constraint(
        "input_matches_task_type", "task_runs",
        "(task_type = 'brief_polish' AND brief_version_id IS NULL "
        "AND input_source_record_id IS NOT NULL AND input_brief_revision IS NULL "
        "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
        "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL AND output_message_id IS NULL) "
        "OR (task_type = 'brief_anchor_extract' AND brief_version_id IS NULL "
        "AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL "
        "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
        "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL AND output_message_id IS NULL) "
        "OR (task_type = 'brief_intake_questions' AND brief_version_id IS NULL "
        "AND input_source_record_id IS NOT NULL AND input_brief_revision IS NULL "
        "AND brief_intake_id IS NOT NULL AND input_brief_intake_revision IS NOT NULL "
        "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL AND output_message_id IS NULL) "
        "OR (task_type = 'brief_intake_synthesize' AND brief_version_id IS NULL "
        "AND input_source_record_id IS NOT NULL AND input_brief_revision IS NULL "
        "AND brief_intake_id IS NOT NULL AND input_brief_intake_revision IS NOT NULL "
        "AND agent_thread_id IS NULL AND input_message_id IS NULL "
        "AND output_message_id IS NULL) "
        "OR (task_type = 'brief_strategy_options' AND brief_version_id IS NOT NULL "
        "AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL "
        "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
        "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL AND output_message_id IS NULL) "
        "OR (task_type = 'brief_to_draft' AND brief_version_id IS NOT NULL "
        "AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL "
        "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
        "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
        "AND input_message_id IS NULL AND output_message_id IS NULL) "
        "OR (task_type = 'casefile_chat' AND brief_version_id IS NULL "
        "AND input_source_record_id IS NULL AND input_brief_revision IS NULL "
        "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
        "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NOT NULL "
        "AND input_message_id IS NOT NULL AND output_message_id IS NOT NULL)",
    )
    op.drop_constraint(op.f("ck_task_runs_task_type_allowed"), "task_runs", type_="check")
    op.create_check_constraint(
        "task_type_allowed", "task_runs",
        "task_type IN ('brief_polish', 'brief_anchor_extract', 'brief_intake_questions', "
        "'brief_intake_synthesize', 'brief_strategy_options', 'brief_to_draft', "
        "'casefile_chat')",
    )
    op.drop_table("parse_items")
    op.drop_table("imported_documents")
