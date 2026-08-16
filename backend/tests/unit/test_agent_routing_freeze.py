"""Routing-hint freeze contract tests (service behavior covered by Postgres tests)."""

from __future__ import annotations

import hashlib

import rfc8785

from casefile.agent_runtime.chat_intent import (
    INTENT_ROUTER_VERSION,
    normalize_routing_hint,
)


def _json_hash(value: dict) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def test_hint_normalization_produces_only_the_frozen_shapes() -> None:
    assert normalize_routing_hint(
        {"entrypoint": "preset", "preset_id": " inspect "}
    ) == {"entrypoint": "preset", "preset_id": "inspect"}
    assert normalize_routing_hint({"entrypoint": "issue_action"}) == {
        "entrypoint": "issue_action",
        "preset_id": None,
    }
    assert normalize_routing_hint({"entrypoint": "preset", "preset_id": "stale"}) == {
        "entrypoint": "free_text",
        "preset_id": None,
    }


def test_input_hash_covers_routing_hint_and_router_version() -> None:
    frozen_without_hint = {
        "message": "检查卷宗。",
        "focus": {"object_ids": [], "event_ids": [], "validation_issue_ids": []},
    }
    frozen_with_hint = {
        **frozen_without_hint,
        "routing_hint": {"entrypoint": "preset", "preset_id": "inspect"},
        "router_version": INTENT_ROUTER_VERSION,
    }

    assert _json_hash(frozen_without_hint) != _json_hash(frozen_with_hint)
    assert _json_hash(frozen_with_hint) == _json_hash(
        {
            **frozen_with_hint,
            "routing_hint": {"entrypoint": "preset", "preset_id": "inspect"},
        }
    )


def test_router_version_constant_is_frozen() -> None:
    assert INTENT_ROUTER_VERSION == "casefile-chat-router-v2"
