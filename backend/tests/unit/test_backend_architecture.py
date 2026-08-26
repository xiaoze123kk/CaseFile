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
