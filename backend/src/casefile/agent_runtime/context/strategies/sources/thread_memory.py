"""Context stage binding the rolling Thread Memory state into the layout."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from casefile.agent_runtime.context.estimators import estimate_jsonable_tokens
from casefile.agent_runtime.context.models import ContextBlock, StageResult
from casefile.agent_runtime.context.protocols import ContextRun
from casefile.agent_runtime.context.thread_memory import (
    ChatThreadMemoryState,
    thread_memory_state_to_jsonable,
)


@dataclass(slots=True)
class ThreadMemoryStage:
    """Emit the ``thread_memory`` block from the frozen context state ref."""

    name: str = "thread_memory_v1"
    version: str = "thread-memory-v1"
    capabilities: frozenset[str] = frozenset(
        {"source", "chat", "deterministic", "compacted_state"}
    )

    def can_run(self, run: ContextRun) -> bool:
        return isinstance(run.extra_input.get("thread_memory_state"), dict)

    def run(self, run: ContextRun) -> StageResult:
        raw_state = run.extra_input.get("thread_memory_state")
        if not isinstance(raw_state, dict):
            return StageResult()
        state = ChatThreadMemoryState.model_validate(raw_state)
        payload: dict[str, Any] = thread_memory_state_to_jsonable(state)
        return StageResult(
            added=(
                ContextBlock(
                    id="thread_memory",
                    kind="thread_memory",
                    payload=payload,
                    tokens=estimate_jsonable_tokens(payload, run.estimator),
                    trimmable=True,
                    metadata={
                        "last_compacted_message_seq": state.last_compacted_message_seq,
                        "constraint_count": len(state.constraints),
                        "verified_fact_count": len(state.verified_facts),
                    },
                ),
            ),
            metrics={
                "last_compacted_message_seq": state.last_compacted_message_seq,
                "constraint_count": len(state.constraints),
                "verified_fact_count": len(state.verified_facts),
            },
        )


__all__ = ["ThreadMemoryStage"]
