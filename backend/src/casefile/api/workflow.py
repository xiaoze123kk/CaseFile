"""HTTP routes for BYOK settings, Brief versions, TaskRuns, and task SSE."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from casefile.api.dependencies import ActorDependency, SessionDependency
from casefile.api.schemas import (
    BriefAnchorExtractTaskRequest,
    BriefConfirmRequest,
    BriefPolishTaskRequest,
    BriefUpdateRequest,
    GenerateTaskRequest,
    ProviderSettingRequest,
    SourceRecordCreateRequest,
)
from casefile.application.errors import ApplicationError
from casefile.application.workflow_service import WorkflowService

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def workflow_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["agent-workflow"])

    @router.get("/settings/provider")
    def get_provider_setting(
        actor: ActorDependency,
        session: SessionDependency,
        provider: Literal["openai", "deepseek"] = "openai",
    ) -> dict[str, Any] | None:
        return WorkflowService(session).get_provider_setting(actor, provider)

    @router.put("/settings/provider")
    def save_provider_setting(
        payload: ProviderSettingRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).save_provider_setting(
            actor,
            provider=payload.provider,
            api_key=payload.api_key,
            model_id=payload.model_id,
            model_is_custom=payload.model_is_custom,
        )

    @router.get("/projects/{project_id}/sources")
    def list_sources(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> list[dict[str, Any]]:
        return WorkflowService(session).list_sources(actor, project_id)

    @router.post("/projects/{project_id}/sources", status_code=201)
    def create_source(
        project_id: int,
        payload: SourceRecordCreateRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).create_source(
            actor,
            project_id,
            source_kind=payload.source_kind,
            content_text=payload.content_text,
            parent_source_record_id=payload.parent_source_record_id,
        )

    @router.get("/projects/{project_id}/brief")
    def get_brief(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).get_brief(actor, project_id)

    @router.put("/projects/{project_id}/brief")
    def update_brief(
        project_id: int,
        payload: BriefUpdateRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).update_brief(
            actor,
            project_id,
            expected_revision=payload.expected_revision,
            content=payload.content,
        )

    @router.post("/projects/{project_id}/brief/confirm", status_code=201)
    def confirm_brief(
        project_id: int,
        payload: BriefConfirmRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).confirm_brief(
            actor,
            project_id,
            expected_revision=payload.expected_revision,
        )

    @router.post("/projects/{project_id}/tasks/generate", status_code=202)
    def create_generation_task(
        project_id: int,
        payload: GenerateTaskRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).create_generation_task(
            actor,
            project_id,
            brief_version_id=payload.brief_version_id,
            expected_draft_revision=payload.expected_draft_revision,
            provider=payload.provider,
        )

    @router.post("/projects/{project_id}/tasks/brief-polish", status_code=202)
    def create_polish_task(
        project_id: int,
        payload: BriefPolishTaskRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).create_polish_task(
            actor,
            project_id,
            source_record_id=payload.source_record_id,
            provider=payload.provider,
        )

    @router.post(
        "/projects/{project_id}/tasks/brief-anchor-extract",
        status_code=202,
    )
    def create_anchor_extract_task(
        project_id: int,
        payload: BriefAnchorExtractTaskRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).create_anchor_extract_task(
            actor,
            project_id,
            expected_brief_revision=payload.expected_brief_revision,
            provider=payload.provider,
        )

    @router.get("/projects/{project_id}/tasks/latest")
    def get_latest_task(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
        task_type: Literal[
            "brief_polish",
            "brief_anchor_extract",
            "brief_to_draft",
        ],
    ) -> dict[str, Any] | None:
        return WorkflowService(session).get_latest_task(
            actor,
            project_id,
            task_type=task_type,
        )

    @router.get("/projects/{project_id}/tasks/{task_run_id}")
    def get_task(
        project_id: int,
        task_run_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).get_task(actor, project_id, task_run_id)

    @router.get("/projects/{project_id}/tasks/{task_run_id}/events")
    def get_task_events(
        project_id: int,
        task_run_id: int,
        actor: ActorDependency,
        session: SessionDependency,
        after_sequence: int = 0,
    ) -> list[dict[str, Any]]:
        return WorkflowService(session).list_task_events(
            actor,
            project_id,
            task_run_id,
            after_sequence=after_sequence,
        )

    @router.get("/projects/{project_id}/tasks/{task_run_id}/stream")
    def stream_task(
        project_id: int,
        task_run_id: int,
        request: Request,
        actor: ActorDependency,
        session: SessionDependency,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        WorkflowService(session).get_task(actor, project_id, task_run_id)
        after_sequence = _last_event_sequence(last_event_id)
        factory = request.app.state.session_factory

        def events() -> Iterator[str]:
            cursor = after_sequence
            idle_polls = 0
            while True:
                with factory() as stream_session:
                    service = WorkflowService(stream_session)
                    rows = service.list_task_events(
                        actor,
                        project_id,
                        task_run_id,
                        after_sequence=cursor,
                    )
                    task = service.get_task(actor, project_id, task_run_id)
                if rows:
                    idle_polls = 0
                    for event in rows:
                        cursor = event["sequence_no"]
                        yield _sse(event)
                else:
                    idle_polls += 1
                    if idle_polls % 20 == 0:
                        yield ": keep-alive\n\n"
                if task["status"] in TERMINAL_STATUSES and not rows:
                    break
                time.sleep(0.5)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


def _last_event_sequence(value: str | None) -> int:
    if value is None:
        return 0
    try:
        sequence = int(value)
    except ValueError as error:
        raise ApplicationError(
            "last_event_id_invalid",
            "Last-Event-ID must be a non-negative integer",
            status_code=422,
        ) from error
    if sequence < 0:
        raise ApplicationError(
            "last_event_id_invalid",
            "Last-Event-ID must be a non-negative integer",
            status_code=422,
        )
    return sequence


def _sse(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['sequence_no']}\nevent: {event['event_type']}\ndata: {data}\n\n"
