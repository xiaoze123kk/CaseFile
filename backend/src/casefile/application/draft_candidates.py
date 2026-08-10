"""Query and explicitly adopt immutable Brief-to-Draft candidates."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
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
from casefile.application.task_events import append_task_event
from casefile.data_postgres.models import (
    Brief,
    BriefVersion,
    Draft,
    DraftSnapshot,
    TaskAttempt,
    TaskRun,
)
from casefile.data_postgres.repositories import OwnedDraft, ProjectRepository


class DraftCandidateService:
    """Transactional boundary for candidate history and explicit Draft creation."""

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
                        current_draft_id=owned.casefile.current_draft_id,
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
            view = self._candidate_view(
                task,
                attempt=attempt,
                current_brief_version_id=brief.current_version_id,
                current_draft_id=owned.casefile.current_draft_id,
            )
            # The successful attempt is the immutable source of truth for preview.
            # Returning it here must never project it into the mutable Draft; adoption
            # remains an explicit POST guarded by the server Current Draft pointer.
            return {
                **view,
                "preview": True,
                "read_only": True,
                "content": attempt.candidate_jsonb,
            }

    def adopt_candidate(
        self,
        actor_user_id: int,
        project_id: int,
        task_run_id: int,
        *,
        expected_current_draft_id: int,
    ) -> dict[str, Any]:
        with self.session.begin():
            current = self._owned(actor_user_id, project_id, lock=True)
            if current.casefile.current_draft_id != expected_current_draft_id:
                raise ApplicationError(
                    "current_draft_changed",
                    "当前工作稿已在其他位置切换，请刷新后重试。",
                    status_code=409,
                    details={"current_draft_id": current.casefile.current_draft_id},
                )
            task = self.session.scalar(
                select(TaskRun)
                .where(
                    TaskRun.id == task_run_id,
                    TaskRun.project_id == current.project.id,
                )
                .with_for_update()
            )
            if task is None or task.task_type != "brief_to_draft":
                raise not_found("DraftCandidate")
            if task.status != "succeeded":
                raise ApplicationError(
                    "candidate_not_ready",
                    "只有生成成功的候选才能被采用。",
                    status_code=409,
                )
            if task.result_snapshot_id is not None:
                raise ApplicationError(
                    "candidate_already_adopted",
                    "该候选已经被采用。",
                    status_code=409,
                )
            attempt = self._successful_candidate_attempt(task)
            if attempt is None or not isinstance(attempt.candidate_jsonb, dict):
                raise ApplicationError(
                    "candidate_not_ready",
                    "生成成功的任务没有可用的已校验候选。",
                    status_code=409,
                )
            source = self.projects.get_owned_draft(
                actor_user_id,
                project_id,
                task.draft_id,
                lock=True,
            )
            if source is None:
                raise not_found("Draft")
            brief = self._brief(current, lock=True)
            if task.brief_version_id is None:
                raise ApplicationError(
                    "candidate_brief_missing",
                    "生成候选没有对应的冻结创作简报版本。",
                    status_code=409,
                )
            if brief.current_version_id != task.brief_version_id:
                raise ApplicationError(
                    "candidate_brief_stale",
                    "该候选属于较早的创作简报版本，已失效。",
                    status_code=409,
                    details={"current_version_id": brief.current_version_id},
                )
            brief_version = self.session.scalar(
                select(BriefVersion).where(
                    BriefVersion.id == task.brief_version_id,
                    BriefVersion.project_id == current.project.id,
                    BriefVersion.brief_id == brief.id,
                )
            )
            if brief_version is None:
                raise not_found("BriefVersion")
            if source.draft.revision != task.input_draft_revision:
                raise ApplicationError(
                    "candidate_source_draft_changed",
                    "生成该候选后，来源工作稿已更新，请重新生成候选。",
                    status_code=409,
                    details={
                        "source_draft_id": source.draft.id,
                        "source_revision": source.draft.revision,
                        "task_revision": task.input_draft_revision,
                    },
                )
            snapshot = project_generation_candidate(
                self.session,
                source,
                current,
                candidate=attempt.candidate_jsonb,
                brief=brief,
                brief_version=brief_version,
                task_run_id=task.id,
                actor_user_id=actor_user_id,
                expected_current_draft_id=expected_current_draft_id,
            )
            task.result_snapshot_id = snapshot.id
            append_task_event(
                self.session,
                task,
                "candidate.adopted",
                "completed",
                {
                    "message": "候选草稿已采用为当前工作稿",
                    "content_hash": snapshot.content_hash,
                    "draft_id": snapshot.draft_id,
                },
            )
            self.session.flush()
            return {
                "task_run_id": task.id,
                "draft_id": snapshot.draft_id,
                "revision": snapshot.snapshot_revision,
                "title": attempt.candidate_jsonb["title"],
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

    def _candidate_view(
        self,
        task: TaskRun,
        *,
        attempt: TaskAttempt,
        current_brief_version_id: int | None,
        current_draft_id: int,
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
        result_draft_id = None
        if task.result_snapshot_id is not None:
            snapshot = self.session.get(DraftSnapshot, task.result_snapshot_id)
            result_draft_id = snapshot.draft_id if snapshot is not None else None
        source_draft = self.session.get(Draft, task.draft_id)
        source_is_eligible = bool(
            source_draft is not None
            and source_draft.status == "active"
            and source_draft.revision == task.input_draft_revision
        )
        return {
            "task_run_id": task.id,
            "brief_version_no": brief_version.version_no,
            "is_current_brief": task.brief_version_id == current_brief_version_id,
            "is_current": result_draft_id == current_draft_id,
            "is_adopted": task.result_snapshot_id is not None,
            "can_adopt": (
                task.brief_version_id == current_brief_version_id
                and task.result_snapshot_id is None
                and source_is_eligible
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


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


__all__ = ["DraftCandidateService"]
