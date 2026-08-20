"""HTTP routes for persisted verification runs and findings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from casefile.api.dependencies import ActorDependency, SessionDependency
from casefile.api.schemas import (
    VerificationFindingReviewRequest,
    VerificationRerunRequest,
)
from casefile.application.verification_service import VerificationService
from casefile.application.workflow_service import WorkflowService


def verification_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["verification"])

    @router.post("/projects/{project_id}/verification-runs/rerun", status_code=202)
    def rerun_verification(
        project_id: int,
        payload: VerificationRerunRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).rerun_verification(
            actor,
            project_id,
            expected_draft_id=payload.expected_draft_id,
            expected_draft_revision=payload.expected_draft_revision,
            provider=payload.provider,
        )

    @router.get("/projects/{project_id}/verification-runs/{verification_run_id}")
    def get_verification_run(
        project_id: int,
        verification_run_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return VerificationService(session).get_run(actor, project_id, verification_run_id)

    @router.get("/projects/{project_id}/verification-findings")
    def list_verification_findings(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
        draft_id: int | None = Query(default=None, ge=1),
        status: str | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        return VerificationService(session).list_findings(
            actor,
            project_id,
            draft_id=draft_id,
            status=status,
        )

    @router.post(
        "/projects/{project_id}/verification-findings/{finding_id}/review",
        status_code=201,
    )
    def review_verification_finding(
        project_id: int,
        finding_id: int,
        payload: VerificationFindingReviewRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return VerificationService(session).review_finding(
            actor,
            project_id,
            finding_id,
            decision=payload.decision,
            note=payload.note,
        )

    return router
