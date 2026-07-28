"""HTTP routes for BYOK settings, Brief versions, TaskRuns, and task SSE."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from casefile.api.dependencies import ActorDependency, SessionDependency
from casefile.api.schemas import (
    BriefConfirmRequest,
    BriefUpdateRequest,
    GenerateTaskRequest,
    ProviderSettingRequest,
)
from casefile.application.errors import ApplicationError
from casefile.application.workflow_service import WorkflowService

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def workflow_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["agent-workflow"])

    @router.get("/settings/provider")
    def get_provider_setting(
        actor: ActorDependency, session: SessionDependency
    ) -> dict[str, Any] | None:
        return WorkflowService(session).get_provider_setting(actor)

    @router.put("/settings/provider")
    def save_provider_setting(
        payload: ProviderSettingRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).save_provider_setting(
            actor,
            api_key=payload.api_key,
            model_id=payload.model_id,
            model_is_custom=payload.model_is_custom,
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
