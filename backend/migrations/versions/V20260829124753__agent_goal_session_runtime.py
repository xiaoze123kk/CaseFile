"""agent_goal_session_runtime

Revision ID: 20260829124753
Revises: 20260826175944
Create Date: 2026-08-29 12:47:53+08:00
"""

# ruff: noqa: E501

from collections.abc import Sequence

from alembic import op

revision: str = "20260829124753"
down_revision: str | None = "20260826175944"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_agent_messages_thread_lineage_id"),
        "agent_messages",
        ["project_id", "thread_id", "id"],
    )
    op.drop_constraint(op.f("ck_agent_messages_status_allowed"), "agent_messages", type_="check")
    op.create_check_constraint(
        op.f("ck_agent_messages_status_allowed"),
        "agent_messages",
        "status IN ('pending', 'completed', 'failed', 'cancelled')",
    )
    op.drop_constraint(op.f("ck_agent_messages_content_shape"), "agent_messages", type_="check")
    op.create_check_constraint(
        op.f("ck_agent_messages_content_shape"),
        "agent_messages",
        "(status = 'pending' AND role = 'assistant' AND content_text IS NULL) OR "
        "(status = 'failed' AND role = 'assistant') OR "
        "(status = 'cancelled' AND role = 'assistant' AND content_text IS NULL) OR "
        "(status = 'completed' AND content_text IS NOT NULL "
        "AND length(btrim(content_text)) > 0)",
    )

    for statement in _TABLE_DDL:
        op.execute(statement)

    op.execute(
        "ALTER TABLE agent_goal_sessions ADD CONSTRAINT "
        "fk_agent_goal_sessions_current_revision_goal_revisions "
        "FOREIGN KEY(project_id, id, current_revision_id) REFERENCES "
        "agent_goal_revisions (project_id, goal_session_id, id) ON DELETE RESTRICT "
        "DEFERRABLE INITIALLY DEFERRED"
    )
    for statement in _INDEX_DDL:
        op.execute(statement)
    for table_name in (
        "agent_goal_sessions",
        "agent_goal_deliveries",
        "agent_goal_task_runs",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_updated_at BEFORE UPDATE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION casefile_set_updated_at()"
        )
    for table_name in (
        "agent_goal_revisions",
        "agent_goal_obligations",
        "agent_goal_obligation_dependencies",
        "agent_goal_observations",
        "agent_goal_transitions",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_immutable BEFORE UPDATE OR DELETE "
            f"ON {table_name} FOR EACH ROW EXECUTE FUNCTION "
            "casefile_reject_history_mutation()"
        )
    _create_lifecycle_triggers()


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_agent_goal_deliveries_lifecycle ON agent_goal_deliveries")
    op.execute("DROP TRIGGER trg_agent_goal_task_runs_lifecycle ON agent_goal_task_runs")
    op.execute("DROP TRIGGER trg_agent_goal_sessions_lifecycle ON agent_goal_sessions")
    op.execute("DROP FUNCTION casefile_protect_goal_delivery_lifecycle()")
    op.execute("DROP FUNCTION casefile_protect_goal_task_run_lifecycle()")
    op.execute("DROP FUNCTION casefile_protect_goal_session_lifecycle()")
    for table_name in (
        "agent_goal_transitions",
        "agent_goal_observations",
        "agent_goal_obligation_dependencies",
        "agent_goal_obligations",
        "agent_goal_revisions",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    for table_name in (
        "agent_goal_task_runs",
        "agent_goal_deliveries",
        "agent_goal_sessions",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_updated_at ON {table_name}")
    op.execute(
        "ALTER TABLE agent_goal_sessions DROP CONSTRAINT "
        "fk_agent_goal_sessions_current_revision_goal_revisions"
    )
    for table_name in (
        "agent_goal_transitions",
        "agent_goal_task_runs",
        "agent_goal_observations",
        "agent_goal_deliveries",
        "agent_goal_obligation_dependencies",
        "agent_goal_obligations",
        "agent_goal_revisions",
        "agent_goal_sessions",
    ):
        op.drop_table(table_name)
    op.drop_constraint(op.f("ck_agent_messages_content_shape"), "agent_messages", type_="check")
    op.create_check_constraint(
        op.f("ck_agent_messages_content_shape"),
        "agent_messages",
        "(status = 'pending' AND role = 'assistant' AND content_text IS NULL) OR "
        "(status = 'failed' AND role = 'assistant') OR "
        "(status = 'completed' AND content_text IS NOT NULL "
        "AND length(btrim(content_text)) > 0)",
    )
    op.drop_constraint(op.f("ck_agent_messages_status_allowed"), "agent_messages", type_="check")
    op.create_check_constraint(
        op.f("ck_agent_messages_status_allowed"),
        "agent_messages",
        "status IN ('pending', 'completed', 'failed')",
    )
    op.drop_constraint(
        op.f("uq_agent_messages_thread_lineage_id"),
        "agent_messages",
        type_="unique",
    )


def _create_lifecycle_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION casefile_protect_goal_session_lifecycle() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'GoalSession history is immutable';
            END IF;
            IF OLD.status IN ('completed', 'cancelled', 'superseded', 'failed') THEN
                RAISE EXCEPTION 'terminal GoalSession is immutable';
            END IF;
            IF (NEW.project_id, NEW.casefile_id, NEW.draft_id, NEW.thread_id,
                NEW.source_message_id, NEW.created_by_user_id)
               IS DISTINCT FROM
               (OLD.project_id, OLD.casefile_id, OLD.draft_id, OLD.thread_id,
                OLD.source_message_id, OLD.created_by_user_id) THEN
                RAISE EXCEPTION 'GoalSession ownership and source are immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_agent_goal_sessions_lifecycle BEFORE UPDATE OR DELETE "
        "ON agent_goal_sessions FOR EACH ROW EXECUTE FUNCTION "
        "casefile_protect_goal_session_lifecycle()"
    )
    op.execute(
        """
        CREATE FUNCTION casefile_protect_goal_task_run_lifecycle() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Goal TaskRun history is immutable';
            END IF;
            IF OLD.status <> 'active' THEN
                RAISE EXCEPTION 'terminal Goal TaskRun binding is immutable';
            END IF;
            IF (NEW.project_id, NEW.goal_session_id, NEW.goal_revision_id,
                NEW.task_run_id, NEW.slice_no, NEW.trigger_kind)
               IS DISTINCT FROM
               (OLD.project_id, OLD.goal_session_id, OLD.goal_revision_id,
                OLD.task_run_id, OLD.slice_no, OLD.trigger_kind) THEN
                RAISE EXCEPTION 'Goal TaskRun identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_agent_goal_task_runs_lifecycle BEFORE UPDATE OR DELETE "
        "ON agent_goal_task_runs FOR EACH ROW EXECUTE FUNCTION "
        "casefile_protect_goal_task_run_lifecycle()"
    )
    op.execute(
        """
        CREATE FUNCTION casefile_protect_goal_delivery_lifecycle() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Goal delivery history is immutable';
            END IF;
            IF OLD.status IN ('consumed', 'cancelled') THEN
                RAISE EXCEPTION 'terminal Goal delivery is immutable';
            END IF;
            IF (NEW.project_id, NEW.thread_id, NEW.goal_session_id,
                NEW.source_message_id, NEW.response_message_id,
                NEW.message_sequence_no, NEW.mode, NEW.expected_goal_revision)
               IS DISTINCT FROM
               (OLD.project_id, OLD.thread_id, OLD.goal_session_id,
                OLD.source_message_id, OLD.response_message_id,
                OLD.message_sequence_no, OLD.mode, OLD.expected_goal_revision) THEN
                RAISE EXCEPTION 'Goal delivery identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_agent_goal_deliveries_lifecycle BEFORE UPDATE OR DELETE "
        "ON agent_goal_deliveries FOR EACH ROW EXECUTE FUNCTION "
        "casefile_protect_goal_delivery_lifecycle()"
    )


_TABLE_DDL = (
    """
    CREATE TABLE agent_goal_sessions (
      project_id BIGINT NOT NULL, casefile_id BIGINT NOT NULL, draft_id BIGINT NOT NULL,
      thread_id BIGINT NOT NULL, source_message_id BIGINT NOT NULL,
      created_by_user_id BIGINT NOT NULL, predecessor_goal_session_id BIGINT,
      status VARCHAR(32) NOT NULL, runtime_version VARCHAR(80) NOT NULL,
      policy_version VARCHAR(80) NOT NULL, capability_registry_version VARCHAR(80) NOT NULL,
      baseline_draft_revision INTEGER NOT NULL, baseline_hash VARCHAR(64) NOT NULL,
      current_revision_id BIGINT, active_patch_set_id BIGINT,
      revision_count INTEGER DEFAULT 0 NOT NULL,
      task_run_slice_count INTEGER DEFAULT 0 NOT NULL,
      consumed_control_count INTEGER DEFAULT 0 NOT NULL,
      terminal_reason_code VARCHAR(80), id BIGINT GENERATED BY DEFAULT AS IDENTITY,
      created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
      updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
      CONSTRAINT pk_agent_goal_sessions PRIMARY KEY (id),
      CONSTRAINT fk_agent_goal_sessions_project_casefile_draft_drafts FOREIGN KEY(project_id, casefile_id, draft_id) REFERENCES drafts(project_id, casefile_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_sessions_project_thread_agent_threads FOREIGN KEY(project_id, thread_id) REFERENCES agent_threads(project_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_sessions_source_message_agent_messages FOREIGN KEY(project_id, thread_id, source_message_id) REFERENCES agent_messages(project_id, thread_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_sessions_project_predecessor_goal_sessions FOREIGN KEY(project_id, predecessor_goal_session_id) REFERENCES agent_goal_sessions(project_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_sessions_project_active_patch_agent_patch_sets FOREIGN KEY(project_id, active_patch_set_id) REFERENCES agent_patch_sets(project_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_sessions_created_by_user_id_users FOREIGN KEY(created_by_user_id) REFERENCES users(id) ON DELETE RESTRICT,
      CONSTRAINT uq_agent_goal_sessions_project_id_id UNIQUE(project_id, id),
      CONSTRAINT uq_agent_goal_sessions_thread_lineage_id UNIQUE(project_id, thread_id, id),
      CONSTRAINT ck_agent_goal_sessions_status_allowed CHECK(status IN ('interpreting','running','waiting_clarification','waiting_patch_review','stale','completed','cancelled','superseded','failed')),
      CONSTRAINT ck_agent_goal_sessions_baseline_revision_positive CHECK(baseline_draft_revision >= 1),
      CONSTRAINT ck_agent_goal_sessions_baseline_hash_format CHECK(baseline_hash ~ '^[0-9a-f]{64}$'),
      CONSTRAINT ck_agent_goal_sessions_revision_count_bounded CHECK(revision_count BETWEEN 0 AND 8),
      CONSTRAINT ck_agent_goal_sessions_slice_count_bounded CHECK(task_run_slice_count BETWEEN 0 AND 12),
      CONSTRAINT ck_agent_goal_sessions_control_count_bounded CHECK(consumed_control_count BETWEEN 0 AND 6),
      CONSTRAINT ck_agent_goal_sessions_current_revision_shape CHECK((revision_count = 0 AND current_revision_id IS NULL) OR (revision_count >= 1 AND current_revision_id IS NOT NULL)),
      CONSTRAINT ck_agent_goal_sessions_patch_review_shape CHECK((status = 'waiting_patch_review' AND active_patch_set_id IS NOT NULL) OR status <> 'waiting_patch_review')
    )
    """,
    """
    CREATE TABLE agent_goal_revisions (
      project_id BIGINT NOT NULL, goal_session_id BIGINT NOT NULL,
      revision_no INTEGER NOT NULL, parent_revision_id BIGINT,
      source_message_id BIGINT NOT NULL, amendment_kind VARCHAR(32) NOT NULL,
      goal_text TEXT NOT NULL, source_excerpt TEXT NOT NULL,
      obligations_hash VARCHAR(64) NOT NULL, state_hash VARCHAR(64) NOT NULL,
      baseline_draft_revision INTEGER NOT NULL, baseline_hash VARCHAR(64) NOT NULL,
      created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
      id BIGINT GENERATED BY DEFAULT AS IDENTITY,
      CONSTRAINT pk_agent_goal_revisions PRIMARY KEY(id),
      CONSTRAINT fk_agent_goal_revisions_project_session_goal_sessions FOREIGN KEY(project_id, goal_session_id) REFERENCES agent_goal_sessions(project_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_revisions_parent_goal_revisions FOREIGN KEY(project_id, goal_session_id, parent_revision_id) REFERENCES agent_goal_revisions(project_id, goal_session_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_revisions_source_message_agent_messages FOREIGN KEY(project_id, source_message_id) REFERENCES agent_messages(project_id, id) ON DELETE RESTRICT,
      CONSTRAINT uq_agent_goal_revisions_lineage_id UNIQUE(project_id, goal_session_id, id),
      CONSTRAINT uq_agent_goal_revisions_session_revision UNIQUE(goal_session_id, revision_no),
      CONSTRAINT ck_agent_goal_revisions_revision_no_bounded CHECK(revision_no BETWEEN 1 AND 8),
      CONSTRAINT ck_agent_goal_revisions_amendment_kind_allowed CHECK(amendment_kind IN ('initial','refine','add_constraint','add_obligation','remove_obligation','post_apply')),
      CONSTRAINT ck_agent_goal_revisions_parent_shape CHECK((revision_no = 1 AND amendment_kind = 'initial' AND parent_revision_id IS NULL) OR (revision_no > 1 AND amendment_kind <> 'initial' AND parent_revision_id IS NOT NULL)),
      CONSTRAINT ck_agent_goal_revisions_goal_text_not_blank CHECK(length(btrim(goal_text)) > 0),
      CONSTRAINT ck_agent_goal_revisions_obligations_hash_format CHECK(obligations_hash ~ '^[0-9a-f]{64}$'),
      CONSTRAINT ck_agent_goal_revisions_state_hash_format CHECK(state_hash ~ '^[0-9a-f]{64}$'),
      CONSTRAINT ck_agent_goal_revisions_baseline_revision_positive CHECK(baseline_draft_revision >= 1),
      CONSTRAINT ck_agent_goal_revisions_baseline_hash_format CHECK(baseline_hash ~ '^[0-9a-f]{64}$')
    )
    """,
    """
    CREATE TABLE agent_goal_obligations (
      project_id BIGINT NOT NULL, goal_session_id BIGINT NOT NULL,
      goal_revision_id BIGINT NOT NULL, obligation_key VARCHAR(40) NOT NULL,
      ordinal INTEGER NOT NULL, capability VARCHAR(32) NOT NULL,
      target_state VARCHAR(16) NOT NULL, instruction TEXT NOT NULL,
      source_excerpt TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
      id BIGINT GENERATED BY DEFAULT AS IDENTITY,
      CONSTRAINT pk_agent_goal_obligations PRIMARY KEY(id),
      CONSTRAINT fk_agent_goal_obligations_revision_goal_revisions FOREIGN KEY(project_id, goal_session_id, goal_revision_id) REFERENCES agent_goal_revisions(project_id, goal_session_id, id) ON DELETE RESTRICT,
      CONSTRAINT uq_agent_goal_obligations_lineage_id UNIQUE(project_id, goal_session_id, goal_revision_id, id),
      CONSTRAINT uq_agent_goal_obligations_revision_key UNIQUE(goal_revision_id, obligation_key),
      CONSTRAINT uq_agent_goal_obligations_revision_ordinal UNIQUE(goal_revision_id, ordinal),
      CONSTRAINT ck_agent_goal_obligations_obligation_key_format CHECK(obligation_key ~ '^obl_[1-9][0-9]*$'),
      CONSTRAINT ck_agent_goal_obligations_ordinal_positive CHECK(ordinal >= 1),
      CONSTRAINT ck_agent_goal_obligations_capability_allowed CHECK(capability IN ('analyze','audit','propose_mutation')),
      CONSTRAINT ck_agent_goal_obligations_target_state_allowed CHECK(target_state IN ('baseline','candidate')),
      CONSTRAINT ck_agent_goal_obligations_candidate_requires_mutation CHECK(capability = 'propose_mutation' OR target_state = 'baseline'),
      CONSTRAINT ck_agent_goal_obligations_instruction_not_blank CHECK(length(btrim(instruction)) > 0),
      CONSTRAINT ck_agent_goal_obligations_source_excerpt_not_blank CHECK(length(btrim(source_excerpt)) > 0)
    )
    """,
    """
    CREATE TABLE agent_goal_obligation_dependencies (
      project_id BIGINT NOT NULL, goal_session_id BIGINT NOT NULL,
      goal_revision_id BIGINT NOT NULL, obligation_id BIGINT NOT NULL,
      depends_on_obligation_id BIGINT NOT NULL,
      created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
      id BIGINT GENERATED BY DEFAULT AS IDENTITY,
      CONSTRAINT pk_agent_goal_obligation_dependencies PRIMARY KEY(id),
      CONSTRAINT fk_agent_goal_obligation_dependencies_child_obligations FOREIGN KEY(project_id, goal_session_id, goal_revision_id, obligation_id) REFERENCES agent_goal_obligations(project_id, goal_session_id, goal_revision_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_obligation_dependencies_parent_goal_obligations FOREIGN KEY(project_id, goal_session_id, goal_revision_id, depends_on_obligation_id) REFERENCES agent_goal_obligations(project_id, goal_session_id, goal_revision_id, id) ON DELETE RESTRICT,
      CONSTRAINT uq_agent_goal_obligation_dependencies_edge UNIQUE(goal_revision_id, obligation_id, depends_on_obligation_id),
      CONSTRAINT ck_agent_goal_obligation_dependencies_not_self_dependency CHECK(obligation_id <> depends_on_obligation_id)
    )
    """,
    """
    CREATE TABLE agent_goal_deliveries (
      project_id BIGINT NOT NULL, thread_id BIGINT NOT NULL,
      goal_session_id BIGINT NOT NULL, source_message_id BIGINT NOT NULL,
      response_message_id BIGINT NOT NULL, message_sequence_no BIGINT NOT NULL,
      mode VARCHAR(16) NOT NULL, status VARCHAR(16) DEFAULT 'queued' NOT NULL,
      expected_goal_revision INTEGER NOT NULL, claimed_by VARCHAR(120),
      lease_expires_at TIMESTAMPTZ, claimed_at TIMESTAMPTZ,
      consumed_at TIMESTAMPTZ, cancelled_at TIMESTAMPTZ, reason_code VARCHAR(80),
      id BIGINT GENERATED BY DEFAULT AS IDENTITY,
      created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
      updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
      CONSTRAINT pk_agent_goal_deliveries PRIMARY KEY(id),
      CONSTRAINT fk_agent_goal_deliveries_thread_session_goal_sessions FOREIGN KEY(project_id, thread_id, goal_session_id) REFERENCES agent_goal_sessions(project_id, thread_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_deliveries_source_message_agent_messages FOREIGN KEY(project_id, thread_id, source_message_id) REFERENCES agent_messages(project_id, thread_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_deliveries_response_message_agent_messages FOREIGN KEY(project_id, thread_id, response_message_id) REFERENCES agent_messages(project_id, thread_id, id) ON DELETE RESTRICT,
      CONSTRAINT uq_agent_goal_deliveries_project_id_id UNIQUE(project_id, id),
      CONSTRAINT uq_agent_goal_deliveries_source_message UNIQUE(source_message_id),
      CONSTRAINT uq_agent_goal_deliveries_thread_sequence UNIQUE(thread_id, message_sequence_no),
      CONSTRAINT ck_agent_goal_deliveries_message_sequence_positive CHECK(message_sequence_no >= 1),
      CONSTRAINT ck_agent_goal_deliveries_mode_allowed CHECK(mode IN ('steer','follow_up','replace')),
      CONSTRAINT ck_agent_goal_deliveries_status_allowed CHECK(status IN ('queued','claimed','consumed','cancelled')),
      CONSTRAINT ck_agent_goal_deliveries_expected_revision_bounded CHECK(expected_goal_revision BETWEEN 1 AND 8),
      CONSTRAINT ck_agent_goal_deliveries_lifecycle_shape CHECK((status = 'queued' AND claimed_at IS NULL AND consumed_at IS NULL AND cancelled_at IS NULL) OR (status = 'claimed' AND claimed_at IS NOT NULL AND claimed_by IS NOT NULL AND lease_expires_at IS NOT NULL AND consumed_at IS NULL AND cancelled_at IS NULL) OR (status = 'consumed' AND consumed_at IS NOT NULL AND cancelled_at IS NULL) OR (status = 'cancelled' AND cancelled_at IS NOT NULL AND consumed_at IS NULL))
    )
    """,
    """
    CREATE TABLE agent_goal_observations (
      project_id BIGINT NOT NULL, goal_session_id BIGINT NOT NULL,
      goal_revision_id BIGINT NOT NULL, obligation_id BIGINT NOT NULL,
      task_run_id BIGINT NOT NULL, agent_step_run_id BIGINT,
      capability VARCHAR(32) NOT NULL, target_state VARCHAR(16) NOT NULL,
      status VARCHAR(16) NOT NULL, draft_revision INTEGER NOT NULL,
      draft_hash VARCHAR(64) NOT NULL, action_hash VARCHAR(64) NOT NULL,
      input_hash VARCHAR(64) NOT NULL, upstream_hash VARCHAR(64) NOT NULL,
      output_hash VARCHAR(64) NOT NULL, candidate_hash VARCHAR(64),
      patch_set_id BIGINT, verification_run_id BIGINT,
      reused_from_observation_id BIGINT, summary_text TEXT,
      created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
      id BIGINT GENERATED BY DEFAULT AS IDENTITY,
      CONSTRAINT pk_agent_goal_observations PRIMARY KEY(id),
      CONSTRAINT fk_agent_goal_observations_obligation_goal_obligations FOREIGN KEY(project_id, goal_session_id, goal_revision_id, obligation_id) REFERENCES agent_goal_obligations(project_id, goal_session_id, goal_revision_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_observations_project_task_run_task_runs FOREIGN KEY(project_id, task_run_id) REFERENCES task_runs(project_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_observations_step_task_run_agent_step_runs FOREIGN KEY(agent_step_run_id, task_run_id) REFERENCES agent_step_runs(id, task_run_id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_observations_project_patch_agent_patch_sets FOREIGN KEY(project_id, patch_set_id) REFERENCES agent_patch_sets(project_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_observations_verification_runs FOREIGN KEY(project_id, verification_run_id) REFERENCES verification_runs(project_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_observations_reused_from_goal_observations FOREIGN KEY(project_id, goal_session_id, reused_from_observation_id) REFERENCES agent_goal_observations(project_id, goal_session_id, id) ON DELETE RESTRICT,
      CONSTRAINT uq_agent_goal_observations_session_id UNIQUE(project_id, goal_session_id, id),
      CONSTRAINT uq_agent_goal_observations_execution_identity UNIQUE(goal_revision_id, obligation_id, task_run_id, input_hash, output_hash),
      CONSTRAINT ck_agent_goal_observations_capability_allowed CHECK(capability IN ('analyze','audit','propose_mutation')),
      CONSTRAINT ck_agent_goal_observations_target_state_allowed CHECK(target_state IN ('baseline','candidate')),
      CONSTRAINT ck_agent_goal_observations_status_allowed CHECK(status IN ('succeeded','failed','reused')),
      CONSTRAINT ck_agent_goal_observations_draft_revision_positive CHECK(draft_revision >= 1),
      CONSTRAINT ck_agent_goal_observations_draft_hash_format CHECK(draft_hash ~ '^[0-9a-f]{64}$'),
      CONSTRAINT ck_agent_goal_observations_action_hash_format CHECK(action_hash ~ '^[0-9a-f]{64}$'),
      CONSTRAINT ck_agent_goal_observations_input_hash_format CHECK(input_hash ~ '^[0-9a-f]{64}$'),
      CONSTRAINT ck_agent_goal_observations_upstream_hash_format CHECK(upstream_hash ~ '^[0-9a-f]{64}$'),
      CONSTRAINT ck_agent_goal_observations_output_hash_format CHECK(output_hash ~ '^[0-9a-f]{64}$'),
      CONSTRAINT ck_agent_goal_observations_candidate_hash_format CHECK(candidate_hash IS NULL OR candidate_hash ~ '^[0-9a-f]{64}$'),
      CONSTRAINT ck_agent_goal_observations_reuse_shape CHECK((status = 'reused' AND reused_from_observation_id IS NOT NULL) OR (status <> 'reused' AND reused_from_observation_id IS NULL)),
      CONSTRAINT ck_agent_goal_observations_patch_shape CHECK((capability = 'propose_mutation' AND patch_set_id IS NOT NULL) OR (capability <> 'propose_mutation' AND patch_set_id IS NULL))
    )
    """,
    """
    CREATE TABLE agent_goal_task_runs (
      project_id BIGINT NOT NULL, goal_session_id BIGINT NOT NULL,
      goal_revision_id BIGINT NOT NULL, task_run_id BIGINT NOT NULL,
      slice_no INTEGER NOT NULL, trigger_kind VARCHAR(24) NOT NULL,
      status VARCHAR(20) DEFAULT 'active' NOT NULL, checkpoint_hash VARCHAR(64),
      finished_at TIMESTAMPTZ, id BIGINT GENERATED BY DEFAULT AS IDENTITY,
      created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
      updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
      CONSTRAINT pk_agent_goal_task_runs PRIMARY KEY(id),
      CONSTRAINT fk_agent_goal_task_runs_project_session_goal_sessions FOREIGN KEY(project_id, goal_session_id) REFERENCES agent_goal_sessions(project_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_task_runs_revision_goal_revisions FOREIGN KEY(project_id, goal_session_id, goal_revision_id) REFERENCES agent_goal_revisions(project_id, goal_session_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_task_runs_project_task_run_task_runs FOREIGN KEY(project_id, task_run_id) REFERENCES task_runs(project_id, id) ON DELETE RESTRICT,
      CONSTRAINT uq_agent_goal_task_runs_task_run UNIQUE(task_run_id),
      CONSTRAINT uq_agent_goal_task_runs_session_slice UNIQUE(goal_session_id, slice_no),
      CONSTRAINT ck_agent_goal_task_runs_slice_no_bounded CHECK(slice_no BETWEEN 1 AND 12),
      CONSTRAINT ck_agent_goal_task_runs_trigger_kind_allowed CHECK(trigger_kind IN ('initial','steer','clarification','post_apply','recovery')),
      CONSTRAINT ck_agent_goal_task_runs_status_allowed CHECK(status IN ('active','checkpointed','completed','failed','cancelled')),
      CONSTRAINT ck_agent_goal_task_runs_terminal_shape CHECK((status = 'active' AND finished_at IS NULL) OR (status <> 'active' AND finished_at IS NOT NULL)),
      CONSTRAINT ck_agent_goal_task_runs_checkpoint_hash_format CHECK(checkpoint_hash IS NULL OR checkpoint_hash ~ '^[0-9a-f]{64}$')
    )
    """,
    """
    CREATE TABLE agent_goal_transitions (
      project_id BIGINT NOT NULL, goal_session_id BIGINT NOT NULL,
      sequence_no INTEGER NOT NULL, from_status VARCHAR(32),
      to_status VARCHAR(32) NOT NULL, reason_code VARCHAR(80) NOT NULL,
      goal_revision_id BIGINT, source_message_id BIGINT, task_run_id BIGINT,
      state_hash VARCHAR(64) NOT NULL,
      occurred_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
      id BIGINT GENERATED BY DEFAULT AS IDENTITY,
      CONSTRAINT pk_agent_goal_transitions PRIMARY KEY(id),
      CONSTRAINT fk_agent_goal_transitions_project_session_goal_sessions FOREIGN KEY(project_id, goal_session_id) REFERENCES agent_goal_sessions(project_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_transitions_revision_goal_revisions FOREIGN KEY(project_id, goal_session_id, goal_revision_id) REFERENCES agent_goal_revisions(project_id, goal_session_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_transitions_source_message_agent_messages FOREIGN KEY(project_id, source_message_id) REFERENCES agent_messages(project_id, id) ON DELETE RESTRICT,
      CONSTRAINT fk_agent_goal_transitions_project_task_run_task_runs FOREIGN KEY(project_id, task_run_id) REFERENCES task_runs(project_id, id) ON DELETE RESTRICT,
      CONSTRAINT uq_agent_goal_transitions_session_sequence UNIQUE(goal_session_id, sequence_no),
      CONSTRAINT ck_agent_goal_transitions_sequence_no_positive CHECK(sequence_no >= 1),
      CONSTRAINT ck_agent_goal_transitions_from_status_allowed CHECK(from_status IS NULL OR from_status IN ('interpreting','running','waiting_clarification','waiting_patch_review','stale','completed','cancelled','superseded','failed')),
      CONSTRAINT ck_agent_goal_transitions_to_status_allowed CHECK(to_status IN ('interpreting','running','waiting_clarification','waiting_patch_review','stale','completed','cancelled','superseded','failed')),
      CONSTRAINT ck_agent_goal_transitions_reason_not_blank CHECK(length(btrim(reason_code)) > 0),
      CONSTRAINT ck_agent_goal_transitions_state_hash_format CHECK(state_hash ~ '^[0-9a-f]{64}$')
    )
    """,
)

_INDEX_DDL = (
    "CREATE INDEX ix_agent_goal_sessions_project_status_updated ON agent_goal_sessions(project_id, status, updated_at)",
    "CREATE UNIQUE INDEX uq_agent_goal_sessions_thread_active ON agent_goal_sessions(thread_id) WHERE status NOT IN ('completed','cancelled','superseded','failed')",
    "CREATE INDEX ix_agent_goal_deliveries_session_fifo ON agent_goal_deliveries(goal_session_id, status, message_sequence_no)",
    "CREATE INDEX ix_agent_goal_observations_revision_obligation ON agent_goal_observations(goal_revision_id, obligation_id, id)",
    "CREATE UNIQUE INDEX uq_agent_goal_task_runs_session_active ON agent_goal_task_runs(goal_session_id) WHERE status = 'active'",
    "CREATE INDEX ix_agent_goal_transitions_session_sequence ON agent_goal_transitions(goal_session_id, sequence_no)",
)
