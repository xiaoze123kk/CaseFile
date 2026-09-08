"""Durable novel editing; one model call plus at most one protocol correction."""

from datetime import UTC, datetime
from functools import partial
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from casefile.agent_runtime.novel_chapter_review import chapter_review_prompt
from casefile.agent_runtime.novel_collaboration import (
    NovelCollaborationProvider,
    collaboration_prompt,
    validate_edits,
)
from casefile.agent_runtime.novel_context import compact_memory, govern_context, validate_memory
from casefile.agent_runtime.novel_prose import POLICY, prompt_hashes
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.application.novel_editor import novel_result_message
from casefile.application.task_events import append_task_event
from casefile.application.task_lease import is_current_task_attempt
from casefile.data_postgres.models import AgentModelCall, AgentStepRun, TaskAttempt, TaskRun
from casefile.data_postgres.models.novel_editor import NovelEdit, NovelExchange
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile.worker.execution import ProviderRequirement, TaskExecutionContext
from casefile.worker.failures import TaskCancellationRequested
from casefile.worker.handlers.novel_editorial import review_candidate
from casefile.worker.handlers.novel_model_calls import NovelModelJournal, NovelProtocolError
from casefile.worker.handlers.novel_prose import ChapterProseWorkflow


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
        model = ctx.task.model_id
        frozen = ctx.task.input_jsonb
        prompt = collaboration_prompt(frozen["context"])
        if (
            canonical_json_sha256(frozen) != ctx.task.input_hash
            or frozen["prompt_hash"] != prompt.system_prompt_sha256
            or (
                frozen["context"].get("editorial_policy") not in {None, POLICY}
                and frozen.get("review_prompt_hash")
                != chapter_review_prompt(frozen["context"]).system_prompt_sha256
            )
        ):
            raise RuntimeError("novel_protocol_version_changed")
        if frozen["context"].get("editorial_policy") == POLICY and frozen.get(
            "prose_prompt_hashes"
        ) != prompt_hashes(frozen["context"]):
            raise RuntimeError("novel_prose_version_changed")
        if (
            frozen.get("history_plan")
            and frozen.get("context_prompt_hash")
            != load_prompt("novel_context_compactor").system_prompt_sha256
        ):
            raise RuntimeError("novel_context_version_changed")
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
                component_version=prompt.version,
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
        journal = NovelModelJournal(ctx, step_id, self.locked)
        memory = None
        if "history_plan" in frozen:
            plan = frozen["history_plan"]
            memory = plan["memory"]
            for batch in plan["batches"]:
                ctx.emit(ctx.task, "novel.activity", "generating", {"text": "正在整理较早的对话…"})
                compact_input = {"old_memory": memory, "new_turns": batch}
                previous = memory
                def validate_compaction(
                    response: Any,
                    old: dict[str, Any] = previous,
                    raw: list[dict[str, Any]] = batch,
                ) -> dict[str, Any]:
                    return validate_memory(response.candidate, old, raw)

                memory = journal.call(
                    "context_compaction",
                    load_prompt("novel_context_compactor"),
                    compact_input,
                    partial(
                        getattr(self.provider, "compact", compact_memory), compact_input, key, model
                    ),
                    validate_compaction,
                )
            payload = govern_context(payload, memory)
        repair = None

        def discuss() -> Any:
            buffer = ""

            def emit(delta: str) -> None:
                nonlocal buffer
                buffer += delta
                if len(buffer) >= 16:
                    ctx.emit(ctx.task, "novel.answer.delta", "generating", {"text": buffer})
                    buffer = ""

            answer, usage = self.provider.discuss(payload, key, model, emit)
            if buffer:
                ctx.emit(ctx.task, "novel.answer.delta", "generating", {"text": buffer})
            return SimpleNamespace(
                candidate={"message": answer, "edits": []}, raw_response=answer, usage=usage
            )

        def validate(result: Any) -> dict[str, Any]:
            edits = validate_edits(result.candidate, payload)
            if (
                repair is None
                and payload["mode"] != "discuss"
                and payload.get("scope") != "chapter_rewrite"
                and result.candidate.get("edits")
                and not edits
            ):
                raise ValueError(
                    "novel_edit_no_progress: 上次修改的 before 与 after 完全相同，"
                    "没有产生实际改动。请根据当前 instruction 修改 target 中相关句段，"
                    "before 必须逐字引用原文，after 必须体现实际修改；"
                    "不要只写完成说明或原样复制。如果确实无需修改，返回空 edits 并说明原因。"
                )
            return dict(result.candidate)

        prose_trace = None
        if payload.get("editorial_policy") == POLICY:
            candidate, editorial_review, prose_trace = ChapterProseWorkflow(
                journal, payload, key, model, getattr(self.provider, "chapter_prose", None)
            ).run()
        else:
            for number in range(2):
                ctx.emit(
                    ctx.task,
                    "novel.activity",
                    "generating",
                    {
                        "text": "正在校正候选格式…"
                        if number
                        else "正在组织回答…"
                        if payload["mode"] == "discuss"
                        else "正在重写完整章节…"
                        if payload.get("scope") == "chapter_rewrite"
                        else "正在生成修改候选…"
                    },
                )
                try:
                    candidate = journal.call(
                        "discuss" if payload["mode"] == "discuss" else "generate",
                        prompt,
                        {"context": payload, "repair": repair},
                        discuss
                        if payload["mode"] == "discuss"
                        else partial(self.provider.edit, payload, key, model, repair),
                        validate,
                    )
                    break
                except NovelProtocolError as error:
                    if number:
                        raise RuntimeError("novel_protocol_repair_exhausted") from error
                    repair = str(error)
            editorial_review = None
            if payload.get("editorial_policy"):
                candidate, editorial_review = review_candidate(
                    journal,
                    self.provider,
                    payload,
                    candidate,
                    key,
                    ctx.task.model_id,
                )
        edits = validate_edits(candidate, payload)
        answer = novel_result_message(
            payload["mode"],
            {
                "message": candidate["message"],
                "editorial_review": editorial_review,
            },
            has_edits=bool(edits),
        )
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
            if memory is not None:
                result["context_memory"] = memory
                result["context_manifest"] = payload["context_manifest"]
            if editorial_review is not None:
                result["editorial_review"] = editorial_review
            if prose_trace is not None:
                result["prose_trace"] = prose_trace
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
