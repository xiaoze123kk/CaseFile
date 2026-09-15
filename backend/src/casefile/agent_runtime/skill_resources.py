"""Small shared primitives for immutable, package-owned skill resources."""

from __future__ import annotations

import re
from hashlib import sha256
from importlib.resources.abc import Traversable


def read_skill_resource(root: Traversable, path: str, expected_hash: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_./-]+", path) or any(
        part in {"", ".", ".."} for part in path.split("/")
    ):
        raise ValueError("Invalid skill resource path")
    content = root.joinpath(*path.split("/")).read_text(encoding="utf-8")
    if sha256(content.encode()).hexdigest() != expected_hash:
        raise ValueError(f"Skill resource hash mismatch: {path}")
    return content


def skill_metadata(content: str) -> tuple[str, str]:
    if not content.startswith("---\n"):
        raise ValueError("Skill descriptor requires frontmatter")
    metadata = dict(line.split(": ", 1) for line in content.split("---", 2)[1].strip().splitlines())
    if not metadata.get("name") or not metadata.get("description"):
        raise ValueError("Skill name and description required")
    return metadata["name"], metadata["description"]
