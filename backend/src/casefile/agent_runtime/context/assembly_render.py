"""Project assembled context blocks into the v2 chat executor prompt contract."""

from __future__ import annotations

from typing import Any

from casefile.agent_runtime.context.models import ContextAssembly

CHAT_CONTEXT_PROMPT_VERSION = "casefile-chat-v4"
_REQUIRED_BLOCK_IDS = frozenset(
    {
        "author_message",
        "casefile_skeleton",
        "focus_objects",
        "thread_history",
        "validation_issues",
    }
)


def chat_input_payload_from_assembly(assembly: ContextAssembly) -> dict[str, Any]:
    """Map countable context blocks to ``ChatExecutorInputV2`` fields.

    The rendered v4 prompt package validates this payload through the typed
    input contract, so a missing or malformed block fails before the provider
    call instead of silently changing what the model sees.
    """

    blocks = {block.id: block.payload for block in assembly.blocks}
    missing = sorted(_REQUIRED_BLOCK_IDS - set(blocks))
    if missing:
        raise ValueError(
            f"Context assembly {assembly.policy_version!r} is missing blocks: {missing!r}"
        )
    routing = blocks.get("routing")
    return {
        "input_hash": blocks.get("input_hash", ""),
        "casefile": blocks.get("casefile_skeleton", {}),
        "focus_objects": blocks.get("focus_objects", {}),
        "thread_history": blocks.get("thread_history", []),
        "author_message": blocks.get("author_message", ""),
        "editable_fields_by_collection": blocks.get("editable_fields_by_collection", {}),
        "focus": blocks.get("focus", {}),
        "validation": blocks.get("validation", {}),
        "validation_issues": blocks.get("validation_issues", []),
        "routing": routing if isinstance(routing, dict) and routing else None,
    }


__all__ = [
    "CHAT_CONTEXT_PROMPT_VERSION",
    "chat_input_payload_from_assembly",
]
