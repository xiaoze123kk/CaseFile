"""brief_intake_production

Revision ID: 20260804233938
Revises: 20260804184013
Create Date: 2026-08-04 23:39:39.339716
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260804233938"
down_revision: str | None = "20260804184013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _create_brief_intakes()
    _extend_task_runs()
    _create_brief_intake_candidates()
    _create_brief_intake_questions()
    _connect_intake_lineage()
    _create_intake_triggers()


def downgrade() -> None:
    _drop_intake_triggers()
    _disconnect_intake_lineage()
    op.drop_table("brief_intake_questions")
    op.drop_table("brief_intake_candidates")
    _remove_intake_tasks()
    _restore_task_runs()
    op.drop_table("brief_intakes")


def _create_brief_intakes() -> None:
    op.create_table(
        "brief_intakes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "stage", sa.String(length=32), server_default=sa.text("'idea'"), nullable=False
        ),
        sa.Column("current_source_record_id", sa.BigInteger(), nullable=True),
        sa.Column("current_questions_task_run_id", sa.BigInteger(), nullable=True),
        sa.Column("current_candidate_id", sa.BigInteger(), nullable=True),
        sa.Column("adopted_candidate_id", sa.BigInteger(), nullable=True),
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
        sa.CheckConstraint("revision >= 1", name=op.f("ck_brief_intakes_revision_positive")),
        sa.CheckConstraint(
            "stage IN ('idea', 'questions', 'confirmation', 'brief_review')",
            name=op.f("ck_brief_intakes_stage_allowed"),
        ),
        sa.CheckConstraint(
            "stage = 'idea' OR current_source_record_id IS NOT NULL",
            name=op.f("ck_brief_intakes_source_required_after_idea"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_brief_intakes_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "current_source_record_id"],
            ["source_records.project_id", "source_records.id"],
            name="fk_brief_intakes_project_current_source_source_records",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brief_intakes")),
        sa.UniqueConstraint("project_id", name="uq_brief_intakes_project_id"),
        sa.UniqueConstraint("project_id", "id", name="uq_brief_intakes_project_id_id"),
    )


def _extend_task_runs() -> None:
    op.add_column("task_runs", sa.Column("brief_intake_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "task_runs", sa.Column("input_brief_intake_revision", sa.Integer(), nullable=True)
    )
    op.add_column(
        "task_runs", sa.Column("base_brief_intake_candidate_id", sa.BigInteger(), nullable=True)
    )
    op.create_unique_constraint(
        "uq_task_runs_intake_lineage_id",
        "task_runs",
        ["project_id", "brief_intake_id", "id"],
    )
    op.create_foreign_key(
        "fk_task_runs_project_brief_intake_brief_intakes",
        "task_runs",
        "brief_intakes",
        ["project_id", "brief_intake_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(op.f("ck_task_runs_task_type_allowed"), "task_runs", type_="check")
    op.drop_constraint(
        op.f("ck_task_runs_input_matches_task_type"), "task_runs", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_task_runs_task_type_allowed"), "task_runs", _TASK_TYPE_CHECK
    )
    op.create_check_constraint(
        op.f("ck_task_runs_input_brief_intake_revision_positive"),
        "task_runs",
        "input_brief_intake_revision IS NULL OR input_brief_intake_revision >= 1",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_input_matches_task_type"), "task_runs", _TASK_INPUT_CHECK
    )


def _create_brief_intake_candidates() -> None:
    op.create_table(
        "brief_intake_candidates",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("intake_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_candidate_id", sa.BigInteger(), nullable=True),
        sa.Column("generated_by_task_run_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("basis_input_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "content_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("saved_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "origin IN ('agent_synthesis', 'dialogue_revision', 'manual_edit', "
            "'legacy_import')",
            name=op.f("ck_brief_intake_candidates_origin_allowed"),
        ),
        sa.CheckConstraint(
            "basis_input_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_brief_intake_candidates_basis_input_hash_format"),
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_brief_intake_candidates_content_hash_format"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(content_jsonb) = 'object'",
            name=op.f("ck_brief_intake_candidates_content_is_object"),
        ),
        sa.CheckConstraint(
            "((origin IN ('agent_synthesis', 'dialogue_revision')) "
            "AND generated_by_task_run_id IS NOT NULL) OR "
            "((origin IN ('manual_edit', 'legacy_import')) "
            "AND generated_by_task_run_id IS NULL)",
            name=op.f("ck_brief_intake_candidates_generator_matches_origin"),
        ),
        sa.CheckConstraint(
            "origin <> 'dialogue_revision' OR parent_candidate_id IS NOT NULL",
            name=op.f("ck_brief_intake_candidates_dialogue_revision_has_parent"),
        ),
        sa.CheckConstraint(
            "(saved_at IS NULL AND saved_by_user_id IS NULL) OR "
            "(saved_at IS NOT NULL AND saved_by_user_id IS NOT NULL)",
            name=op.f("ck_brief_intake_candidates_save_bookmark_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "intake_id"],
            ["brief_intakes.project_id", "brief_intakes.id"],
            name="fk_brief_intake_candidates_project_intake_brief_intakes",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "intake_id", "parent_candidate_id"],
            [
                "brief_intake_candidates.project_id",
                "brief_intake_candidates.intake_id",
                "brief_intake_candidates.id",
            ],
            name="fk_intake_candidates_project_parent_candidates",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "intake_id", "generated_by_task_run_id"],
            ["task_runs.project_id", "task_runs.brief_intake_id", "task_runs.id"],
            name="fk_brief_intake_candidates_generated_task_task_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_brief_intake_candidates_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["saved_by_user_id"],
            ["users.id"],
            name=op.f("fk_brief_intake_candidates_saved_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brief_intake_candidates")),
        sa.UniqueConstraint(
            "project_id",
            "intake_id",
            "id",
            name="uq_brief_intake_candidates_lineage_id",
        ),
        sa.UniqueConstraint(
            "generated_by_task_run_id",
            name="uq_brief_intake_candidates_generated_task",
        ),
    )
    op.create_index(
        "ix_brief_intake_candidates_intake_id_created_at",
        "brief_intake_candidates",
        ["intake_id", "created_at"],
    )
    op.create_index(
        "ix_brief_intake_candidates_parent_candidate_id",
        "brief_intake_candidates",
        ["parent_candidate_id"],
    )


def _create_brief_intake_questions() -> None:
    op.create_table(
        "brief_intake_questions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("intake_id", sa.BigInteger(), nullable=False),
        sa.Column("generated_by_task_run_id", sa.BigInteger(), nullable=False),
        sa.Column("question_key", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False),
        sa.Column(
            "suggestions_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "answer_status",
            sa.String(length=32),
            server_default=sa.text("'unanswered'"),
            nullable=False,
        ),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("answer_source", sa.String(length=32), nullable=True),
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
            "ordinal BETWEEN 1 AND 2",
            name=op.f("ck_brief_intake_questions_ordinal_range"),
        ),
        sa.CheckConstraint(
            "question_key ~ '^question_[a-z0-9][a-z0-9_]{0,53}$'",
            name=op.f("ck_brief_intake_questions_question_key_format"),
        ),
        sa.CheckConstraint(
            "btrim(prompt) <> ''", name=op.f("ck_brief_intake_questions_prompt_not_blank")
        ),
        sa.CheckConstraint(
            "btrim(impact) <> ''", name=op.f("ck_brief_intake_questions_impact_not_blank")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(suggestions_jsonb) = 'array'",
            name=op.f("ck_brief_intake_questions_suggestions_is_array"),
        ),
        sa.CheckConstraint(
            "answer_status IN "
            "('unanswered', 'user_answered', 'suggestion_accepted', 'pending')",
            name=op.f("ck_brief_intake_questions_answer_status_allowed"),
        ),
        sa.CheckConstraint(
            "(answer_status = 'unanswered' "
            "AND answer_text IS NULL AND answer_source IS NULL) OR "
            "(answer_status = 'user_answered' "
            "AND btrim(answer_text) <> '' AND answer_source = 'user_confirmed') OR "
            "(answer_status = 'suggestion_accepted' "
            "AND btrim(answer_text) <> '' AND answer_source = 'agent_suggestion') OR "
            "(answer_status = 'pending' AND is_required = false "
            "AND answer_text IS NULL AND answer_source = 'unresolved')",
            name=op.f("ck_brief_intake_questions_answer_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "intake_id"],
            ["brief_intakes.project_id", "brief_intakes.id"],
            name="fk_brief_intake_questions_project_intake_brief_intakes",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "intake_id", "generated_by_task_run_id"],
            ["task_runs.project_id", "task_runs.brief_intake_id", "task_runs.id"],
            name="fk_brief_intake_questions_generated_task_task_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brief_intake_questions")),
        sa.UniqueConstraint(
            "project_id",
            "intake_id",
            "id",
            name="uq_brief_intake_questions_lineage_id",
        ),
        sa.UniqueConstraint(
            "generated_by_task_run_id",
            "question_key",
            name="uq_brief_intake_questions_task_question_key",
        ),
        sa.UniqueConstraint(
            "generated_by_task_run_id",
            "ordinal",
            name="uq_brief_intake_questions_task_ordinal",
        ),
    )
    op.create_index(
        "ix_brief_intake_questions_intake_id",
        "brief_intake_questions",
        ["intake_id"],
    )
    op.create_index(
        "uq_brief_intake_questions_task_required",
        "brief_intake_questions",
        ["generated_by_task_run_id"],
        unique=True,
        postgresql_where=sa.text("is_required = true"),
    )


def _connect_intake_lineage() -> None:
    op.create_foreign_key(
        "fk_task_runs_base_intake_candidate_candidates",
        "task_runs",
        "brief_intake_candidates",
        ["project_id", "brief_intake_id", "base_brief_intake_candidate_id"],
        ["project_id", "intake_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_brief_intakes_current_questions_task_task_runs",
        "brief_intakes",
        "task_runs",
        ["project_id", "id", "current_questions_task_run_id"],
        ["project_id", "brief_intake_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_brief_intakes_current_candidate_brief_intake_candidates",
        "brief_intakes",
        "brief_intake_candidates",
        ["project_id", "id", "current_candidate_id"],
        ["project_id", "intake_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_brief_intakes_adopted_candidate_brief_intake_candidates",
        "brief_intakes",
        "brief_intake_candidates",
        ["project_id", "id", "adopted_candidate_id"],
        ["project_id", "intake_id", "id"],
        ondelete="RESTRICT",
    )


def _create_intake_triggers() -> None:
    for table_name in ("brief_intakes", "brief_intake_questions"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_updated_at
            BEFORE UPDATE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION casefile_set_updated_at()
            """
        )
    op.execute(
        """
        CREATE FUNCTION prevent_brief_intake_candidate_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'brief intake candidate history is immutable';
            END IF;
            IF (
                (to_jsonb(NEW) - ARRAY['saved_at', 'saved_by_user_id'])
                IS DISTINCT FROM
                (to_jsonb(OLD) - ARRAY['saved_at', 'saved_by_user_id'])
            ) THEN
                RAISE EXCEPTION 'brief intake candidate content and lineage are immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_brief_intake_candidates_immutable
        BEFORE UPDATE OR DELETE ON brief_intake_candidates
        FOR EACH ROW EXECUTE FUNCTION prevent_brief_intake_candidate_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_brief_intake_question_definition()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'brief intake question history is immutable';
            END IF;
            IF (
                (
                    to_jsonb(NEW)
                    - ARRAY['answer_status', 'answer_text', 'answer_source', 'updated_at']
                )
                IS DISTINCT FROM
                (
                    to_jsonb(OLD)
                    - ARRAY['answer_status', 'answer_text', 'answer_source', 'updated_at']
                )
            ) THEN
                RAISE EXCEPTION 'brief intake question definition is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_brief_intake_questions_definition_immutable
        BEFORE UPDATE OR DELETE ON brief_intake_questions
        FOR EACH ROW EXECUTE FUNCTION protect_brief_intake_question_definition()
        """
    )


def _drop_intake_triggers() -> None:
    op.execute(
        "DROP TRIGGER trg_brief_intake_questions_definition_immutable "
        "ON brief_intake_questions"
    )
    op.execute("DROP FUNCTION protect_brief_intake_question_definition()")
    op.execute(
        "DROP TRIGGER trg_brief_intake_candidates_immutable ON brief_intake_candidates"
    )
    op.execute("DROP FUNCTION prevent_brief_intake_candidate_mutation()")
    for table_name in ("brief_intake_questions", "brief_intakes"):
        op.execute(f"DROP TRIGGER trg_{table_name}_updated_at ON {table_name}")


def _disconnect_intake_lineage() -> None:
    op.drop_constraint(
        "fk_brief_intakes_adopted_candidate_brief_intake_candidates",
        "brief_intakes",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_brief_intakes_current_candidate_brief_intake_candidates",
        "brief_intakes",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_brief_intakes_current_questions_task_task_runs",
        "brief_intakes",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_task_runs_base_intake_candidate_candidates",
        "task_runs",
        type_="foreignkey",
    )


def _remove_intake_tasks() -> None:
    op.execute("ALTER TABLE task_events DISABLE TRIGGER trg_task_events_immutable")
    op.execute(
        "DELETE FROM task_events WHERE task_run_id IN "
        "(SELECT id FROM task_runs WHERE task_type IN "
        "('brief_intake_questions', 'brief_intake_synthesize'))"
    )
    op.execute("ALTER TABLE task_events ENABLE TRIGGER trg_task_events_immutable")
    op.execute("ALTER TABLE task_attempts DISABLE TRIGGER trg_task_attempt_candidate_immutable")
    op.execute(
        "DELETE FROM task_attempts WHERE task_run_id IN "
        "(SELECT id FROM task_runs WHERE task_type IN "
        "('brief_intake_questions', 'brief_intake_synthesize'))"
    )
    op.execute("ALTER TABLE task_attempts ENABLE TRIGGER trg_task_attempt_candidate_immutable")
    op.execute(
        "DELETE FROM task_runs WHERE task_type IN "
        "('brief_intake_questions', 'brief_intake_synthesize')"
    )


def _restore_task_runs() -> None:
    op.drop_constraint(
        op.f("ck_task_runs_input_matches_task_type"), "task_runs", type_="check"
    )
    op.drop_constraint(
        op.f("ck_task_runs_input_brief_intake_revision_positive"),
        "task_runs",
        type_="check",
    )
    op.drop_constraint(op.f("ck_task_runs_task_type_allowed"), "task_runs", type_="check")
    op.drop_constraint(
        "fk_task_runs_project_brief_intake_brief_intakes",
        "task_runs",
        type_="foreignkey",
    )
    op.drop_constraint("uq_task_runs_intake_lineage_id", "task_runs", type_="unique")
    op.drop_column("task_runs", "base_brief_intake_candidate_id")
    op.drop_column("task_runs", "input_brief_intake_revision")
    op.drop_column("task_runs", "brief_intake_id")
    op.create_check_constraint(
        op.f("ck_task_runs_task_type_allowed"), "task_runs", _OLD_TASK_TYPE_CHECK
    )
    op.create_check_constraint(
        op.f("ck_task_runs_input_matches_task_type"), "task_runs", _OLD_TASK_INPUT_CHECK
    )


_TASK_TYPE_CHECK = (
    "task_type IN ('brief_polish', 'brief_anchor_extract', 'brief_intake_questions', "
    "'brief_intake_synthesize', 'brief_to_draft', 'casefile_chat')"
)

_TASK_INPUT_CHECK = (
    "(task_type = 'brief_polish' AND brief_version_id IS NULL "
    "AND input_source_record_id IS NOT NULL AND input_brief_revision IS NULL "
    "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
    "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
    "AND input_message_id IS NULL AND output_message_id IS NULL) OR "
    "(task_type = 'brief_anchor_extract' AND brief_version_id IS NULL "
    "AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL "
    "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
    "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
    "AND input_message_id IS NULL AND output_message_id IS NULL) OR "
    "(task_type = 'brief_intake_questions' AND brief_version_id IS NULL "
    "AND input_source_record_id IS NOT NULL AND input_brief_revision IS NULL "
    "AND brief_intake_id IS NOT NULL AND input_brief_intake_revision IS NOT NULL "
    "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
    "AND input_message_id IS NULL AND output_message_id IS NULL) OR "
    "(task_type = 'brief_intake_synthesize' AND brief_version_id IS NULL "
    "AND input_source_record_id IS NOT NULL AND input_brief_revision IS NULL "
    "AND brief_intake_id IS NOT NULL AND input_brief_intake_revision IS NOT NULL "
    "AND agent_thread_id IS NULL AND input_message_id IS NULL "
    "AND output_message_id IS NULL) OR "
    "(task_type = 'brief_to_draft' AND brief_version_id IS NOT NULL "
    "AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL "
    "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
    "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
    "AND input_message_id IS NULL AND output_message_id IS NULL) OR "
    "(task_type = 'casefile_chat' AND brief_version_id IS NULL "
    "AND input_source_record_id IS NULL AND input_brief_revision IS NULL "
    "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
    "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NOT NULL "
    "AND input_message_id IS NOT NULL AND output_message_id IS NOT NULL)"
)

_OLD_TASK_TYPE_CHECK = (
    "task_type IN ('brief_polish', 'brief_anchor_extract', 'brief_to_draft', "
    "'casefile_chat')"
)

_OLD_TASK_INPUT_CHECK = (
    "(task_type = 'brief_polish' AND brief_version_id IS NULL "
    "AND input_source_record_id IS NOT NULL AND input_brief_revision IS NULL "
    "AND agent_thread_id IS NULL AND input_message_id IS NULL "
    "AND output_message_id IS NULL) OR "
    "(task_type = 'brief_anchor_extract' AND brief_version_id IS NULL "
    "AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL "
    "AND agent_thread_id IS NULL AND input_message_id IS NULL "
    "AND output_message_id IS NULL) OR "
    "(task_type = 'brief_to_draft' AND brief_version_id IS NOT NULL "
    "AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL "
    "AND agent_thread_id IS NULL AND input_message_id IS NULL "
    "AND output_message_id IS NULL) OR "
    "(task_type = 'casefile_chat' AND brief_version_id IS NULL "
    "AND input_source_record_id IS NULL AND input_brief_revision IS NULL "
    "AND agent_thread_id IS NOT NULL AND input_message_id IS NOT NULL "
    "AND output_message_id IS NOT NULL)"
)
