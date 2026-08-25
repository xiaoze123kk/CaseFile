"""Shared contracts for one claimed Worker task execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session, sessionmaker

from casefile.agent_runtime import AgentProvider
from casefile.data_postgres.models import TaskRun

ProviderRequirement = Literal["required", "none"]
EventEmitter = Callable[[int, str, str, dict[str, Any]], None]


@dataclass(slots=True)
class ExecutionState:
    """Mutable audit state shared with the runtime exception boundary."""

    candidate: dict[str, Any] | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    sensitive_values: tuple[str, ...] = field(default_factory=tuple, repr=False)


@dataclass(frozen=True, slots=True)
class TaskExecutionContext:
    """Detached frozen task plus narrowly scoped runtime services.

    ``session_factory`` opens short transactions on demand.  A live Session is
    deliberately never carried across Provider calls.
    """

    task: TaskRun
    attempt_id: int
    session_factory: sessionmaker[Session] = field(repr=False)
    config: Any = field(repr=False)
    emit: EventEmitter = field(repr=False)
    state: ExecutionState = field(repr=False)
    provider: AgentProvider | None = field(default=None, repr=False)
    api_key: str | None = field(default=None, repr=False)

    def require_provider(self) -> tuple[AgentProvider, str]:
        if self.provider is None or self.api_key is None:
            raise RuntimeError(f"Task handler requires Provider access: {self.task.task_type}")
        return self.provider, self.api_key


__all__ = [
    "EventEmitter",
    "ExecutionState",
    "ProviderRequirement",
    "TaskExecutionContext",
]
