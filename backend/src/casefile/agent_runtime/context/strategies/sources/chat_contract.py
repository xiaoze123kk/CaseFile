"""Chat executor contract source: the fixed fields shared by every executor route."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from casefile.agent_runtime.context.estimators import estimate_jsonable_tokens
from casefile.agent_runtime.context.models import ContextBlock, StageResult
from casefile.agent_runtime.context.protocols import ContextRun


def _validation_meta(validation: dict[str, Any]) -> dict[str, Any]:
    """Drop the duplicated issues list; ``validation_issues`` carries it trimmed."""

    return {key: value for key, value in validation.items() if key != "issues"}


def _jsonable_editable_fields(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[str]] = {}
    for collection, fields in value.items():
        if isinstance(collection, str) and isinstance(fields, (list, tuple, set)):
            result[collection] = [
                str(field)
                for field in fields
                if isinstance(field, str) and field
            ]
    return result


@dataclass(slots=True)
class ChatContractStage:
    """Emit the v2 prompt-contract fields as countable blocks."""

    name: str = "chat_contract_v2"
    version: str = "chat-contract-v2"
    capabilities: frozenset[str] = frozenset({"source", "chat", "contract", "deterministic"})

    def can_run(self, run: ContextRun) -> bool:
        return True

    def run(self, run: ContextRun) -> StageResult:
        message = run.frozen_input.get("message")
        author_message = str(message) if isinstance(message, str) else ""
        focus = run.frozen_input.get("focus")
        validation = run.frozen_input.get("validation")
        editable = _jsonable_editable_fields(run.extra_input.get("editable_fields_by_collection"))
        routing = run.routing if isinstance(run.routing, dict) else {}
        payloads: list[tuple[str, str, Any]] = [
            ("input_hash", "chat_input_hash", run.input_hash),
            ("author_message", "author_message", author_message),
            ("editable_fields_by_collection", "editable_fields", editable),
            ("focus", "focus", focus if isinstance(focus, dict) else {}),
            (
                "validation",
                "validation_meta",
                _validation_meta(validation) if isinstance(validation, dict) else {},
            ),
            ("routing", "routing", routing),
        ]
        blocks = tuple(
            ContextBlock(
                id=block_id,
                kind=kind,
                payload=payload,
                tokens=estimate_jsonable_tokens(payload, run.estimator),
            )
            for block_id, kind, payload in payloads
        )
        return StageResult(
            added=blocks,
            metrics={"author_message_chars": len(author_message)},
        )


__all__ = ["ChatContractStage"]
