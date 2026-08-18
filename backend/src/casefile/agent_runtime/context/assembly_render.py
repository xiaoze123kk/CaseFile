"""Project assembled context blocks into the v2 chat executor prompt contract."""

from __future__ import annotations

from typing import Any

from casefile.agent_runtime.context.models import ContextAssembly

CHAT_CONTEXT_PROMPT_VERSION = "casefile-chat-v4"
#: Phase 3 prompt package paired with ``casefile-chat-context-v2``.
CHAT_CONTEXT_PROMPT_V2_VERSION = "casefile-chat-v5"
#: Phase 4 prompt package originally shipped with ``casefile-chat-context-v3``.
CHAT_CONTEXT_PROMPT_V3_VERSION = "casefile-chat-v6"
#: Phase 4 v3-fix prompt package now paired with ``casefile-chat-context-v3``.
CHAT_CONTEXT_PROMPT_V4_VERSION = "casefile-chat-v7"
#: Audit prompt package paired with ``casefile-chat-context-v4``.
CHAT_CONTEXT_PROMPT_V5_VERSION = "casefile-chat-v8"
#: Structured audit findings prompt package paired with ``casefile-chat-context-v5``.
CHAT_CONTEXT_PROMPT_V6_VERSION = "casefile-chat-v9"
_REQUIRED_BLOCK_IDS = frozenset(
    {
        "author_message",
        "casefile_skeleton",
        "focus_objects",
        "thread_history",
        "validation_issues",
    }
)


def chat_input_payload_from_assembly(
    assembly: ContextAssembly,
    *,
    require_thread_memory: bool = False,
    dashboard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map countable context blocks to the ``ChatExecutorInputV2`` contract.

    The rendered v4/v5 prompt packages validate this payload through the typed
    input contract, so a missing or malformed block fails before the provider
    call instead of silently changing what the model sees. Phase 3 policies
    additionally require the ``thread_memory`` block; ``dashboard`` embeds the
    read-only runtime guardrail view consumed by the Phase 4 executor
    instructions.
    """

    blocks = {block.id: block.payload for block in assembly.blocks}
    required_ids = set(_REQUIRED_BLOCK_IDS)
    if require_thread_memory:
        required_ids.add("thread_memory")
    missing = sorted(required_ids - set(blocks))
    if missing:
        raise ValueError(
            f"Context assembly {assembly.policy_version!r} is missing blocks: {missing!r}"
        )
    routing = blocks.get("routing")
    payload: dict[str, Any] = {
        "input_hash": blocks.get("input_hash", ""),
        "casefile": blocks.get("casefile_skeleton", {}),
        "focus_objects": blocks.get("focus_objects", {}),
        "thread_history": blocks.get("thread_history", []),
        "thread_memory": blocks.get("thread_memory"),
        "author_message": blocks.get("author_message", ""),
        "editable_fields_by_collection": blocks.get("editable_fields_by_collection", {}),
        "focus": blocks.get("focus", {}),
        "validation": blocks.get("validation", {}),
        "validation_issues": blocks.get("validation_issues", []),
        "routing": routing if isinstance(routing, dict) and routing else None,
    }
    if dashboard is not None:
        payload["context_dashboard"] = dict(dashboard)
    return payload


__all__ = [
    "CHAT_CONTEXT_PROMPT_VERSION",
    "CHAT_CONTEXT_PROMPT_V2_VERSION",
    "CHAT_CONTEXT_PROMPT_V3_VERSION",
    "CHAT_CONTEXT_PROMPT_V4_VERSION",
    "CHAT_CONTEXT_PROMPT_V5_VERSION",
    "CHAT_CONTEXT_PROMPT_V6_VERSION",
    "chat_input_payload_from_assembly",
]
