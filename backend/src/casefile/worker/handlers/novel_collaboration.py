"""Durable novel editing; one model call plus at most one protocol correction."""

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import select

from casefile.agent_runtime.novel_collaboration import (
    VERSION,
    NovelCollaborationProvider,
    validate_edits,
)
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.application.task_events import append_task_event
from casefile.application.task_lease import is_current_task_attempt
from casefile.data_postgres.models import AgentModelCall, AgentStepRun, TaskAttempt, TaskRun
from casefile.data_postgres.models.novel_editor import NovelEdit, NovelExchange
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile.worker.execution import ProviderRequirement, TaskExecutionContext
from casefile.worker.failures import TaskCancellationRequested, merge_numeric_usage


class NovelCollaborationHandler:
    task_types = frozenset({"novel_collaborate"})
    provider_requirement: ProviderRequirement = "required"

    def __init__(self, provider: Any = None):
        self.provider = provider or NovelCollaborationProvider()

    def locked(
        self, session: Any, ctx: TaskExecutionContext, *, allow_cancel: bool = False
    ) -> tuple[TaskRun, TaskAttempt]:
        task = session.scalar(select(TaskRun).where(TaskRun.id == ctx.task.id).with_for_update())
        attempt = session.get(TaskAttempt, ctx.attempt_id)
        if (
            task is None
            or attempt is None
            or task.leased_by != ctx.task.leased_by
            or not is_current_task_attempt(task, attempt)
        ):
            raise RuntimeError("novel_task_lease_lost")
        if task.status == "cancelling" and not allow_cancel:
            raise TaskCancellationRequested
        return task, attempt

    def execute(self, ctx: TaskExecutionContext) -> None:
        try:
            self._execute(ctx)
        except Exception:
            # Close this component's audit rows while this attempt still owns the lease.
            with ctx.session_factory() as session, session.begin():
                try:
                    self.locked(session, ctx, allow_cancel=True)
                except RuntimeError:
                    raise
                for step in session.scalars(
                    select(AgentStepRun).where(
                        AgentStepRun.task_attempt_id == ctx.attempt_id,
                        AgentStepRun.component_id == "novel_collaboration",
                        AgentStepRun.status == "running",
                    )
                ):
                    step.status = "failed"
                    step.finished_at = datetime.now(UTC)
                    step.usage_jsonb = ctx.state.usage
            raise

    def _execute(self, ctx: TaskExecutionContext) -> None:
        _, key = ctx.require_provider()
        assert ctx.task.model_id is not None
        frozen = ctx.task.input_jsonb
        prompt = load_prompt("novel_collaboration")
        if (
            canonical_json_sha256(frozen) != ctx.task.input_hash
            or frozen["prompt_hash"] != prompt.system_prompt_sha256
        ):
            raise RuntimeError("novel_protocol_version_changed")
        with ctx.session_factory() as session, session.begin():
            self.locked(session, ctx)
            # An uncertain earlier transport must never be replayed automatically.
            if session.scalar(
                select(AgentModelCall.id).where(AgentModelCall.task_run_id == ctx.task.id).limit(1)
            ):
                raise RuntimeError("novel_previous_call_not_replayed")
            step = AgentStepRun(
                project_id=ctx.task.project_id,
                task_run_id=ctx.task.id,
                task_attempt_id=ctx.attempt_id,
                component_id="novel_collaboration",
                ir_schema_id="novel-editor-v1",
                component_version=VERSION,
                execution_no=1,
                status="running",
                input_hash=ctx.task.input_hash,
                upstream_hashes_jsonb={},
                diagnostic_jsonb={},
                usage_jsonb={},
            )
            session.add(step)
            session.flush()
            step_id = step.id
        payload = frozen["context"]
        repair = None
        edits = []
        answer = ""
        for number in range(1, 3):
            with ctx.session_factory() as session, session.begin():
                self.locked(session, ctx)
                call = AgentModelCall(
                    project_id=ctx.task.project_id,
                    task_run_id=ctx.task.id,
                    task_attempt_id=ctx.attempt_id,
                    agent_step_run_id=step_id,
                    call_no=number,
                    status="running",
                    provider="deepseek",
                    model_id=ctx.task.model_id,
                    output_protocol="text" if payload["mode"] == "discuss" else "json_object",
                    prompt_version=VERSION,
                    prompt_component_id="novel_collaboration",
                    prompt_sha256=prompt.system_prompt_sha256,
                    target_schema_id="novel-editor-v1",
                    input_hash=canonical_json_sha256({"context": payload, "repair": repair}),
                    issues_jsonb=[],
                    usage_jsonb={},
                )
                session.add(call)
                session.flush()
                call_id = call.id
            raw = ""
            usage = {}
            problem = None
            ctx.emit(
                ctx.task,
                "novel.activity",
                "generating",
                {
                    "text": "正在组织回答…"
                    if payload["mode"] == "discuss"
                    else "正在生成修改候选…"
                    if number == 1
                    else "正在校正候选格式…"
                },
            )
            try:
                if payload["mode"] == "discuss":
                    buffer = ""

                    def emit(delta: str) -> None:
                        nonlocal buffer
                        buffer += delta
                        if len(buffer) >= 16:
                            ctx.emit(ctx.task, "novel.answer.delta", "generating", {"text": buffer})
                            buffer = ""

                    answer, usage = self.provider.discuss(payload, key, ctx.task.model_id, emit)
                    if buffer:
                        ctx.emit(ctx.task, "novel.answer.delta", "generating", {"text": buffer})
                    raw = answer
                else:
                    result = self.provider.edit(payload, key, ctx.task.model_id, repair)
                    raw = result.raw_response
                    usage = result.usage
                    ctx.emit(
                        ctx.task,
                        "novel.activity",
                        "validating",
                        {"text": "正在核对修改范围与格式…"},
                    )
                    try:
                        edits = validate_edits(result.candidate, payload)
                        answer = result.candidate["message"]
                    except (ValueError, TypeError) as error:
                        problem = type(error).__name__ + ": " + str(error)[:300]
            except TaskCancellationRequested:
                problem = "cancelled"
                raise
            except Exception as error:
                problem = type(error).__name__
                raise RuntimeError("novel_provider_failed:" + problem) from None
            finally:
                ctx.state.usage = merge_numeric_usage(ctx.state.usage, usage)
                with ctx.session_factory() as session, session.begin():
                    self.locked(session, ctx, allow_cancel=True)
                    call = session.get_one(AgentModelCall, call_id)
                    call.status = "failed" if problem else "succeeded"
                    call.raw_output_text = raw
                    call.output_hash = sha256(raw.encode()).hexdigest()
                    call.output_size_bytes = len(raw.encode())
                    call.usage_jsonb = usage
                    call.error_code = problem[:80] if problem else None
                    call.finished_at = datetime.now(UTC)
            if problem is None:
                break
            repair = problem
        else:
            raise RuntimeError("novel_protocol_repair_exhausted")
        with ctx.session_factory() as session, session.begin():
            task, attempt = self.locked(session, ctx)
            exchange = session.scalar(select(NovelExchange).where(NovelExchange.task_id == task.id))
            assert exchange is not None
            for edit in edits:
                session.add(
                    NovelEdit(
                        project_id=task.project_id,
                        exchange_id=exchange.id,
                        start_offset=edit["start"],
                        end_offset=edit["end"],
                        before=edit["before"],
                        after=edit["after"],
                        reason=edit["reason"],
                    )
                )
            result = {"message": answer, "edits_count": len(edits)}
            now = datetime.now(UTC)
            step = session.get_one(AgentStepRun, step_id)
            step.status = "succeeded"
            step.output_jsonb = result
            step.output_hash = canonical_json_sha256(result)
            step.usage_jsonb = ctx.state.usage
            step.finished_at = now
            attempt.status = "succeeded"
            attempt.candidate_jsonb = result
            attempt.usage_jsonb = ctx.state.usage
            attempt.finished_at = now
            task.status = "succeeded"
            task.stage = "completed"
            task.result_jsonb = result
            task.usage_jsonb = ctx.state.usage
            task.completed_at = now
            task.leased_by = None
            task.lease_expires_at = None
            append_task_event(
                session,
                task,
                "task.succeeded",
                "completed",
                {"message": "小说协作完成，正文尚未修改。", "usage": ctx.state.usage},
            )
