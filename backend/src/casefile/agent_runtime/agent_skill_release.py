"""Immutable release binding for all active Agent prompts and existing Prompt Packages."""

from __future__ import annotations

import json
from functools import cache
from hashlib import sha256
from importlib.resources import files
from typing import Any

from casefile.agent_runtime.skill_assembly import InstructionResource, assemble_instructions
from casefile.agent_runtime.skill_resources import read_skill_resource, skill_metadata

RELEASE = "agent-skill-runtime-v1"


def _digest(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


@cache
def release_manifest() -> dict[str, Any]:
    releases = files("casefile.agent_runtime").joinpath("skill_releases")
    registry = json.loads(releases.joinpath("registry.json").read_text(encoding="utf-8"))
    if registry != {"schema_version": 1, "current_release": RELEASE}:
        raise ValueError("Unsupported Agent Skill registry")
    root = releases.joinpath(RELEASE)
    manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    if (
        manifest.get("schema_version") != 1
        or manifest.get("release") != RELEASE
        or manifest.get("assembly_version") != "stable-prefix-v1"
    ):
        raise ValueError("Unsupported Agent Skill release")
    return manifest


def binding_for_prompt_version(prompt_version: str, component: str | None = None) -> dict[str, Any]:
    bindings = {}
    for agent, entry in release_manifest()["agents"].items():
        if entry["prompt_version"] != prompt_version:
            continue
        selected: Any = entry.get("selected", [])
        tool_policy: Any = entry["tool_policy"]
        if entry["strategy"] == "prompt_package":
            selected = entry["components"].get(component, entry["components"])
            tool_policy = entry["tool_policy"].get(component, entry["tool_policy"])
        bindings[agent] = {
            "strategy": entry["strategy"],
            "selected_resources": selected,
            "reasons": entry.get("reasons", "runtime_component_required"),
            "tool_policy": tool_policy,
        }
    return {"skill_release": RELEASE, "skill_bindings": bindings} if bindings else {}


def bind_prompt(
    agent_id: str,
    prompt_version: str,
    system_prompt: str,
    component_prompts: dict[str, str],
    package: Any = None,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Validate the selected release and return byte-identical model instructions."""
    root = files("casefile.agent_runtime").joinpath("skill_releases", RELEASE)
    entry = release_manifest()["agents"].get(agent_id)
    if not isinstance(entry, dict) or entry.get("prompt_version") != prompt_version:
        return system_prompt, component_prompts, {}
    strategy = entry["strategy"]
    if strategy == "prompt_package":
        if package is None:
            raise ValueError("Agent Skill Prompt Package binding missing")
        expected_resources = {item["name"]: item["sha256"] for item in entry["resources"]}
        actual_resources = {name: fragment.sha256 for name, fragment in package.fragments.items()}
        actual_components = {
            name: list(component.instruction_fragments)
            for name, component in package.components.items()
        }
        actual_tool_policy = {
            name: component.tool_policy_id for name, component in package.components.items()
        }
        if (
            expected_resources != actual_resources
            or entry["components"] != actual_components
            or entry["tool_policy"] != actual_tool_policy
        ):
            raise ValueError("Agent Skill Prompt Package release mismatch")
        descriptor = entry["descriptor"]
        content = read_skill_resource(root, descriptor["file"], descriptor["sha256"])
        name, description = skill_metadata(content)
        selected: dict[str, list[str]] = {}
        for component, resource_names in entry["components"].items():
            if component not in component_prompts:
                raise ValueError("Agent Skill component binding mismatch")
            selected[component] = list(dict.fromkeys(resource_names))
        metadata = {
            "release": RELEASE,
            "skill": name,
            "description": description,
            "strategy": strategy,
            "selected_resources": selected,
            "reasons": "runtime_component_required",
            "tool_policy": entry["tool_policy"],
            "manifest_hash": _digest(release_manifest()),
            "stable_prefix_hash": sha256(system_prompt.encode()).hexdigest(),
        }
        return system_prompt, component_prompts, metadata
    role = entry["role"]
    role_text = read_skill_resource(root, role["file"], role["sha256"])
    resources = tuple(
        InstructionResource(item["name"], item["file"], item["sha256"])
        for item in entry["resources"]
    )
    assembly = assemble_instructions(
        root=root,
        role=role_text.rstrip("\n"),
        contract="",
        resources=resources,
        selected=entry["selected"],
        reasons=entry["reasons"],
        descriptor_names=frozenset(item["name"] for item in entry["resources"]),
    )
    prefix = assembly.prefix + "\n"
    if sha256(prefix.encode()).hexdigest() != entry["assembled_sha256"] or prefix != system_prompt:
        raise ValueError("Agent Skill assembled prompt mismatch")
    skills = [
        skill_metadata(read_skill_resource(root, item["file"], item["sha256"]))
        for item in entry["resources"]
    ]
    metadata = {
        "release": RELEASE,
        "strategy": strategy,
        "skills": [{"name": name, "description": description} for name, description in skills],
        "resources": list(assembly.resources),
        "reasons": dict(assembly.reasons),
        "tool_policy": entry["tool_policy"],
        "manifest_hash": _digest(release_manifest()),
        "stable_prefix_hash": sha256(prefix.encode()).hexdigest(),
    }
    return prefix, component_prompts, metadata
