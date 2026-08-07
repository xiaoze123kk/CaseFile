"""Query and explicitly adopt immutable Brief-to-Draft candidates."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.agent_runtime.models import (
    CANDIDATE_STRATEGY_LABELS,
    CANDIDATE_STRATEGY_VERSION,
    CandidateStrategy,
)
from casefile.application.casefile_v1 import (
    adopt_generation_candidate as project_generation_candidate,
)
from casefile.application.casefile_v1 import generation_candidate_summary
from casefile.application.errors import ApplicationError, not_found
from casefile.data_postgres.models import (
    Brief,
    BriefVersion,
    DraftOperation,
    TaskAttempt,
    TaskEvent,
    TaskRun,
)
from casefile.data_postgres.repositories import OwnedDraft, ProjectRepository


class DraftCandidateService:
    """Transactional boundary for candidate history and explicit Draft replacement."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

    def list_candidates(
        self,
        actor_user_id: int,
        project_id: int,
    ) -> list[dict[str, Any]]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            brief = self._brief(owned)
            current_task_run_id = self._current_generation_task_run_id(owned)
            tasks = list(
                self.session.scalars(
                    select(TaskRun)
                    .where(
                        TaskRun.project_id == owned.project.id,
                        TaskRun.task_type == "brief_to_draft",
                        TaskRun.status == "succeeded",
                    )
                    .order_by(TaskRun.completed_at.desc(), TaskRun.id.desc())
                )
            )
            candidates: list[dict[str, Any]] = []
            for task in tasks:
                attempt = self._successful_candidate_attempt(task)
                if attempt is None:
                    continue
                candidates.append(
                    self._candidate_view(
                        task,
                        attempt=attempt,
                        current_brief_version_id=brief.current_version_id,
                        current_task_run_id=current_task_run_id,
                    )
                )
            return candidates

    def get_candidate(
        self,
        actor_user_id: int,
        project_id: int,
        task_run_id: int,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id)
            brief = self._brief(owned)
            task = self.session.scalar(
                select(TaskRun).where(
                    TaskRun.id == task_run_id,
                    TaskRun.project_id == owned.project.id,
                )
            )
            if task is None:
                raise not_found("DraftCandidate")
            attempt = self._successful_candidate_attempt(task)
            if attempt is None:
                raise not_found("DraftCandidate")
            return self._candidate_view(
                task,
                attempt=attempt,
                current_brief_version_id=brief.current_version_id,
                current_task_run_id=self._current_generation_task_run_id(owned),
            )

    def adopt_candidate(
        self,
        actor_user_id: int,
        project_id: int,
        task_run_id: int,
        *,
        expected_draft_revision: int,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            task = self.session.scalar(
                select(TaskRun)
                .where(
                    TaskRun.id == task_run_id,
                    TaskRun.project_id == owned.project.id,
                )
                .with_for_update()
            )
            if task is None or task.task_type != "brief_to_draft":
                raise not_found("DraftCandidate")
            if task.status != "succeeded":
                raise ApplicationError(
                    "candidate_not_ready",
                    "Only a successful generation candidate can be adopted",
                    status_code=409,
                )
            if task.result_snapshot_id is not None:
                raise ApplicationError(
                    "candidate_already_adopted",
                    "This candidate has already been adopted",
                    status_code=409,
                )
            attempt = self._successful_candidate_attempt(task)
            if attempt is None or not isinstance(attempt.candidate_jsonb, dict):
                raise ApplicationError(
                    "candidate_not_ready",
                    "The successful generation task has no validated candidate",
                    status_code=409,
                )
            brief = self._brief(owned, lock=True)
            if task.brief_version_id is None:
                raise ApplicationError(
                    "candidate_brief_missing",
                    "The generation candidate has no frozen Brief version",
                    status_code=409,
                )
            if brief.current_version_id != task.brief_version_id:
                raise ApplicationError(
                    "candidate_brief_stale",
                    "The candidate belongs to an older Brief version",
                    status_code=409,
                    details={"current_version_id": brief.current_version_id},
                )
            brief_version = self.session.scalar(
                select(BriefVersion).where(
                    BriefVersion.id == task.brief_version_id,
                    BriefVersion.project_id == owned.project.id,
                    BriefVersion.brief_id == brief.id,
                )
            )
            if brief_version is None:
                raise not_found("BriefVersion")
            snapshot = project_generation_candidate(
                self.session,
                owned,
                candidate=attempt.candidate_jsonb,
                brief=brief,
                brief_version=brief_version,
                task_run_id=task.id,
                actor_user_id=actor_user_id,
                expected_draft_revision=expected_draft_revision,
            )
            task.result_snapshot_id = snapshot.id
            self._append_event(
                task,
                "candidate.adopted",
                "completed",
                {
                    "message": "候选草稿已采用为当前工作稿",
                    "content_hash": snapshot.content_hash,
                },
            )
            self.session.flush()
            return {
                "task_run_id": task.id,
                "title": owned.casefile.title,
                "content_hash": snapshot.content_hash,
                "adopted": True,
            }

    def _owned(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        lock: bool = False,
    ) -> OwnedDraft:
        owned = self.projects.get_owned(actor_user_id, project_id, lock=lock)
        if owned is None:
            raise not_found("Project")
        return owned

    def _brief(self, owned: OwnedDraft, *, lock: bool = False) -> Brief:
        statement = select(Brief).where(Brief.project_id == owned.project.id)
        if lock:
            statement = statement.with_for_update()
        brief = self.session.scalar(statement)
        if brief is None:
            raise not_found("Brief")
        return brief

    def _successful_candidate_attempt(self, task: TaskRun) -> TaskAttempt | None:
        if task.task_type != "brief_to_draft" or task.status != "succeeded":
            return None
        return self.session.scalar(
            select(TaskAttempt)
            .where(
                TaskAttempt.task_run_id == task.id,
                TaskAttempt.status == "succeeded",
                TaskAttempt.candidate_jsonb.is_not(None),
            )
            .order_by(TaskAttempt.attempt_no.desc())
            .limit(1)
        )

    def _current_generation_task_run_id(self, owned: OwnedDraft) -> int | None:
        operations = self.session.scalars(
            select(DraftOperation)
            .where(
                DraftOperation.draft_id == owned.draft.id,
                DraftOperation.operation_type.in_(
                    (
                        "agent_generate_from_brief",
                        "agent_adopt_brief_candidate",
                    )
                ),
            )
            .order_by(DraftOperation.sequence_no.desc())
        )
        for operation in operations:
            payload = operation.new_value_jsonb
            if not isinstance(payload, dict):
                continue
            task_run_id = payload.get("task_run_id")
            if isinstance(task_run_id, int) and not isinstance(task_run_id, bool):
                return task_run_id
        return None

    def _candidate_view(
        self,
        task: TaskRun,
        *,
        attempt: TaskAttempt,
        current_brief_version_id: int | None,
        current_task_run_id: int | None,
    ) -> dict[str, Any]:
        if (
            task.task_type != "brief_to_draft"
            or task.status != "succeeded"
            or task.brief_version_id is None
            or not isinstance(attempt.candidate_jsonb, dict)
        ):
            raise not_found("DraftCandidate")
        brief_version = self.session.scalar(
            select(BriefVersion).where(
                BriefVersion.id == task.brief_version_id,
                BriefVersion.project_id == task.project_id,
            )
        )
        if brief_version is None:
            raise not_found("BriefVersion")
        summary = generation_candidate_summary(attempt.candidate_jsonb)
        raw_strategy = task.input_jsonb.get(
            "candidate_strategy",
            CandidateStrategy.BALANCED.value,
        )
        try:
            candidate_strategy = CandidateStrategy(raw_strategy)
        except ValueError:
            candidate_strategy = CandidateStrategy.BALANCED
        candidate_strategy_version = task.input_jsonb.get(
            "candidate_strategy_version",
            CANDIDATE_STRATEGY_VERSION,
        )
        return {
            "task_run_id": task.id,
            "brief_version_no": brief_version.version_no,
            "is_current_brief": task.brief_version_id == current_brief_version_id,
            "is_current": task.id == current_task_run_id,
            "is_adopted": task.result_snapshot_id is not None,
            "can_adopt": (
                task.brief_version_id == current_brief_version_id
                and task.result_snapshot_id is None
            ),
            "provider": task.provider,
            "model_id": task.model_id,
            "title": summary["title"],
            "content_hash": summary["content_hash"],
            "object_counts": summary["object_counts"],
            "reasoning_questions": summary["reasoning_questions"],
            "constraint_statements": summary["constraint_statements"],
            "candidate_strategy": candidate_strategy.value,
            "candidate_strategy_version": candidate_strategy_version,
            "candidate_strategy_label": CANDIDATE_STRATEGY_LABELS[candidate_strategy],
            "attempt_count": task.attempt_count,
            "created_at": _time(task.created_at),
            "completed_at": _time(task.completed_at),
        }

    def _append_event(
        self,
        task: TaskRun,
        event_type: str,
        stage: str,
        payload: dict[str, Any],
    ) -> TaskEvent:
        sequence = int(
            self.session.scalar(
                select(func.coalesce(func.max(TaskEvent.sequence_no), 0) + 1).where(
                    TaskEvent.task_run_id == task.id
                )
            )
            or 1
        )
        event = TaskEvent(
            project_id=task.project_id,
            task_run_id=task.id,
            sequence_no=sequence,
            event_type=event_type,
            stage=stage,
            payload_jsonb=payload,
        )
        self.session.add(event)
        self.session.flush()
        return event


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


__all__ = ["DraftCandidateService"]
