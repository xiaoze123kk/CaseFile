"""Frozen source lineage for Closure Repair qualification reports."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

REPAIR_RUNTIME_FINGERPRINT_VERSION = "closure-repair-runtime-v1"


def repair_runtime_fingerprint(repo_root: Path) -> str:
    """Hash every production seam that can change repair proof or execution."""

    relative_files = {
        Path("backend/src/casefile/domain/verification_engine.py"),
        Path("backend/src/casefile/application/closure_repair.py"),
        Path("backend/src/casefile/application/workflow_service.py"),
        Path("backend/src/casefile/worker/runtime.py"),
        Path("backend/src/casefile/agent_runtime/closure_repair.py"),
        Path("backend/src/casefile/agent_runtime/closure_repair_prompt.py"),
        Path("backend/src/casefile/agent_runtime/structured_output.py"),
        Path("backend/src/casefile/agent_runtime/transport_diagnostics.py"),
        Path("backend/src/casefile/agent_runtime/provider_adapters/shared.py"),
        Path("backend/src/casefile/agent_runtime/provider_adapters/deepseek.py"),
    }
    for pattern in (
        "backend/src/casefile/domain/logical_mutation/repair/**/*.py",
        "backend/src/casefile/domain/logical_mutation/closure/**/*.py",
        "backend/src/casefile/agent_runtime/prompts/closure_repair/v3/**/*",
    ):
        relative_files.update(
            path.relative_to(repo_root) for path in repo_root.glob(pattern) if path.is_file()
        )
    digest = sha256(REPAIR_RUNTIME_FINGERPRINT_VERSION.encode("utf-8"))
    for relative in sorted(relative_files, key=lambda item: item.as_posix()):
        path = repo_root / relative
        if not path.is_file():
            raise ValueError(f"repair_runtime_source_missing:{relative.as_posix()}")
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


__all__ = ["REPAIR_RUNTIME_FINGERPRINT_VERSION", "repair_runtime_fingerprint"]
