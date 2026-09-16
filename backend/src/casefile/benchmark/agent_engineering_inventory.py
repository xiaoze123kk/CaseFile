"""Offline inventory of prompt releases and real model-client construction sites."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from casefile.agent_runtime.prompt_repository import packaged_prompt_repository


def inventory(root: Path | None = None) -> dict[str, Any]:
    root = root or Path(__file__).resolve().parents[1] / "agent_runtime"
    clients = []
    literal_versions: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        relative = path.relative_to(root).as_posix()
        imports = {
            alias.asname or alias.name: (node.module or "") + "." + alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                target = imports.get(node.func.id, "")
                if target in {"openai.OpenAI", "openai.AsyncOpenAI"} or target.startswith(
                    "casefile.agent_runtime.deepseek_transport.model_checked_"
                ):
                    clients.append(
                        {
                            "file": relative,
                            "line": node.lineno,
                            "factory": target,
                            "explicit_http_client": any(
                                k.arg == "http_client" for k in node.keywords
                            ),
                        }
                    )
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                import re

                if re.fullmatch(r"[a-z][a-z-]+-v[0-9]+", node.value):
                    literal_versions.setdefault(node.value, set()).add(relative)
    repository = packaged_prompt_repository()
    return {
        "schema_version": 1,
        "scope": "runtime source inventory; literals are references, not activation proof",
        "agents": [
            {
                "agent": agent,
                "registry_version": repository.current_version(agent),
                "migration_status": "active_skill_release",
            }
            for agent in repository.expected_agent_ids
        ],
        "client_sites": clients,
        "version_references": {
            key: sorted(value) for key, value in sorted(literal_versions.items())
        },
    }


def main() -> None:
    print(json.dumps(inventory(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
