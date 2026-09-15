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
        "casefile-chat-v21",
        "casefile-chat-v22",
        "casefile-chat-v23",
        "casefile-chat-v24",
        "casefile-chat-v25",
        "casefile-chat-v26",
        "casefile-chat-v27",
    }
)
PUBLIC_LANGUAGE_PROMPT_VERSIONS: Final = frozenset(
    {
        "casefile-chat-v16",
        "casefile-chat-v17",
        "casefile-chat-v18",
        "casefile-chat-v19",
        "casefile-chat-v20",
        "casefile-chat-v21",
        "casefile-chat-v22",
        "casefile-chat-v23",
        "casefile-chat-v24",
        "casefile-chat-v25",
        "casefile-chat-v26",
        "casefile-chat-v27",
    }
)


__all__ = ["PUBLIC_LANGUAGE_PROMPT_VERSIONS", "SAFE_PATCH_PROMPT_VERSIONS"]
