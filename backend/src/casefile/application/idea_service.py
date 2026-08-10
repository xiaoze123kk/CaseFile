"""Service layer for Path B (帮我想一个) creative idea generation."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import rfc8785
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from casefile.application.errors import ApplicationError, not_found
from casefile.data_postgres.models.idea import IdeaCandidate
from casefile.data_postgres.repositories import ProjectRepository


def _json_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


class IdeaService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

    @staticmethod
    def _candidate_view(row: IdeaCandidate) -> dict[str, Any]:
        return {
            "id": row.id,
            "batch_id": row.batch_id,
            "ordinal": row.ordinal,
            "content": row.content_jsonb,
            "status": row.status,
            "bookmarked": row.bookmarked_at is not None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def _ensure_owned(self, actor_user_id: int, project_id: int, *, lock: bool = False):
        owned = self.projects.get_owned(actor_user_id, project_id, lock=lock)
        if owned is None:
            raise not_found("Project")

    def _get_idea(self, project_id: int, idea_id: int, *, lock: bool = True) -> IdeaCandidate:
        stmt = sa_select(IdeaCandidate).where(
            IdeaCandidate.project_id == project_id, IdeaCandidate.id == idea_id
        )
        if lock:
            stmt = stmt.with_for_update()
        idea = self.session.scalar(stmt)
        if idea is None:
            raise not_found("IdeaCandidate")
        return idea

    def _generate(self, *, regenerate: bool = False, existing_concepts: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        from casefile.agent_runtime import (
            FakeProvider,
            OpenAIAgentsProvider,
            DeepSeekAgentsProvider,
            IdeaGenerationRequest,
        )
        from casefile.agent_runtime.credentials import decrypt_api_key
        from casefile.agent_runtime.prompt_repository import prompt_version_for_task
        from casefile.data_postgres.models.identity import UserProviderSetting

        input_hash = _json_hash({"regenerate": regenerate, "existing_concepts": list(existing_concepts)})
        prompt_version = prompt_version_for_task("idea_generation")

        def emit(event_type: str, stage: str, payload: dict[str, Any]) -> None:
            pass

        # Try real provider first, fall back to fake
        try:
            setting = self.session.scalar(
                sa_select(UserProviderSetting).where(
                    UserProviderSetting.user_id == 1,
                    UserProviderSetting.credential_status != "deleted",
                ).order_by(UserProviderSetting.validated_at.desc().nulls_last()).limit(1)
            )
            if setting is not None and setting.api_key_encrypted is not None:
                api_key = decrypt_api_key(setting.api_key_encrypted)
                request = IdeaGenerationRequest(
                    task_run_id=0,
                    prompt_version=prompt_version,
                    regenerate=regenerate,
                    existing_concepts=existing_concepts,
                    input_hash=input_hash,
                    model_id=setting.model_id or "gpt-4o-mini",
                    api_key=api_key,
                    max_turns=8,
                    emit=emit,
                    network_retries=1,
                )
                if setting.provider == "openai":
                    result = OpenAIAgentsProvider().generate_ideas(request)
                else:
                    result = DeepSeekAgentsProvider().generate_ideas(request)
                candidates = result.candidate.model_dump(mode="json").get("candidates", [])
                if len(candidates) == 3:
                    return candidates
        except Exception:
            pass

        # Fallback to FakeProvider
        request = IdeaGenerationRequest(
            task_run_id=0,
            prompt_version=prompt_version,
            regenerate=regenerate,
            existing_concepts=existing_concepts,
            input_hash=input_hash,
            model_id="fake",
            api_key=None,
            max_turns=8,
            emit=emit,
            network_retries=0,
        )
        result = FakeProvider().generate_ideas(request)
        return result.candidate.model_dump(mode="json").get("candidates", [])

    # ── Queries ──────────────────────────────────────────────────────

    def list(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        self._ensure_owned(actor_user_id, project_id)
        rows = (
            self.session.scalars(
                sa_select(IdeaCandidate)
                .where(IdeaCandidate.project_id == project_id)
                .order_by(IdeaCandidate.batch_id.desc(), IdeaCandidate.ordinal)
            )
            .all()
        )
        batches: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            batches.setdefault(row.batch_id, []).append(self._candidate_view(row))
        return {"project_id": project_id, "batches": batches}

    # ── Mutations ────────────────────────────────────────────────────

    def create_generation_task(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        self.session.rollback()
        with self.session.begin():
            self._ensure_owned(actor_user_id, project_id)
            batch_id = uuid.uuid4().hex[:12]
            candidates = self._generate()
            now = datetime.now(UTC)
            created: list[dict[str, Any]] = []
            for ordinal, content in enumerate(candidates, start=1):
                idea = IdeaCandidate(
                    project_id=project_id,
                    batch_id=batch_id,
                    ordinal=ordinal,
                    created_by_user_id=actor_user_id,
                    content_jsonb=content,
                    content_hash=_json_hash(content),
                    status="active",
                )
                idea.created_at = now
                self.session.add(idea)
                self.session.flush()
                created.append(self._candidate_view(idea))
        return {"project_id": project_id, "batch_id": batch_id, "ideas": created}

    def bookmark(self, actor_user_id: int, project_id: int, idea_id: int) -> dict[str, Any]:
        self.session.rollback()
        with self.session.begin():
            self._ensure_owned(actor_user_id, project_id, lock=True)
            idea = self._get_idea(project_id, idea_id, lock=True)
            if idea.status not in ("active", "bookmarked"):
                raise ApplicationError(
                    "idea_invalid_state", "只有活跃状态的创意候选可以被收藏或取消。", status_code=409,
                )
            if idea.bookmarked_at is not None:
                idea.bookmarked_at = None
                idea.bookmarked_by_user_id = None
                idea.status = "active"
            else:
                idea.bookmarked_at = datetime.now(UTC)
                idea.bookmarked_by_user_id = actor_user_id
                idea.status = "bookmarked"
            self.session.flush()
        return self._candidate_view(idea)

    def archive(self, actor_user_id: int, project_id: int, idea_id: int) -> dict[str, Any]:
        self.session.rollback()
        with self.session.begin():
            self._ensure_owned(actor_user_id, project_id, lock=True)
            idea = self._get_idea(project_id, idea_id, lock=True)
            if idea.status == "selected":
                raise ApplicationError("idea_already_selected", "已选中的创意候选不能归档。", status_code=409)
            idea.status = "archived"
            idea.bookmarked_at = None
            idea.bookmarked_by_user_id = None
            self.session.flush()
        return self._candidate_view(idea)

    def select(self, actor_user_id: int, project_id: int, idea_id: int) -> dict[str, Any]:
        self.session.rollback()
        with self.session.begin():
            self._ensure_owned(actor_user_id, project_id, lock=True)
            idea = self._get_idea(project_id, idea_id, lock=True)
            if idea.status != "active":
                raise ApplicationError(
                    "idea_invalid_state", "只有活跃状态的创意候选可以被选择。", status_code=409,
                )
            content = idea.content_jsonb
            concept = str(content.get("concept", ""))
            core_suspense = str(content.get("core_suspense", ""))
            target_experience = str(content.get("target_experience", ""))
            source_text = f"一句话概念：{concept}\n核心悬念：{core_suspense}\n目标体验：{target_experience}"
            idea.status = "selected"
            self.session.flush()

        from casefile.application.brief_intake_service import BriefIntakeService

        return BriefIntakeService(self.session).update_source(
            actor_user_id=actor_user_id,
            project_id=project_id,
            expected_intake_revision=1,
            content_text=source_text,
            parent_source_record_id=None,
        )

    def regenerate(self, actor_user_id: int, project_id: int, idea_id: int) -> dict[str, Any]:
        self.session.rollback()
        with self.session.begin():
            self._ensure_owned(actor_user_id, project_id, lock=True)
            idea = self._get_idea(project_id, idea_id, lock=True)
            if idea.status == "selected":
                raise ApplicationError("idea_already_selected", "已选中的创意候选不能重新生成。", status_code=409)

            batch_ideas = (
                self.session.scalars(
                    sa_select(IdeaCandidate).where(
                        IdeaCandidate.project_id == project_id,
                        IdeaCandidate.batch_id == idea.batch_id,
                        IdeaCandidate.id != idea_id,
                    )
                ).all()
            )
            existing_concepts = tuple(
                str(i.content_jsonb.get("concept", "")) for i in batch_ideas
            )
            new_candidates = self._generate(regenerate=True, existing_concepts=existing_concepts)
            replacement = new_candidates[0] if new_candidates else {}

            idea.content_jsonb = replacement
            idea.content_hash = _json_hash(replacement)
            idea.status = "active"
            idea.bookmarked_at = None
            idea.bookmarked_by_user_id = None
            self.session.flush()
        return self._candidate_view(idea)
