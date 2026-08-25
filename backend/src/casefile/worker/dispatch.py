"""Fail-closed TaskRun type to handler dispatch."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from casefile.worker.execution import ProviderRequirement, TaskExecutionContext

SUPPORTED_TASK_TYPES = frozenset(
    {
        "brief_polish",
        "brief_anchor_extract",
        "brief_intake_questions",
        "brief_intake_synthesize",
        "brief_strategy_options",
        "brief_to_draft",
        "casefile_chat",
        "reverse_parse",
    }
)


class TaskHandler(Protocol):
    task_types: frozenset[str]
    provider_requirement: ProviderRequirement

    def execute(self, context: TaskExecutionContext) -> None: ...


class TaskDispatcher:
    def __init__(
        self,
        handlers: Iterable[TaskHandler],
        *,
        expected_task_types: frozenset[str] = SUPPORTED_TASK_TYPES,
    ) -> None:
        registry: dict[str, TaskHandler] = {}
        for handler in handlers:
            if not handler.task_types:
                raise ValueError("TaskHandler must declare at least one task type")
            for task_type in handler.task_types:
                if task_type in registry:
                    raise ValueError(f"Duplicate Worker handler registration: {task_type}")
                registry[task_type] = handler
        actual = frozenset(registry)
        if actual != expected_task_types:
            missing = sorted(expected_task_types - actual)
            unexpected = sorted(actual - expected_task_types)
            raise ValueError(
                "Worker handler registry does not match executable task types: "
                f"missing={missing}, unexpected={unexpected}"
            )
        self._registry = registry

    @property
    def task_types(self) -> frozenset[str]:
        return frozenset(self._registry)

    def resolve(self, task_type: str) -> TaskHandler:
        handler = self._registry.get(task_type)
        if handler is None:
            raise RuntimeError(f"Unsupported TaskRun type: {task_type}")
        return handler


__all__ = ["SUPPORTED_TASK_TYPES", "TaskDispatcher", "TaskHandler"]
