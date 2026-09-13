"""Owned, revision-bound recommendation; releases DB transaction during inference."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.agent_runtime.credentials import decrypt_api_key
from casefile.agent_runtime.novel_recommendation import recommend_novel
from casefile.application.casefile_v1 import build_casefile_document
from casefile.application.errors import ApplicationError, not_found, revision_conflict
from casefile.data_postgres.models import AuditEvent, UserProviderSetting
from casefile.data_postgres.repositories import ProjectRepository
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile_contracts import NovelRecommendation


def recommend_for_draft(
    session: Session, actor: int, project_id: int, draft_id: int, revision: int, preferences: str
) -> NovelRecommendation:
    projects = ProjectRepository(session)
    with session.begin():
        owned = projects.get_owned(actor, project_id)
        if owned is None:
            raise not_found("Project")
        if owned.draft.id != draft_id:
            raise ApplicationError(
                "current_draft_changed", "工作稿已切换，请重新打开小说编译。", status_code=409
            )
        if owned.draft.revision != revision:
            raise revision_conflict(expected=owned.draft.revision, received=revision)
        document = build_casefile_document(session, owned)
        setting = session.scalar(
            select(UserProviderSetting).where(
                UserProviderSetting.user_id == actor,
                UserProviderSetting.provider == "deepseek",
                UserProviderSetting.credential_status != "deleted",
            )
        )
        if (
            setting is None
            or setting.secret_ciphertext is None
            or setting.secret_nonce is None
            or setting.key_version is None
        ):
            raise ApplicationError(
                "provider_setting_required", "请先在设置中配置 DeepSeek。", status_code=422
            )
        key = decrypt_api_key(
            setting.secret_ciphertext,
            setting.secret_nonce,
            user_id=actor,
            provider="deepseek",
            key_version=setting.key_version,
        )
    try:
        recommendation, usage = recommend_novel(document, preferences, key)
    except Exception:
        raise ApplicationError(
            "novel_recommendation_failed",
            "小说方案推荐未完成，请稍后重试或检查模型设置。",
            status_code=502,
        ) from None
    with session.begin():
        session.expire_all()
        current = projects.get_owned(actor, project_id, lock=True)
        if current is None:
            raise not_found("Project")
        if current.draft.id != draft_id or current.draft.revision != revision:
            raise ApplicationError(
                "novel_recommendation_stale",
                "推荐期间工作稿发生变化，请按最新工作稿重新推荐。",
                status_code=409,
            )
        session.add(
            AuditEvent(
                project_id=project_id,
                casefile_id=current.casefile.id,
                actor_kind="user",
                actor_user_id=actor,
                actor_ref=None,
                action="compiler.recommendation_created",
                target_type="draft",
                target_id=draft_id,
                trace_id=None,
                details_jsonb={
                    "input_hash": canonical_json_sha256(
                        {"document": document, "preferences": preferences}
                    ),
                    "recommendation": recommendation.model_dump(mode="json"),
                    "usage": usage,
                },
            )
        )
    return recommendation
