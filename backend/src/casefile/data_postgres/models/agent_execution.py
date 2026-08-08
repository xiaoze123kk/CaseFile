"""Persistent v8 Agent component steps and individual model calls."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from casefile.data_postgres.base import Base, BigIntIdentityPrimaryKeyMixin

if TYPE_CHECKING:
    from casefile.data_postgres.models.workflow import TaskAttempt


class AgentStepRun(BigIntIdentityPrimaryKeyMixin, Base):
    """One immutable terminal component execution inside a TaskAttempt."""

    __tablename__ = "agent_step_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_agent_step_runs_project_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_attempt_id", "task_run_id"],
            ["task_attempts.id", "task_attempts.task_run_id"],
            name="fk_agent_step_runs_attempt_task_run_task_attempts",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "task_attempt_id",
            "component_id",
            "execution_no",
            name="uq_agent_step_runs_attempt_component_execution",
        ),
        CheckConstraint("execution_no >= 1", name="execution_no_positive"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'reused', 'skipped')",
            name="status_allowed",
        ),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="input_hash_format"),
        CheckConstraint(
            "output_hash IS NULL OR output_hash ~ '^[0-9a-f]{64}$'",
            name="output_hash_format",
        ),
        CheckConstraint(
            "jsonb_typeof(upstream_hashes_jsonb) = 'object'",
            name="upstream_hashes_is_object",
        ),
        CheckConstraint(
            "output_jsonb IS NULL OR jsonb_typeof(output_jsonb) IN ('object', 'array')",
            name="output_is_structured",
        ),
        CheckConstraint(
            "jsonb_typeof(diagnostic_jsonb) = 'object'",
            name="diagnostic_is_object",
        ),
        CheckConstraint("jsonb_typeof(usage_jsonb) = 'object'", name="usage_is_object"),
        Index("ix_agent_step_runs_task_run_id_id", "task_run_id", "id"),
        Index(
            "ix_agent_step_runs_attempt_component_status",
            "task_attempt_id",
            "component_id",
            "status",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_attempt_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    component_id: Mapped[str] = mapped_column(String(80), nullable=False)
    parent_component_id: Mapped[str | None] = mapped_column(String(80))
    execution_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    upstream_hashes_jsonb: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    output_hash: Mapped[str | None] = mapped_column(String(64))
    ir_schema_id: Mapped[str] = mapped_column(String(80), nullable=False)
    component_version: Mapped[str] = mapped_column(String(80), nullable=False)
    output_jsonb: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    diagnostic_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    usage_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    resumed_from_step_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("agent_step_runs.id", ondelete="RESTRICT"),
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    task_attempt: Mapped[TaskAttempt] = relationship(
        "TaskAttempt",
        primaryjoin=(
            "and_(foreign(AgentStepRun.task_attempt_id) == TaskAttempt.id, "
            "foreign(AgentStepRun.task_run_id) == TaskAttempt.task_run_id)"
        ),
        lazy="joined",
        viewonly=True,
    )


class AgentModelCall(BigIntIdentityPrimaryKeyMixin, Base):
    """One provider invocation with protocol, schema, diagnostics, and bounded raw output."""

    __tablename__ = "agent_model_calls"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_agent_model_calls_project_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_attempt_id", "task_run_id"],
            ["task_attempts.id", "task_attempts.task_run_id"],
            name="fk_agent_model_calls_attempt_task_run_task_attempts",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("agent_step_run_id", "call_no", name="uq_agent_model_calls_step_call"),
        CheckConstraint("call_no >= 1", name="call_no_positive"),
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="status_allowed"),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="input_hash_format"),
        CheckConstraint(
            "output_hash IS NULL OR output_hash ~ '^[0-9a-f]{64}$'",
            name="output_hash_format",
        ),
        CheckConstraint(
            "output_size_bytes IS NULL OR output_size_bytes >= 0", name="output_size_nonnegative"
        ),
        CheckConstraint("jsonb_typeof(issues_jsonb) = 'array'", name="issues_is_array"),
        CheckConstraint("jsonb_typeof(usage_jsonb) = 'object'", name="usage_is_object"),
        Index("ix_agent_model_calls_task_run_id_id", "task_run_id", "id"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_attempt_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    agent_step_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agent_step_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    call_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model_id: Mapped[str] = mapped_column(String(160), nullable=False)
    output_protocol: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_component_id: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    target_schema_id: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    output_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    raw_output_text: Mapped[str | None] = mapped_column(Text)
    raw_output_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    issues_jsonb: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    usage_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
