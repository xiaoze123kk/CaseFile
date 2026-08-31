from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _architecture_check_module() -> ModuleType:
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "check-backend-architecture.py"
    spec = importlib.util.spec_from_file_location("check_backend_architecture", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_backend_architecture_and_stable_exports_are_locked() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module = _architecture_check_module()

    assert module.collect_violations(repo_root) == []


def test_literal_string_set_accepts_frozenset_and_fails_closed() -> None:
    module = _architecture_check_module()

    literal_tree = ast.parse('SUPPORTED_TASK_TYPES = frozenset({"one", "two"})')
    computed_tree = ast.parse("SUPPORTED_TASK_TYPES = frozenset(build_types())")

    assert module._literal_string_set(literal_tree, "SUPPORTED_TASK_TYPES") == {
        "one",
        "two",
    }
    assert module._literal_string_set(computed_tree, "SUPPORTED_TASK_TYPES") is None


def test_architecture_check_rejects_nonliteral_supported_task_types() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module = _architecture_check_module()
    literal_string_set = module._literal_string_set

    def reject_supported_task_types(tree: ast.Module, name: str) -> set[str] | None:
        if name == "SUPPORTED_TASK_TYPES":
            return None
        return literal_string_set(tree, name)

    module._literal_string_set = reject_supported_task_types

    violations = module.collect_violations(repo_root)

    assert any(
        violation.path == "backend/src/casefile/worker/dispatch.py"
        and "must be a literal string collection" in violation.message
        for violation in violations
    )


def test_agent_route_internal_view_detection_is_fail_closed() -> None:
    module = _architecture_check_module()
    tree = ast.parse(
        """
@router.get('/projects/{project_id}/agent/runs/{run_id}')
def leaked_agent_run():
    return task_view(TaskRun.result_jsonb)
"""
    )
    function = tree.body[0]
    assert isinstance(function, ast.FunctionDef)

    assert module._http_route_path(function) == ("/projects/{project_id}/agent/runs/{run_id}")
    assert module._agent_route_internal_names(function) == {
        "TaskRun",
        "result_jsonb",
        "task_view",
    }


def test_m38_goal_session_architecture_markers_are_frozen() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module = _architecture_check_module()

    assert module._goal_session_spec_violations(repo_root) == []


@pytest.mark.parametrize(
    "missing_marker",
    [
        "GoalSession > TaskRun",
        "succeeded + checkpointed",
        "CASEFILE_CHAT_GOAL_SESSION_ROLLOUT=off | shadow | active",
        "no_auto_apply=true",
    ],
)
def test_m38_goal_session_architecture_check_fails_closed(
    tmp_path: Path,
    missing_marker: str,
) -> None:
    module = _architecture_check_module()
    spec = tmp_path / module.GOAL_SESSION_SPEC_PATH
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "\n".join(
            sorted(module.GOAL_SESSION_REQUIRED_MARKERS - {missing_marker})
        ),
        encoding="utf-8",
    )
    for relative in module.GOAL_SESSION_REFERENCE_PATHS:
        reference_path = tmp_path / relative
        reference_path.parent.mkdir(parents=True, exist_ok=True)
        reference_path.write_text(
            module.GOAL_SESSION_SPEC_PATH,
            encoding="utf-8",
        )

    violations = module._goal_session_spec_violations(tmp_path)

    assert len(violations) == 1
    assert missing_marker in violations[0].message


def test_m38_goal_session_architecture_requires_responsibility_references(
    tmp_path: Path,
) -> None:
    module = _architecture_check_module()
    spec = tmp_path / module.GOAL_SESSION_SPEC_PATH
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "\n".join(sorted(module.GOAL_SESSION_REQUIRED_MARKERS)),
        encoding="utf-8",
    )

    violations = module._goal_session_spec_violations(tmp_path)

    assert {violation.path for violation in violations} == set(
        module.GOAL_SESSION_REFERENCE_PATHS
    )
