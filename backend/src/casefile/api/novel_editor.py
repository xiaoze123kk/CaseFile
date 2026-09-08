"""Thin HTTP adapter for the independent novel workspace."""

from typing import Any

from fastapi import APIRouter

from casefile.api.dependencies import ActorDependency, SessionDependency
from casefile.application.novel_collaboration import NovelCollaborationService
from casefile.application.novel_editor import NovelEditorService
from casefile_contracts import (
    NovelEditorCreate,
    NovelEditorDecision,
    NovelEditorExchange,
    NovelEditorRequest,
    NovelEditorRestore,
    NovelEditorSave,
    NovelEditorSummary,
    NovelEditorVersion,
    NovelEditorView,
)


def novel_editor_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/projects/{project_id}/novels", tags=["novel-editor"])

    @router.get("", response_model=list[NovelEditorSummary])
    def list_novels(
        project_id: int, draft_id: int, actor: ActorDependency, session: SessionDependency
    ) -> Any:
        return NovelEditorService(session).list_manuscripts(actor, project_id, draft_id)

    @router.post("", response_model=NovelEditorView, status_code=201)
    def create(
        project_id: int,
        payload: NovelEditorCreate,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> Any:
        return NovelEditorService(session).create(
            actor, project_id, payload.model_dump(mode="json")
        )

    @router.get("/{novel_id}", response_model=NovelEditorView)
    def get(
        project_id: int, novel_id: int, actor: ActorDependency, session: SessionDependency
    ) -> Any:
        return NovelEditorService(session).get(actor, project_id, novel_id)

    @router.put("/{novel_id}", response_model=NovelEditorView)
    def save(
        project_id: int,
        novel_id: int,
        payload: NovelEditorSave,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> Any:
        return NovelEditorService(session).save(
            actor, project_id, novel_id, payload.model_dump(mode="json")
        )

    @router.get("/{novel_id}/versions", response_model=list[NovelEditorVersion])
    def history(
        project_id: int, novel_id: int, actor: ActorDependency, session: SessionDependency
    ) -> Any:
        return NovelEditorService(session).history(actor, project_id, novel_id)

    @router.post("/{novel_id}/restore", response_model=NovelEditorView)
    def restore(
        project_id: int,
        novel_id: int,
        payload: NovelEditorRestore,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> Any:
        return NovelEditorService(session).restore(
            actor, project_id, novel_id, payload.model_dump(mode="json")
        )

    @router.post("/{novel_id}/collaborations", response_model=NovelEditorExchange, status_code=202)
    def submit(
        project_id: int,
        novel_id: int,
        payload: NovelEditorRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> Any:
        return NovelCollaborationService(session).submit(
            actor, project_id, novel_id, payload.model_dump(mode="json")
        )

    @router.post(
        "/{novel_id}/collaborations/{exchange_id}/decisions", response_model=NovelEditorView
    )
    def decide(
        project_id: int,
        novel_id: int,
        exchange_id: int,
        payload: NovelEditorDecision,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> Any:
        return NovelEditorService(session).decide(
            actor, project_id, novel_id, exchange_id, payload.model_dump(mode="json")
        )

    return router
