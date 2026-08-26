"""Focused regression tests for Worker attempt recovery."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from casefile.worker.runtime import WorkerConfig
from casefile.worker.support import previous_attempt_failed_steps


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

    failed_steps = previous_attempt_failed_steps(session, task)

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

    assert previous_attempt_failed_steps(session, task) == []
