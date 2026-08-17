"""Byte-identical legacy adapters for pre-context-pipeline chat TaskRuns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from casefile.agent_runtime.context.models import (
    ContextBlock,
    ContextDecision,
    StageResult,
)
from casefile.agent_runtime.context.protocols import ContextRun
from casefile.agent_runtime.models import (
    CaseFileChatRequest,
    chat_routing_payload_as_dict,
)


def legacy_chat_routing_payload(request: CaseFileChatRequest) -> dict[str, Any] | None:
    """Backward-compatible alias for the shared routing serialization helper."""

    return chat_routing_payload_as_dict(request)


@dataclass(slots=True)
class LegacyChatInputStage:
    """Wrap the already-rendered legacy input into one measurable block.

    Phase 0 does not change what the model sees: the Worker renders the same
    legacy input string providers already use and passes it as
    ``prebuilt_input``. This stage only estimates and audits it. Phase 2
    sources replace this stage through new policy versions.
    """

    name: str = "legacy_full_injection_v1"
    version: str = "legacy-full-injection-v1"
    capabilities: frozenset[str] = frozenset({"source", "chat", "legacy"})

    def can_run(self, run: ContextRun) -> bool:
        return run.prebuilt_input is not None

    def run(self, run: ContextRun) -> StageResult:
        input_text = run.prebuilt_input
        if input_text is None:
            return StageResult(
                decisions=(
                    ContextDecision(
                        stage="legacy_chat_input",
                        code="legacy_input_missing",
                        detail="prebuilt_input is required by the legacy policy",
                    ),
                ),
            )
        tokens = run.estimator.estimate(input_text)
        return StageResult(
            added=(
                ContextBlock(
                    id="legacy_chat_input",
                    kind="legacy_chat_input",
                    payload=input_text,
                    tokens=tokens,
                    metadata={"input_hash": run.input_hash},
                ),
            ),
            metrics={"tokens": tokens},
        )


__all__ = ["LegacyChatInputStage", "legacy_chat_routing_payload"]
