"""Read-only A-path funnel and post-adoption metrics derived from durable facts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.application.errors import not_found
from casefile.data_postgres.models import (
    AgentModelCall,
    DraftOperation,
    TaskAttempt,
    TaskEvent,
    TaskRun,
)
from casefile.data_postgres.repositories import ProjectRepository

A_PATH_METRICS_VERSION = "a-path-funnel-v1"
_POST_ADOPTION_EDIT_TYPES = frozenset(
    {
        "add",
        "remove",
        "replace",
        "agent_patch_apply",
        "agent_patch_undo",
        "logical_mutation_apply",
        "logical_mutation_undo",
        "logical_mutation_redo",
        "logical_mutation_normalize",
    }
)
_USAGE_KEYS = (
    "requests",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
)


@dataclass(frozen=True, slots=True)
class APathTaskFact:
    task_run_id: int
    status: str
    result_snapshot_id: int | None
    usage: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class APathEventFact:
    task_run_id: int
    event_type: str


@dataclass(frozen=True, slots=True)
class APathAttemptFact:
    attempt_id: int
    task_run_id: int
    attempt_no: int
    usage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class APathModelCallFact:
    task_run_id: int
    task_attempt_id: int
    agent_step_run_id: int
    call_no: int
    usage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class APathOperationFact:
    draft_id: int
    sequence_no: int
    operation_type: str
    new_value: Any


class APathMetricsService:
    """Query existing append-only facts without creating analytics persistence."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

    def project_metrics(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id)
            if owned is None:
                raise not_found("Project")
            task_rows = list(
                self.session.scalars(
                    select(TaskRun)
                    .where(
                        TaskRun.project_id == owned.project.id,
                        TaskRun.task_type == "brief_to_draft",
                    )
                    .order_by(TaskRun.id)
                )
            )
            task_ids = [task.id for task in task_rows]
            attempt_rows = (
                list(
                    self.session.scalars(
                        select(TaskAttempt)
                        .where(TaskAttempt.task_run_id.in_(task_ids))
                        .order_by(TaskAttempt.task_run_id, TaskAttempt.attempt_no)
                    )
                )
                if task_ids
                else []
            )
            attempt_ids = [attempt.id for attempt in attempt_rows]
            model_call_rows = (
                list(
                    self.session.scalars(
                        select(AgentModelCall)
                        .where(
                            AgentModelCall.task_run_id.in_(task_ids),
                            AgentModelCall.task_attempt_id.in_(attempt_ids),
                        )
                        .order_by(
                            AgentModelCall.task_attempt_id,
                            AgentModelCall.agent_step_run_id,
                            AgentModelCall.call_no,
                        )
                    )
                )
                if attempt_ids
                else []
            )
            event_rows = (
                list(
                    self.session.scalars(
                        select(TaskEvent)
                        .where(TaskEvent.task_run_id.in_(task_ids))
                        .order_by(TaskEvent.task_run_id, TaskEvent.sequence_no)
                    )
                )
                if task_ids
                else []
            )
            operation_rows = list(
                self.session.scalars(
                    select(DraftOperation)
                    .where(DraftOperation.project_id == owned.project.id)
                    .order_by(DraftOperation.id)
                )
            )
            return derive_a_path_metrics(
                tasks=[
                    APathTaskFact(
                        task_run_id=task.id,
                        status=task.status,
                        result_snapshot_id=task.result_snapshot_id,
                        usage=dict(task.usage_jsonb),
                        created_at=task.created_at,
                        completed_at=task.completed_at,
                    )
                    for task in task_rows
                ],
                events=[
                    APathEventFact(
                        task_run_id=event.task_run_id,
                        event_type=event.event_type,
                    )
                    for event in event_rows
                ],
                attempts=[
                    APathAttemptFact(
                        attempt_id=attempt.id,
                        task_run_id=attempt.task_run_id,
                        attempt_no=attempt.attempt_no,
                        usage=dict(attempt.usage_jsonb),
                    )
                    for attempt in attempt_rows
                ],
                model_calls=[
                    APathModelCallFact(
                        task_run_id=model_call.task_run_id,
                        task_attempt_id=model_call.task_attempt_id,
                        agent_step_run_id=model_call.agent_step_run_id,
                        call_no=model_call.call_no,
                        usage=dict(model_call.usage_jsonb),
                    )
                    for model_call in model_call_rows
                ],
                operations=[
                    APathOperationFact(
                        draft_id=operation.draft_id,
                        # Draft sequence numbers restart for every working Draft;
                        # the project funnel needs the append-only global order.
                        sequence_no=operation.id,
                        operation_type=operation.operation_type,
                        new_value=operation.new_value_jsonb,
                    )
                    for operation in operation_rows
                ],
            )


def derive_a_path_metrics(
    *,
    tasks: list[APathTaskFact],
    events: list[APathEventFact],
    attempts: list[APathAttemptFact] | None = None,
    model_calls: list[APathModelCallFact] | None = None,
    operations: list[APathOperationFact],
) -> dict[str, Any]:
    """Derive a stable funnel snapshot from TaskRun/Event/DraftOperation facts."""

    task_ids = {task.task_run_id for task in tasks}
    statuses = Counter(task.status for task in tasks)
    event_counts = Counter(event.event_type for event in events if event.task_run_id in task_ids)
    succeeded_task_ids = {task.task_run_id for task in tasks if task.status == "succeeded"}
    adopted_task_ids = {task.task_run_id for task in tasks if task.result_snapshot_id is not None}
    adopted_task_ids.update(
        event.task_run_id
        for event in events
        if event.task_run_id in task_ids and event.event_type == "candidate.adopted"
    )

    ordered_operations = sorted(operations, key=lambda operation: operation.sequence_no)
    adoption_operation_count = 0
    latest_adoption_by_draft: dict[int, int] = {}
    adoption_has_edits: list[bool] = []
    post_adoption_edits: list[APathOperationFact] = []
    for operation in ordered_operations:
        if operation.operation_type == "agent_adopt_brief_candidate":
            latest_adoption_by_draft[operation.draft_id] = adoption_operation_count
            adoption_has_edits.append(False)
            adoption_operation_count += 1
            task_run_id = _operation_task_run_id(operation)
            if task_run_id in task_ids:
                adopted_task_ids.add(task_run_id)
            continue
        if operation.operation_type not in _POST_ADOPTION_EDIT_TYPES:
            continue
        adoption_position = latest_adoption_by_draft.get(operation.draft_id)
        if adoption_position is None:
            continue
        adoption_has_edits[adoption_position] = True
        post_adoption_edits.append(operation)

    edited_adoptions = sum(adoption_has_edits)

    attempt_facts = [attempt for attempt in attempts or [] if attempt.task_run_id in task_ids]
    attempt_by_id = {attempt.attempt_id: attempt for attempt in attempt_facts}
    attempts_by_task: dict[int, list[APathAttemptFact]] = {}
    for attempt in attempt_facts:
        attempts_by_task.setdefault(attempt.task_run_id, []).append(attempt)

    model_call_facts: list[APathModelCallFact] = []
    model_calls_by_attempt: dict[int, dict[int, list[APathModelCallFact]]] = {}
    for model_call in model_calls or []:
        owning_attempt = attempt_by_id.get(model_call.task_attempt_id)
        if owning_attempt is None or owning_attempt.task_run_id != model_call.task_run_id:
            continue
        model_call_facts.append(model_call)
        model_calls_by_attempt.setdefault(model_call.task_attempt_id, {}).setdefault(
            model_call.agent_step_run_id,
            [],
        ).append(model_call)

    usage_sources: list[dict[str, Any]] = []
    model_call_attempts = 0
    model_call_usage_snapshots = 0
    task_attempt_fallbacks = 0
    task_run_fallbacks = 0
    for task in tasks:
        task_attempts = attempts_by_task.get(task.task_run_id, [])
        if not task_attempts:
            usage_sources.append(task.usage)
            task_run_fallbacks += 1
            continue
        for attempt in task_attempts:
            calls_by_step = model_calls_by_attempt.get(attempt.attempt_id)
            if not calls_by_step:
                usage_sources.append(attempt.usage)
                task_attempt_fallbacks += 1
                continue
            model_call_attempts += 1
            for step_calls in calls_by_step.values():
                # A successful structured-output call stores the cumulative usage for
                # that component execution. Earlier retry rows can therefore overlap;
                # consume only the latest row that carries a usage snapshot.
                rows_with_usage = [
                    row for row in step_calls if any(key in row.usage for key in _USAGE_KEYS)
                ]
                source = max(rows_with_usage or step_calls, key=lambda row: row.call_no)
                usage_sources.append(source.usage)
                model_call_usage_snapshots += 1
    usage_totals = {key: 0 for key in _USAGE_KEYS}
    for usage in usage_sources:
        for key in _USAGE_KEYS:
            usage_totals[key] += _non_negative_int(usage.get(key))

    completion_durations = [
        max((task.completed_at - task.created_at).total_seconds() * 1000, 0.0)
        for task in tasks
        if task.completed_at is not None
    ]
    generated_count = len(succeeded_task_ids)
    adopted_count = len(adopted_task_ids & succeeded_task_ids)
    return {
        "version": A_PATH_METRICS_VERSION,
        "funnel": {
            "task_runs": len(tasks),
            "generated_candidates": generated_count,
            "adopted_candidates": adopted_count,
            "post_adoption_edited_candidates": edited_adoptions,
            "generation_success_rate": _rate(generated_count, len(tasks)),
            "adoption_rate": _rate(adopted_count, generated_count),
            "post_adoption_edit_rate": _rate(
                edited_adoptions,
                adoption_operation_count,
            ),
        },
        "task_statuses": dict(sorted(statuses.items())),
        "durable_events": dict(sorted(event_counts.items())),
        "post_adoption": {
            "adoption_operations": adoption_operation_count,
            "edit_operations": len(post_adoption_edits),
            "edited_adoptions": edited_adoptions,
            "operation_types": dict(
                sorted(Counter(item.operation_type for item in post_adoption_edits).items())
            ),
        },
        "usage_totals": usage_totals,
        "usage_observations": {
            "task_attempts": len(attempt_facts),
            "model_calls": len(model_call_facts),
            "model_call_attempts": model_call_attempts,
            "model_call_usage_snapshots": model_call_usage_snapshots,
            "task_attempt_fallbacks": task_attempt_fallbacks,
            "task_run_fallbacks": task_run_fallbacks,
        },
        "completion_latency_ms": {
            "observed_tasks": len(completion_durations),
            "average": (
                round(sum(completion_durations) / len(completion_durations), 3)
                if completion_durations
                else None
            ),
            "maximum": round(max(completion_durations), 3) if completion_durations else None,
        },
        "unobservable_stages": [
            {
                "stage": "candidate_previewed",
                "reason": "candidate preview is a read-only GET and has no durable event",
            }
        ],
    }


def _operation_task_run_id(operation: APathOperationFact) -> int | None:
    if not isinstance(operation.new_value, dict):
        return None
    value = operation.new_value.get("task_run_id")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _rate(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 4)


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(int(value), 0)


__all__ = [
    "A_PATH_METRICS_VERSION",
    "APathAttemptFact",
    "APathEventFact",
    "APathMetricsService",
    "APathModelCallFact",
    "APathOperationFact",
    "APathTaskFact",
    "derive_a_path_metrics",
]
