"""HTTP routes for Path B (帮我想一个) creative idea generation."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body
from pydantic import BaseModel

from casefile.api.dependencies import ActorDependency, SessionDependency
from casefile.application.idea_service import IdeaService


class IdeaGenerateBody(BaseModel):
    """可选的创作偏好：时代、场景、氛围与自由关键词（均为软约束）。"""

    preferences: dict[str, Any] | None = None


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
        body: Annotated[IdeaGenerateBody | None, Body()] = None,
    ) -> dict[str, Any]:
        preferences = body.preferences if body else None
        return IdeaService(session).create_generation_task(actor, project_id, preferences)

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
