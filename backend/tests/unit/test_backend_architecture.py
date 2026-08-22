from __future__ import annotations

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
