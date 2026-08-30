"""Shared feature gates for immutable CaseFile Chat prompt packages."""

from typing import Final

SAFE_PATCH_PROMPT_VERSIONS: Final = frozenset(
    {
        "casefile-chat-v15",
        "casefile-chat-v16",
        "casefile-chat-v17",
        "casefile-chat-v18",
        "casefile-chat-v19",
        "casefile-chat-v20",
    }
)
PUBLIC_LANGUAGE_PROMPT_VERSIONS: Final = frozenset(
    {
        "casefile-chat-v16",
        "casefile-chat-v17",
        "casefile-chat-v18",
        "casefile-chat-v19",
        "casefile-chat-v20",
    }
)


__all__ = ["PUBLIC_LANGUAGE_PROMPT_VERSIONS", "SAFE_PATCH_PROMPT_VERSIONS"]
