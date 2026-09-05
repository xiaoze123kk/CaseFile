"""Workflow Task input freezing, credential locking and queue persistence."""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.agent_runtime.chat_tools import (
    CHAT_TOOLSET_V3_VERSION,
    CHAT_TOOLSET_V4_VERSION,
    CHAT_TOOLSET_VERSION,
)
from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_V2_VERSION,
    CHAT_CONTEXT_POLICY_V3_VERSION,
    CHAT_CONTEXT_POLICY_V4_VERSION,
    CHAT_CONTEXT_POLICY_V5_VERSION,
    CHAT_CONTEXT_POLICY_V6_VERSION,
    CHAT_CONTEXT_POLICY_VERSION,
    CHAT_CONTEXT_PROMPT_V2_VERSION,
    CHAT_CONTEXT_PROMPT_V4_VERSION,
    CHAT_CONTEXT_PROMPT_V5_VERSION,
    CHAT_CONTEXT_PROMPT_V6_VERSION,
    CHAT_CONTEXT_PROMPT_V10_VERSION,
    CHAT_CONTEXT_PROMPT_VERSION,
)
from casefile.agent_runtime.goal.policy import GoalRuntimeConfig
from casefile.agent_runtime.prompt import agent_version_for_task
from casefile.agent_runtime.prompt_repository import prompt_version_for_task
from casefile.agent_runtime.tools import TOOLSET_VERSION
from casefile.application.errors import ApplicationError
from casefile.application.workflow_common import (
    _append_event,
    _chat_context_policy_version,
    _json_hash,
    _task_view,
)
from casefile.contracts import CASEFILE_SCHEMA_VERSION
from casefile.data_postgres.models import TaskRun, UserProviderSetting
from casefile.data_postgres.repositories import OwnedDraft


def require_provider_setting(
    session: Session,
    actor_user_id: int,
    provider: str,
) -> UserProviderSetting:
    setting = session.scalar(
        select(UserProviderSetting)
        .where(
            UserProviderSetting.user_id == actor_user_id,
            UserProviderSetting.provider == provider,
        )
        .with_for_update()
    )
    if setting is None or setting.credential_status == "deleted":
        raise ApplicationError(
            "provider_setting_required",
            f"开始任务前请先配置 {provider} API 密钥。",
            status_code=409,
            details={"provider": provider},
        )
    return setting


def new_task(
    owned: OwnedDraft,
    *,
    actor_user_id: int,
    setting: UserProviderSetting,
    task_type: str,
    brief_version_id: int | None,
    input_source_record_id: int | None,
    input_brief_revision: int | None,
    input_hash: str,
    input_jsonb: dict[str, Any],
    agent_thread_id: int | None = None,
    input_message_id: int | None = None,
    output_message_id: int | None = None,
) -> TaskRun:
    prompt_version = prompt_version_for_task(task_type)
    policy_version: str | None = None
    if task_type == "casefile_chat":
        policy_version = _chat_context_policy_version()
        if policy_version == CHAT_CONTEXT_POLICY_VERSION:
            prompt_version = CHAT_CONTEXT_PROMPT_VERSION
        elif policy_version == CHAT_CONTEXT_POLICY_V2_VERSION:
            prompt_version = CHAT_CONTEXT_PROMPT_V2_VERSION
        elif policy_version == CHAT_CONTEXT_POLICY_V3_VERSION:
            prompt_version = CHAT_CONTEXT_PROMPT_V4_VERSION
        elif policy_version == CHAT_CONTEXT_POLICY_V4_VERSION:
            prompt_version = CHAT_CONTEXT_PROMPT_V5_VERSION
        elif policy_version == CHAT_CONTEXT_POLICY_V5_VERSION:
            prompt_version = CHAT_CONTEXT_PROMPT_V6_VERSION
        elif policy_version == CHAT_CONTEXT_POLICY_V6_VERSION:
            prompt_version = CHAT_CONTEXT_PROMPT_V10_VERSION
        else:
            prompt_version = "casefile-chat-v3"
        rollout_prompt = os.environ.get("CASEFILE_CHAT_PROMPT_ROLLOUT", "").strip()
        if rollout_prompt in {
            "casefile-chat-v13",
            "casefile-chat-v14",
            "casefile-chat-v15",
            "casefile-chat-v16",
        }:
            # Explicit immutable override for rollback or controlled evaluation.
            prompt_version = rollout_prompt
        goal_rollout = os.environ.get(
            "CASEFILE_CHAT_GOAL_ROLLOUT", "active"
        ).strip().lower()
        if goal_rollout in {"shadow", "active"}:
            goal_runtime = GoalRuntimeConfig.model_validate({"mode": goal_rollout})
            input_jsonb = {
                **input_jsonb,
                "goal_runtime": goal_runtime.model_dump(mode="json"),
            }
            input_hash = _json_hash(input_jsonb)
            prompt_version = "casefile-chat-v22"
    return TaskRun(
        project_id=owned.project.id,
        casefile_id=owned.casefile.id,
        draft_id=owned.draft.id,
        brief_version_id=brief_version_id,
        input_source_record_id=input_source_record_id,
        input_brief_revision=input_brief_revision,
        brief_intake_id=None,
        input_brief_intake_revision=None,
        base_brief_intake_candidate_id=None,
        agent_thread_id=agent_thread_id,
        input_message_id=input_message_id,
        output_message_id=output_message_id,
        input_hash=input_hash,
        input_jsonb=input_jsonb,
        actor_user_id=actor_user_id,
        provider_setting_id=setting.id,
        task_type=task_type,
        status="queued",
        stage="queued",
        input_draft_revision=owned.draft.revision,
        provider=setting.provider,
        model_id=setting.model_id,
        provider_config_version=setting.config_version,
        schema_version=CASEFILE_SCHEMA_VERSION,
        agent_version=agent_version_for_task(task_type, prompt_version),
        prompt_version=prompt_version,
        toolset_version=(
            CHAT_TOOLSET_V4_VERSION
            if task_type == "casefile_chat"
            and policy_version
            in {
                CHAT_CONTEXT_POLICY_V4_VERSION,
                CHAT_CONTEXT_POLICY_V5_VERSION,
                CHAT_CONTEXT_POLICY_V6_VERSION,
            }
            else (
                CHAT_TOOLSET_V3_VERSION
                if task_type == "casefile_chat"
                and policy_version == CHAT_CONTEXT_POLICY_V3_VERSION
                else (CHAT_TOOLSET_VERSION if task_type == "casefile_chat" else TOOLSET_VERSION)
            )
        ),
        budget_jsonb=dict(setting.default_budget_jsonb),
        usage_jsonb={},
        attempt_count=0,
        result_jsonb=None,
        error_details_jsonb={},
    )


def queue_task(session: Session, task: TaskRun, *, message: str) -> dict[str, Any]:
    session.add(task)
    session.flush()
    _append_event(
        session,
        task,
        "task.queued",
        "queued",
        {
            "message": message,
            "task_type": task.task_type,
            "model_id": task.model_id,
            "input_hash": task.input_hash,
        },
    )
    return _task_view(task)
