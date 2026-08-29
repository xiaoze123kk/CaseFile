"""Persistent long-lived GoalSession state, revisions, deliveries, and evidence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.orm import Mapped, mapped_column

from casefile.data_postgres.base import Base, BigIntIdentityPrimaryKeyMixin, TimestampMixin

_GOAL_STATUSES = (
    "interpreting",
    "running",
    "waiting_clarification",
    "waiting_patch_review",
    "stale",
    "completed",
    "cancelled",
    "superseded",
    "failed",
)


class AgentGoalSession(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """Mutable projection for one long-lived collaboration goal."""

    __tablename__ = "agent_goal_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "casefile_id", "draft_id"],
            ["drafts.project_id", "drafts.casefile_id", "drafts.id"],
            name="fk_agent_goal_sessions_project_casefile_draft_drafts",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "thread_id"],
            ["agent_threads.project_id", "agent_threads.id"],
            name="fk_agent_goal_sessions_project_thread_agent_threads",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "thread_id", "source_message_id"],
            ["agent_messages.project_id", "agent_messages.thread_id", "agent_messages.id"],
            name="fk_agent_goal_sessions_source_message_agent_messages",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "predecessor_goal_session_id"],
            ["agent_goal_sessions.project_id", "agent_goal_sessions.id"],
            name="fk_agent_goal_sessions_project_predecessor_goal_sessions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "active_patch_set_id"],
            ["agent_patch_sets.project_id", "agent_patch_sets.id"],
            name="fk_agent_goal_sessions_project_active_patch_agent_patch_sets",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "id", "current_revision_id"],
            [
                "agent_goal_revisions.project_id",
                "agent_goal_revisions.goal_session_id",
                "agent_goal_revisions.id",
            ],
            name="fk_agent_goal_sessions_current_revision_goal_revisions",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint("project_id", "id", name="uq_agent_goal_sessions_project_id_id"),
        UniqueConstraint(
            "project_id", "thread_id", "id", name="uq_agent_goal_sessions_thread_lineage_id"
        ),
        CheckConstraint(
            "status IN (" + ", ".join(f"'{value}'" for value in _GOAL_STATUSES) + ")",
            name="status_allowed",
        ),
        CheckConstraint("baseline_draft_revision >= 1", name="baseline_revision_positive"),
        CheckConstraint("baseline_hash ~ '^[0-9a-f]{64}$'", name="baseline_hash_format"),
        CheckConstraint("revision_count BETWEEN 0 AND 8", name="revision_count_bounded"),
        CheckConstraint("task_run_slice_count BETWEEN 0 AND 12", name="slice_count_bounded"),
        CheckConstraint("consumed_control_count BETWEEN 0 AND 6", name="control_count_bounded"),
        CheckConstraint(
            "(revision_count = 0 AND current_revision_id IS NULL) OR "
            "(revision_count >= 1 AND current_revision_id IS NOT NULL)",
            name="current_revision_shape",
        ),
        CheckConstraint(
            "(status = 'waiting_patch_review' AND active_patch_set_id IS NOT NULL) OR "
            "(status <> 'waiting_patch_review')",
            name="patch_review_shape",
        ),
        Index(
            "uq_agent_goal_sessions_thread_active",
            "thread_id",
            unique=True,
            postgresql_where=text(
                "status NOT IN ('completed', 'cancelled', 'superseded', 'failed')"
            ),
        ),
        Index(
            "ix_agent_goal_sessions_project_status_updated", "project_id", "status", "updated_at"
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    casefile_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    predecessor_goal_session_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    runtime_version: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    capability_registry_version: Mapped[str] = mapped_column(String(80), nullable=False)
    baseline_draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_revision_id: Mapped[int | None] = mapped_column(BigInteger)
    active_patch_set_id: Mapped[int | None] = mapped_column(BigInteger)
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    task_run_slice_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    consumed_control_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    terminal_reason_code: Mapped[str | None] = mapped_column(String(80))


class AgentGoalRevision(BigIntIdentityPrimaryKeyMixin, Base):
    """Append-only normalized goal interpretation at one baseline."""

    __tablename__ = "agent_goal_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "goal_session_id"],
            ["agent_goal_sessions.project_id", "agent_goal_sessions.id"],
            name="fk_agent_goal_revisions_project_session_goal_sessions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "goal_session_id", "parent_revision_id"],
            [
                "agent_goal_revisions.project_id",
                "agent_goal_revisions.goal_session_id",
                "agent_goal_revisions.id",
            ],
            name="fk_agent_goal_revisions_parent_goal_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "source_message_id"],
            ["agent_messages.project_id", "agent_messages.id"],
            name="fk_agent_goal_revisions_source_message_agent_messages",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_id", "goal_session_id", "id", name="uq_agent_goal_revisions_lineage_id"
        ),
        UniqueConstraint(
            "goal_session_id", "revision_no", name="uq_agent_goal_revisions_session_revision"
        ),
        CheckConstraint("revision_no BETWEEN 1 AND 8", name="revision_no_bounded"),
        CheckConstraint(
            "amendment_kind IN ('initial', 'refine', 'add_constraint', "
            "'add_obligation', 'remove_obligation', 'post_apply')",
            name="amendment_kind_allowed",
        ),
        CheckConstraint(
            "(revision_no = 1 AND amendment_kind = 'initial' AND parent_revision_id IS NULL) OR "
            "(revision_no > 1 AND amendment_kind <> 'initial' AND parent_revision_id IS NOT NULL)",
            name="parent_shape",
        ),
        CheckConstraint("length(btrim(goal_text)) > 0", name="goal_text_not_blank"),
        CheckConstraint("obligations_hash ~ '^[0-9a-f]{64}$'", name="obligations_hash_format"),
        CheckConstraint("state_hash ~ '^[0-9a-f]{64}$'", name="state_hash_format"),
        CheckConstraint("baseline_draft_revision >= 1", name="baseline_revision_positive"),
        CheckConstraint("baseline_hash ~ '^[0-9a-f]{64}$'", name="baseline_hash_format"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    goal_session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[int | None] = mapped_column(BigInteger)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amendment_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    obligations_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class AgentGoalObligation(BigIntIdentityPrimaryKeyMixin, Base):
    """Append-only capability obligation owned by one goal revision."""

    __tablename__ = "agent_goal_obligations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "goal_session_id", "goal_revision_id"],
            [
                "agent_goal_revisions.project_id",
                "agent_goal_revisions.goal_session_id",
                "agent_goal_revisions.id",
            ],
            name="fk_agent_goal_obligations_revision_goal_revisions",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_id",
            "goal_session_id",
            "goal_revision_id",
            "id",
            name="uq_agent_goal_obligations_lineage_id",
        ),
        UniqueConstraint(
            "goal_revision_id", "obligation_key", name="uq_agent_goal_obligations_revision_key"
        ),
        UniqueConstraint(
            "goal_revision_id", "ordinal", name="uq_agent_goal_obligations_revision_ordinal"
        ),
        CheckConstraint("obligation_key ~ '^obl_[1-9][0-9]*$'", name="obligation_key_format"),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint(
            "capability IN ('analyze', 'audit', 'propose_mutation')",
            name="capability_allowed",
        ),
        CheckConstraint("target_state IN ('baseline', 'candidate')", name="target_state_allowed"),
        CheckConstraint(
            "capability = 'propose_mutation' OR target_state = 'baseline'",
            name="candidate_requires_mutation",
        ),
        CheckConstraint("length(btrim(instruction)) > 0", name="instruction_not_blank"),
        CheckConstraint("length(btrim(source_excerpt)) > 0", name="source_excerpt_not_blank"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    goal_session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    goal_revision_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    obligation_key: Mapped[str] = mapped_column(String(40), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    capability: Mapped[str] = mapped_column(String(32), nullable=False)
    target_state: Mapped[str] = mapped_column(String(16), nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    source_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class AgentGoalObligationDependency(BigIntIdentityPrimaryKeyMixin, Base):
    """Append-only edge in one revision's obligation DAG."""

    __tablename__ = "agent_goal_obligation_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "goal_session_id", "goal_revision_id", "obligation_id"],
            [
                "agent_goal_obligations.project_id",
                "agent_goal_obligations.goal_session_id",
                "agent_goal_obligations.goal_revision_id",
                "agent_goal_obligations.id",
            ],
            name="fk_agent_goal_obligation_dependencies_child_obligations",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "goal_session_id", "goal_revision_id", "depends_on_obligation_id"],
            [
                "agent_goal_obligations.project_id",
                "agent_goal_obligations.goal_session_id",
                "agent_goal_obligations.goal_revision_id",
                "agent_goal_obligations.id",
            ],
            name="fk_agent_goal_obligation_dependencies_parent_goal_obligations",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "goal_revision_id",
            "obligation_id",
            "depends_on_obligation_id",
            name="uq_agent_goal_obligation_dependencies_edge",
        ),
        CheckConstraint("obligation_id <> depends_on_obligation_id", name="not_self_dependency"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    goal_session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    goal_revision_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    obligation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    depends_on_obligation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class AgentGoalDelivery(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """Crash-safe FIFO delivery claimed and consumed at controller safe points."""

    __tablename__ = "agent_goal_deliveries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "thread_id", "goal_session_id"],
            [
                "agent_goal_sessions.project_id",
                "agent_goal_sessions.thread_id",
                "agent_goal_sessions.id",
            ],
            name="fk_agent_goal_deliveries_thread_session_goal_sessions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "thread_id", "source_message_id"],
            ["agent_messages.project_id", "agent_messages.thread_id", "agent_messages.id"],
            name="fk_agent_goal_deliveries_source_message_agent_messages",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "thread_id", "response_message_id"],
            ["agent_messages.project_id", "agent_messages.thread_id", "agent_messages.id"],
            name="fk_agent_goal_deliveries_response_message_agent_messages",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("project_id", "id", name="uq_agent_goal_deliveries_project_id_id"),
        UniqueConstraint("source_message_id", name="uq_agent_goal_deliveries_source_message"),
        UniqueConstraint(
            "thread_id", "message_sequence_no", name="uq_agent_goal_deliveries_thread_sequence"
        ),
        CheckConstraint("message_sequence_no >= 1", name="message_sequence_positive"),
        CheckConstraint("mode IN ('steer', 'follow_up', 'replace')", name="mode_allowed"),
        CheckConstraint(
            "status IN ('queued', 'claimed', 'consumed', 'cancelled')", name="status_allowed"
        ),
        CheckConstraint("expected_goal_revision BETWEEN 1 AND 8", name="expected_revision_bounded"),
        CheckConstraint(
            "(status = 'queued' AND claimed_at IS NULL AND consumed_at IS NULL "
            "AND cancelled_at IS NULL) OR "
            "(status = 'claimed' AND claimed_at IS NOT NULL AND claimed_by IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND consumed_at IS NULL AND cancelled_at IS NULL) OR "
            "(status = 'consumed' AND consumed_at IS NOT NULL AND cancelled_at IS NULL) OR "
            "(status = 'cancelled' AND cancelled_at IS NOT NULL AND consumed_at IS NULL)",
            name="lifecycle_shape",
        ),
        Index(
            "ix_agent_goal_deliveries_session_fifo",
            "goal_session_id",
            "status",
            "message_sequence_no",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    thread_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    goal_session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    response_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'queued'"))
    expected_goal_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason_code: Mapped[str | None] = mapped_column(String(80))


class AgentGoalObservation(BigIntIdentityPrimaryKeyMixin, Base):
    """Append-only hash-bound capability observation for completion evidence."""

    __tablename__ = "agent_goal_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "goal_session_id", "goal_revision_id", "obligation_id"],
            [
                "agent_goal_obligations.project_id",
                "agent_goal_obligations.goal_session_id",
                "agent_goal_obligations.goal_revision_id",
                "agent_goal_obligations.id",
            ],
            name="fk_agent_goal_observations_obligation_goal_obligations",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_agent_goal_observations_project_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["agent_step_run_id", "task_run_id"],
            ["agent_step_runs.id", "agent_step_runs.task_run_id"],
            name="fk_agent_goal_observations_step_task_run_agent_step_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "patch_set_id"],
            ["agent_patch_sets.project_id", "agent_patch_sets.id"],
            name="fk_agent_goal_observations_project_patch_agent_patch_sets",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "verification_run_id"],
            ["verification_runs.project_id", "verification_runs.id"],
            name="fk_agent_goal_observations_verification_runs",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "goal_session_id", "reused_from_observation_id"],
            [
                "agent_goal_observations.project_id",
                "agent_goal_observations.goal_session_id",
                "agent_goal_observations.id",
            ],
            name="fk_agent_goal_observations_reused_from_goal_observations",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_id", "goal_session_id", "id", name="uq_agent_goal_observations_session_id"
        ),
        UniqueConstraint(
            "goal_revision_id",
            "obligation_id",
            "task_run_id",
            "input_hash",
            "output_hash",
            name="uq_agent_goal_observations_execution_identity",
        ),
        CheckConstraint(
            "capability IN ('analyze', 'audit', 'propose_mutation')", name="capability_allowed"
        ),
        CheckConstraint("target_state IN ('baseline', 'candidate')", name="target_state_allowed"),
        CheckConstraint("status IN ('succeeded', 'failed', 'reused')", name="status_allowed"),
        CheckConstraint("draft_revision >= 1", name="draft_revision_positive"),
        CheckConstraint("draft_hash ~ '^[0-9a-f]{64}$'", name="draft_hash_format"),
        CheckConstraint("action_hash ~ '^[0-9a-f]{64}$'", name="action_hash_format"),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="input_hash_format"),
        CheckConstraint("upstream_hash ~ '^[0-9a-f]{64}$'", name="upstream_hash_format"),
        CheckConstraint("output_hash ~ '^[0-9a-f]{64}$'", name="output_hash_format"),
        CheckConstraint(
            "candidate_hash IS NULL OR candidate_hash ~ '^[0-9a-f]{64}$'",
            name="candidate_hash_format",
        ),
        CheckConstraint(
            "(status = 'reused' AND reused_from_observation_id IS NOT NULL) OR "
            "(status <> 'reused' AND reused_from_observation_id IS NULL)",
            name="reuse_shape",
        ),
        CheckConstraint(
            "(capability = 'propose_mutation' AND patch_set_id IS NOT NULL) OR "
            "(capability <> 'propose_mutation' AND patch_set_id IS NULL)",
            name="patch_shape",
        ),
        Index(
            "ix_agent_goal_observations_revision_obligation",
            "goal_revision_id",
            "obligation_id",
            "id",
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    goal_session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    goal_revision_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    obligation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    agent_step_run_id: Mapped[int | None] = mapped_column(BigInteger)
    capability: Mapped[str] = mapped_column(String(32), nullable=False)
    target_state: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    draft_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    upstream_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_hash: Mapped[str | None] = mapped_column(String(64))
    patch_set_id: Mapped[int | None] = mapped_column(BigInteger)
    verification_run_id: Mapped[int | None] = mapped_column(BigInteger)
    reused_from_observation_id: Mapped[int | None] = mapped_column(BigInteger)
    summary_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class AgentGoalTaskRun(BigIntIdentityPrimaryKeyMixin, TimestampMixin, Base):
    """Mutable-until-terminal binding of an immutable TaskRun execution slice."""

    __tablename__ = "agent_goal_task_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "goal_session_id"],
            ["agent_goal_sessions.project_id", "agent_goal_sessions.id"],
            name="fk_agent_goal_task_runs_project_session_goal_sessions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "goal_session_id", "goal_revision_id"],
            [
                "agent_goal_revisions.project_id",
                "agent_goal_revisions.goal_session_id",
                "agent_goal_revisions.id",
            ],
            name="fk_agent_goal_task_runs_revision_goal_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_agent_goal_task_runs_project_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("task_run_id", name="uq_agent_goal_task_runs_task_run"),
        UniqueConstraint(
            "goal_session_id", "slice_no", name="uq_agent_goal_task_runs_session_slice"
        ),
        CheckConstraint("slice_no BETWEEN 1 AND 12", name="slice_no_bounded"),
        CheckConstraint(
            "trigger_kind IN ('initial', 'steer', 'clarification', 'post_apply', 'recovery')",
            name="trigger_kind_allowed",
        ),
        CheckConstraint(
            "status IN ('active', 'checkpointed', 'completed', 'failed', 'cancelled')",
            name="status_allowed",
        ),
        CheckConstraint(
            "(status = 'active' AND finished_at IS NULL) OR "
            "(status <> 'active' AND finished_at IS NOT NULL)",
            name="terminal_shape",
        ),
        CheckConstraint(
            "checkpoint_hash IS NULL OR checkpoint_hash ~ '^[0-9a-f]{64}$'",
            name="checkpoint_hash_format",
        ),
        Index(
            "uq_agent_goal_task_runs_session_active",
            "goal_session_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    goal_session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    goal_revision_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    task_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    slice_no: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'active'"))
    checkpoint_hash: Mapped[str | None] = mapped_column(String(64))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentGoalTransition(BigIntIdentityPrimaryKeyMixin, Base):
    """Append-only state transition audit event."""

    __tablename__ = "agent_goal_transitions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "goal_session_id"],
            ["agent_goal_sessions.project_id", "agent_goal_sessions.id"],
            name="fk_agent_goal_transitions_project_session_goal_sessions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "goal_session_id", "goal_revision_id"],
            [
                "agent_goal_revisions.project_id",
                "agent_goal_revisions.goal_session_id",
                "agent_goal_revisions.id",
            ],
            name="fk_agent_goal_transitions_revision_goal_revisions",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "source_message_id"],
            ["agent_messages.project_id", "agent_messages.id"],
            name="fk_agent_goal_transitions_source_message_agent_messages",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "task_run_id"],
            ["task_runs.project_id", "task_runs.id"],
            name="fk_agent_goal_transitions_project_task_run_task_runs",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "goal_session_id", "sequence_no", name="uq_agent_goal_transitions_session_sequence"
        ),
        CheckConstraint("sequence_no >= 1", name="sequence_no_positive"),
        CheckConstraint(
            "from_status IS NULL OR from_status IN ("
            + ", ".join(f"'{value}'" for value in _GOAL_STATUSES)
            + ")",
            name="from_status_allowed",
        ),
        CheckConstraint(
            "to_status IN (" + ", ".join(f"'{value}'" for value in _GOAL_STATUSES) + ")",
            name="to_status_allowed",
        ),
        CheckConstraint("length(btrim(reason_code)) > 0", name="reason_not_blank"),
        CheckConstraint("state_hash ~ '^[0-9a-f]{64}$'", name="state_hash_format"),
        Index("ix_agent_goal_transitions_session_sequence", "goal_session_id", "sequence_no"),
    )

    project_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    goal_session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    goal_revision_id: Mapped[int | None] = mapped_column(BigInteger)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger)
    task_run_id: Mapped[int | None] = mapped_column(BigInteger)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


__all__ = [
    "AgentGoalDelivery",
    "AgentGoalObligation",
    "AgentGoalObligationDependency",
    "AgentGoalObservation",
    "AgentGoalRevision",
    "AgentGoalSession",
    "AgentGoalTaskRun",
    "AgentGoalTransition",
]
