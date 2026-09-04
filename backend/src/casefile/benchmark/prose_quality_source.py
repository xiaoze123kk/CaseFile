"""Read-only Git identity shared by Quality diagnostics and qualification."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from casefile.domain.narrative_compiler import canonical_json_sha256


def quality_source_identity(root: Path) -> dict[str, Any]:
    def git(*arguments: str) -> str:
        return subprocess.check_output(
            ["git", *arguments],
            cwd=root,
            text=True,
            encoding="utf-8",
        ).strip()

    return {
        "revision": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "clean": not bool(git("status", "--porcelain", "--untracked-files=normal")),
        "tracked_source_hash": canonical_json_sha256(git("ls-files", "-s", "--", ".").splitlines()),
    }
