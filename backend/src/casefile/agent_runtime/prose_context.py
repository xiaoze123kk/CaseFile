"""Provider-only scene projection; authoritative replay states remain unchanged."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from casefile.domain.narrative_compiler import canonical_json_sha256


def unique(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: unique(v) for k, v in value.items()}
    if isinstance(value, list):
        result: dict[str, Any] = {}
        for item in value:
            item = unique(item)
            result.setdefault(canonical_json_sha256(item), item)
        return list(result.values())
    return value


def scene_generation_context(context: dict[str, Any]) -> dict[str, Any]:
    """Only scope state rows; never truncate strings, directives, or referenced objects."""
    result = {k: deepcopy(v) for k, v in context.items() if k != "previous_scene_render"}
    participants = {r["object_id"] for r in context.get("participant_refs", [])}
    for name in ("state_before", "expected_state_after"):
        state = unique(result.get(name, {}))
        if name in result:
            result[name] = state
        for field in ("locations", "character_knowledge"):
            if field in state:
                state[field] = [
                    row
                    for row in state[field]
                    if row.get("subject_ref", {}).get("object_id") in participants
                ]
    before = result.get("state_before", {})
    after = result.get("expected_state_after", {})
    changes = {}
    for key in before.keys() | after.keys():
        if before.get(key) == after.get(key):
            continue
        prior = {canonical_json_sha256(v): v for v in before.get(key, [])}
        later = {canonical_json_sha256(v): v for v in after.get(key, [])}
        changes[key] = {
            "added": [later[h] for h in sorted(later.keys() - prior.keys())],
            "removed": [prior[h] for h in sorted(prior.keys() - later.keys())],
        }
    result["state_changes"] = changes
    result["projection_source_hash"] = canonical_json_sha256(context)
    return result
