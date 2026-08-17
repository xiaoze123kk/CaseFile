"""Plugin protocols for the extensible context pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from casefile.agent_runtime.context.models import (
    ContextBlock,
    ContextPolicy,
    StageResult,
)


@runtime_checkable
class TokenEstimator(Protocol):
    """Deterministic input-size estimation before a provider call."""

    name: str

    def estimate(self, text: str) -> int: ...

    def supports(self, provider: str, model_id: str) -> bool: ...


@dataclass(slots=True)
class ContextRun:
    """Mutable working set passed through the ordered pipeline stages."""

    policy: ContextPolicy
    frozen_input: dict[str, Any]
    input_hash: str
    estimator: TokenEstimator
    routing: dict[str, Any] | None = None
    prebuilt_input: str | None = None
    blocks: dict[str, ContextBlock] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ContextStage(Protocol):
    """One replaceable context pipeline strategy.

    Stages must stay deterministic for the same frozen inputs and version.
    Behavior changes require a new ``version`` registered under a new name.
    """

    name: str
    version: str
    capabilities: frozenset[str]

    def can_run(self, run: ContextRun) -> bool: ...

    def run(self, run: ContextRun) -> StageResult: ...


__all__ = ["ContextRun", "ContextStage", "TokenEstimator"]
