"""Freeze a novel collaboration request into the shared durable task queue."""

from typing import Any

from sqlalchemy import select

from casefile.agent_runtime.novel_chapter_review import chapter_review_prompt
from casefile.agent_runtime.novel_collaboration import (
    collaboration_prompt,
    prepare_context,
)
from casefile.agent_runtime.novel_context import govern_context, history_messages
from casefile.agent_runtime.novel_prose import POLICY, prompt_hashes
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.application.errors import ApplicationError
from casefile.application.novel_context import load_history
from casefile.application.novel_editor import NovelEditorService, conflict
from casefile.application.task_events import append_task_event
from casefile.data_postgres.models import TaskRun, UserProviderSetting
from casefile.data_postgres.models.novel_editor import NovelExchange
from casefile.domain.narrative_compiler import canonical_json_sha256


class NovelCollaborationService(NovelEditorService):
    def submit(
        self, actor: int, project: int, manuscript: int, request: dict[str, Any]
    ) -> dict[str, Any]:
        if request.get("history_after_exchange_id") is None:
            request = {k: v for k, v in request.items() if k != "history_after_exchange_id"}
        if request.get("requirements") is None:
            request = {k: v for k, v in request.items() if k != "requirements"}
        with self.session.begin():
            if not request["instruction"].strip():
                raise ApplicationError(
                    "novel_instruction_empty", "请填写协作要求。", status_code=422
                )
            m = self.owned(actor, project, manuscript, lock=True)
            existing = self.session.scalar(
                select(NovelExchange).where(
                    NovelExchange.manuscript_id == m.id,
                    NovelExchange.request_key == request["request_key"],
                )
            )
            if existing:
                prior = self.session.get(TaskRun, existing.task_id)
                assert prior is not None
                if prior.input_jsonb["request"] != request:
                    raise conflict()
                return self.exchange_view(existing)
            if m.revision != request["expected_revision"]:
                raise conflict()
            active = self.session.scalar(
                select(NovelExchange.id)
                .join(TaskRun, TaskRun.id == NovelExchange.task_id)
                .where(
                    NovelExchange.manuscript_id == m.id,
                    TaskRun.status.in_(["queued", "running", "cancelling"]),
                )
            )
            if active:
                raise ApplicationError(
                    "novel_busy", "请等待当前协作完成或先停止。", status_code=409
                )
            boundary = request.get("history_after_exchange_id", 0)
            if boundary and not self.session.scalar(
                select(NovelExchange.id).where(
                    NovelExchange.id == boundary, NovelExchange.manuscript_id == m.id
                )
            ):
                raise ApplicationError(
                    "novel_history_boundary_invalid",
                    "对话起点无效，请刷新后重试。",
                    status_code=422,
                )
            anchor = request["anchor"]
            if anchor and anchor["original"] and request["mode"] != "discuss":
                raise ApplicationError(
                    "novel_original_readonly", "原始稿仅支持讨论。", status_code=422
                )
            version = self.version(m, 1 if anchor and anchor["original"] else m.revision)
            try:
                history_plan = load_history(
                    self.session,
                    m.id,
                    request["chapter_id"],
                    bool(anchor and anchor["original"]),
                    boundary,
                )
            except ValueError as error:
                raise ApplicationError(
                    str(error), "历史对话超出单次整理预算，请新建小说副本后继续。", status_code=422
                ) from None
            history = history_messages(history_plan["recent"])
            try:
                context = prepare_context(self.chapters(version), request, history)
            except ValueError as error:
                raise ApplicationError(
                    str(error),
                    "整章重写目前支持最多 12,000 字，请拆分章节后再试。"
                    if str(error) == "novel_chapter_rewrite_too_large"
                    else "整章重写需使用改写模式，并取消正文选段引用。"
                    if str(error) == "novel_chapter_rewrite_scope_invalid"
                    else "必须保留与允许调整目前用于整章重写，请选择整章重写范围。"
                    if str(error) == "novel_requirements_scope_invalid"
                    else "选区已变化、正文为空或上下文过长，请重新选择较小范围。",
                    status_code=422,
                ) from None
            context["novel_title"] = version.title
            # A textual setting digest stays bounded and is advisory, never a compiler gate.
            owned = self.owned(actor, project)
            import json

            from casefile.application.casefile_v1 import build_casefile_document

            try:
                document = build_casefile_document(self.session, owned)
            except ApplicationError as error:
                if error.code != "brief_version_missing":
                    raise
                # Imported prose is editable before a CaseFile brief exists.
                document = {}
            related = []

            def collect(value: Any) -> None:
                if isinstance(value, dict):
                    name = value.get("name") or value.get("title")
                    if isinstance(name, str) and len(name) > 1 and name in context["target"]:
                        encoded = json.dumps(value, ensure_ascii=False)
                        if len(encoded) <= 3000:
                            related.append(value)
                            return
                    for child in value.values():
                        collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)

            collect(document)
            context["related_settings"] = related[:8] if owned.draft.id == m.draft_id else []
            try:
                context = govern_context(context, history_plan["memory"])
            except ValueError as error:
                raise ApplicationError(
                    str(error), "当前正文和要求超出上下文预算，请缩小范围。", status_code=422
                ) from None
            setting = self.session.scalar(
                select(UserProviderSetting).where(
                    UserProviderSetting.user_id == actor,
                    UserProviderSetting.provider == "deepseek",
                    UserProviderSetting.credential_status != "deleted",
                )
            )
            if setting is None or not setting.secret_ciphertext:
                raise ApplicationError(
                    "provider_setting_required", "请先配置 DeepSeek。", status_code=422
                )
            prompt = collaboration_prompt(context)
            frozen = {
                "request": request,
                "context": context,
                "anchor": anchor,
                "prompt_hash": prompt.system_prompt_sha256,
                "history_plan": history_plan,
                "context_prompt_hash": load_prompt("novel_context_compactor").system_prompt_sha256,
            }
            if context.get("editorial_policy") == POLICY:
                frozen["prose_prompt_hashes"] = prompt_hashes(context)
            elif context.get("editorial_policy"):
                frozen["review_prompt_hash"] = chapter_review_prompt(context).system_prompt_sha256
            task = TaskRun(
                project_id=project,
                casefile_id=owned.casefile.id,
                draft_id=owned.draft.id,
                actor_user_id=actor,
                task_type="novel_collaborate",
                status="queued",
                stage="queued",
                input_hash=canonical_json_sha256(frozen),
                input_jsonb=frozen,
                input_draft_revision=owned.draft.revision,
                provider_setting_id=setting.id,
                provider="deepseek",
                model_id=setting.model_id,
                provider_config_version=setting.config_version,
                schema_version="novel-editor-v1",
                agent_version=prompt.version,
                prompt_version=prompt.version,
                toolset_version="none",
                budget_jsonb={
                    "max_calls": len(history_plan["batches"])
                    + (
                        18
                        if context.get("editorial_policy") == POLICY
                        else 8
                        if context.get("editorial_policy")
                        else 2
                    )
                },
                usage_jsonb={},
                attempt_count=0,
                error_details_jsonb={},
            )
            self.session.add(task)
            self.session.flush()
            exchange = NovelExchange(
                project_id=project,
                manuscript_id=m.id,
                revision=m.revision,
                request_key=request["request_key"],
                task_id=task.id,
                mode=request["mode"],
                chapter_key=request["chapter_id"],
                instruction=request["instruction"],
            )
            self.session.add(exchange)
            self.session.flush()
            append_task_event(
                self.session, task, "task.queued", "queued", {"message": "小说协作已排队。"}
            )
            return self.exchange_view(exchange)
