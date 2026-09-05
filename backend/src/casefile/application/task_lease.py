"""Shared ownership predicate for writes by a claimed TaskAttempt."""

from datetime import UTC, datetime

from casefile.data_postgres.models import TaskAttempt, TaskRun


def is_current_task_attempt(task: TaskRun, attempt: TaskAttempt) -> bool:
    """Call under the TaskRun row lock; this does not renew or consume controls."""
    return (
        task.status in {"running", "cancelling"}
        and task.leased_by is not None
        and task.lease_expires_at is not None
        and task.lease_expires_at > datetime.now(UTC)
        and attempt.task_run_id == task.id
        and attempt.status == "running"
        and attempt.attempt_no == task.attempt_count
    )
