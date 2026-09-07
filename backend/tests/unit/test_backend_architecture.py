from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType


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


def test_architecture_check_rejects_nonliteral_supported_task_types(tmp_path: Path) -> None:
    module = _architecture_check_module()
    # Exercise the real scanner on a minimal repository; the positive test above
    # owns the single full-workspace scan.
    dispatch = tmp_path / "backend/src/casefile/worker/dispatch.py"
    dispatch.parent.mkdir(parents=True)
    dispatch.write_text("SUPPORTED_TASK_TYPES = frozenset(build_types())\n", encoding="utf-8")
    for relative, exports in module.STABLE_EXPORTS.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"__all__ = {sorted(exports)!r}\n", encoding="utf-8")

    runtime = tmp_path / "backend/src/casefile/worker/runtime.py"
    runtime.write_text(
        runtime.read_text(encoding="utf-8") + "\nclass Worker:\n    pass\n", encoding="utf-8",
    )

    violations = module.collect_violations(tmp_path)

    assert len(violations) == 1
    assert violations[0].path == "backend/src/casefile/worker/dispatch.py"
    assert "must be a literal string collection" in violations[0].message


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
