"""HTTP routes for BYOK settings, Brief versions, TaskRuns, and task SSE."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import StreamingResponse

from casefile.api.dependencies import ActorDependency, SessionDependency
from casefile.api.schemas import (
    AgentMessageCreateRequest,
    AgentPatchApplyRequest,
    AgentPatchUndoRequest,
    AgentThreadCreateRequest,
    AgentThreadUpdateRequest,
    BriefAnchorExtractTaskRequest,
    BriefConfirmRequest,
    BriefPolishTaskRequest,
    BriefStrategyOptionsTaskRequest,
    BriefUpdateRequest,
    DraftCandidateAdoptRequest,
    GenerateTaskRequest,
    ProviderSettingRequest,
    ResumeGenerationTaskRequest,
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

    @router.delete("/settings/provider", status_code=204)
    def delete_provider_setting(
        actor: ActorDependency,
        session: SessionDependency,
        provider: Literal["openai", "deepseek"] = "openai",
    ) -> Response:
        WorkflowService(session).delete_provider_setting(actor, provider)
        return Response(status_code=204)

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

    @router.get("/projects/{project_id}/agent/threads")
    def list_agent_threads(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
        query: str | None = None,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        return WorkflowService(session).list_agent_threads(
            actor,
            project_id,
            query=query,
            include_archived=include_archived,
        )

    @router.post("/projects/{project_id}/agent/threads", status_code=201)
    def create_agent_thread(
        project_id: int,
        payload: AgentThreadCreateRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).create_agent_thread(
            actor,
            project_id,
            title=payload.title,
        )

    @router.patch("/projects/{project_id}/agent/threads/{thread_id}")
    def update_agent_thread(
        project_id: int,
        thread_id: int,
        payload: AgentThreadUpdateRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).update_agent_thread(
            actor,
            project_id,
            thread_id,
            changes=payload.model_dump(exclude_unset=True),
        )

    @router.get("/projects/{project_id}/agent/threads/{thread_id}/messages")
    def list_agent_messages(
        project_id: int,
        thread_id: int,
        actor: ActorDependency,
        session: SessionDependency,
        after_sequence: int = 0,
    ) -> list[dict[str, Any]]:
        return WorkflowService(session).list_agent_messages(
            actor,
            project_id,
            thread_id,
            after_sequence=after_sequence,
        )

    @router.post(
        "/projects/{project_id}/agent/threads/{thread_id}/messages",
        status_code=202,
    )
    def send_agent_message(
        project_id: int,
        thread_id: int,
        payload: AgentMessageCreateRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).send_agent_message(
            actor,
            project_id,
            thread_id,
            content=payload.content,
            provider=payload.provider,
        )

    @router.post("/projects/{project_id}/agent/patch-sets/{patch_set_id}/apply")
    def apply_agent_patch_set(
        project_id: int,
        patch_set_id: int,
        payload: AgentPatchApplyRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).apply_agent_patch_set(
            actor,
            project_id,
            patch_set_id,
            expected_revision=payload.expected_revision,
            operation_ids=payload.operation_ids,
        )

    @router.post("/projects/{project_id}/agent/patch-sets/{patch_set_id}/undo")
    def undo_agent_patch_set(
        project_id: int,
        patch_set_id: int,
        payload: AgentPatchUndoRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).undo_agent_patch_set(
            actor,
            project_id,
            patch_set_id,
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
            candidate_strategy=payload.candidate_strategy,
            candidate_strategy_attempt=payload.candidate_strategy_attempt,
        )

    @router.post(
        "/projects/{project_id}/tasks/brief-strategy-options",
        status_code=202,
    )
    def create_strategy_options_task(
        project_id: int,
        payload: BriefStrategyOptionsTaskRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).create_strategy_options_task(
            actor,
            project_id,
            brief_version_id=payload.brief_version_id,
            provider=payload.provider,
            refresh=payload.refresh,
        )

    @router.get("/projects/{project_id}/draft-candidates")
    def list_draft_candidates(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> list[dict[str, Any]]:
        return WorkflowService(session).list_generation_candidates(actor, project_id)

    @router.get("/projects/{project_id}/draft-candidates/{task_run_id}")
    def get_draft_candidate(
        project_id: int,
        task_run_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).get_generation_candidate(
            actor,
            project_id,
            task_run_id,
        )

    @router.post("/projects/{project_id}/draft-candidates/{task_run_id}/adopt")
    def adopt_draft_candidate(
        project_id: int,
        task_run_id: int,
        payload: DraftCandidateAdoptRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).adopt_generation_candidate(
            actor,
            project_id,
            task_run_id,
            expected_draft_revision=payload.expected_draft_revision,
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
            polish_mode=payload.polish_mode,
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
            mode=payload.mode,
            content=payload.content,
        )

    @router.get("/projects/{project_id}/tasks/latest")
    def get_latest_task(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
        task_type: Literal[
            "brief_polish",
            "brief_anchor_extract",
            "brief_intake_questions",
            "brief_intake_synthesize",
            "brief_strategy_options",
            "brief_to_draft",
            "casefile_chat",
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

    @router.post(
        "/projects/{project_id}/tasks/{task_run_id}/resume",
        status_code=202,
    )
    def resume_generation_task(
        project_id: int,
        task_run_id: int,
        payload: ResumeGenerationTaskRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkflowService(session).resume_generation_task(
            actor,
            project_id,
            task_run_id,
            expected_draft_revision=payload.expected_draft_revision,
            expected_brief_revision=payload.expected_brief_revision,
        )

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
            "事件序号必须是非负整数。",
            status_code=422,
        ) from error
    if sequence < 0:
        raise ApplicationError(
            "last_event_id_invalid",
            "事件序号必须是非负整数。",
            status_code=422,
        )
    return sequence


def _sse(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['sequence_no']}\nevent: {event['event_type']}\ndata: {data}\n\n"
