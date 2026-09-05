"""Worker handler for deterministic and optionally model-assisted compilation."""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from casefile.worker.execution import ProviderRequirement, TaskExecutionContext
from casefile.worker.executors.compiler import CompilerExecutor
from casefile.worker.executors.prose_store import ProseLeaseLost


class CompilerHandler:
    task_types = frozenset({"novel_compile"})
    provider_requirement: ProviderRequirement = "none"

    def __init__(self, executor: CompilerExecutor) -> None:
        self._executor = executor

    def execute(self, context: TaskExecutionContext) -> None:
        try:
            self._executor.execute(context.task.id, context.attempt_id)
        except ProseLeaseLost:
            return
        except SQLAlchemyError:
            if not context.task.input_jsonb.get("prose_renderer_shadow"):
                raise
            # Storage cannot certify completion; leave the task recoverable by lease.
            return


__all__ = ["CompilerHandler"]
