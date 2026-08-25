"""Focused regression tests for Worker attempt recovery."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from casefile.worker.dispatch import SUPPORTED_TASK_TYPES, TaskDispatcher
from casefile.worker.execution import ExecutionState, ProviderRequirement, TaskExecutionContext
from casefile.worker.generation_reuse import previous_attempt_failed_steps
from casefile.worker.runtime import Worker, WorkerConfig


class _ProbeHandler:
    task_types = frozenset({"probe"})
    provider_requirement: ProviderRequirement = "none"

    def __init__(self) -> None:
        self.called = False

    def execute(self, context: TaskExecutionContext) -> None:
        provider, api_key = context.provider, context.api_key
        assert provider is None
        assert api_key is None
        self.called = True


def test_general_mutation_mode_defaults_off_and_rejects_invalid_value() -> None:
    assert WorkerConfig(worker_id="test").general_mutation_mode == "off"
    try:
        WorkerConfig(worker_id="test", general_mutation_mode="invalid")  # type: ignore[arg-type]
    except ValueError as error:
        assert "CASEFILE_CHAT_GENERAL_MUTATION_MODE" in str(error)
    else:
        raise AssertionError("invalid General Mutation mode was accepted")


def test_worker_environment_defaults_enable_all_general_mutation_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CASEFILE_CHAT_GENERAL_MUTATION_MODE", raising=False)
    monkeypatch.delenv("CASEFILE_CHAT_GENERAL_MUTATION_CREATE_ENABLED", raising=False)
    monkeypatch.delenv("CASEFILE_CHAT_GENERAL_MUTATION_DELETE_ENABLED", raising=False)

    config = WorkerConfig.from_environment()

    assert config.general_mutation_mode == "suggest"
    assert config.general_mutation_create_enabled is True
    assert config.general_mutation_delete_enabled is True


def test_worker_environment_can_disable_all_general_mutation_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CASEFILE_CHAT_GENERAL_MUTATION_MODE", "off")
    monkeypatch.setenv("CASEFILE_CHAT_GENERAL_MUTATION_CREATE_ENABLED", "false")
    monkeypatch.setenv("CASEFILE_CHAT_GENERAL_MUTATION_DELETE_ENABLED", "false")

    config = WorkerConfig.from_environment()

    assert config.general_mutation_mode == "off"
    assert config.general_mutation_create_enabled is False
    assert config.general_mutation_delete_enabled is False


def test_previous_attempt_failed_steps_uses_only_last_generation_run() -> None:
    previous_attempt = SimpleNamespace(id=31)
    stale_gate_failure = SimpleNamespace(
        id=101,
        component_id="quality_repair_gate",
        execution_no=5,
        status="failed",
    )
    latest_context = SimpleNamespace(
        id=102,
        component_id="context_pack_builder",
        execution_no=3,
        status="succeeded",
    )
    repaired_planner = SimpleNamespace(
        id=103,
        component_id="case_blueprint_planner",
        execution_no=3,
        status="succeeded",
    )
    latest_temporal_failure = SimpleNamespace(
        id=104,
        component_id="temporal_structure_planner",
        execution_no=3,
        status="failed",
    )
    session = MagicMock()
    session.scalar.return_value = previous_attempt
    session.scalars.return_value = [
        stale_gate_failure,
        latest_context,
        repaired_planner,
        latest_temporal_failure,
    ]
    task = SimpleNamespace(id=17, attempt_count=2)

    failed_steps = previous_attempt_failed_steps(session, task)  # type: ignore[arg-type]

    assert failed_steps == [latest_temporal_failure]


def test_previous_attempt_failed_steps_returns_empty_without_failures() -> None:
    session = MagicMock()
    session.scalar.return_value = SimpleNamespace(id=31)
    session.scalars.return_value = [
        SimpleNamespace(
            id=101,
            component_id="context_pack_builder",
            execution_no=1,
            status="succeeded",
        ),
    ]
    task = SimpleNamespace(id=17, attempt_count=2)

    assert previous_attempt_failed_steps(session, task) == []  # type: ignore[arg-type]


def test_dispatcher_rejects_duplicate_unknown_and_incomplete_registries() -> None:
    first = _ProbeHandler()
    second = _ProbeHandler()
    with pytest.raises(ValueError, match="Duplicate Worker handler registration"):
        TaskDispatcher((first, second), expected_task_types=frozenset({"probe"}))
    with pytest.raises(ValueError, match="missing=.*other"):
        TaskDispatcher((first,), expected_task_types=frozenset({"probe", "other"}))
    dispatcher = TaskDispatcher((first,), expected_task_types=frozenset({"probe"}))
    with pytest.raises(RuntimeError, match="Unsupported TaskRun type: unknown"):
        dispatcher.resolve("unknown")


def test_providerless_handler_executes_without_provider_access() -> None:
    handler = _ProbeHandler()
    dispatcher = TaskDispatcher((handler,), expected_task_types=frozenset({"probe"}))
    context = TaskExecutionContext(
        task=SimpleNamespace(task_type="probe"),  # type: ignore[arg-type]
        attempt_id=1,
        session_factory=MagicMock(),
        config=SimpleNamespace(),
        emit=MagicMock(),
        state=ExecutionState(),
    )

    dispatcher.resolve("probe").execute(context)

    assert handler.called is True


def test_worker_registers_exact_task_types_without_eager_provider_creation() -> None:
    provider_factory = MagicMock()
    worker = Worker(
        MagicMock(),
        config=WorkerConfig(worker_id="dispatch-test"),
        provider_factory=provider_factory,
    )

    assert worker._dispatcher.task_types == SUPPORTED_TASK_TYPES
    provider_factory.assert_not_called()


def test_unknown_task_fails_before_provider_resolution() -> None:
    worker = Worker(MagicMock(), config=WorkerConfig(worker_id="dispatch-test"))
    resolver = MagicMock()
    cancel = MagicMock(return_value=False)
    fail = MagicMock()
    worker._load_task_snapshot = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(task_type="unknown")
    )
    worker._provider_resolver = resolver
    worker._cancel = cancel  # type: ignore[method-assign]
    worker._fail = fail  # type: ignore[method-assign]

    worker._execute(1, 2)

    resolver.resolve.assert_not_called()
    failure = fail.call_args.args[2]
    assert isinstance(failure, RuntimeError)
    assert str(failure) == "Unsupported TaskRun type: unknown"
