"""Service layer for Path C (已有内容反向解析)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from casefile.application.errors import ApplicationError, not_found
from casefile.application.idea_service import _json_hash
from casefile.data_postgres.models.identity import UserProviderSetting
from casefile.data_postgres.models.reverse_parse import ImportedDocument, ParseItem
from casefile.data_postgres.models.workflow import TaskRun
from casefile.data_postgres.repositories import OwnedDraft, ProjectRepository
from casefile.importers.text_extraction import ExtractionError, extract_text, split_blocks

GRADING_LABELS = {
    "explicit": "已明确",
    "inferred": "高可信推断",
    "needs_confirmation": "待用户确认",
    "conflicting": "前后冲突",
    "missing_important": "缺失但可能重要",
}
HIGH_RISK_GRADINGS = {"conflicting", "missing_important"}
BRIEF_FIELD_ORDER = (
    "concept",
    "core_selling_points",
    "content_outline",
    "reasoning_goal",
    "resolution_mode",
    "conclusion_mode",
    "author_answer",
    "constraints",
    "pending_decisions",
    "scope_estimate",
    "risk_notes",
    "field_sources",
)


class ReverseParseService:
    """Own Path C upload, parse completion, item confirmation, and Brief assembly."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

    # ── helpers ─────────────────────────────────────────────────────

    def _ensure_owned(
        self, actor_user_id: int, project_id: int, *, lock: bool = False
    ) -> OwnedDraft:
        owned = self.projects.get_owned(actor_user_id, project_id, lock=lock)
        if owned is None:
            raise not_found("Project")
        return owned

    def _document(
        self, project_id: int, document_id: int, *, lock: bool = True
    ) -> ImportedDocument:
        stmt = sa_select(ImportedDocument).where(
            ImportedDocument.project_id == project_id,
            ImportedDocument.id == document_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        document = self.session.scalar(stmt)
        if document is None:
            raise not_found("ImportedDocument")
        return document

    def _item(self, project_id: int, item_id: int, *, lock: bool = True) -> ParseItem:
        stmt = sa_select(ParseItem).where(
            ParseItem.project_id == project_id, ParseItem.id == item_id
        )
        if lock:
            stmt = stmt.with_for_update()
        item = self.session.scalar(stmt)
        if item is None:
            raise not_found("ParseItem")
        return item

    @staticmethod
    def _document_view(document: ImportedDocument) -> dict[str, Any]:
        return {
            "id": document.id,
            "filename": document.filename,
            "media_type": document.media_type,
            "parse_status": document.parse_status,
            "current_task_run_id": document.current_task_run_id,
            "created_at": document.created_at.isoformat() if document.created_at else None,
        }

    @staticmethod
    def _item_view(item: ParseItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "item_type": item.item_type,
            "content": item.content_jsonb,
            "grading": item.grading,
            "grading_label": GRADING_LABELS.get(item.grading, item.grading),
            "source_block_refs": item.source_block_refs,
            "source_quote": item.source_quote,
            "confirm_status": item.confirm_status,
        }

    def _new_parse_task(
        self,
        owned: OwnedDraft,
        document: ImportedDocument,
        actor_user_id: int,
        setting: UserProviderSetting,
    ) -> TaskRun:
        from casefile.agent_runtime.prompt import AGENT_VERSION
        from casefile.agent_runtime.prompt_repository import prompt_version_for_task
        from casefile.agent_runtime.tools import TOOLSET_VERSION
        from casefile.contracts import CASEFILE_SCHEMA_VERSION

        input_jsonb = {"document_id": document.id, "blocks": document.blocks_jsonb}
        return TaskRun(
            project_id=owned.project.id,
            casefile_id=owned.casefile.id,
            draft_id=owned.draft.id,
            brief_version_id=None,
            input_source_record_id=None,
            input_brief_revision=None,
            brief_intake_id=None,
            input_brief_intake_revision=None,
            base_brief_intake_candidate_id=None,
            agent_thread_id=None,
            input_message_id=None,
            output_message_id=None,
            input_hash=_json_hash(input_jsonb),
            input_jsonb=input_jsonb,
            actor_user_id=actor_user_id,
            provider_setting_id=setting.id,
            task_type="reverse_parse",
            status="queued",
            stage="queued",
            input_draft_revision=owned.draft.revision,
            provider=setting.provider,
            model_id=setting.model_id,
            provider_config_version=setting.config_version,
            schema_version=CASEFILE_SCHEMA_VERSION,
            agent_version=AGENT_VERSION,
            prompt_version=prompt_version_for_task("reverse_parse"),
            toolset_version=TOOLSET_VERSION,
            budget_jsonb=dict(setting.default_budget_jsonb),
            usage_jsonb={},
            attempt_count=0,
            result_jsonb=None,
            error_details_jsonb={},
        )

    # ── queries ─────────────────────────────────────────────────────

    def list_documents(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        self._ensure_owned(actor_user_id, project_id)
        rows = self.session.scalars(
            sa_select(ImportedDocument)
            .where(ImportedDocument.project_id == project_id)
            .order_by(ImportedDocument.created_at.desc())
        ).all()
        return {"project_id": project_id, "documents": [self._document_view(d) for d in rows]}

    def get_document(self, actor_user_id: int, project_id: int, document_id: int) -> dict[str, Any]:
        self._ensure_owned(actor_user_id, project_id)
        document = self._document(project_id, document_id, lock=False)
        items = self.session.scalars(
            sa_select(ParseItem).where(ParseItem.document_id == document_id).order_by(ParseItem.id)
        ).all()
        return {
            "document": self._document_view(document),
            "items": [self._item_view(i) for i in items],
        }

    def get_blocks(self, actor_user_id: int, project_id: int, document_id: int) -> dict[str, Any]:
        self._ensure_owned(actor_user_id, project_id)
        document = self._document(project_id, document_id, lock=False)
        return {"blocks": list(document.blocks_jsonb)}

    # ── mutations ────────────────────────────────────────────────────

    def upload_document(
        self,
        actor_user_id: int,
        project_id: int,
        filename: str,
        media_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        from casefile.application.task_events import append_task_event

        try:
            text = extract_text(filename, data)
        except ExtractionError as error:
            raise ApplicationError(
                "document_extraction_failed", str(error), status_code=422
            ) from error
        blocks = [{"block_no": i + 1, "text": b} for i, b in enumerate(split_blocks(text))]
        if not blocks:
            raise ApplicationError("document_empty", "文档没有可解析的文本内容。", status_code=422)

        self.session.rollback()
        with self.session.begin():
            owned = self._ensure_owned(actor_user_id, project_id, lock=True)
            setting = self.session.scalar(
                sa_select(UserProviderSetting)
                .where(
                    UserProviderSetting.user_id == actor_user_id,
                    UserProviderSetting.credential_status != "deleted",
                )
                .order_by(UserProviderSetting.validated_at.desc().nulls_last())
                .limit(1)
            )
            if setting is None:
                raise ApplicationError(
                    "provider_required",
                    "请先在设置中配置模型供应商后再使用路径 C。",
                    status_code=409,
                )
            document = ImportedDocument(
                project_id=project_id,
                filename=filename,
                media_type=media_type,
                original_bytes=data,
                extracted_text=text,
                blocks_jsonb=blocks,
                parse_status="queued",
                created_by_user_id=actor_user_id,
            )
            self.session.add(document)
            self.session.flush()
            task = self._new_parse_task(owned, document, actor_user_id, setting)
            self.session.add(task)
            self.session.flush()
            document.current_task_run_id = task.id
            self.session.flush()
            append_task_event(
                self.session,
                task,
                "task.queued",
                "queued",
                {
                    "message": "文档解析任务已排队。",
                    "task_type": "reverse_parse",
                    "model_id": task.model_id,
                    "input_hash": task.input_hash,
                },
            )
            from casefile.application.workflow_views import task_view

            return {"document": self._document_view(document), "task": task_view(task)}

    def complete_parse_task(
        self,
        task_run_id: int,
        attempt_id: int,
        *,
        items: list[dict[str, Any]],
        usage: dict[str, Any],
    ) -> dict[str, Any]:
        from casefile.application.task_events import append_task_event
        from casefile.data_postgres.models.workflow import TaskAttempt

        self.session.rollback()
        with self.session.begin():
            task = self.session.scalar(
                sa_select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
            )
            attempt = self.session.scalar(
                sa_select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
            )
            if task is None or attempt is None or task.task_type != "reverse_parse":
                raise RuntimeError("Reverse parse TaskRun/Attempt disappeared")
            if (
                attempt.task_run_id != task.id
                or task.status != "running"
                or attempt.status != "running"
            ):
                raise RuntimeError("Reverse parse TaskAttempt no longer owns completion")
            document = self._document(task.project_id, task.input_jsonb["document_id"], lock=True)
            document.parse_status = "succeeded"
            now = datetime.now(UTC)
            for item_payload in items:
                item = ParseItem(
                    project_id=task.project_id,
                    document_id=document.id,
                    item_type=item_payload["item_type"],
                    content_jsonb=item_payload["content"],
                    grading=item_payload["grading"],
                    source_block_refs=list(item_payload.get("source_block_refs", [])),
                    source_quote=item_payload.get("source_quote", ""),
                    confirm_status="unconfirmed",
                )
                item.created_at = now
                self.session.add(item)
            attempt.status = "succeeded"
            attempt.candidate_jsonb = {"items": items}
            attempt.usage_jsonb = usage
            attempt.finished_at = now
            task.status = "succeeded"
            task.stage = "completed"
            task.usage_jsonb = usage
            task.completed_at = now
            task.leased_by = None
            task.lease_expires_at = None
            append_task_event(
                self.session,
                task,
                "task.succeeded",
                "completed",
                {
                    "message": "文档解析完成。",
                    "item_count": len(items),
                    "usage": usage,
                },
            )
        return {"document_id": document.id, "item_count": len(items)}

    def confirm_item(
        self, actor_user_id: int, project_id: int, item_id: int, *, action: str
    ) -> dict[str, Any]:
        if action not in ("confirm", "reject"):
            raise ApplicationError(
                "invalid_action", "操作只能是 confirm 或 reject。", status_code=422
            )
        self.session.rollback()
        with self.session.begin():
            self._ensure_owned(actor_user_id, project_id, lock=True)
            item = self._item(project_id, item_id, lock=True)
            item.confirm_status = "confirmed" if action == "confirm" else "rejected"
            item.confirmed_by_user_id = actor_user_id
            item.confirmed_at = datetime.now(UTC)
            self.session.flush()
        return self._item_view(item)

    def retry_parse(self, actor_user_id: int, project_id: int, document_id: int) -> dict[str, Any]:
        from casefile.application.task_events import append_task_event

        self.session.rollback()
        with self.session.begin():
            owned = self._ensure_owned(actor_user_id, project_id, lock=True)
            document = self._document(project_id, document_id, lock=True)
            if document.parse_status == "succeeded":
                raise ApplicationError(
                    "already_succeeded", "文档已解析成功，无需重试。", status_code=409
                )
            setting = self.session.scalar(
                sa_select(UserProviderSetting)
                .where(
                    UserProviderSetting.user_id == actor_user_id,
                    UserProviderSetting.credential_status != "deleted",
                )
                .order_by(UserProviderSetting.validated_at.desc().nulls_last())
                .limit(1)
            )
            if setting is None:
                raise ApplicationError("provider_required", "请先配置模型供应商。", status_code=409)
            document.parse_status = "queued"
            task = self._new_parse_task(owned, document, actor_user_id, setting)
            self.session.add(task)
            self.session.flush()
            document.current_task_run_id = task.id
            self.session.flush()
            append_task_event(
                self.session,
                task,
                "task.queued",
                "queued",
                {
                    "message": "文档解析重试任务已排队。",
                    "task_type": "reverse_parse",
                    "model_id": task.model_id,
                    "input_hash": task.input_hash,
                },
            )
            from casefile.application.workflow_views import task_view

            return {"task": task_view(task)}

    def form_brief(self, actor_user_id: int, project_id: int, document_id: int) -> dict[str, Any]:
        from casefile.application.brief_intake_service import BriefIntakeService

        self.session.rollback()
        with self.session.begin():
            self._ensure_owned(actor_user_id, project_id, lock=True)
            document = self._document(project_id, document_id, lock=True)
            if document.parse_status != "succeeded":
                raise ApplicationError("parse_not_ready", "文档尚未解析成功。", status_code=409)
            items = list(
                self.session.scalars(
                    sa_select(ParseItem)
                    .where(ParseItem.document_id == document_id)
                    .order_by(ParseItem.id)
                ).all()
            )
            unconfirmed_high_risk = [
                i
                for i in items
                if i.confirm_status == "unconfirmed" and i.grading in HIGH_RISK_GRADINGS
            ]
            if unconfirmed_high_risk:
                raise ApplicationError(
                    "high_risk_unconfirmed",
                    "存在未处理的高风险项（前后冲突/缺失但可能重要），请先确认或驳回。",
                    status_code=409,
                    details={"item_ids": [i.id for i in unconfirmed_high_risk]},
                )
            confirmed_questions = [
                i
                for i in items
                if i.item_type == "candidate_question"
                and i.confirm_status == "confirmed"
                and str(i.content_jsonb.get("question") or "").strip()
            ]
            if not confirmed_questions:
                raise ApplicationError(
                    "brief_reasoning_goal_missing",
                    "请至少确认一个候选目标问题，才能拼装创作简报。",
                    status_code=409,
                )
            content = self._assemble_brief_content(document, items)
            extracted_text = document.extracted_text
        intake_service = BriefIntakeService(self.session)
        intake_view = intake_service.get(actor_user_id, project_id)
        intake_service.update_source(
            actor_user_id=actor_user_id,
            project_id=project_id,
            expected_intake_revision=intake_view["revision"],
            content_text=extracted_text,
            parent_source_record_id=None,
        )
        return intake_service.create_import_candidate(
            actor_user_id=actor_user_id, project_id=project_id, content=content
        )

    @staticmethod
    def _assemble_brief_content(
        document: ImportedDocument, items: list[ParseItem]
    ) -> dict[str, Any]:
        confirmed = [i for i in items if i.confirm_status == "confirmed"]
        questions = [
            i.content_jsonb.get("question", "")
            for i in confirmed
            if i.item_type == "candidate_question"
        ]
        conclusions = [i for i in confirmed if i.item_type == "candidate_conclusion"]
        events = sorted(
            (i.content_jsonb for i in confirmed if i.item_type == "event"),
            key=lambda e: e.get("order_index", 0),
        )
        entities = [i.content_jsonb for i in confirmed if i.item_type == "entity_alias"]
        first_block = document.blocks_jsonb[0]["text"] if document.blocks_jsonb else ""
        conclusion_mode = "undetermined"
        if conclusions:
            mode = conclusions[0].content_jsonb.get("mode", "")
            conclusion_mode = {
                "unique": "unique",
                "multiple": "finite_multiple",
                "open": "open_interpretation",
            }.get(mode, "undetermined")
        return {
            "concept": first_block[:1000],
            "core_selling_points": [
                f"{e['name']}：{e.get('description', '')}" for e in entities[:3]
            ],
            "content_outline": [
                f"{e.get('order_index', idx + 1)}. {e.get('title', '')}"
                for idx, e in enumerate(events[:8])
            ],
            "reasoning_goal": questions[0] if questions else "",
            "resolution_mode": "agent_proposed",
            "conclusion_mode": conclusion_mode,
            "author_answer": None,
            "constraints": [],
            "pending_decisions": [],
            "scope_estimate": None,
            "risk_notes": [],
            "field_sources": {
                "concept": "user_original",
                "core_selling_points": "user_confirmed",
                "content_outline": "user_confirmed",
                "reasoning_goal": "user_confirmed",
                "resolution_mode": "user_confirmed",
                "conclusion_mode": "user_confirmed",
                "author_answer": "unresolved",
                "constraints": "unresolved",
                "scope_estimate": "unresolved",
                "risk_notes": "unresolved",
            },
        }
