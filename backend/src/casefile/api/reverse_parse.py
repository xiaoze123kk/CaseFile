"""HTTP routes for Path C (已有内容反向解析)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from casefile.api.dependencies import ActorDependency, SessionDependency
from casefile.application.reverse_parse_service import ReverseParseService


class ItemActionRequest(BaseModel):
    action: str
    model_config = {"extra": "forbid"}


def reverse_parse_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["reverse-parse"])

    @router.post("/projects/{project_id}/reverse-parse/documents", status_code=201)
    def upload_document(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
        file: Annotated[UploadFile, File()],
    ) -> dict[str, Any]:
        data = file.file.read()
        return ReverseParseService(session).upload_document(
            actor,
            project_id,
            file.filename or "unnamed",
            file.content_type or "application/octet-stream",
            data,
        )

    @router.get("/projects/{project_id}/reverse-parse/documents")
    def list_documents(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return ReverseParseService(session).list_documents(actor, project_id)

    @router.get("/projects/{project_id}/reverse-parse/documents/{document_id}")
    def get_document(
        project_id: int,
        document_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return ReverseParseService(session).get_document(actor, project_id, document_id)

    @router.get("/projects/{project_id}/reverse-parse/documents/{document_id}/blocks")
    def get_blocks(
        project_id: int,
        document_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return ReverseParseService(session).get_blocks(actor, project_id, document_id)

    @router.patch("/projects/{project_id}/reverse-parse/items/{item_id}")
    def confirm_item(
        project_id: int,
        item_id: int,
        payload: ItemActionRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return ReverseParseService(session).confirm_item(
            actor, project_id, item_id, action=payload.action
        )

    @router.post(
        "/projects/{project_id}/reverse-parse/documents/{document_id}/retry",
        status_code=202,
    )
    def retry_parse(
        project_id: int,
        document_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return ReverseParseService(session).retry_parse(actor, project_id, document_id)

    @router.post("/projects/{project_id}/reverse-parse/documents/{document_id}/form-brief")
    def form_brief(
        project_id: int,
        document_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return ReverseParseService(session).form_brief(actor, project_id, document_id)

    return router


__all__ = ["reverse_parse_router"]
