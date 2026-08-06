"""brief_draft_candidates

Revision ID: 20260730093618
Revises: 20260729161235
Create Date: 2026-07-30 09:36:19.496089
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260730093618"
down_revision: str | None = "20260729161235"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
        "'agent_adopt_brief_candidate', 'agent_patch_apply', 'agent_patch_undo')",
    )
    op.execute(
        """
        CREATE FUNCTION prevent_task_attempt_candidate_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND OLD.candidate_jsonb IS NOT NULL THEN
                RAISE EXCEPTION 'successful task attempt candidates are immutable';
            END IF;
            IF (
                TG_OP = 'UPDATE'
                AND OLD.candidate_jsonb IS NOT NULL
                AND NEW.candidate_jsonb IS DISTINCT FROM OLD.candidate_jsonb
            ) THEN
                RAISE EXCEPTION 'successful task attempt candidates are immutable';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_task_attempt_candidate_immutable
        BEFORE UPDATE OF candidate_jsonb OR DELETE ON task_attempts
        FOR EACH ROW EXECUTE FUNCTION prevent_task_attempt_candidate_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER trg_task_attempt_candidate_immutable ON task_attempts"
    )
    op.execute("DROP FUNCTION prevent_task_attempt_candidate_mutation()")
    op.drop_constraint(
        op.f("ck_draft_operations_type_allowed"),
        "draft_operations",
        type_="check",
    )
    op.execute(
        "ALTER TABLE draft_operations "
        "DISABLE TRIGGER trg_draft_operations_immutable"
    )
    op.execute(
        """
        UPDATE draft_operations
           SET operation_type = 'agent_generate_from_brief'
         WHERE operation_type = 'agent_adopt_brief_candidate'
        """
    )
    op.execute(
        "ALTER TABLE draft_operations "
        "ENABLE TRIGGER trg_draft_operations_immutable"
    )
    op.create_check_constraint(
        op.f("ck_draft_operations_type_allowed"),
        "draft_operations",
        "operation_type IN "
        "('add', 'remove', 'replace', 'agent_generate_from_brief', "
        "'agent_patch_apply', 'agent_patch_undo')",
    )
