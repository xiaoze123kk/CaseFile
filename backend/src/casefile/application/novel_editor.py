"""Owned novel versions, immutable history and explicit editorial adoption."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.application.errors import ApplicationError, not_found
from casefile.data_postgres.models import TaskRun
from casefile.data_postgres.models.novel_editor import NovelChapterRecord as Chapter
from casefile.data_postgres.models.novel_editor import (
    NovelEdit,
    NovelEditDecision,
    NovelExchange,
    NovelVersion,
)
from casefile.data_postgres.models.novel_editor import NovelManuscriptRecord as Manuscript
from casefile.data_postgres.repositories import ProjectRepository


def conflict() -> ApplicationError:
    return ApplicationError(
        "novel_revision_conflict", "正文已变化，请保留本地修改并重新核对版本。", status_code=409
    )


def novel_result_message(mode: str, result: dict[str, Any], *, has_edits: bool) -> str:
    """Public completion reflects effective edits rather than a model's self-description."""
    if mode != "discuss" and not has_edits and not result.get("editorial_review"):
        return "本次未生成有效的正文修改，原文保持不变。"
    message = result.get("message", "")
    return message if isinstance(message, str) else ""


class NovelEditorService:
    def __init__(self, session: Session):
        self.session = session

    def owned(
        self, actor: int, project: int, manuscript: int | None = None, *, lock: bool = False
    ) -> Any:
        owned = ProjectRepository(self.session).get_owned(actor, project, lock=lock)
        if owned is None or owned.project.status != "active":
            raise not_found("project")
        if manuscript is None:
            return owned
        query = select(Manuscript).where(
            Manuscript.id == manuscript, Manuscript.project_id == project
        )
        row = self.session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise not_found("novel")
        return row

    def version(self, manuscript: Manuscript, revision: int) -> NovelVersion:
        row = self.session.scalar(
            select(NovelVersion).where(
                NovelVersion.manuscript_id == manuscript.id, NovelVersion.revision == revision
            )
        )
        if row is None:
            raise not_found("novel version")
        return row

    def chapters(self, version: NovelVersion) -> list[dict[str, str]]:
        return [
            {"id": c.chapter_key, "title": c.title, "text": c.text}
            for c in self.session.scalars(
                select(Chapter).where(Chapter.version_id == version.id).order_by(Chapter.ordinal)
            )
        ]

    def append_version(
        self, manuscript: Manuscript, title: str, chapters: list[dict[str, str]], reason: str
    ) -> NovelVersion:
        if (
            not chapters
            or len({c["id"] for c in chapters}) != len(chapters)
            or sum(len(c["text"]) for c in chapters) > 2_000_000
        ):
            raise ApplicationError(
                "novel_content_invalid", "章节标识重复或正文超过保存上限。", status_code=422
            )
        version = NovelVersion(
            project_id=manuscript.project_id,
            manuscript_id=manuscript.id,
            revision=manuscript.revision,
            title=title,
            reason=reason,
        )
        self.session.add(version)
        self.session.flush()
        self.session.add_all(
            [
                Chapter(
                    project_id=manuscript.project_id,
                    version_id=version.id,
                    chapter_key=c["id"],
                    ordinal=i,
                    title=c["title"],
                    text=c["text"],
                )
                for i, c in enumerate(chapters)
            ]
        )
        self.session.flush()
        return version

    def view(self, manuscript: Manuscript) -> dict[str, Any]:
        version = self.version(manuscript, manuscript.revision)
        return {
            "id": manuscript.id,
            "source_key": manuscript.source_key,
            "source_label": manuscript.source_label,
            "revision": manuscript.revision,
            "title": version.title,
            "chapters": self.chapters(version),
            "original_chapters": self.chapters(self.version(manuscript, 1)),
            "exchanges": [
                self.exchange_view(e)
                for e in self.session.scalars(
                    select(NovelExchange)
                    .where(NovelExchange.manuscript_id == manuscript.id)
                    .order_by(NovelExchange.id)
                )
            ],
        }

    def exchange_view(self, exchange: NovelExchange) -> dict[str, Any]:
        task = self.session.get(TaskRun, exchange.task_id)
        assert task is not None
        edits = []
        for edit in self.session.scalars(
            select(NovelEdit)
            .where(NovelEdit.exchange_id == exchange.id)
            .order_by(NovelEdit.start_offset)
        ):
            decision = self.session.scalar(
                select(NovelEditDecision).where(NovelEditDecision.edit_id == edit.id)
            )
            edits.append(
                {
                    "id": edit.id,
                    "start": edit.start_offset,
                    "end": edit.end_offset,
                    "before": edit.before,
                    "after": edit.after,
                    "reason": edit.reason,
                    "status": "pending"
                    if decision is None
                    else "accepted"
                    if decision.action == "accept"
                    else "rejected",
                }
            )
        return {
            "id": exchange.id,
            "task_id": task.id,
            "history_after_exchange_id": task.input_jsonb["request"].get(
                "history_after_exchange_id", 0
            ),
            "revision": exchange.revision,
            "mode": exchange.mode,
            "scope": task.input_jsonb["request"]["scope"],
            "requirements": task.input_jsonb["request"].get("requirements"),
            "editorial_review": (task.result_jsonb or {}).get("editorial_review"),
            "chapter_id": exchange.chapter_key,
            "instruction": exchange.instruction,
            "anchor": task.input_jsonb.get("anchor"),
            "message": novel_result_message(
                exchange.mode, task.result_jsonb or {}, has_edits=bool(edits)
            )
            if task.status == "succeeded"
            else (task.result_jsonb or {}).get("message", ""),
            "status": task.status,
            "error": task.error_code,
            "usage": {k: v for k, v in task.usage_jsonb.items() if type(v) is int},
            "edits": edits,
        }

    def create(self, actor: int, project: int, payload: dict[str, Any]) -> dict[str, Any]:
        with self.session.begin():
            owned = self.owned(actor, project, lock=True)
            if payload["draft_id"] != owned.draft.id:
                raise conflict()
            manuscript = self.session.scalar(
                select(Manuscript).where(
                    Manuscript.project_id == project,
                    Manuscript.draft_id == payload["draft_id"],
                    Manuscript.source_key == payload["source_key"],
                )
            )
            if manuscript is None:
                manuscript = Manuscript(
                    casefile_id=owned.casefile.id,
                    project_id=project,
                    draft_id=payload["draft_id"],
                    source_key=payload["source_key"],
                    source_label=payload["source_label"],
                    revision=1,
                )
                self.session.add(manuscript)
                self.session.flush()
                self.append_version(
                    manuscript, payload["title"], payload["original_chapters"], "original"
                )
                if payload["chapters"] != payload["original_chapters"]:
                    manuscript.revision = 2
                    self.append_version(
                        manuscript, payload["title"], payload["chapters"], "local_import"
                    )
            return self.view(manuscript)

    def list_manuscripts(self, actor: int, project: int, draft: int) -> list[dict[str, Any]]:
        with self.session.begin():
            self.owned(actor, project)
            return [
                {
                    "id": m.id,
                    "title": self.version(m, m.revision).title,
                    "source_key": m.source_key,
                    "revision": m.revision,
                }
                for m in self.session.scalars(
                    select(Manuscript)
                    .where(Manuscript.project_id == project, Manuscript.draft_id == draft)
                    .order_by(Manuscript.id.desc())
                )
            ]

    def get(self, actor: int, project: int, manuscript: int) -> dict[str, Any]:
        with self.session.begin():
            return self.view(self.owned(actor, project, manuscript))

    def save(
        self, actor: int, project: int, manuscript: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self.session.begin():
            m = self.owned(actor, project, manuscript, lock=True)
            current = self.version(m, m.revision)
            if current.title == payload["title"] and self.chapters(current) == payload["chapters"]:
                return self.view(m)
            if m.revision != payload["expected_revision"]:
                raise conflict()
            m.revision += 1
            self.append_version(m, payload["title"], payload["chapters"], "manual")
            return self.view(m)

    def checkpoint(
        self, actor: int, project: int, manuscript: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self.session.begin():
            m = self.owned(actor, project, manuscript, lock=True)
            current = self.version(m, m.revision)
            same = (
                current.title == payload["title"] and self.chapters(current) == payload["chapters"]
            )
            if (
                current.reason == "checkpoint"
                and same
                and m.revision in (payload["expected_revision"], payload["expected_revision"] + 1)
            ):
                return self.view(m)
            if m.revision != payload["expected_revision"]:
                raise conflict()
            m.revision += 1
            self.append_version(m, payload["title"], payload["chapters"], "checkpoint")
            return self.view(m)

    def history(self, actor: int, project: int, manuscript: int) -> list[dict[str, Any]]:
        with self.session.begin():
            m = self.owned(actor, project, manuscript)
            return [
                {
                    "revision": v.revision,
                    "title": v.title,
                    "reason": v.reason,
                    "created_at": v.created_at.isoformat(),
                }
                for v in self.session.scalars(
                    select(NovelVersion)
                    .where(
                        NovelVersion.manuscript_id == m.id,
                        NovelVersion.reason.in_(["original", "local_import", "checkpoint"]),
                    )
                    .order_by(NovelVersion.revision.desc())
                )
            ]

    def version_detail(
        self, actor: int, project: int, manuscript: int, revision: int
    ) -> dict[str, Any]:
        with self.session.begin():
            m = self.owned(actor, project, manuscript)
            version = self.version(m, revision)
            previous = self.session.scalar(
                select(NovelVersion)
                .where(
                    NovelVersion.manuscript_id == m.id,
                    NovelVersion.revision < revision,
                    NovelVersion.reason.in_(["original", "local_import", "checkpoint"]),
                )
                .order_by(NovelVersion.revision.desc())
                .limit(1)
            )
            return {
                "revision": version.revision,
                "title": version.title,
                "previous_title": previous.title if previous else None,
                "chapters": self.chapters(version),
                "previous_chapters": self.chapters(previous) if previous else [],
            }

    def restore(
        self, actor: int, project: int, manuscript: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self.session.begin():
            m = self.owned(actor, project, manuscript, lock=True)
            if m.revision != payload["expected_revision"]:
                raise conflict()
            old = self.version(m, payload["revision"])
            chapters = self.chapters(old)
            m.revision += 1
            self.append_version(m, old.title, chapters, "restore")
            return self.view(m)

    def decide(
        self, actor: int, project: int, manuscript: int, exchange_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self.session.begin():
            m = self.owned(actor, project, manuscript, lock=True)
            exchange = self.session.scalar(
                select(NovelExchange).where(
                    NovelExchange.id == exchange_id, NovelExchange.manuscript_id == m.id
                )
            )
            if exchange is None:
                raise not_found("exchange")
            edits = list(
                self.session.scalars(select(NovelEdit).where(NovelEdit.exchange_id == exchange.id))
            )
            chosen = [e for e in edits if e.id in payload["edit_ids"]]
            if len(chosen) != len(payload["edit_ids"]):
                raise not_found("edit")
            decisions = {
                d.edit_id: d
                for d in self.session.scalars(
                    select(NovelEditDecision).where(
                        NovelEditDecision.edit_id.in_([e.id for e in edits])
                    )
                )
            }
            if all(
                e.id in decisions and decisions[e.id].action == payload["action"] for e in chosen
            ):
                return self.view(m)
            if any(e.id in decisions for e in chosen) or m.revision != payload["expected_revision"]:
                raise conflict()
            current = self.version(m, m.revision)
            if payload["action"] == "accept":
                prior = [d for d in decisions.values() if d.action == "accept"]
                allowed_revision = max(
                    list(
                        self.session.scalars(
                            select(NovelVersion.revision).where(
                                NovelVersion.id.in_([d.version_id for d in prior])
                            )
                        )
                    ),
                    default=exchange.revision,
                )
                if m.revision != allowed_revision:
                    raise conflict()
                baseline = self.chapters(self.version(m, exchange.revision))
                selected_ids = {e.id for e in chosen} | {d.edit_id for d in prior}
                for chapter in baseline:
                    if chapter["id"] == exchange.chapter_key:
                        for edit in sorted(
                            (e for e in edits if e.id in selected_ids),
                            key=lambda e: e.start_offset,
                            reverse=True,
                        ):
                            if chapter["text"][edit.start_offset : edit.end_offset] != edit.before:
                                raise conflict()
                            chapter["text"] = (
                                chapter["text"][: edit.start_offset]
                                + edit.after
                                + chapter["text"][edit.end_offset :]
                            )
                m.revision += 1
                current = self.append_version(m, current.title, baseline, "ai_accept")
            self.session.add_all(
                [
                    NovelEditDecision(
                        project_id=m.project_id,
                        edit_id=e.id,
                        version_id=current.id,
                        action=payload["action"],
                    )
                    for e in chosen
                ]
            )
            self.session.flush()
            return self.view(m)
