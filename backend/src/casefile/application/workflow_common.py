"""Shared contracts and deterministic helpers for workflow use cases."""

from __future__ import annotations

import hashlib
import os
from typing import Any

import rfc8785
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_V2_VERSION,
    CHAT_CONTEXT_POLICY_V3_VERSION,
    CHAT_CONTEXT_POLICY_V4_VERSION,
    CHAT_CONTEXT_POLICY_V5_VERSION,
    CHAT_CONTEXT_POLICY_V6_VERSION,
    CHAT_CONTEXT_POLICY_VERSION,
)
from casefile.agent_runtime.models import (
    LEGACY_CONTEXT_POLICY_VERSION,
)
from casefile.application.errors import ApplicationError
from casefile.application.task_events import append_task_event
from casefile.application.workflow_views import (
    event_view,
    provider_view,
    source_view,
    task_view,
    time_view,
)
from casefile.data_postgres.models import (
    AgentThreadContextState,
)

DEFAULT_PROVIDER = "openai"

DEFAULT_MODEL = "gpt-5.6-sol"

SUPPORTED_PROVIDERS = frozenset({"deepseek", "openai"})

SUPPORTED_CHAT_VIEWS = frozenset(
    {"timeline", "relations", "reasoning", "map", "export", "compile", "evidence"}
)

DEFAULT_BUDGET: dict[str, Any] = {
    "max_turns": 12,
    "network_retries": 2,
    "structural_repair_attempts": 5,
}


class ChatReferenceValidationError(RuntimeError):
    """A structured chat candidate references IDs outside the frozen CaseFile.

    The Worker uses the structured fields for one controlled provider repair
    call before failing the TaskRun.
    """

    def __init__(
        self,
        *,
        object_ids: tuple[str, ...] = (),
        event_ids: tuple[str, ...] = (),
        issue_ids: tuple[str, ...] = (),
    ) -> None:
        self.object_ids = tuple(object_ids)
        self.event_ids = tuple(event_ids)
        self.issue_ids = tuple(issue_ids)
        self.repair_attempted = False
        parts = [
            f"objects={sorted(self.object_ids)!r}",
            f"events={sorted(self.event_ids)!r}",
            f"validation_issues={sorted(self.issue_ids)!r}",
        ]
        super().__init__("Chat result references unknown IDs: " + ", ".join(parts))


_append_event = append_task_event

_event_view = event_view

_provider_view = provider_view

_source_view = source_view

_task_view = task_view

_time = time_view


def _chat_context_policy_version() -> str:
    """Return the default or opted-in chat context policy version.

    ``casefile-chat-context-v6`` is the accepted default (pairs with the
    public-language hardened ``casefile-chat-v16`` and ``casefile-chat-tools-v4``).
    ``CASEFILE_CHAT_CONTEXT_ROLLOUT=casefile-chat-context-v5`` restores the
    structured-audit policy (paired with ``casefile-chat-v9``), ``...v4``
    restores the audit prompt policy (paired with ``casefile-chat-v8``),
    ``...v3`` restores the Phase 4 policy (paired with ``casefile-chat-v7``),
    ``...v2`` opts in to the Phase 3 rolling Thread Memory policy, ``...v1``
    restores the frozen Phase 2 policy (paired with ``casefile-chat-v4``),
    and ``agent-focus-v1`` forces the legacy policy (paired with the legacy
    prompt render in ``_new_task``) for rollback. Unknown rollout values are
    ignored.
    """

    rollout = os.environ.get("CASEFILE_CHAT_CONTEXT_ROLLOUT")
    if rollout == LEGACY_CONTEXT_POLICY_VERSION:
        return LEGACY_CONTEXT_POLICY_VERSION
    if rollout == CHAT_CONTEXT_POLICY_VERSION:
        return CHAT_CONTEXT_POLICY_VERSION
    if rollout == CHAT_CONTEXT_POLICY_V2_VERSION:
        return CHAT_CONTEXT_POLICY_V2_VERSION
    if rollout == CHAT_CONTEXT_POLICY_V3_VERSION:
        return CHAT_CONTEXT_POLICY_V3_VERSION
    if rollout == CHAT_CONTEXT_POLICY_V4_VERSION:
        return CHAT_CONTEXT_POLICY_V4_VERSION
    if rollout == CHAT_CONTEXT_POLICY_V5_VERSION:
        return CHAT_CONTEXT_POLICY_V5_VERSION
    if rollout == CHAT_CONTEXT_POLICY_V6_VERSION:
        return CHAT_CONTEXT_POLICY_V6_VERSION
    return CHAT_CONTEXT_POLICY_V6_VERSION


chat_context_policy_version = _chat_context_policy_version


def _latest_context_state_ref(
    session: Session,
    *,
    project_id: int,
    thread_id: int,
) -> dict[str, Any] | None:
    """Freeze the latest Thread Memory state pointer for deterministic replay."""

    state = session.scalar(
        select(AgentThreadContextState)
        .where(
            AgentThreadContextState.project_id == project_id,
            AgentThreadContextState.thread_id == thread_id,
        )
        .order_by(AgentThreadContextState.id.desc())
        .limit(1)
    )
    if state is None:
        return None
    return {
        "state_id": int(state.id),
        "policy_version": state.policy_version,
        "state_kind": state.state_kind,
        "from_message_seq": state.from_message_seq,
        "to_message_seq": state.to_message_seq,
        "input_hash": state.input_hash,
    }


def _json_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _supported_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ApplicationError(
            "provider_not_supported",
            f"不支持的模型服务：{provider}。",
            status_code=422,
            details={"supported_providers": sorted(SUPPORTED_PROVIDERS)},
        )
    return normalized


__all__ = [
    "ChatReferenceValidationError",
    "DEFAULT_BUDGET",
    "DEFAULT_MODEL",
    "DEFAULT_PROVIDER",
    "SUPPORTED_CHAT_VIEWS",
    "SUPPORTED_PROVIDERS",
    "chat_context_policy_version",
]
