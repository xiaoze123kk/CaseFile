"""narrative_compiler_runtime

Revision ID: 20260824175326
Revises: 20260823133155
Create Date: 2026-08-24 17:53:48.533110
"""

# Long SQL constraint expressions stay contiguous so upgrade/downgrade definitions
# remain directly comparable with the PostgreSQL catalog form.
# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260824175326"
down_revision: str | None = "20260823133155"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_task_runs_task_type_allowed"), "task_runs", type_="check")
    op.drop_constraint(op.f("ck_task_runs_input_matches_task_type"), "task_runs", type_="check")
    op.drop_constraint(op.f("ck_task_runs_provider_version_positive"), "task_runs", type_="check")
    op.create_table(
        "compiler_profiles",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("profile_key", sa.String(length=160), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("current_version_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
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
            "profile_key ~ '^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$'",
            name=op.f("ck_compiler_profiles_profile_key_format"),
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0", name=op.f("ck_compiler_profiles_name_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_compiler_profiles_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_compiler_profiles_project_id_projects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compiler_profiles")),
        sa.UniqueConstraint("project_id", "id", name="uq_compiler_profiles_project_id_id"),
        sa.UniqueConstraint("project_id", "profile_key", name="uq_compiler_profiles_project_key"),
    )
    op.create_table(
        "compiler_profile_versions",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("compiler_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("schema_id", sa.String(length=160), nullable=False),
        sa.Column("payload_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_compiler_profile_versions_content_hash_format"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload_jsonb) = 'object'",
            name=op.f("ck_compiler_profile_versions_payload_is_object"),
        ),
        sa.CheckConstraint(
            "length(btrim(schema_id)) > 0",
            name=op.f("ck_compiler_profile_versions_schema_id_not_blank"),
        ),
        sa.CheckConstraint(
            "version_no >= 1", name=op.f("ck_compiler_profile_versions_version_no_positive")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_compiler_profile_versions_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "compiler_profile_id"],
            ["compiler_profiles.project_id", "compiler_profiles.id"],
            name="fk_compiler_profile_versions_project_profile_profiles",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compiler_profile_versions")),
        sa.UniqueConstraint(
            "compiler_profile_id", "version_no", name="uq_compiler_profile_versions_version"
        ),
        sa.UniqueConstraint(
            "project_id",
            "compiler_profile_id",
            "id",
            name="uq_compiler_profile_versions_profile_lineage_id",
        ),
        sa.UniqueConstraint("project_id", "id", name="uq_compiler_profile_versions_project_id_id"),
    )
    op.create_foreign_key(
        "fk_compiler_profiles_current_version_profile_versions",
        "compiler_profiles",
        "compiler_profile_versions",
        ["project_id", "id", "current_version_id"],
        ["project_id", "compiler_profile_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_compiler_profile_versions_project_profile_profiles",
        "compiler_profile_versions",
        "compiler_profiles",
        ["project_id", "compiler_profile_id"],
        ["project_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_agent_step_runs_id_task_run_id", "agent_step_runs", ["id", "task_run_id"]
    )
    op.create_unique_constraint(
        "uq_canon_versions_snapshot_lineage_id",
        "canon_versions",
        ["project_id", "casefile_id", "id", "source_snapshot_id"],
    )
    op.create_unique_constraint(
        "uq_draft_snapshots_lineage_id",
        "draft_snapshots",
        ["project_id", "casefile_id", "draft_id", "id"],
    )
    op.create_unique_constraint(
        "uq_task_runs_lineage_id",
        "task_runs",
        ["project_id", "casefile_id", "draft_id", "id"],
    )
    op.create_table(
        "compile_runs",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("task_run_id", sa.BigInteger(), nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("compile_mode", sa.String(length=32), nullable=False),
        sa.Column("source_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("source_canon_version_id", sa.BigInteger(), nullable=True),
        sa.Column("exposure_plan_revision_id", sa.BigInteger(), nullable=True),
        sa.Column("compiler_profile_version_id", sa.BigInteger(), nullable=False),
        sa.Column("compiler_version", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.CheckConstraint(
            "(compile_mode = 'preview' AND source_canon_version_id IS NULL) OR (compile_mode = 'canonical' AND source_canon_version_id IS NOT NULL)",
            name=op.f("ck_compile_runs_canon_binding_matches_mode"),
        ),
        sa.CheckConstraint(
            "compile_mode IN ('preview', 'canonical')",
            name=op.f("ck_compile_runs_compile_mode_allowed"),
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_compile_runs_input_hash_format")
        ),
        sa.CheckConstraint("target_kind = 'novel'", name=op.f("ck_compile_runs_target_kind_novel")),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_compile_runs_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "exposure_plan_revision_id"],
            [
                "exposure_plan_revisions.project_id",
                "exposure_plan_revisions.casefile_id",
                "exposure_plan_revisions.draft_id",
                "exposure_plan_revisions.id",
            ],
            name="fk_compile_runs_lineage_exposure_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "source_snapshot_id"],
            [
                "draft_snapshots.project_id",
                "draft_snapshots.casefile_id",
                "draft_snapshots.draft_id",
                "draft_snapshots.id",
            ],
            name="fk_compile_runs_lineage_snapshot_snapshots",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.casefile_id", "task_runs.draft_id", "task_runs.id"],
            name="fk_compile_runs_lineage_task_task_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_compile_runs_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "source_canon_version_id", "source_snapshot_id"],
            [
                "canon_versions.project_id",
                "canon_versions.casefile_id",
                "canon_versions.id",
                "canon_versions.source_snapshot_id",
            ],
            name="fk_compile_runs_canon_snapshot_canon_versions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "compiler_profile_version_id"],
            ["compiler_profile_versions.project_id", "compiler_profile_versions.id"],
            name="fk_compile_runs_project_profile_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compile_runs")),
        sa.UniqueConstraint("id", "task_run_id", name="uq_compile_runs_id_task_run_id"),
        sa.UniqueConstraint(
            "project_id",
            "casefile_id",
            "id",
            name="uq_compile_runs_project_casefile_id",
        ),
        sa.UniqueConstraint("project_id", "id", name="uq_compile_runs_project_id_id"),
        sa.UniqueConstraint("task_run_id", name="uq_compile_runs_task_run_id"),
    )
    op.create_table(
        "compile_artifacts",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("casefile_id", sa.BigInteger(), nullable=False),
        sa.Column("compile_run_id", sa.BigInteger(), nullable=False),
        sa.Column("task_run_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_step_run_id", sa.BigInteger(), nullable=False),
        sa.Column("artifact_kind", sa.String(length=40), nullable=False),
        sa.Column("artifact_key", sa.String(length=160), nullable=False),
        sa.Column("schema_id", sa.String(length=160), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_jsonb", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.CheckConstraint(
            "artifact_key = 'compiler.input_manifest'",
            name=op.f("ck_compile_artifacts_artifact_key_n4_1"),
        ),
        sa.CheckConstraint(
            "artifact_kind = 'input_manifest'", name=op.f("ck_compile_artifacts_artifact_kind_n4_1")
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_compile_artifacts_content_hash_format")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(content_jsonb) = 'object'",
            name=op.f("ck_compile_artifacts_content_is_object"),
        ),
        sa.CheckConstraint(
            "schema_id = 'compiler.input-manifest.v1'",
            name=op.f("ck_compile_artifacts_schema_id_n4_1"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_step_run_id", "task_run_id"],
            ["agent_step_runs.id", "agent_step_runs.task_run_id"],
            name="fk_compile_artifacts_step_task_agent_step_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["compile_run_id", "task_run_id"],
            ["compile_runs.id", "compile_runs.task_run_id"],
            name="fk_compile_artifacts_run_task_compile_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "casefile_id", "compile_run_id"],
            ["compile_runs.project_id", "compile_runs.casefile_id", "compile_runs.id"],
            name="fk_compile_artifacts_project_casefile_run_compile_runs",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compile_artifacts")),
        sa.UniqueConstraint(
            "compile_run_id", "artifact_key", name="uq_compile_artifacts_run_artifact_key"
        ),
    )
    op.alter_column("task_runs", "provider_setting_id", existing_type=sa.BIGINT(), nullable=True)
    op.alter_column("task_runs", "provider", existing_type=sa.VARCHAR(length=40), nullable=True)
    op.alter_column("task_runs", "model_id", existing_type=sa.VARCHAR(length=160), nullable=True)
    op.alter_column(
        "task_runs", "provider_config_version", existing_type=sa.BIGINT(), nullable=True
    )
    op.create_check_constraint(
        op.f("ck_task_runs_provider_binding_matches_task_type"),
        "task_runs",
        "(task_type = 'novel_compile' AND provider_setting_id IS NULL AND provider IS NULL AND model_id IS NULL AND provider_config_version IS NULL) OR (task_type <> 'novel_compile' AND provider_setting_id IS NOT NULL AND provider IS NOT NULL AND model_id IS NOT NULL AND provider_config_version IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_provider_version_positive"),
        "task_runs",
        "provider_config_version IS NULL OR provider_config_version >= 1",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_task_type_allowed"),
        "task_runs",
        "task_type IN ('brief_polish', 'brief_anchor_extract', "
        "'brief_intake_questions', 'brief_intake_synthesize', "
        "'brief_strategy_options', 'brief_to_draft', 'casefile_chat', "
        "'reverse_parse', 'novel_compile')",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_input_matches_task_type"),
        "task_runs",
        "(task_type = 'brief_polish' AND brief_version_id IS NULL AND input_source_record_id IS NOT NULL AND input_brief_revision IS NULL AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL AND input_message_id IS NULL AND output_message_id IS NULL) OR "
        "(task_type = 'brief_anchor_extract' AND brief_version_id IS NULL AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL AND input_message_id IS NULL AND output_message_id IS NULL) OR "
        "(task_type = 'brief_intake_questions' AND brief_version_id IS NULL AND input_source_record_id IS NOT NULL AND input_brief_revision IS NULL AND brief_intake_id IS NOT NULL AND input_brief_intake_revision IS NOT NULL AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL AND input_message_id IS NULL AND output_message_id IS NULL) OR "
        "(task_type = 'brief_intake_synthesize' AND brief_version_id IS NULL AND input_source_record_id IS NOT NULL AND input_brief_revision IS NULL AND brief_intake_id IS NOT NULL AND input_brief_intake_revision IS NOT NULL AND agent_thread_id IS NULL AND input_message_id IS NULL AND output_message_id IS NULL) OR "
        "(task_type = 'brief_strategy_options' AND brief_version_id IS NOT NULL AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL AND input_message_id IS NULL AND output_message_id IS NULL) OR "
        "(task_type = 'brief_to_draft' AND brief_version_id IS NOT NULL AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL AND input_message_id IS NULL AND output_message_id IS NULL) OR "
        "(task_type = 'casefile_chat' AND brief_version_id IS NULL AND input_source_record_id IS NULL AND input_brief_revision IS NULL AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NOT NULL AND input_message_id IS NOT NULL AND output_message_id IS NOT NULL) OR "
        "(task_type IN ('reverse_parse', 'novel_compile') AND brief_version_id IS NULL AND input_source_record_id IS NULL AND input_brief_revision IS NULL AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL AND input_message_id IS NULL AND output_message_id IS NULL)",
    )
    op.execute(
        """
        CREATE FUNCTION casefile_validate_compiler_profile_pointer()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_pointer BIGINT;
        BEGIN
            SELECT current_version_id INTO current_pointer
              FROM compiler_profiles WHERE id = NEW.id;
            IF current_pointer IS NULL THEN
                RAISE EXCEPTION 'Compiler Profile current version is required';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_compiler_profiles_pointer_required
        AFTER INSERT OR UPDATE ON compiler_profiles
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION casefile_validate_compiler_profile_pointer()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_compiler_profiles_updated_at
        BEFORE UPDATE ON compiler_profiles
        FOR EACH ROW EXECUTE FUNCTION casefile_set_updated_at()
        """
    )
    for table_name in ("compiler_profile_versions", "compile_runs", "compile_artifacts"):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_immutable BEFORE UPDATE OR DELETE "
            f"ON {table_name} FOR EACH ROW EXECUTE FUNCTION casefile_reject_history_mutation()"
        )
    op.execute(
        """
        CREATE FUNCTION casefile_freeze_task_run_inputs()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF ROW(
                OLD.project_id, OLD.casefile_id, OLD.draft_id, OLD.brief_version_id,
                OLD.input_source_record_id, OLD.input_brief_revision, OLD.brief_intake_id,
                OLD.input_brief_intake_revision, OLD.base_brief_intake_candidate_id,
                OLD.agent_thread_id, OLD.input_message_id, OLD.output_message_id,
                OLD.input_hash, OLD.input_jsonb, OLD.actor_user_id, OLD.provider_setting_id,
                OLD.task_type, OLD.input_draft_revision, OLD.provider, OLD.model_id,
                OLD.provider_config_version, OLD.schema_version, OLD.agent_version,
                OLD.prompt_version, OLD.toolset_version, OLD.budget_jsonb
            ) IS DISTINCT FROM ROW(
                NEW.project_id, NEW.casefile_id, NEW.draft_id, NEW.brief_version_id,
                NEW.input_source_record_id, NEW.input_brief_revision, NEW.brief_intake_id,
                NEW.input_brief_intake_revision, NEW.base_brief_intake_candidate_id,
                NEW.agent_thread_id, NEW.input_message_id, NEW.output_message_id,
                NEW.input_hash, NEW.input_jsonb, NEW.actor_user_id, NEW.provider_setting_id,
                NEW.task_type, NEW.input_draft_revision, NEW.provider, NEW.model_id,
                NEW.provider_config_version, NEW.schema_version, NEW.agent_version,
                NEW.prompt_version, NEW.toolset_version, NEW.budget_jsonb
            ) THEN
                RAISE EXCEPTION 'TaskRun frozen input and configuration are immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_task_runs_frozen_inputs
        BEFORE UPDATE ON task_runs
        FOR EACH ROW EXECUTE FUNCTION casefile_freeze_task_run_inputs()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_task_runs_frozen_inputs ON task_runs")
    op.execute("DROP FUNCTION casefile_freeze_task_run_inputs()")
    for table_name in ("compile_artifacts", "compile_runs", "compiler_profile_versions"):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP TRIGGER trg_compiler_profiles_updated_at ON compiler_profiles")
    op.execute("DROP TRIGGER trg_compiler_profiles_pointer_required ON compiler_profiles")
    op.execute("DROP FUNCTION casefile_validate_compiler_profile_pointer()")
    op.drop_table("compile_artifacts")
    op.drop_table("compile_runs")
    op.execute("ALTER TABLE agent_step_runs DISABLE TRIGGER trg_agent_step_runs_immutable")
    op.execute("ALTER TABLE task_events DISABLE TRIGGER trg_task_events_immutable")
    op.execute(
        "DELETE FROM agent_model_calls WHERE task_run_id IN "
        "(SELECT id FROM task_runs WHERE task_type = 'novel_compile')"
    )
    op.execute(
        "DELETE FROM agent_step_runs WHERE task_run_id IN "
        "(SELECT id FROM task_runs WHERE task_type = 'novel_compile')"
    )
    op.execute(
        "DELETE FROM task_events WHERE task_run_id IN "
        "(SELECT id FROM task_runs WHERE task_type = 'novel_compile')"
    )
    op.execute(
        "DELETE FROM task_attempts WHERE task_run_id IN "
        "(SELECT id FROM task_runs WHERE task_type = 'novel_compile')"
    )
    op.execute("DELETE FROM task_runs WHERE task_type = 'novel_compile'")
    op.execute("ALTER TABLE task_events ENABLE TRIGGER trg_task_events_immutable")
    op.execute("ALTER TABLE agent_step_runs ENABLE TRIGGER trg_agent_step_runs_immutable")
    op.drop_constraint(op.f("ck_task_runs_input_matches_task_type"), "task_runs", type_="check")
    op.drop_constraint(op.f("ck_task_runs_task_type_allowed"), "task_runs", type_="check")
    op.drop_constraint(op.f("ck_task_runs_provider_version_positive"), "task_runs", type_="check")
    op.drop_constraint(
        op.f("ck_task_runs_provider_binding_matches_task_type"), "task_runs", type_="check"
    )
    op.drop_constraint("uq_task_runs_lineage_id", "task_runs", type_="unique")
    op.alter_column(
        "task_runs", "provider_config_version", existing_type=sa.BIGINT(), nullable=False
    )
    op.alter_column("task_runs", "model_id", existing_type=sa.VARCHAR(length=160), nullable=False)
    op.alter_column("task_runs", "provider", existing_type=sa.VARCHAR(length=40), nullable=False)
    op.alter_column("task_runs", "provider_setting_id", existing_type=sa.BIGINT(), nullable=False)
    op.drop_constraint("uq_draft_snapshots_lineage_id", "draft_snapshots", type_="unique")
    op.drop_constraint("uq_canon_versions_snapshot_lineage_id", "canon_versions", type_="unique")
    op.drop_constraint("uq_agent_step_runs_id_task_run_id", "agent_step_runs", type_="unique")
    op.drop_constraint(
        "fk_compiler_profiles_current_version_profile_versions",
        "compiler_profiles",
        type_="foreignkey",
    )
    op.execute(
        "ALTER TABLE compiler_profile_versions DROP CONSTRAINT IF EXISTS "
        "fk_compiler_profile_versions_project_profile_profiles"
    )
    op.drop_table("compiler_profiles")
    op.drop_table("compiler_profile_versions")
    op.create_check_constraint(
        op.f("ck_task_runs_provider_version_positive"),
        "task_runs",
        "provider_config_version >= 1",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_task_type_allowed"),
        "task_runs",
        "task_type IN ('brief_polish', 'brief_anchor_extract', "
        "'brief_intake_questions', 'brief_intake_synthesize', "
        "'brief_strategy_options', 'brief_to_draft', 'casefile_chat', 'reverse_parse')",
    )
    op.create_check_constraint(
        op.f("ck_task_runs_input_matches_task_type"),
        "task_runs",
        "(task_type = 'brief_polish' AND input_source_record_id IS NOT NULL AND brief_version_id IS NULL) OR "
        "(task_type = 'brief_anchor_extract' AND input_brief_revision IS NOT NULL AND brief_version_id IS NULL) OR "
        "(task_type IN ('brief_intake_questions', 'brief_intake_synthesize') AND brief_intake_id IS NOT NULL) OR "
        "(task_type IN ('brief_strategy_options', 'brief_to_draft') AND brief_version_id IS NOT NULL) OR "
        "(task_type = 'casefile_chat' AND agent_thread_id IS NOT NULL AND input_message_id IS NOT NULL AND output_message_id IS NOT NULL) OR "
        "(task_type = 'reverse_parse' AND brief_version_id IS NULL AND input_source_record_id IS NULL AND brief_intake_id IS NULL AND agent_thread_id IS NULL)",
    )
