"""Stable Worker composition root.

Owns only the claim -> resolve -> execute -> finalize loop and public Worker
configuration. Task-specific execution belongs to registered handlers.
"""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile.application.closure_repair import ClosureRepairMode
from casefile.data_postgres.models import TaskRun
from casefile.worker.dispatch import TaskDispatcher
from casefile.worker.execution import (
    ChatRuntimeConfig,
    ExecutionState,
    GoalSafePointObserver,
    TaskExecutionContext,
    WorkerEventPorts,
)
from casefile.worker.executors.chat import ChatTaskExecutor
from casefile.worker.executors.compiler import CompilerExecutor
from casefile.worker.executors.completion import CompletionExecutor
from casefile.worker.failures import TaskCancellationRequested, merge_numeric_usage
from casefile.worker.finalization import TaskFinalizer
from casefile.worker.handlers import (
    AuxiliaryBriefHandler,
    BriefGenerationHandler,
    BriefIntakeHandler,
    ChatHandler,
    CompilerHandler,
    ReverseParseHandler,
)
from casefile.worker.provider_resolution import (
    ProviderFactory,
    ProviderResolver,
    provider_for_task,
)
from casefile.worker.queue import TaskQueue

GeneralMutationMode = Literal["off", "shadow", "suggest"]


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    worker_id: str
    poll_seconds: float = 1.0
    lease_seconds: int = 600
    closure_repair_mode: ClosureRepairMode = "shadow"
    general_mutation_mode: GeneralMutationMode = "off"
    general_mutation_create_enabled: bool = False
    general_mutation_delete_enabled: bool = False

    def __post_init__(self) -> None:
        if self.closure_repair_mode not in {"off", "shadow", "suggest"}:
            raise ValueError("CLOSURE_REPAIR_MODE must be one of: off, shadow, suggest")
        if self.general_mutation_mode not in {"off", "shadow", "suggest"}:
            raise ValueError(
                "CASEFILE_CHAT_GENERAL_MUTATION_MODE must be one of: off, shadow, suggest"
            )

    @classmethod
    def from_environment(cls) -> WorkerConfig:
        default_id = f"{socket.gethostname()}-{os.getpid()}"
        return cls(
            worker_id=os.environ.get("CASEFILE_WORKER_ID", default_id),
            poll_seconds=float(os.environ.get("CASEFILE_WORKER_POLL_SECONDS", "1")),
            lease_seconds=int(os.environ.get("CASEFILE_WORKER_LEASE_SECONDS", "600")),
            closure_repair_mode=cast(
                ClosureRepairMode,
                os.environ.get("CLOSURE_REPAIR_MODE", "shadow").strip().lower(),
            ),
            general_mutation_mode=cast(
                GeneralMutationMode,
                os.environ.get("CASEFILE_CHAT_GENERAL_MUTATION_MODE", "suggest").strip().lower(),
            ),
            general_mutation_create_enabled=(
                os.environ.get("CASEFILE_CHAT_GENERAL_MUTATION_CREATE_ENABLED", "true")
                .strip()
                .lower()
                in {"1", "true", "yes", "on"}
            ),
            general_mutation_delete_enabled=(
                os.environ.get("CASEFILE_CHAT_GENERAL_MUTATION_DELETE_ENABLED", "true")
                .strip()
                .lower()
                in {"1", "true", "yes", "on"}
            ),
        )


class Worker:
    """Consume TaskRuns with PostgreSQL leasing; one instance executes serially."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        config: WorkerConfig,
        provider_factory: ProviderFactory | None = None,
        goal_safe_point_observer: GoalSafePointObserver | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.config = config
        self.provider_factory = provider_factory or provider_for_task
        self._chat_config = ChatRuntimeConfig(
            closure_repair_mode=config.closure_repair_mode,
            general_mutation_mode=config.general_mutation_mode,
            general_mutation_create_enabled=config.general_mutation_create_enabled,
            general_mutation_delete_enabled=config.general_mutation_delete_enabled,
        )

        self._queue = TaskQueue(
            session_factory,
            worker_id=config.worker_id,
            lease_seconds=config.lease_seconds,
        )
        self._finalizer = TaskFinalizer(
            session_factory,
            worker_id=config.worker_id,
            lease_seconds=config.lease_seconds,
        )
        event_ports = WorkerEventPorts(
            emit=self._emit,
            emit_after_completion=self._emit_after_completion,
        )
        self._completion = CompletionExecutor(
            session_factory,
            worker_id=config.worker_id,
            emit=event_ports.emit,
        )
        self._chat = ChatTaskExecutor(
            session_factory,
            config=self._chat_config,
            events=event_ports,
        )
        self._compiler = CompilerExecutor(
            session_factory,
            worker_id=config.worker_id,
            provider_factory=self.provider_factory,
            completion=self._completion,
        )
        self._provider_resolver = ProviderResolver(session_factory, self.provider_factory)
        self._dispatcher = TaskDispatcher(
            (
                AuxiliaryBriefHandler(self._completion),
                BriefIntakeHandler(self._completion),
                ReverseParseHandler(self._completion),
                BriefGenerationHandler(self._completion),
                ChatHandler(
                    self._chat,
                    self._complete_chat,
                    goal_safe_point_observer=goal_safe_point_observer,
                ),
                CompilerHandler(self._compiler),
            )
        )

    def run_forever(self) -> None:
        while True:
            if not self.run_once():
                time.sleep(self.config.poll_seconds)

    def run_once(self, *, task_run_id: int | None = None) -> bool:
        """Run one claim, optionally restricted to an already known TaskRun."""

        claimed = (
            self._claim_next()
            if task_run_id is None
            else self._queue._claim_specific(task_run_id)
        )
        if claimed is None:
            return False
        if claimed == "cancelled":
            return True
        self._execute(*claimed)
        return True

    def _execute(self, task_run_id: int, attempt_id: int) -> None:
        state = ExecutionState()
        try:
            task = self._load_task_snapshot(task_run_id)
            handler = self._dispatcher.resolve(task.task_type)
            provider = None
            api_key = None
            if handler.provider_requirement == "required":
                resolved = self._provider_resolver.resolve(task)
                provider = resolved.provider
                api_key = resolved.api_key
                state.sensitive_values = (api_key,)
            handler.execute(
                TaskExecutionContext(
                    task=task,
                    attempt_id=attempt_id,
                    session_factory=self.session_factory,
                    chat_config=self._chat_config,
                    emit=self._emit,
                    state=state,
                    provider=provider,
                    api_key=api_key,
                )
            )
        except TaskCancellationRequested:
            self._cancel(
                task_run_id,
                attempt_id,
                usage=state.usage,
                validation_errors=state.validation_errors,
            )
        except Exception as error:
            provider_usage = getattr(error, "usage", None)
            if isinstance(provider_usage, dict):
                state.usage = merge_numeric_usage(state.usage, provider_usage)
            provider_tools = getattr(error, "tools", None)
            if provider_tools is not None and hasattr(provider_tools, "as_dict"):
                state.usage["tool_metrics"] = provider_tools.as_dict()
            if self._cancel(
                task_run_id,
                attempt_id,
                usage=state.usage,
                validation_errors=state.validation_errors,
            ):
                return
            self._fail(
                task_run_id,
                attempt_id,
                error,
                candidate=state.candidate,
                usage=state.usage,
                validation_errors=state.validation_errors,
                sensitive_values=state.sensitive_values,
            )

    def _load_task_snapshot(self, task_run_id: int) -> TaskRun:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(TaskRun).where(TaskRun.id == task_run_id))
            if task is None or task.status != "running" or task.leased_by != self.config.worker_id:
                raise RuntimeError("TaskRun lease is no longer owned by this worker")
            session.expunge(task)
            return task

    # Thin compatibility seams used by integration tests and benchmark executors.
    def _claim_next(self) -> tuple[int, int] | Literal["cancelled"] | None:
        return self._queue._claim_next()

    def _cancel(
        self,
        task_run_id: int,
        attempt_id: int,
        *,
        usage: dict[str, Any],
        validation_errors: list[dict[str, Any]],
    ) -> bool:
        return self._finalizer._cancel(
            task_run_id,
            attempt_id,
            usage=usage,
            validation_errors=validation_errors,
        )

    def _fail(
        self,
        task_run_id: int,
        attempt_id: int,
        error: Exception,
        *,
        candidate: dict[str, Any] | None,
        usage: dict[str, Any],
        validation_errors: list[dict[str, Any]],
        sensitive_values: tuple[str, ...],
    ) -> None:
        self._finalizer._fail(
            task_run_id,
            attempt_id,
            error,
            candidate=candidate,
            usage=usage,
            validation_errors=validation_errors,
            sensitive_values=sensitive_values,
        )

    def _emit(
        self,
        task_run_id: int,
        event_type: str,
        stage: str,
        payload: dict[str, Any],
    ) -> None:
        self._finalizer._emit(task_run_id, event_type, stage, payload)

    def _emit_after_completion(
        self,
        task_run_id: int,
        event_type: str,
        stage: str,
        payload: dict[str, Any],
    ) -> None:
        self._finalizer._emit_after_completion(task_run_id, event_type, stage, payload)

    def _complete_chat(self, *args: Any, **kwargs: Any) -> None:
        self._chat._complete_chat(*args, **kwargs)


__all__ = ["Worker", "WorkerConfig", "provider_for_task"]
