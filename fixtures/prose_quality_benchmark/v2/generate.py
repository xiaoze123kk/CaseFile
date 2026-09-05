"""Build the v2 public Quality suite for the position-stable pairwise prompt."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from casefile.agent_runtime.prose_quality_critic import (
    PROSE_QUALITY_COMPONENT_HASH,
    PROSE_QUALITY_PAIRWISE_PROMPT_VERSION,
)

ROOT: Final = Path(__file__).resolve().parents[3]
OUT: Final = ROOT / "fixtures/prose_quality_benchmark/v2"
V1_GENERATOR: Final = ROOT / "fixtures/prose_quality_benchmark/v1/generate.py"


def _v1_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prose_quality_v1_generator", V1_GENERATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError("public Quality v1 generator unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_suite() -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    helper = _v1_module()
    suite, attestation, assets = helper.build_suite()
    suite["suite_id"] = "n4.5-b3-quality-public-development-v2"
    suite["pairwise_prompt_version"] = PROSE_QUALITY_PAIRWISE_PROMPT_VERSION
    suite["quality_component_hash"] = PROSE_QUALITY_COMPONENT_HASH
    suite.pop("suite_hash", None)
    suite["suite_hash"] = helper.canonical_hash(suite)
    attestation["suite_hash"] = suite["suite_hash"]
    attestation.pop("attestation_hash", None)
    attestation["attestation_hash"] = helper.canonical_hash(attestation)
    return suite, attestation, assets


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    suite, attestation, _ = build_suite()
    _write(OUT / "suite.json", suite)
    _write(OUT / "review-attestation.json", attestation)


if __name__ == "__main__":
    main()
