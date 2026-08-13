"""HTTP routes for Path B (帮我想一个) creative idea generation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from casefile.api.dependencies import ActorDependency, SessionDependency
from casefile.application.idea_service import IdeaService


def ideas_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["ideas"])

    @router.get("/projects/{project_id}/ideas")
    def list_ideas(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return IdeaService(session).list(actor, project_id)

    @router.post("/projects/{project_id}/ideas/generate", status_code=201)
    def generate_ideas(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return IdeaService(session).create_generation_task(actor, project_id)

    @router.post("/projects/{project_id}/ideas/{idea_id}/bookmark")
    def bookmark_idea(
        project_id: int,
        idea_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return IdeaService(session).bookmark(actor, project_id, idea_id)

    @router.post("/projects/{project_id}/ideas/{idea_id}/archive")
    def archive_idea(
        project_id: int,
        idea_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return IdeaService(session).archive(actor, project_id, idea_id)

    @router.post("/projects/{project_id}/ideas/{idea_id}/select")
    def select_idea(
        project_id: int,
        idea_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return IdeaService(session).select(actor, project_id, idea_id)

    @router.post("/projects/{project_id}/ideas/{idea_id}/regenerate")
    def regenerate_idea(
        project_id: int,
        idea_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return IdeaService(session).regenerate(actor, project_id, idea_id)

    return router
