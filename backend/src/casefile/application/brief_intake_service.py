"""Transactional Brief Intake aggregate before the formal Brief review workflow."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, cast

import rfc8785
from casefile_contracts import (
    Brief as BriefContract,
)
from casefile_contracts import (
    BriefIntakeCandidate as BriefIntakeCandidateContract,
)
from casefile_contracts import (
    BriefIntakeQuestionSet as BriefIntakeQuestionSetContract,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.agent_runtime.prompt import AGENT_VERSION
from casefile.agent_runtime.prompt_repository import prompt_version_for_task
from casefile.agent_runtime.tools import TOOLSET_VERSION
from casefile.application.errors import ApplicationError, not_found
from casefile.application.workflow_service import (
    append_task_event,
    source_view,
    task_view,
)
from casefile.contracts import CASEFILE_SCHEMA_VERSION
from casefile.data_postgres.models import (
    Brief,
    BriefIntake,
    BriefIntakeCandidate,
    BriefIntakeQuestion,
    SourceRecord,
    TaskAttempt,
    TaskRun,
    UserProviderSetting,
)
from casefile.data_postgres.repositories import OwnedDraft, ProjectRepository

SUPPORTED_PROVIDERS = frozenset({"deepseek", "openai"})
ACTIVE_TASK_STATUSES = ("queued", "running", "cancelling")
RESOLVED_QUESTION_STATUSES = frozenset({"user_answered", "suggestion_accepted"})


class BriefIntakeService:
    """Own all Brief Intake state transitions and optimistic concurrency checks."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

    def get(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            intake = self._ensure_intake(owned)
            return self._view(owned, intake)

    def update_source(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        expected_intake_revision: int,
        content_text: str,
        parent_source_record_id: int | None,
    ) -> dict[str, Any]:
        normalized = content_text.strip()
        if not normalized:
            raise ApplicationError(
                "brief_intake_source_blank",
                "The original idea must not be blank",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            intake = self._ensure_intake(owned, lock=True)
            self._expected_revision(intake, expected_intake_revision)
            self._require_editable_intake(intake)
            current_source = self._current_source(intake)
            if current_source is not None and current_source.content_text == normalized:
                return self._view(owned, intake)

            if current_source is None:
                if parent_source_record_id is not None:
                    raise ApplicationError(
                        "brief_intake_source_parent_forbidden",
                        "The first source cannot reference a parent",
                        status_code=422,
                    )
                source_kind = "human_original"
                parent_id = None
            else:
                source_kind = "human_revision"
                parent_id = parent_source_record_id or current_source.id
                parent = self.session.scalar(
                    select(SourceRecord).where(
                        SourceRecord.id == parent_id,
                        SourceRecord.project_id == owned.project.id,
                    )
                )
                if parent is None:
                    raise not_found("SourceRecord parent")

            source = SourceRecord(
                project_id=owned.project.id,
                source_kind=source_kind,
                content_text=normalized,
                content_hash=_text_hash(normalized),
                parent_source_record_id=parent_id,
                generated_by_task_run_id=None,
                created_by_user_id=actor_user_id,
            )
            self.session.add(source)
            self.session.flush()
            intake.current_source_record_id = source.id
            intake.current_questions_task_run_id = None
            intake.current_candidate_id = None
            intake.adopted_candidate_id = None
            intake.stage = "idea"
            intake.revision += 1
            self.session.flush()
            return self._view(owned, intake)

    def answer_question(
        self,
        actor_user_id: int,
        project_id: int,
        question_key: str,
        *,
        expected_intake_revision: int,
        answer_mode: str,
        answer_text: str | None,
        suggestion_index: int | None,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            intake = self._ensure_intake(owned, lock=True)
            self._expected_revision(intake, expected_intake_revision)
            self._require_editable_intake(intake)
            question = self._current_question(intake, question_key, lock=True)

            if answer_mode == "answer":
                normalized = "" if answer_text is None else answer_text.strip()
                if not normalized:
                    raise ApplicationError(
                        "brief_intake_answer_blank",
                        "A free-form answer must not be blank",
                        status_code=422,
                    )
                question.answer_status = "user_answered"
                question.answer_text = normalized
                question.answer_source = "user_confirmed"
            elif answer_mode == "suggestion":
                suggestions = [str(value) for value in question.suggestions_jsonb]
                if (
                    suggestion_index is None
                    or suggestion_index < 0
                    or suggestion_index >= len(suggestions)
                ):
                    raise ApplicationError(
                        "brief_intake_suggestion_invalid",
                        "The selected Agent suggestion does not exist",
                        status_code=422,
                    )
                question.answer_status = "suggestion_accepted"
                question.answer_text = suggestions[suggestion_index]
                question.answer_source = "agent_suggestion"
            elif answer_mode == "pending":
                if question.is_required:
                    raise ApplicationError(
                        "brief_intake_required_question_pending",
                        "A required direction question cannot be left pending",
                        status_code=422,
                        details={"question_key": question.question_key},
                    )
                question.answer_status = "pending"
                question.answer_text = None
                question.answer_source = "unresolved"
            else:
                raise ApplicationError(
                    "brief_intake_answer_mode_invalid",
                    "Unsupported Brief Intake answer mode",
                    status_code=422,
                )

            intake.revision += 1
            self.session.flush()
            return self._view(owned, intake)

    def create_manual_candidate(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        expected_intake_revision: int,
        content: dict[str, Any],
        parent_candidate_id: int | None,
        activate: bool,
    ) -> dict[str, Any]:
        normalized = validate_candidate_content(content)
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            intake = self._ensure_intake(owned, lock=True)
            self._expected_revision(intake, expected_intake_revision)
            self._require_editable_intake(intake)
            self._require_source(intake)
            self._require_hard_questions_resolved(intake)
            parent = None
            if parent_candidate_id is not None:
                parent = self._candidate(intake, parent_candidate_id)
            candidate = BriefIntakeCandidate(
                project_id=owned.project.id,
                intake_id=intake.id,
                parent_candidate_id=None if parent is None else parent.id,
                generated_by_task_run_id=None,
                created_by_user_id=actor_user_id,
                origin="manual_edit",
                basis_input_hash=self._basis_hash(intake),
                content_jsonb=normalized,
                content_hash=_json_hash(normalized),
                saved_at=None,
                saved_by_user_id=None,
            )
            self.session.add(candidate)
            self.session.flush()
            if activate:
                intake.current_candidate_id = candidate.id
                intake.stage = "confirmation"
            intake.revision += 1
            self.session.flush()
            return self._view(owned, intake)

    def save_candidate(
        self,
        actor_user_id: int,
        project_id: int,
        candidate_id: int,
        *,
        expected_intake_revision: int,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            intake = self._ensure_intake(owned, lock=True)
            self._expected_revision(intake, expected_intake_revision)
            candidate = self._candidate(intake, candidate_id, lock=True)
            if candidate.saved_at is None:
                candidate.saved_at = datetime.now(UTC)
                candidate.saved_by_user_id = actor_user_id
                intake.revision += 1
                self.session.flush()
            return self._view(owned, intake)

    def activate_candidate(
        self,
        actor_user_id: int,
        project_id: int,
        candidate_id: int,
        *,
        expected_intake_revision: int,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            intake = self._ensure_intake(owned, lock=True)
            self._expected_revision(intake, expected_intake_revision)
            self._require_editable_intake(intake)
            candidate = self._candidate(intake, candidate_id)
            self._require_candidate_fresh(intake, candidate)
            self._require_hard_questions_resolved(intake)
            if intake.current_candidate_id != candidate.id or intake.stage != "confirmation":
                intake.current_candidate_id = candidate.id
                intake.stage = "confirmation"
                intake.revision += 1
                self.session.flush()
            return self._view(owned, intake)

    def adopt_candidate(
        self,
        actor_user_id: int,
        project_id: int,
        candidate_id: int,
        *,
        expected_intake_revision: int,
        expected_brief_revision: int,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            intake = self._ensure_intake(owned, lock=True)
            self._expected_revision(intake, expected_intake_revision)
            self._require_editable_intake(intake)
            candidate = self._candidate(intake, candidate_id)
            self._require_candidate_fresh(intake, candidate)
            self._require_hard_questions_resolved(intake)
            brief = self._brief(owned, lock=True)
            if brief.draft_revision != expected_brief_revision:
                raise ApplicationError(
                    "brief_revision_conflict",
                    "Brief draft revision is stale",
                    status_code=409,
                    details={
                        "current_revision": brief.draft_revision,
                        "received_revision": expected_brief_revision,
                    },
                )
            if brief.current_version_id is not None:
                raise ApplicationError(
                    "brief_intake_formal_version_exists",
                    "A confirmed Brief version already exists; continue in formal review",
                    status_code=409,
                )

            source = self._require_source(intake)
            projected = project_candidate_to_brief(
                candidate.content_jsonb,
                source_record_ids=self._source_lineage_ids(owned, source),
            )
            if brief.draft_jsonb != projected:
                brief.draft_jsonb = projected
                brief.draft_revision += 1
                brief.current_version_id = None
            intake.current_candidate_id = candidate.id
            intake.adopted_candidate_id = candidate.id
            intake.stage = "brief_review"
            intake.revision += 1
            self.session.flush()
            return {
                "intake": self._view(owned, intake),
                "brief": _brief_view(brief),
            }

    def create_questions_task(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        expected_intake_revision: int,
        provider: str,
    ) -> dict[str, Any]:
        provider = _supported_provider(provider)
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            intake = self._ensure_intake(owned, lock=True)
            self._expected_revision(intake, expected_intake_revision)
            self._require_editable_intake(intake)
            source = self._require_source(intake)
            self._require_no_active_task(intake, "brief_intake_questions")
            setting = self._provider_setting(actor_user_id, provider)
            existing_questions = self._question_inputs(intake)
            frozen_input = {
                "source": {
                    "source_record_id": source.id,
                    "content_text": source.content_text,
                    "content_hash": source.content_hash,
                },
                "mode": "additional" if existing_questions else "initial",
                "existing_questions": existing_questions,
            }
            task = self._new_task(
                owned,
                intake,
                actor_user_id=actor_user_id,
                setting=setting,
                task_type="brief_intake_questions",
                input_source_record_id=source.id,
                input_intake_revision=intake.revision,
                base_candidate_id=None,
                input_jsonb=frozen_input,
            )
            self._queue_task(task, message="关键追问任务已进入队列")
            intake.current_questions_task_run_id = task.id
            intake.current_candidate_id = None
            intake.stage = "questions"
            intake.revision += 1
            self.session.flush()
            return task_view(task)

    def create_synthesize_task(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        expected_intake_revision: int,
        provider: str,
        base_candidate_id: int | None,
        instruction: str | None,
    ) -> dict[str, Any]:
        provider = _supported_provider(provider)
        normalized_instruction = None if instruction is None else instruction.strip()
        if instruction is not None and not normalized_instruction:
            raise ApplicationError(
                "brief_intake_instruction_blank",
                "A dialogue revision instruction must not be blank",
                status_code=422,
            )
        with self.session.begin():
            owned = self._owned(actor_user_id, project_id, lock=True)
            intake = self._ensure_intake(owned, lock=True)
            self._expected_revision(intake, expected_intake_revision)
            self._require_editable_intake(intake)
            source = self._require_source(intake)
            self._require_hard_questions_resolved(intake)
            self._require_no_active_task(intake, "brief_intake_synthesize")
            base_candidate = None
            if base_candidate_id is not None:
                base_candidate = self._candidate(intake, base_candidate_id)
                self._require_candidate_fresh(intake, base_candidate)
            if normalized_instruction is not None and base_candidate is None:
                raise ApplicationError(
                    "brief_intake_base_candidate_required",
                    "Dialogue revision requires a base candidate",
                    status_code=422,
                )
            setting = self._provider_setting(actor_user_id, provider)
            basis_hash = self._basis_hash(intake)
            frozen_input = {
                "source": {
                    "source_record_id": source.id,
                    "content_text": source.content_text,
                    "content_hash": source.content_hash,
                },
                "questions": self._question_inputs(intake),
                "basis_hash": basis_hash,
                "active_candidate_id": intake.current_candidate_id,
                "base_candidate": (
                    None
                    if base_candidate is None
                    else {
                        "candidate_id": base_candidate.id,
                        "content": deepcopy(base_candidate.content_jsonb),
                    }
                ),
                "instruction": normalized_instruction,
            }
            task = self._new_task(
                owned,
                intake,
                actor_user_id=actor_user_id,
                setting=setting,
                task_type="brief_intake_synthesize",
                input_source_record_id=source.id,
                input_intake_revision=intake.revision,
                base_candidate_id=None if base_candidate is None else base_candidate.id,
                input_jsonb=frozen_input,
            )
            self._queue_task(task, message="创作简报候选任务已进入队列")
            intake.revision += 1
            self.session.flush()
            return task_view(task)

    def complete_questions_task(
        self,
        task_run_id: int,
        attempt_id: int,
        *,
        questions: list[dict[str, Any]],
        usage: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = validate_question_set(questions)
        with self.session.begin():
            task, attempt = self._completion_rows(
                task_run_id,
                attempt_id,
                expected_task_type="brief_intake_questions",
            )
            intake = self._task_intake(task, lock=True)
            source = self._current_source(intake)
            frozen_source = _required_object(task.input_jsonb, "source")
            stale = (
                intake.current_questions_task_run_id != task.id
                or source is None
                or source.id != task.input_source_record_id
                or source.content_hash != frozen_source.get("content_hash")
            )
            if not stale:
                existing_questions = self._current_questions(intake)
                existing_keys = {
                    question.question_key for question in existing_questions
                }
                is_additional = task.input_jsonb.get("mode") == "additional"
                for question in normalized:
                    if is_additional:
                        question["required"] = False
                    base_key = question["question_key"]
                    unique_key = base_key
                    suffix = 2
                    while unique_key in existing_keys:
                        suffix_text = f"_{suffix}"
                        unique_key = f"{base_key[: 64 - len(suffix_text)]}{suffix_text}"
                        suffix += 1
                    question["question_key"] = unique_key
                    existing_keys.add(unique_key)
                    self.session.add(
                        BriefIntakeQuestion(
                            project_id=task.project_id,
                            intake_id=intake.id,
                            generated_by_task_run_id=task.id,
                            question_key=question["question_key"],
                            ordinal=question["ordinal"],
                            prompt=question["prompt"],
                            impact=question["impact"],
                            is_required=question["required"],
                            suggestions_jsonb=question["suggestions"],
                            answer_status="unanswered",
                            answer_text=None,
                            answer_source=None,
                        )
                    )
                intake.stage = (
                    "questions" if existing_questions or normalized else "confirmation"
                )
                intake.revision += 1
            result = {
                "input_hash": task.input_hash,
                "questions": normalized,
                "stale": stale,
            }
            self._finish_task(
                task,
                attempt,
                result=result,
                usage=usage,
                message=(
                    "关键追问已生成，等待作者回答"
                    if normalized and not stale
                    else "当前原文已变化；旧追问结果仅保留为历史"
                    if stale
                    else "当前方向无需追加关键追问"
                ),
            )
            return task_view(task)

    def complete_synthesize_task(
        self,
        task_run_id: int,
        attempt_id: int,
        *,
        content: dict[str, Any],
        usage: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = validate_candidate_content(content)
        with self.session.begin():
            task, attempt = self._completion_rows(
                task_run_id,
                attempt_id,
                expected_task_type="brief_intake_synthesize",
            )
            intake = self._task_intake(task, lock=True)
            frozen_basis_hash = task.input_jsonb.get("basis_hash")
            frozen_active_candidate_id = task.input_jsonb.get("active_candidate_id")
            stale = (
                not isinstance(frozen_basis_hash, str)
                or self._basis_hash(intake) != frozen_basis_hash
                or intake.current_candidate_id != frozen_active_candidate_id
            )
            origin = (
                "dialogue_revision"
                if task.base_brief_intake_candidate_id is not None
                else "agent_synthesis"
            )
            candidate = BriefIntakeCandidate(
                project_id=task.project_id,
                intake_id=intake.id,
                parent_candidate_id=task.base_brief_intake_candidate_id,
                generated_by_task_run_id=task.id,
                created_by_user_id=task.actor_user_id,
                origin=origin,
                basis_input_hash=(
                    frozen_basis_hash if isinstance(frozen_basis_hash, str) else task.input_hash
                ),
                content_jsonb=normalized,
                content_hash=_json_hash(normalized),
                saved_at=None,
                saved_by_user_id=None,
            )
            self.session.add(candidate)
            self.session.flush()
            if not stale:
                intake.current_candidate_id = candidate.id
                intake.stage = "confirmation"
                intake.revision += 1
            result = {
                "input_hash": task.input_hash,
                "candidate_id": candidate.id,
                "content_hash": candidate.content_hash,
                "origin": candidate.origin,
                "stale": stale,
            }
            self._finish_task(
                task,
                attempt,
                result=result,
                usage=usage,
                message=(
                    "创作简报候选已生成，等待作者确认"
                    if not stale
                    else "Intake 输入已变化；候选已归档但未激活"
                ),
            )
            return task_view(task)

    def _ensure_intake(self, owned: OwnedDraft, *, lock: bool = False) -> BriefIntake:
        statement = select(BriefIntake).where(
            BriefIntake.project_id == owned.project.id
        )
        if lock:
            statement = statement.with_for_update()
        intake = self.session.scalar(statement)
        if intake is not None:
            return intake
        brief = self._brief(owned)
        source = self._legacy_current_source(owned, brief)
        has_brief = bool(brief.draft_jsonb) or brief.current_version_id is not None
        intake = BriefIntake(
            project_id=owned.project.id,
            revision=1,
            stage="brief_review" if has_brief else "idea",
            current_source_record_id=None if source is None else source.id,
            current_questions_task_run_id=None,
            current_candidate_id=None,
            adopted_candidate_id=None,
        )
        self.session.add(intake)
        self.session.flush()
        return intake

    def _legacy_current_source(self, owned: OwnedDraft, brief: Brief) -> SourceRecord | None:
        source_ids = brief.draft_jsonb.get("source_record_ids", [])
        if isinstance(source_ids, list) and source_ids:
            row = self.session.scalar(
                select(SourceRecord)
                .where(
                    SourceRecord.project_id == owned.project.id,
                    SourceRecord.id.in_(
                        [value for value in source_ids if type(value) is int and value > 0]
                    ),
                )
                .order_by(SourceRecord.id.desc())
                .limit(1)
            )
            if row is not None:
                return row
        return self.session.scalar(
            select(SourceRecord)
            .where(
                SourceRecord.project_id == owned.project.id,
                SourceRecord.source_kind.in_(("human_original", "human_revision")),
            )
            .order_by(SourceRecord.id.desc())
            .limit(1)
        )

    def _view(self, owned: OwnedDraft, intake: BriefIntake) -> dict[str, Any]:
        source = self._current_source(intake)
        questions = self._current_questions(intake)
        basis_hash = self._basis_hash(intake) if source is not None else None
        candidates = list(
            self.session.scalars(
                select(BriefIntakeCandidate)
                .where(BriefIntakeCandidate.intake_id == intake.id)
                .order_by(BriefIntakeCandidate.id.desc())
            )
        )
        current_candidate = next(
            (row for row in candidates if row.id == intake.current_candidate_id),
            None,
        )
        brief = self._brief(owned)
        hard_questions_resolved = all(
            not question.is_required
            or question.answer_status in RESOLVED_QUESTION_STATUSES
            for question in questions
        )
        return {
            "brief_intake_id": intake.id,
            "project_id": intake.project_id,
            "revision": intake.revision,
            "stage": intake.stage,
            "current_source": None if source is None else source_view(source),
            "current_questions_task_run_id": intake.current_questions_task_run_id,
            "questions": [
                _question_view(question, ordinal=index)
                for index, question in enumerate(questions, start=1)
            ],
            "hard_questions_resolved": hard_questions_resolved,
            "current_candidate_id": intake.current_candidate_id,
            "adopted_candidate_id": intake.adopted_candidate_id,
            "candidates": [
                _candidate_view(
                    candidate,
                    current_candidate_id=intake.current_candidate_id,
                    adopted_candidate_id=intake.adopted_candidate_id,
                    basis_hash=basis_hash,
                )
                for candidate in candidates
            ],
            "pending_decisions": (
                []
                if current_candidate is None
                else deepcopy(current_candidate.content_jsonb.get("pending_decisions", []))
            ),
            "brief": {
                "brief_id": brief.id,
                "draft_revision": brief.draft_revision,
                "current_version_id": brief.current_version_id,
                "has_content": bool(brief.draft_jsonb),
            },
            "updated_at": _time(intake.updated_at),
        }

    def _basis_hash(self, intake: BriefIntake) -> str:
        source = self._require_source(intake)
        return _json_hash(
            {
                "source_record_id": source.id,
                "source_hash": source.content_hash,
                "questions_task_run_id": intake.current_questions_task_run_id,
                "questions": self._question_inputs(intake),
            }
        )

    def _question_inputs(self, intake: BriefIntake) -> list[dict[str, Any]]:
        return [
            {
                "question_key": row.question_key,
                "prompt": row.prompt,
                "impact": row.impact,
                "required": row.is_required,
                "answer_status": row.answer_status,
                "answer_text": row.answer_text,
                "answer_source": row.answer_source,
            }
            for row in self._current_questions(intake)
        ]

    def _current_questions(self, intake: BriefIntake) -> list[BriefIntakeQuestion]:
        source = self._current_source(intake)
        if source is None:
            return []
        return list(
            self.session.scalars(
                select(BriefIntakeQuestion)
                .join(
                    TaskRun,
                    TaskRun.id == BriefIntakeQuestion.generated_by_task_run_id,
                )
                .where(
                    BriefIntakeQuestion.intake_id == intake.id,
                    TaskRun.input_source_record_id == source.id,
                )
                .order_by(
                    BriefIntakeQuestion.generated_by_task_run_id,
                    BriefIntakeQuestion.ordinal,
                )
            )
        )

    def _current_question(
        self, intake: BriefIntake, question_key: str, *, lock: bool
    ) -> BriefIntakeQuestion:
        source = self._current_source(intake)
        if source is None:
            raise not_found("BriefIntakeQuestion")
        statement = (
            select(BriefIntakeQuestion)
            .join(
                TaskRun,
                TaskRun.id == BriefIntakeQuestion.generated_by_task_run_id,
            )
            .where(
                BriefIntakeQuestion.intake_id == intake.id,
                TaskRun.input_source_record_id == source.id,
                BriefIntakeQuestion.question_key == question_key,
            )
        )
        if lock:
            statement = statement.with_for_update()
        question = self.session.scalar(statement)
        if question is None:
            raise not_found("BriefIntakeQuestion")
        return question

    def _candidate(
        self, intake: BriefIntake, candidate_id: int, *, lock: bool = False
    ) -> BriefIntakeCandidate:
        statement = select(BriefIntakeCandidate).where(
            BriefIntakeCandidate.id == candidate_id,
            BriefIntakeCandidate.intake_id == intake.id,
            BriefIntakeCandidate.project_id == intake.project_id,
        )
        if lock:
            statement = statement.with_for_update()
        candidate = self.session.scalar(statement)
        if candidate is None:
            raise not_found("BriefIntakeCandidate")
        return candidate

    def _require_candidate_fresh(
        self, intake: BriefIntake, candidate: BriefIntakeCandidate
    ) -> None:
        if candidate.basis_input_hash != self._basis_hash(intake):
            raise ApplicationError(
                "brief_intake_candidate_stale",
                "The candidate was produced from older Intake input",
                status_code=409,
                details={"candidate_id": candidate.id},
            )

    def _require_editable_intake(self, intake: BriefIntake) -> None:
        if intake.stage == "brief_review":
            raise ApplicationError(
                "brief_intake_already_adopted",
                "This intake has already entered formal Brief review",
                status_code=409,
            )

    def _require_hard_questions_resolved(self, intake: BriefIntake) -> None:
        unresolved = [
            row.question_key
            for row in self._current_questions(intake)
            if row.is_required and row.answer_status not in RESOLVED_QUESTION_STATUSES
        ]
        if unresolved:
            raise ApplicationError(
                "brief_intake_required_questions_unanswered",
                "Resolve every required direction question before continuing",
                status_code=422,
                details={"question_keys": unresolved},
            )

    def _require_no_active_task(self, intake: BriefIntake, task_type: str) -> None:
        active_task_id = self.session.scalar(
            select(TaskRun.id)
            .where(
                TaskRun.brief_intake_id == intake.id,
                TaskRun.task_type == task_type,
                TaskRun.status.in_(ACTIVE_TASK_STATUSES),
            )
            .limit(1)
        )
        if active_task_id is not None:
            raise ApplicationError(
                "brief_intake_task_active",
                "A matching Brief Intake task is already active",
                status_code=409,
                details={"task_run_id": active_task_id, "task_type": task_type},
            )

    def _provider_setting(
        self, actor_user_id: int, provider: str
    ) -> UserProviderSetting:
        setting = self.session.scalar(
            select(UserProviderSetting)
            .where(
                UserProviderSetting.user_id == actor_user_id,
                UserProviderSetting.provider == provider,
            )
            .with_for_update()
        )
        if setting is None or setting.credential_status == "deleted":
            raise ApplicationError(
                "provider_setting_required",
                f"Configure a {provider} API key before starting the task",
                status_code=409,
                details={"provider": provider},
            )
        return setting

    def _new_task(
        self,
        owned: OwnedDraft,
        intake: BriefIntake,
        *,
        actor_user_id: int,
        setting: UserProviderSetting,
        task_type: str,
        input_source_record_id: int,
        input_intake_revision: int,
        base_candidate_id: int | None,
        input_jsonb: dict[str, Any],
    ) -> TaskRun:
        return TaskRun(
            project_id=owned.project.id,
            casefile_id=owned.casefile.id,
            draft_id=owned.draft.id,
            brief_version_id=None,
            input_source_record_id=input_source_record_id,
            input_brief_revision=None,
            brief_intake_id=intake.id,
            input_brief_intake_revision=input_intake_revision,
            base_brief_intake_candidate_id=base_candidate_id,
            agent_thread_id=None,
            input_message_id=None,
            output_message_id=None,
            input_hash=_json_hash(input_jsonb),
            input_jsonb=input_jsonb,
            actor_user_id=actor_user_id,
            provider_setting_id=setting.id,
            task_type=task_type,
            status="queued",
            stage="queued",
            input_draft_revision=owned.draft.revision,
            provider=setting.provider,
            model_id=setting.model_id,
            provider_config_version=setting.config_version,
            schema_version=CASEFILE_SCHEMA_VERSION,
            agent_version=AGENT_VERSION,
            prompt_version=prompt_version_for_task(task_type),
            toolset_version=TOOLSET_VERSION,
            budget_jsonb=dict(setting.default_budget_jsonb),
            usage_jsonb={},
            attempt_count=0,
            result_jsonb=None,
            error_details_jsonb={},
        )

    def _queue_task(self, task: TaskRun, *, message: str) -> None:
        self.session.add(task)
        self.session.flush()
        append_task_event(
            self.session,
            task,
            "task.queued",
            "queued",
            {
                "message": message,
                "task_type": task.task_type,
                "model_id": task.model_id,
                "input_hash": task.input_hash,
            },
        )

    def _completion_rows(
        self, task_run_id: int, attempt_id: int, *, expected_task_type: str
    ) -> tuple[TaskRun, TaskAttempt]:
        task = self.session.scalar(
            select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
        )
        attempt = self.session.scalar(
            select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
        )
        if task is None or attempt is None:
            raise RuntimeError("TaskRun or TaskAttempt disappeared")
        if task.task_type != expected_task_type:
            raise RuntimeError("Brief Intake TaskRun dispatch type changed")
        if (
            attempt.task_run_id != task.id
            or attempt.status != "running"
            or task.status != "running"
        ):
            raise RuntimeError("Brief Intake TaskAttempt no longer owns completion")
        return task, attempt

    def _task_intake(self, task: TaskRun, *, lock: bool) -> BriefIntake:
        if task.brief_intake_id is None:
            raise RuntimeError("Brief Intake TaskRun has no intake lineage")
        statement = select(BriefIntake).where(
            BriefIntake.id == task.brief_intake_id,
            BriefIntake.project_id == task.project_id,
        )
        if lock:
            statement = statement.with_for_update()
        intake = self.session.scalar(statement)
        if intake is None:
            raise RuntimeError("Brief Intake aggregate disappeared")
        return intake

    def _finish_task(
        self,
        task: TaskRun,
        attempt: TaskAttempt,
        *,
        result: dict[str, Any],
        usage: dict[str, Any],
        message: str,
    ) -> None:
        now = datetime.now(UTC)
        attempt.status = "succeeded"
        attempt.candidate_jsonb = deepcopy(result)
        attempt.validation_errors_jsonb = []
        attempt.usage_jsonb = usage
        attempt.finished_at = now
        task.status = "succeeded"
        task.stage = "completed"
        task.usage_jsonb = usage
        task.result_jsonb = result
        task.completed_at = now
        task.leased_by = None
        task.lease_expires_at = None
        append_task_event(
            self.session,
            task,
            "task.succeeded",
            "completed",
            {
                "message": message,
                "task_type": task.task_type,
                "input_hash": task.input_hash,
                "stale": bool(result.get("stale")),
                "usage": usage,
            },
        )

    def _source_lineage_ids(
        self, owned: OwnedDraft, source: SourceRecord
    ) -> list[int]:
        lineage: list[int] = []
        current: SourceRecord | None = source
        visited: set[int] = set()
        while current is not None:
            if current.id in visited:
                raise RuntimeError("SourceRecord lineage contains a cycle")
            visited.add(current.id)
            lineage.append(current.id)
            if current.parent_source_record_id is None:
                break
            current = self.session.scalar(
                select(SourceRecord).where(
                    SourceRecord.id == current.parent_source_record_id,
                    SourceRecord.project_id == owned.project.id,
                )
            )
            if current is None:
                raise RuntimeError("SourceRecord lineage is incomplete")
        lineage.reverse()
        return lineage

    def _current_source(self, intake: BriefIntake) -> SourceRecord | None:
        if intake.current_source_record_id is None:
            return None
        return self.session.scalar(
            select(SourceRecord).where(
                SourceRecord.id == intake.current_source_record_id,
                SourceRecord.project_id == intake.project_id,
            )
        )

    def _require_source(self, intake: BriefIntake) -> SourceRecord:
        source = self._current_source(intake)
        if source is None:
            raise ApplicationError(
                "brief_intake_source_required",
                "Save the original idea before continuing",
                status_code=422,
            )
        return source

    def _expected_revision(self, intake: BriefIntake, received: int) -> None:
        if intake.revision != received:
            raise ApplicationError(
                "brief_intake_revision_conflict",
                "Brief Intake revision is stale",
                status_code=409,
                details={
                    "current_revision": intake.revision,
                    "received_revision": received,
                },
            )

    def _brief(self, owned: OwnedDraft, *, lock: bool = False) -> Brief:
        statement = select(Brief).where(Brief.project_id == owned.project.id)
        if lock:
            statement = statement.with_for_update()
        brief = self.session.scalar(statement)
        if brief is None:
            raise not_found("Brief")
        return brief

    def _owned(
        self, actor_user_id: int, project_id: int, *, lock: bool
    ) -> OwnedDraft:
        owned = self.projects.get_owned(actor_user_id, project_id, lock=lock)
        if owned is None:
            raise not_found("Project")
        return owned


def validate_question_set(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        model = BriefIntakeQuestionSetContract.model_validate({"questions": questions})
    except ValidationError as error:
        raise RuntimeError("Agent questions do not satisfy the Brief Intake contract") from error
    normalized = cast(
        list[dict[str, Any]],
        model.model_dump(mode="json")["questions"],
    )
    for question in normalized:
        question["prompt"] = question["prompt"].strip()
        question["impact"] = question["impact"].strip()
        question["suggestions"] = [value.strip() for value in question["suggestions"]]
    if len(normalized) > 2:
        raise RuntimeError("Brief Intake may contain at most two questions")
    if sum(1 for question in normalized if question["required"]) > 1:
        raise RuntimeError("Brief Intake may contain at most one required question")
    if len({question["question_key"] for question in normalized}) != len(normalized):
        raise RuntimeError("Brief Intake question keys must be unique")
    if [question["ordinal"] for question in normalized] != list(
        range(1, len(normalized) + 1)
    ):
        raise RuntimeError("Brief Intake question ordinals must be contiguous")
    if any(
        not question["prompt"]
        or not question["impact"]
        or any(not value for value in question["suggestions"])
        for question in normalized
    ):
        raise RuntimeError("Brief Intake question text must not be blank")
    return normalized


def validate_candidate_content(content: dict[str, Any]) -> dict[str, Any]:
    try:
        model = BriefIntakeCandidateContract.model_validate(content)
    except ValidationError as error:
        raise ApplicationError(
            "brief_intake_candidate_invalid",
            "Candidate does not satisfy the Brief Intake contract",
            status_code=422,
            details={"issues": error.errors(include_url=False)},
        ) from error
    normalized = model.model_dump(mode="json")
    normalized["concept"] = normalized["concept"].strip()
    normalized["reasoning_goal"] = normalized["reasoning_goal"].strip()
    normalized["core_selling_points"] = [
        value.strip() for value in normalized["core_selling_points"]
    ]
    normalized["content_outline"] = [
        value.strip() for value in normalized["content_outline"]
    ]
    normalized["risk_notes"] = [value.strip() for value in normalized["risk_notes"]]
    if normalized["author_answer"] is not None:
        normalized["author_answer"] = normalized["author_answer"].strip()
    if normalized["scope_estimate"] is not None:
        normalized["scope_estimate"] = normalized["scope_estimate"].strip()
    for constraint in normalized["constraints"]:
        constraint["statement"] = constraint["statement"].strip()
    for decision in normalized["pending_decisions"]:
        decision["prompt"] = decision["prompt"].strip()
        decision["impact"] = decision["impact"].strip()
    text_values = [
        normalized["concept"],
        normalized["reasoning_goal"],
        *normalized["core_selling_points"],
        *normalized["content_outline"],
        *normalized["risk_notes"],
        *(item["statement"] for item in normalized["constraints"]),
        *(item["prompt"] for item in normalized["pending_decisions"]),
        *(item["impact"] for item in normalized["pending_decisions"]),
    ]
    if any(not value for value in text_values):
        raise ApplicationError(
            "brief_intake_candidate_invalid",
            "Candidate text fields must not be blank",
            status_code=422,
        )
    mode = normalized["resolution_mode"]
    answer = normalized["author_answer"]
    if mode == "author_anchored" and not answer:
        raise ApplicationError(
            "brief_intake_author_answer_required",
            "Author-anchored resolution requires the author's answer",
            status_code=422,
        )
    if mode != "author_anchored" and answer is not None:
        raise ApplicationError(
            "brief_intake_resolution_conflict",
            "Only author-anchored resolution may contain an author answer",
            status_code=422,
        )
    constraint_keys = [item["constraint_key"] for item in normalized["constraints"]]
    decision_keys = [item["decision_key"] for item in normalized["pending_decisions"]]
    if len(constraint_keys) != len(set(constraint_keys)) or len(decision_keys) != len(
        set(decision_keys)
    ):
        raise ApplicationError(
            "brief_intake_candidate_key_duplicate",
            "Candidate constraint and pending-decision keys must be unique",
            status_code=422,
        )
    return normalized


def project_candidate_to_brief(
    content: dict[str, Any], *, source_record_ids: list[int]
) -> dict[str, Any]:
    candidate = validate_candidate_content(content)
    confirmed_constraints = [
        item for item in candidate["constraints"] if item["confirmed"]
    ]
    boundary_text = (
        None
        if not confirmed_constraints
        else "\n".join(
            f"{'必须' if item['strength'] == 'hard' else '偏好'}：{item['statement']}"
            for item in confirmed_constraints
        )
    )
    projected = {
        "source_record_ids": source_record_ids,
        "creative_intent": candidate["concept"],
        "reasoning_proposition": candidate["reasoning_goal"],
        "resolution_mode": candidate["resolution_mode"],
        "author_answer": candidate["author_answer"],
        "author_anchors": [],
        "boundary_text": boundary_text,
        "creative_constraints": [],
        "core_selling_points": candidate["core_selling_points"],
        "content_outline": candidate["content_outline"],
        "scope_estimate": candidate["scope_estimate"],
        "risk_notes": candidate["risk_notes"],
    }
    try:
        return BriefContract.model_validate(projected).model_dump(
            mode="json", exclude_none=False
        )
    except ValidationError as error:
        raise ApplicationError(
            "brief_intake_projection_invalid",
            "Candidate cannot be projected into a formal Brief draft",
            status_code=422,
            details={"issues": error.errors(include_url=False)},
        ) from error


def _candidate_view(
    candidate: BriefIntakeCandidate,
    *,
    current_candidate_id: int | None,
    adopted_candidate_id: int | None,
    basis_hash: str | None,
) -> dict[str, Any]:
    stale = basis_hash is None or candidate.basis_input_hash != basis_hash
    return {
        "candidate_id": candidate.id,
        "parent_candidate_id": candidate.parent_candidate_id,
        "generated_by_task_run_id": candidate.generated_by_task_run_id,
        "origin": candidate.origin,
        "basis_input_hash": candidate.basis_input_hash,
        "content_hash": candidate.content_hash,
        "content": deepcopy(candidate.content_jsonb),
        "is_current": candidate.id == current_candidate_id,
        "is_adopted": candidate.id == adopted_candidate_id,
        "is_saved": candidate.saved_at is not None,
        "is_stale": stale,
        "can_activate": not stale,
        "saved_at": _time(candidate.saved_at),
        "created_at": _time(candidate.created_at),
    }


def _question_view(
    question: BriefIntakeQuestion, *, ordinal: int | None = None
) -> dict[str, Any]:
    return {
        "question_key": question.question_key,
        "ordinal": question.ordinal if ordinal is None else ordinal,
        "prompt": question.prompt,
        "impact": question.impact,
        "required": question.is_required,
        "suggestions": list(question.suggestions_jsonb),
        "answer_status": question.answer_status,
        "answer_text": question.answer_text,
        "answer_source": question.answer_source,
    }


def _brief_view(brief: Brief) -> dict[str, Any]:
    return {
        "brief_id": brief.id,
        "public_id": brief.public_id,
        "draft_revision": brief.draft_revision,
        "content": deepcopy(brief.draft_jsonb),
        "current_version_id": brief.current_version_id,
        "updated_at": _time(brief.updated_at),
    }


def _supported_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ApplicationError(
            "provider_not_supported",
            f"Provider is not supported: {provider}",
            status_code=422,
            details={"supported_providers": sorted(SUPPORTED_PROVIDERS)},
        )
    return normalized


def _required_object(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise RuntimeError(f"Frozen Brief Intake task input is missing {key}")
    return result


def _json_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


__all__ = [
    "BriefIntakeService",
    "project_candidate_to_brief",
    "validate_candidate_content",
    "validate_question_set",
]
