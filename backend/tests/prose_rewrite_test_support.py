"""Synthetic current-runtime Rewrite inputs; never overwrite frozen benchmark assets."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

GENERATOR = (
    Path(__file__).resolve().parents[2] / "fixtures/prose_rewrite_benchmark/v1/generate.py"
)


def generated_package():
    spec = importlib.util.spec_from_file_location("prose_rewrite_fixture_generator", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)
    spec.loader.exec_module(module)
    return module.build_suite()


@pytest.fixture(scope="module")
def current_package(tmp_path_factory):
    folder = tmp_path_factory.mktemp("rewrite-current-runtime")
    suite, attestation, _assets = generated_package()
    paths = folder / "suite.json", folder / "attestation.json"
    for path, value in zip(paths, (suite, attestation), strict=True):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return paths
