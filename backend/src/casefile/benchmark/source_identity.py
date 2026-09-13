"""Git revision, branch and working-tree identity for benchmark reports."""

import subprocess
from pathlib import Path
from typing import Any


class GitIdentityUnavailable(RuntimeError):
    """A required Git identity command returned a nonzero exit status."""


def read_git_identity(repo_root: Path, *, strict: bool = False) -> dict[str, Any]:
    """Preserve diagnostic best-effort reads and opt-in qualification failure checks."""
    def run(*args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, check=False
        )
        if strict and result.returncode != 0:
            raise GitIdentityUnavailable("git_identity_unavailable")
        return result.stdout.strip()

    return {
        "revision": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(run("status", "--porcelain")),
    }
