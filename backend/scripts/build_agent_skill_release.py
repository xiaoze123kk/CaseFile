"""Build the immutable all-Agent Skill release from active prompt packages."""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path
from typing import Any


def digest(content: str) -> str:
    return sha256(content.encode()).hexdigest()


def skill_text(name: str, description: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}"


def build(prompt_root: Path, output_root: Path) -> dict[str, Any]:
    registry = json.loads((prompt_root / "registry.json").read_text(encoding="utf-8"))
    agents: dict[str, Any] = {}
    for agent_id, selected in registry["agents"].items():
        version = selected["current_version"]
        directory = prompt_root / agent_id / ("v" + version.rsplit("-v", 1)[1])
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        release_root = output_root / agent_id
        release_root.mkdir(parents=True, exist_ok=True)
        if manifest.get("schema_version") in {2, 3}:
            package_resources = []
            for fragment_id, entry in manifest["fragments"].items():
                content = (directory / entry["file"]).read_text(encoding="utf-8")
                if digest(content) != entry["sha256"]:
                    raise ValueError(f"Prompt fragment changed: {agent_id}/{fragment_id}")
                package_resources.append({"name": fragment_id, "sha256": entry["sha256"]})
            descriptor = skill_text(
                agent_id.replace("_", "-"),
                f"Runtime-selected methods for {agent_id}.",
                "组件方法由现有运行时按阶段选择；本描述不重复注入模型上下文。\n",
            )
            (release_root / "SKILL.md").write_text(descriptor, encoding="utf-8", newline="\n")
            agents[agent_id] = {
                "prompt_version": version,
                "strategy": "prompt_package",
                "descriptor": {"file": f"{agent_id}/SKILL.md", "sha256": digest(descriptor)},
                "resources": package_resources,
                "components": {
                    component: list(value["instruction_fragments"])
                    for component, value in manifest["components"].items()
                },
                "tool_policy": {
                    component: value["tool_policy_id"]
                    for component, value in manifest["components"].items()
                },
            }
            continue

        original = (directory / manifest["system_prompt_file"]).read_text(encoding="utf-8")
        if digest(original) != manifest["system_prompt_sha256"]:
            raise ValueError(f"System prompt changed: {agent_id}")
        role, separator, method = original.partition("\n\n")
        if not separator or not role.strip() or not method.strip():
            # A one-block instruction is a simple Agent; it remains a role-only release.
            role, method = original, ""
        role_text = role + ("\n" if method else "")
        (release_root / "role.md").write_text(role_text, encoding="utf-8", newline="\n")
        resources: list[dict[str, str]] = []
        if method:
            descriptor = skill_text(
                agent_id.replace("_", "-"),
                f"Stable execution method for {agent_id}.",
                method,
            )
            (release_root / "SKILL.md").write_text(descriptor, encoding="utf-8", newline="\n")
            resources.append(
                {"name": "method", "file": f"{agent_id}/SKILL.md", "sha256": digest(descriptor)}
            )
        agents[agent_id] = {
            "prompt_version": version,
            "strategy": "role_skill" if method else "role_only",
            "role": {
                "file": f"{agent_id}/role.md",
                "sha256": digest(role_text),
            },
            "resources": resources,
            "selected": [item["name"] for item in resources],
            "reasons": {item["name"]: "stage_required" for item in resources},
            "assembled_sha256": digest(original),
            "tool_policy": "existing-runtime-policy",
        }
    return {
        "schema_version": 1,
        "release": "agent-skill-runtime-v1",
        "assembly_version": "stable-prefix-v1",
        "agents": agents,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    manifest = build(args.prompt_root, args.output_root)
    (args.output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
