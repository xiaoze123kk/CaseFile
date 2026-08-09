"""Persist immutable Agent component steps and model calls."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260808154126"
down_revision: str | None = "20260808003000"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_task_attempts_id_task_run_id", "task_attempts", ["id", "task_run_id"]
    )
    op.create_table(
        "agent_step_runs",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("task_run_id", sa.BigInteger(), nullable=False),
        sa.Column("task_attempt_id", sa.BigInteger(), nullable=False),
        sa.Column("component_id", sa.String(length=80), nullable=False),
        sa.Column("parent_component_id", sa.String(length=80), nullable=True),
        sa.Column("execution_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "upstream_hashes_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("ir_schema_id", sa.String(length=80), nullable=False),
        sa.Column("component_version", sa.String(length=80), nullable=False),
        sa.Column(
            "output_jsonb",
            postgresql.JSONB(astext_type=sa.Text(), none_as_null=True),
            nullable=True,
        ),
        sa.Column(
            "diagnostic_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "usage_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("resumed_from_step_run_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=False),
            nullable=False,
        ),
        sa.CheckConstraint(
            "execution_no >= 1", name=op.f("ck_agent_step_runs_execution_no_positive")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'reused', 'skipped')",
            name=op.f("ck_agent_step_runs_status_allowed"),
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_agent_step_runs_input_hash_format")
        ),
        sa.CheckConstraint(
            "output_hash IS NULL OR output_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_agent_step_runs_output_hash_format"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(upstream_hashes_jsonb) = 'object'",
            name=op.f("ck_agent_step_runs_upstream_hashes_is_object"),
        ),
        sa.CheckConstraint(
            "output_jsonb IS NULL OR jsonb_typeof(output_jsonb) IN ('object', 'array')",
            name=op.f("ck_agent_step_runs_output_is_structured"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(diagnostic_jsonb) = 'object'",
            name=op.f("ck_agent_step_runs_diagnostic_is_object"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(usage_jsonb) = 'object'", name=op.f("ck_agent_step_runs_usage_is_object")
        ),
        sa.ForeignKeyConstraint(
            ["resumed_from_step_run_id"],
            ["agent_step_runs.id"],
            name=op.f("fk_agent_step_runs_resumed_from_step_run_id_agent_step_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_agent_step_runs_project_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_attempt_id", "task_run_id"],
            ["task_attempts.id", "task_attempts.task_run_id"],
            name="fk_agent_step_runs_attempt_task_run_task_attempts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_step_runs")),
        sa.UniqueConstraint(
            "task_attempt_id",
            "component_id",
            "execution_no",
            name="uq_agent_step_runs_attempt_component_execution",
        ),
    )
    op.create_index("ix_agent_step_runs_task_run_id_id", "agent_step_runs", ["task_run_id", "id"])
    op.create_index(
        "ix_agent_step_runs_attempt_component_status",
        "agent_step_runs",
        ["task_attempt_id", "component_id", "status"],
    )
    op.create_table(
        "agent_model_calls",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("task_run_id", sa.BigInteger(), nullable=False),
        sa.Column("task_attempt_id", sa.BigInteger(), nullable=False),
        sa.Column("agent_step_run_id", sa.BigInteger(), nullable=False),
        sa.Column("call_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model_id", sa.String(length=160), nullable=False),
        sa.Column("output_protocol", sa.String(length=40), nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("prompt_component_id", sa.String(length=80), nullable=False),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=True),
        sa.Column("target_schema_id", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=True),
        sa.Column("output_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("raw_output_text", sa.Text(), nullable=True),
        sa.Column(
            "raw_output_truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "issues_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "usage_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.CheckConstraint("call_no >= 1", name=op.f("ck_agent_model_calls_call_no_positive")),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name=op.f("ck_agent_model_calls_status_allowed"),
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_agent_model_calls_input_hash_format")
        ),
        sa.CheckConstraint(
            "output_hash IS NULL OR output_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_agent_model_calls_output_hash_format"),
        ),
        sa.CheckConstraint(
            "output_size_bytes IS NULL OR output_size_bytes >= 0",
            name=op.f("ck_agent_model_calls_output_size_nonnegative"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(issues_jsonb) = 'array'",
            name=op.f("ck_agent_model_calls_issues_is_array"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(usage_jsonb) = 'object'",
            name=op.f("ck_agent_model_calls_usage_is_object"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_step_run_id"],
            ["agent_step_runs.id"],
            name=op.f("fk_agent_model_calls_agent_step_run_id_agent_step_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_agent_model_calls_project_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["task_attempt_id", "task_run_id"],
            ["task_attempts.id", "task_attempts.task_run_id"],
            name="fk_agent_model_calls_attempt_task_run_task_attempts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_model_calls")),
        sa.UniqueConstraint("agent_step_run_id", "call_no", name="uq_agent_model_calls_step_call"),
    )
    op.create_index(
        "ix_agent_model_calls_task_run_id_id", "agent_model_calls", ["task_run_id", "id"]
    )
    op.execute(
        """
        CREATE FUNCTION reject_terminal_agent_execution_mutation() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'agent execution history is immutable';
          END IF;
          IF OLD.status IN ('succeeded', 'failed', 'reused', 'skipped') THEN
            RAISE EXCEPTION 'terminal agent execution is immutable';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_agent_step_runs_immutable BEFORE UPDATE OR DELETE "
        "ON agent_step_runs FOR EACH ROW EXECUTE FUNCTION "
        "reject_terminal_agent_execution_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_agent_model_calls_immutable BEFORE UPDATE OR DELETE "
        "ON agent_model_calls FOR EACH ROW EXECUTE FUNCTION "
        "reject_terminal_agent_execution_mutation()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_agent_model_calls_immutable ON agent_model_calls")
    op.execute("DROP TRIGGER trg_agent_step_runs_immutable ON agent_step_runs")
    op.execute("DROP FUNCTION reject_terminal_agent_execution_mutation()")
    op.drop_index("ix_agent_model_calls_task_run_id_id", table_name="agent_model_calls")
    op.drop_table("agent_model_calls")
    op.drop_index("ix_agent_step_runs_attempt_component_status", table_name="agent_step_runs")
    op.drop_index("ix_agent_step_runs_task_run_id_id", table_name="agent_step_runs")
    op.drop_table("agent_step_runs")
    op.drop_constraint("uq_task_attempts_id_task_run_id", "task_attempts", type_="unique")
