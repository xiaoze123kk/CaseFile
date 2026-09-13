"""One journaled, fenced call shared by novel generation and editorial review."""

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import func, select

from casefile.agent_runtime.prompt_repository import PromptDefinition
from casefile.data_postgres.models import AgentModelCall
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile.worker.execution import TaskExecutionContext
from casefile.worker.failures import TaskCancellationRequested, merge_numeric_usage


class NovelCallError(RuntimeError):
    pass


class NovelProtocolError(NovelCallError):
    pass


class NovelModelJournal:
    def __init__(self, ctx: TaskExecutionContext, step_id: int, locked: Callable[..., Any]):
        self.ctx, self.step_id, self.locked = ctx, step_id, locked

    def call[T](
        self,
        phase: str,
        prompt: PromptDefinition,
        payload: dict[str, Any],
        invoke: Callable[[], Any],
        validate: Callable[[Any], T],
    ) -> T:
        ctx = self.ctx
        with ctx.session_factory() as session, session.begin():
            task, _ = self.locked(session, ctx)
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(AgentModelCall)
                    .where(
                        AgentModelCall.task_run_id == task.id,
                    )
                )
                or 0
            )
            cap = (
                18
                if task.input_jsonb.get("context", {}).get("editorial_policy") == "chapter-prose-v1"
                else 8
            )
            cap += len(task.input_jsonb.get("history_plan", {}).get("batches", []))
            if count >= min(task.budget_jsonb.get("max_calls", 2), cap):
                raise NovelCallError("novel_model_budget_exhausted")
            call = AgentModelCall(
                project_id=task.project_id,
                task_run_id=task.id,
                task_attempt_id=ctx.attempt_id,
                agent_step_run_id=self.step_id,
                call_no=count + 1,
                status="running",
                provider="deepseek",
                model_id=task.model_id,
                output_protocol="text" if phase == "discuss" else "json_object",
                prompt_version=prompt.version,
                prompt_component_id=prompt.agent_id,
                prompt_sha256=prompt.system_prompt_sha256,
                target_schema_id="novel-context-v1"
                if phase == "context_compaction"
                else "novel-editor-v1",
                input_hash=canonical_json_sha256({"phase": phase, "payload": payload}),
                issues_jsonb=[],
                usage_jsonb={},
            )
            session.add(call)
            session.flush()
            call_id = call.id
        raw, usage, problem = "", {"requests": 1, "unknown_usage_count": 1}, None
        try:
            try:
                result = invoke()
            except TaskCancellationRequested:
                raise
            except Exception as error:
                problem = "novel_provider_failed:" + type(error).__name__
                raise NovelCallError(problem) from None
            raw = result.raw_response
            usage = {"requests": 1, **result.usage}
            attempts = getattr(result, "transport_attempts", ())
            if not result.usage or any(
                getattr(attempt, "status", None) == "completed"
                and getattr(attempt, "usage", None) is None
                for attempt in attempts
            ):
                usage["unknown_usage_count"] = 1
            try:
                if getattr(result, "finish_reason", None) == "length":
                    raise ValueError("novel_output_truncated")
                return validate(result)
            except (ValueError, TypeError) as error:
                problem = type(error).__name__ + ": " + str(error)[:300]
                raise NovelProtocolError(problem) from None
        except TaskCancellationRequested:
            problem = "cancelled"
            raise
        except Exception as error:
            problem = problem or type(error).__name__
            raise
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
