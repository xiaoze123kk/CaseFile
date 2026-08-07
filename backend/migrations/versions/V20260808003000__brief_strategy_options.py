"""Add the frozen Brief strategy-options TaskRun type."""

from alembic import op

revision: str = "20260808003000"
down_revision: str | None = "20260804233938"
branch_labels: str | None = None
depends_on: str | None = None


_OLD_TASK_TYPE_CHECK = (
    "task_type IN ('brief_polish', 'brief_anchor_extract', 'brief_intake_questions', "
    "'brief_intake_synthesize', 'brief_to_draft', 'casefile_chat')"
)

_NEW_TASK_TYPE_CHECK = (
    "task_type IN ('brief_polish', 'brief_anchor_extract', 'brief_intake_questions', "
    "'brief_intake_synthesize', 'brief_strategy_options', 'brief_to_draft', "
    "'casefile_chat')"
)

_COMMON_PREFIX = (
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
)

_COMMON_SUFFIX = (
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

_STRATEGY_INPUT_CHECK = (
    "(task_type = 'brief_strategy_options' AND brief_version_id IS NOT NULL "
    "AND input_source_record_id IS NULL AND input_brief_revision IS NOT NULL "
    "AND brief_intake_id IS NULL AND input_brief_intake_revision IS NULL "
    "AND base_brief_intake_candidate_id IS NULL AND agent_thread_id IS NULL "
    "AND input_message_id IS NULL AND output_message_id IS NULL) OR "
)

_OLD_TASK_INPUT_CHECK = _COMMON_PREFIX + _COMMON_SUFFIX
_NEW_TASK_INPUT_CHECK = _COMMON_PREFIX + _STRATEGY_INPUT_CHECK + _COMMON_SUFFIX


def upgrade() -> None:
    op.drop_constraint(op.f("ck_task_runs_task_type_allowed"), "task_runs", type_="check")
    op.drop_constraint(
        op.f("ck_task_runs_input_matches_task_type"), "task_runs", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_task_runs_task_type_allowed"), "task_runs", _NEW_TASK_TYPE_CHECK
    )
    op.create_check_constraint(
        op.f("ck_task_runs_input_matches_task_type"), "task_runs", _NEW_TASK_INPUT_CHECK
    )


def downgrade() -> None:
    op.execute("ALTER TABLE task_events DISABLE TRIGGER trg_task_events_immutable")
    op.execute(
        "ALTER TABLE task_attempts DISABLE TRIGGER trg_task_attempt_candidate_immutable"
    )
    op.execute(
        "DELETE FROM task_events WHERE task_run_id IN "
        "(SELECT id FROM task_runs WHERE task_type = 'brief_strategy_options')"
    )
    op.execute(
        "DELETE FROM task_attempts WHERE task_run_id IN "
        "(SELECT id FROM task_runs WHERE task_type = 'brief_strategy_options')"
    )
    op.execute("DELETE FROM task_runs WHERE task_type = 'brief_strategy_options'")
    op.execute(
        "ALTER TABLE task_attempts ENABLE TRIGGER trg_task_attempt_candidate_immutable"
    )
    op.execute("ALTER TABLE task_events ENABLE TRIGGER trg_task_events_immutable")
    op.drop_constraint(
        op.f("ck_task_runs_input_matches_task_type"), "task_runs", type_="check"
    )
    op.drop_constraint(op.f("ck_task_runs_task_type_allowed"), "task_runs", type_="check")
    op.create_check_constraint(
        op.f("ck_task_runs_task_type_allowed"), "task_runs", _OLD_TASK_TYPE_CHECK
    )
    op.create_check_constraint(
        op.f("ck_task_runs_input_matches_task_type"), "task_runs", _OLD_TASK_INPUT_CHECK
    )
