"""Opt-in prose releases: stable role/schema/skill prefix, dynamic task suffix."""

from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from importlib.resources import files
from typing import Any, cast

from casefile.agent_runtime.prose_model_view import VIEW_VERSION, model_view_text
from casefile.agent_runtime.skill_assembly import InstructionResource, assemble_instructions
from casefile.agent_runtime.skill_resources import read_skill_resource, skill_metadata

SKILL_RELEASES = {
    "prose-writer-v5": "scene-writing",
    "prose-rewriter-v8": "scene-revision",
    "prose-writer-v6": "scene-writing",
    "prose-rewriter-v9": "scene-revision",
    "prose-writer-v7": "scene-writing-plan-execute",
    "prose-rewriter-v10": "scene-revision-plan-execute",
}
PROJECTED_RELEASES = frozenset(
    {"prose-writer-v6", "prose-rewriter-v9", "prose-writer-v7", "prose-rewriter-v10"}
)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return sha256(compact_json(value).encode()).hexdigest()


def assemble_skill(request: Any, schema: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    root = files("casefile.agent_runtime").joinpath("skill_releases", request.prompt_version)
    manifest = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    if (
        manifest["schema_version"] != 1
        or manifest["prompt_version"] != request.prompt_version
        or manifest["role_sha256"] != sha256(request.system_prompt.encode()).hexdigest()
        or manifest["schema_sha256"] != digest(schema)
        or manifest["tool_policy"] != "no-tools-json-object-v1"
        or manifest["skill"] != SKILL_RELEASES[request.prompt_version]
    ):
        raise ValueError("Prose skill release binding mismatch")
    if request.prompt_version in PROJECTED_RELEASES and manifest.get("model_view") != VIEW_VERSION:
        raise ValueError("Prose model view binding mismatch")
    contents = {
        name: read_skill_resource(root, item["file"], item["sha256"])
        for name, item in manifest["resources"].items()
    }
    name, description = skill_metadata(contents["skill"])
    if name != manifest["skill"]:
        raise ValueError("Prose skill identity mismatch")
    selected = tuple(dict.fromkeys(manifest["order"]))
    if set(selected) != set(contents):
        raise ValueError("Prose skill resource order mismatch")
    assembly = assemble_instructions(
        root=root,
        role=request.system_prompt,
        contract="\n\n必须严格遵守以下 JSON Schema：\n" + compact_json(schema),
        resources=tuple(
            InstructionResource(
                key, manifest["resources"][key]["file"], manifest["resources"][key]["sha256"]
            )
            for key in selected
        ),
        selected=selected,
        reasons=dict.fromkeys(selected, "stage_required"),
        descriptor_names=frozenset({"skill"}),
    )
    prefix = assembly.prefix
    return prefix, {
        "release": request.prompt_version,
        "skill": name,
        "description": description,
        "resources": list(selected),
        "reasons": dict.fromkeys(selected, "stage_required"),
        "manifest_hash": digest(manifest),
        "stable_prefix_hash": sha256(prefix.encode()).hexdigest(),
    }


def bind_skill_request[T](request: T, schema: dict[str, Any]) -> T:
    value: Any = request
    if value.prompt_version not in SKILL_RELEASES:
        return request
    _, metadata = assemble_skill(value, schema)
    return cast(
        T,
        replace(
            value,
            prompt_metadata=metadata,
            request_fingerprint=digest(
                {
                    "source_request": value.request_fingerprint,
                    "skill": metadata,
                }
            ),
        ),
    )


def prose_messages(request: Any, schema: dict[str, Any], focus: str) -> list[dict[str, Any]]:
    """Legacy requests remain byte-compatible; opted-in requests load verified skills."""
    if request.prompt_version not in SKILL_RELEASES:
        return [
            {
                "role": "system",
                "content": request.system_prompt
                + "\n\n必须严格遵守以下 JSON Schema：\n"
                + compact_json(schema),
            },
            {"role": "user", "content": compact_json(request.input_payload)},
            {"role": "user", "content": focus},
        ]
    prefix, metadata = assemble_skill(request, schema)
    if metadata != request.prompt_metadata:
        raise ValueError("Prose skill changed after request freeze")
    payload = dict(request.input_payload)
    repair = payload.pop("generation_repair", None)
    messages = [
        {"role": "system", "content": prefix},
        {
            "role": "user",
            "content": model_view_text(payload)
            if request.prompt_version in PROJECTED_RELEASES
            else compact_json(payload),
        },
    ]
    if repair is not None:
        messages.append({"role": "user", "content": compact_json({"generation_repair": repair})})
    messages.append({"role": "user", "content": focus})
    return messages


def completion_body(request: Any, schema: dict[str, Any], focus: str) -> dict[str, Any]:
    return {
        "model": request.model_id,
        "messages": prose_messages(request, schema, focus),
        "response_format": {"type": "json_object"},
        "temperature": request.temperature,
        "max_tokens": request.max_output_tokens,
        "extra_body": {"thinking": {"type": "disabled"}},
    }
