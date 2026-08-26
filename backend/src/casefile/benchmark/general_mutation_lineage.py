"""Frozen source and runtime lineage for M3.4-07f."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

RUNTIME_FINGERPRINT_VERSION = "general-mutation-runtime-v1"


def general_mutation_runtime_fingerprint(repo_root: Path) -> str:
    relative_files = {
        Path("backend/src/casefile/agent_runtime/chat_intent.py"),
        Path("backend/src/casefile/agent_runtime/chat_routing.py"),
        Path("backend/src/casefile/application/agent_mutation.py"),
        Path("backend/src/casefile/application/agent_patch_mutation.py"),
        Path("backend/src/casefile/application/v1_editing.py"),
        Path("backend/src/casefile/application/workflow/agent.py"),
        Path("backend/src/casefile/application/workflow/mutation_history.py"),
        Path("backend/src/casefile/domain/verification_engine.py"),
        Path("backend/src/casefile/worker/runtime.py"),
        Path("backend/src/casefile/worker/executors/chat.py"),
        Path("backend/src/casefile/agent_runtime/general_mutation.py"),
        Path("backend/src/casefile/agent_runtime/general_mutation_prompt.py"),
        Path("backend/src/casefile/agent_runtime/provider_adapters/deepseek.py"),
        Path("backend/src/casefile/benchmark/general_mutation_capability.py"),
        Path("backend/src/casefile/benchmark/general_mutation_evidence.py"),
        Path("backend/src/casefile/benchmark/general_mutation_holdout.py"),
        Path("backend/src/casefile/benchmark/general_mutation_lineage.py"),
        Path("backend/src/casefile/benchmark/general_mutation_qualification.py"),
        Path("backend/src/casefile/benchmark/general_mutation_safety.py"),
        Path("backend/src/casefile/benchmark/general_mutation_safety_executor.py"),
        Path("backend/src/casefile/benchmark/general_mutation_backend_release.py"),
        Path("backend/src/casefile/benchmark/general_mutation_backend_executor.py"),
        Path("backend/src/casefile/benchmark/policies/general-mutation-gate-v1.json"),
        Path("backend/src/casefile/benchmark/policies/general-mutation-holdout-v1-descriptor.json"),
        Path("scripts/acceptance-general-mutation-v1.ps1"),
    }
    for pattern in (
        "backend/src/casefile/domain/logical_mutation/**/*.py",
        "backend/src/casefile/agent_runtime/prompts/general_mutation_planner/v6/**/*",
        "backend/src/casefile/agent_runtime/prompts/closure_repair/v3/**/*",
    ):
        relative_files.update(
            path.relative_to(repo_root) for path in repo_root.glob(pattern) if path.is_file()
        )
    digest = sha256(RUNTIME_FINGERPRINT_VERSION.encode("utf-8"))
    for relative in sorted(relative_files, key=lambda item: item.as_posix()):
        path = repo_root / relative
        if not path.is_file():
            raise ValueError(f"general_mutation_runtime_source_missing:{relative.as_posix()}")
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


__all__ = ["RUNTIME_FINGERPRINT_VERSION", "general_mutation_runtime_fingerprint"]
