"""Provider-neutral assembly of explicitly selected, immutable instruction resources."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources.abc import Traversable
from typing import Any

from casefile.agent_runtime.skill_resources import read_skill_resource


@dataclass(frozen=True)
class InstructionResource:
    name: str
    path: str
    content_hash: str


@dataclass(frozen=True)
class InstructionAssembly:
    prefix: str
    resources: tuple[str, ...]
    resource_hashes: tuple[tuple[str, str], ...]
    reasons: tuple[tuple[str, str], ...]
    stable_prefix_hash: str


def assemble_instructions(
    *,
    root: Traversable,
    role: str,
    contract: str,
    resources: Sequence[InstructionResource],
    selected: Sequence[str],
    reasons: Mapping[str, str],
    descriptor_names: frozenset[str] = frozenset(),
) -> InstructionAssembly:
    """Order comes from the release, selection from the existing runtime stage.

    No history is edited here. Independent requests always receive full instructions.
    Resources are verified on each assembly; a local cache must not mask changed files.
    """
    by_name = {resource.name: resource for resource in resources}
    if len(by_name) != len(resources):
        raise ValueError("Duplicate release resource")
    requested = set(selected)
    if requested - by_name.keys() or requested - reasons.keys():
        raise ValueError("Unknown resource or missing selection reason")
    ordered = tuple(resource for resource in resources if resource.name in requested)
    bodies = []
    for resource in ordered:
        content = read_skill_resource(root, resource.path, resource.content_hash)
        if resource.name in descriptor_names:
            if not content.startswith("---\n") or len(content.split("---", 2)) != 3:
                raise ValueError("Invalid skill descriptor")
            content = content.split("---", 2)[2].strip()
        bodies.append(content)
    prefix = role + contract
    if bodies:
        prefix += "\n\n" + "\n\n".join(bodies)
    return InstructionAssembly(
        prefix=prefix,
        resources=tuple(resource.name for resource in ordered),
        resource_hashes=tuple((resource.name, resource.content_hash) for resource in ordered),
        reasons=tuple((resource.name, reasons[resource.name]) for resource in ordered),
        stable_prefix_hash=sha256(prefix.encode()).hexdigest(),
    )


def request_identity(body: Mapping[str, Any]) -> dict[str, str]:
    """Hash actual ordered wire content without exposing task data or credentials.

    This measures only the leading instruction messages, not arbitrary system
    messages later in history. Tools and output contracts have separate identities.
    """
    import json

    def digest(value: Any) -> str:
        return sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    instructions: list[Any] = []
    if body.get("instructions") is not None:
        instructions.append(body["instructions"])
    messages = body.get("messages", body.get("input", []))
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in {"system", "developer"}:
                break
            instructions.append(message)
    return {
        "stable_prefix_hash": digest(instructions),
        "tool_definition_hash": digest(body.get("tools", [])),
        "output_contract_hash": digest(body.get("response_format", body.get("text", {}))),
        "request_fingerprint": digest(dict(body)),
    }
