"""Worker completion executors for non-Chat task families."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile.agent_runtime import (
    CANDIDATE_STRATEGY_VERSION,
    BriefAnchorExtractResult,
    BriefIntakeQuestionsResult,
    BriefIntakeSynthesizeResult,
    BriefPolishResult,
    BriefStrategyOptionsResult,
    CandidateStrategy,
    GenerationResult,
    ReverseParseResult,
)
from casefile.agent_runtime.observability import (
    brief_semantic_coverage,
    standardize_generation_cost_usage,
)
from casefile.application.brief_intake_service import BriefIntakeService
from casefile.application.casefile_v1 import (
    generation_candidate_summary,
    prepare_generation_candidate,
    validate_generation_candidate_context,
)
from casefile.application.reverse_parse_service import ReverseParseService
from casefile.application.workflow_service import (
    append_task_event,
    source_view,
)
from casefile.data_postgres.models import (
    Brief,
    BriefVersion,
    SourceRecord,
    TaskAttempt,
    TaskRun,
)
from casefile.data_postgres.repositories import ProjectRepository
from casefile.worker.support import (
    TaskCancellationRequested,
    _required_string,
    _text_hash,
)


class CompletionExecutorMixin:
    session_factory: sessionmaker[Session]
    config: Any

    def _emit(self, task_run_id: int, event_type: str, stage: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def _complete_polish(
        self,
        task_run_id: int,
        attempt_id: int,
        result: BriefPolishResult,
    ) -> None:
        with self.session_factory() as session, session.begin():
            task, attempt = self._locked_completion_rows(
                session,
                task_run_id,
                attempt_id,
                expected_task_type="brief_polish",
            )
            if task.input_source_record_id is None:
                raise RuntimeError("Polish TaskRun has no input SourceRecord")
            source = session.scalar(
                select(SourceRecord).where(
                    SourceRecord.id == task.input_source_record_id,
                    SourceRecord.project_id == task.project_id,
                )
            )
            if source is None:
                raise RuntimeError("Polish input SourceRecord disappeared")
            frozen_mode = _required_string(task.input_jsonb, "polish_mode")
            if frozen_mode != result.polish_mode:
                raise RuntimeError("Polish result mode does not match its frozen task input")
            polished_text = result.candidate.polished_text
            proposal = SourceRecord(
                project_id=task.project_id,
                source_kind="agent_polish_proposal",
                content_text=polished_text,
                content_hash=_text_hash(polished_text),
                parent_source_record_id=source.id,
                generated_by_task_run_id=task.id,
                created_by_user_id=task.actor_user_id,
            )
            session.add(proposal)
            session.flush()
            result_json = {
                "input_hash": task.input_hash,
                "polish_mode": result.polish_mode,
                **result.candidate.model_dump(mode="json"),
                "proposal_source_record": source_view(proposal),
            }
            self._finish_auxiliary_success(
                session,
                task,
                attempt,
                candidate=result_json,
                usage=result.usage,
                message="Agent 润色候选已生成，原稿未被覆盖",
            )

    def _complete_anchor_extract(
        self,
        task_run_id: int,
        attempt_id: int,
        result: BriefAnchorExtractResult,
    ) -> None:
        with self.session_factory() as session, session.begin():
            task, attempt = self._locked_completion_rows(
                session,
                task_run_id,
                attempt_id,
                expected_task_type="brief_anchor_extract",
            )
            result_json = {
                "input_hash": task.input_hash,
                **result.candidate.model_dump(mode="json"),
            }
            self._finish_auxiliary_success(
                session,
                task,
                attempt,
                candidate=result_json,
                usage=result.usage,
                message="原子拆解候选已生成，等待作者确认",
            )

    def _complete_strategy_options(
        self,
        task_run_id: int,
        attempt_id: int,
        result: BriefStrategyOptionsResult,
    ) -> None:
        with self.session_factory() as session, session.begin():
            task, attempt = self._locked_completion_rows(
                session,
                task_run_id,
                attempt_id,
                expected_task_type="brief_strategy_options",
            )
            result_json = {
                "input_hash": task.input_hash,
                **result.candidate.model_dump(mode="json"),
            }
            self._finish_auxiliary_success(
                session,
                task,
                attempt,
                candidate=result_json,
                usage=result.usage,
                message="三种定制策略已形成，等待作者选择。",
            )

    def _complete_intake_questions(
        self,
        task_run_id: int,
        attempt_id: int,
        result: BriefIntakeQuestionsResult,
    ) -> None:
        payload = result.candidate.model_dump(mode="json")
        with self.session_factory() as session:
            BriefIntakeService(session).complete_questions_task(
                task_run_id,
                attempt_id,
                questions=list(payload["questions"]),
                usage=result.usage,
            )

    def _complete_intake_synthesize(
        self,
        task_run_id: int,
        attempt_id: int,
        result: BriefIntakeSynthesizeResult,
    ) -> None:
        with self.session_factory() as session:
            BriefIntakeService(session).complete_synthesize_task(
                task_run_id,
                attempt_id,
                content=result.candidate.model_dump(mode="json"),
                usage=result.usage,
            )

    def _complete_reverse_parse(
        self,
        task_run_id: int,
        attempt_id: int,
        result: ReverseParseResult,
    ) -> None:
        payload = result.candidate.model_dump(mode="json")
        with self.session_factory() as session:
            ReverseParseService(session).complete_parse_task(
                task_run_id,
                attempt_id,
                items=list(payload["items"]),
                usage=result.usage,
            )

    def _locked_completion_rows(
        self,
        session: Session,
        task_run_id: int,
        attempt_id: int,
        *,
        expected_task_type: str,
    ) -> tuple[TaskRun, TaskAttempt]:
        task = session.scalar(select(TaskRun).where(TaskRun.id == task_run_id).with_for_update())
        attempt = session.scalar(
            select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
        )
        if task is None or attempt is None:
            raise RuntimeError("TaskRun or TaskAttempt disappeared")
        if task.task_type != expected_task_type:
            raise RuntimeError("TaskRun dispatch type changed")
        if task.status == "cancelling" and task.leased_by == self.config.worker_id:
            raise TaskCancellationRequested
        if task.leased_by != self.config.worker_id or task.status != "running":
            raise RuntimeError("TaskRun lease was lost before the final write")
        return task, attempt

    def _finish_auxiliary_success(
        self,
        session: Session,
        task: TaskRun,
        attempt: TaskAttempt,
        *,
        candidate: dict[str, Any],
        usage: dict[str, Any],
        message: str,
    ) -> None:
        now = datetime.now(UTC)
        attempt.status = "succeeded"
        attempt.candidate_jsonb = candidate
        attempt.validation_errors_jsonb = []
        attempt.usage_jsonb = usage
        attempt.finished_at = now
        task.status = "succeeded"
        task.stage = "completed"
        task.usage_jsonb = usage
        task.result_jsonb = candidate
        task.completed_at = now
        task.leased_by = None
        task.lease_expires_at = None
        append_task_event(
            session,
            task,
            "task.succeeded",
            "completed",
            {
                "message": message,
                "task_type": task.task_type,
                "input_hash": task.input_hash,
                "usage": usage,
            },
        )

    def _complete_generation_candidate(
        self,
        task_run_id: int,
        attempt_id: int,
        candidate: dict[str, Any],
        result: GenerationResult,
        validation_errors: list[dict[str, Any]],
    ) -> None:
        self._emit(
            task_run_id,
            "validation.started",
            "validating",
            {"layers": ["schema", "id", "refs", "db_mapping", "revision"]},
        )
        with self.session_factory() as session, session.begin():
            task, attempt = self._locked_completion_rows(
                session,
                task_run_id,
                attempt_id,
                expected_task_type="brief_to_draft",
            )
            owned = ProjectRepository(session).get_owned(
                task.actor_user_id, task.project_id, lock=True
            )
            brief_version = session.get(BriefVersion, task.brief_version_id)
            if owned is None or brief_version is None:
                raise RuntimeError("TaskRun aggregate disappeared")
            brief = session.get(Brief, brief_version.brief_id)
            if brief is None:
                raise RuntimeError("Brief disappeared")
            candidate = prepare_generation_candidate(owned, candidate)
            validate_generation_candidate_context(
                owned,
                candidate,
                brief=brief,
                brief_version=brief_version,
            )
            summary = generation_candidate_summary(candidate)
            raw_strategy = task.input_jsonb.get(
                "candidate_strategy",
                CandidateStrategy.BALANCED.value,
            )
            try:
                candidate_strategy = CandidateStrategy(raw_strategy)
            except ValueError as error:
                raise RuntimeError("Frozen candidate strategy is invalid") from error
            semantic_coverage = brief_semantic_coverage(
                brief_version.content_jsonb,
                candidate,
            )
            cost_usage = standardize_generation_cost_usage(
                result.usage,
                provider=task.provider,
                model_id=task.model_id,
            )
            summary.update(
                {
                    "candidate_strategy": candidate_strategy.value,
                    "candidate_strategy_version": task.input_jsonb.get(
                        "candidate_strategy_version",
                        CANDIDATE_STRATEGY_VERSION,
                    ),
                    "semantic_coverage": semantic_coverage,
                    "cost_usage": cost_usage,
                }
            )
            now = datetime.now(UTC)
            attempt.status = "succeeded"
            attempt.candidate_jsonb = candidate
            attempt.validation_errors_jsonb = validation_errors
            attempt.usage_jsonb = {**result.usage, "tools": result.tools.as_dict()}
            attempt.finished_at = now
            task.status = "succeeded"
            task.stage = "completed"
            task.usage_jsonb = attempt.usage_jsonb
            task.result_snapshot_id = None
            task.result_jsonb = summary
            task.completed_at = now
            task.leased_by = None
            task.lease_expires_at = None
            append_task_event(
                session,
                task,
                "validation.completed",
                "validating",
                {"valid": True, "content_hash": summary["content_hash"]},
            )
            append_task_event(
                session,
                task,
                "task.succeeded",
                "completed",
                {
                    "message": "候选草稿已生成，等待作者采用",
                    "content_hash": summary["content_hash"],
                    "usage": task.usage_jsonb,
                    "semantic_coverage": semantic_coverage,
                    "cost_usage": cost_usage,
                },
            )


__all__ = ["CompletionExecutorMixin"]
