"""Immutable strategy registry for the context pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from casefile.agent_runtime.context.models import ContextPolicy
from casefile.agent_runtime.context.protocols import ContextStage
from casefile.agent_runtime.context.strategies.legacy import LegacyChatInputStage
from casefile.agent_runtime.context.strategies.selectors.history_window import (
    HistoryWindowStage,
)
from casefile.agent_runtime.context.strategies.sources.casefile_skeleton import (
    CaseFileSkeletonStage,
)
from casefile.agent_runtime.context.strategies.sources.chat_contract import ChatContractStage
from casefile.agent_runtime.context.strategies.sources.focus_objects import FocusObjectsStage
from casefile.agent_runtime.context.strategies.sources.thread_memory import ThreadMemoryStage
from casefile.agent_runtime.context.strategies.transformers.validation_trim import (
    ValidationTrimStage,
)


class ContextRegistryError(RuntimeError):
    """A strategy registration or lookup contract violation."""


@dataclass(slots=True)
class ContextRegistry:
    """Name-addressed strategy table; same name may only bind one version."""

    _stages: dict[str, ContextStage] = field(default_factory=dict)

    def register(self, stage: ContextStage) -> None:
        existing = self._stages.get(stage.name)
        if existing is not None and existing.version != stage.version:
            raise ContextRegistryError(
                f"Context strategy {stage.name!r} is already registered at "
                f"version {existing.version!r}; cannot register {stage.version!r}"
            )
        self._stages[stage.name] = stage

    def get(self, name: str) -> ContextStage:
        stage = self._stages.get(name)
        if stage is None:
            raise ContextRegistryError(f"Unknown context strategy: {name!r}")
        return stage

    def missing_strategies(self, policy: ContextPolicy) -> tuple[str, ...]:
        return tuple(
            policy_stage.strategy
            for policy_stage in policy.stages
            if policy_stage.strategy not in self._stages
        )

    def names(self) -> tuple[str, ...]:
        return tuple(self._stages)


def default_context_registry() -> ContextRegistry:
    """Build the registry with the strategies shipped with this runtime."""

    registry = ContextRegistry()
    registry.register(LegacyChatInputStage())
    registry.register(CaseFileSkeletonStage())
    registry.register(FocusObjectsStage())
    registry.register(HistoryWindowStage())
    registry.register(ThreadMemoryStage())
    registry.register(ValidationTrimStage())
    registry.register(ChatContractStage())
    return registry


__all__ = [
    "ContextRegistry",
    "ContextRegistryError",
    "default_context_registry",
]
