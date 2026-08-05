"""HTTP routes for the recoverable pre-Brief intake aggregate."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from casefile.api.dependencies import ActorDependency, SessionDependency
from casefile.api.schemas import (
    BriefIntakeCandidateActionRequest,
    BriefIntakeCandidateAdoptRequest,
    BriefIntakeCandidateCreateRequest,
    BriefIntakeQuestionAnswerRequest,
    BriefIntakeQuestionsTaskRequest,
    BriefIntakeSourceUpdateRequest,
    BriefIntakeSynthesizeTaskRequest,
)
from casefile.application.brief_intake_service import BriefIntakeService


def brief_intake_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["brief-intake"])

    @router.get("/projects/{project_id}/brief-intake")
    def get_brief_intake(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return BriefIntakeService(session).get(actor, project_id)

    @router.put("/projects/{project_id}/brief-intake/source")
    def update_brief_intake_source(
        project_id: int,
        payload: BriefIntakeSourceUpdateRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return BriefIntakeService(session).update_source(
            actor,
            project_id,
            expected_intake_revision=payload.expected_intake_revision,
            content_text=payload.content_text,
            parent_source_record_id=payload.parent_source_record_id,
        )

    @router.patch("/projects/{project_id}/brief-intake/questions/{question_key}")
    def answer_brief_intake_question(
        project_id: int,
        question_key: str,
        payload: BriefIntakeQuestionAnswerRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return BriefIntakeService(session).answer_question(
            actor,
            project_id,
            question_key,
            expected_intake_revision=payload.expected_intake_revision,
            answer_mode=payload.answer_mode,
            answer_text=payload.answer_text,
            suggestion_index=payload.suggestion_index,
        )

    @router.post("/projects/{project_id}/brief-intake/candidates", status_code=201)
    def create_brief_intake_candidate(
        project_id: int,
        payload: BriefIntakeCandidateCreateRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return BriefIntakeService(session).create_manual_candidate(
            actor,
            project_id,
            expected_intake_revision=payload.expected_intake_revision,
            content=payload.content,
            parent_candidate_id=payload.parent_candidate_id,
            activate=payload.activate,
        )

    @router.post("/projects/{project_id}/brief-intake/candidates/{candidate_id}/save")
    def save_brief_intake_candidate(
        project_id: int,
        candidate_id: int,
        payload: BriefIntakeCandidateActionRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return BriefIntakeService(session).save_candidate(
            actor,
            project_id,
            candidate_id,
            expected_intake_revision=payload.expected_intake_revision,
        )

    @router.post("/projects/{project_id}/brief-intake/candidates/{candidate_id}/activate")
    def activate_brief_intake_candidate(
        project_id: int,
        candidate_id: int,
        payload: BriefIntakeCandidateActionRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return BriefIntakeService(session).activate_candidate(
            actor,
            project_id,
            candidate_id,
            expected_intake_revision=payload.expected_intake_revision,
        )

    @router.post("/projects/{project_id}/brief-intake/candidates/{candidate_id}/adopt")
    def adopt_brief_intake_candidate(
        project_id: int,
        candidate_id: int,
        payload: BriefIntakeCandidateAdoptRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return BriefIntakeService(session).adopt_candidate(
            actor,
            project_id,
            candidate_id,
            expected_intake_revision=payload.expected_intake_revision,
            expected_brief_revision=payload.expected_brief_revision,
        )

    @router.post("/projects/{project_id}/tasks/brief-intake-questions", status_code=202)
    def create_brief_intake_questions_task(
        project_id: int,
        payload: BriefIntakeQuestionsTaskRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return BriefIntakeService(session).create_questions_task(
            actor,
            project_id,
            expected_intake_revision=payload.expected_intake_revision,
            provider=payload.provider,
        )

    @router.post("/projects/{project_id}/tasks/brief-intake-synthesize", status_code=202)
    def create_brief_intake_synthesize_task(
        project_id: int,
        payload: BriefIntakeSynthesizeTaskRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return BriefIntakeService(session).create_synthesize_task(
            actor,
            project_id,
            expected_intake_revision=payload.expected_intake_revision,
            provider=payload.provider,
            base_candidate_id=payload.base_candidate_id,
            instruction=payload.instruction,
        )

    return router


__all__ = ["brief_intake_router"]
