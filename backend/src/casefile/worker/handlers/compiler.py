"""Worker handler for deterministic and optionally model-assisted compilation."""

from __future__ import annotations

from casefile.worker.execution import ProviderRequirement, TaskExecutionContext
from casefile.worker.executors.compiler import CompilerExecutor


class CompilerHandler:
    task_types = frozenset({"novel_compile"})
    provider_requirement: ProviderRequirement = "none"

    def __init__(self, executor: CompilerExecutor) -> None:
        self._executor = executor

    def execute(self, context: TaskExecutionContext) -> None:
        self._executor.execute(context.task.id, context.attempt_id)


__all__ = ["CompilerHandler"]
