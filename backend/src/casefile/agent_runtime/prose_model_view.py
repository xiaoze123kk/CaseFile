"""Remove compiler trace hashes only from known envelopes, never authored content."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

VIEW_VERSION = "prose-model-view-v1"
TRACE_KEYS = frozenset(
    {
        "candidate_schema_hash",
        "checklist_hash",
        "component_hash",
        "component_input_hash",
        "consensus_hash",
        "current_render_hash",
        "judge_report_hashes",
        "narrative_ir_hash",
        "previous_scene_render_hash",
        "profile_hash",
        "prompt_hash",
        "render_schema_hash",
        "scene_plan_hash",
        "checklist_policy_hash",
        "previous_render_hash",
        "render_hash",
        "arbiter_report_hash",
        "arbiter_request_hash",
        "council_policy_hash",
        "report_hash",
        "projection_source_hash",
        "source_fragment_hash",
    }
)


def _strip(envelope: dict[str, Any]) -> None:
    for key in TRACE_KEYS:
        envelope.pop(key, None)


def model_view(payload: dict[str, Any]) -> dict[str, Any]:
    """The complete original payload remains server-side for binding/recovery/validation."""
    result = deepcopy(payload)
    _strip(result.get("server_bindings", {}))
    data = result.get("untrusted_data", {})
    _strip(data.get("checklist", {}).get("source", {}))
    context = data.get("scene_context", {})
    _strip(context)
    for item in context.get("object_catalog", []):
        _strip(item.get("source_ref", {}))  # do not enter item.value (authored data)
    for beat in context.get("beats", []):
        for reference in beat.get("source_refs", []):
            _strip(reference)
    if isinstance(data.get("continuity_reference"), dict):
        _strip(data["continuity_reference"])
    render = data.get("current_render", {})
    _strip(render)
    _strip(render.get("source", {}))
    consensus = data.get("consensus", {})
    _strip(consensus)
    for check in consensus.get("checks", []):
        for verdict in check.get("role_verdicts", []):
            _strip(verdict)
    return result


def model_view_text(payload: dict[str, Any]) -> str:
    # Canonicalize recursively first, then move frequently reusable sections ahead
    # of per-attempt bindings without moving data into the trusted system message.
    value = json.loads(json.dumps(model_view(payload), ensure_ascii=False, sort_keys=True))
    data = value.get("untrusted_data", {})
    context = data.get("scene_context", {})
    if "object_catalog" in context:
        data["scene_context"] = {"object_catalog": context.pop("object_catalog"), **context}
    data = {
        key: data[key]
        for key in ("scene_context", "profile", "checklist", "continuity_reference")
        if key in data
    } | data
    ordered = {"untrusted_data": data, **{k: v for k, v in value.items() if k != "untrusted_data"}}
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
