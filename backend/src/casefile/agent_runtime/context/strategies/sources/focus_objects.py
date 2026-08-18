"""Focus-first source: full focus objects plus bounded one-hop neighbor summaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from casefile.agent_runtime.chat_tools import find_casefile_object, related_casefile_objects
from casefile.agent_runtime.context.estimators import estimate_jsonable_tokens
from casefile.agent_runtime.context.models import (
    ContextBlock,
    ContextDecision,
    StageResult,
)
from casefile.agent_runtime.context.protocols import ContextRun

_MAX_FULL_OBJECTS = 8
_MAX_NEIGHBORS = 16


def _focus_ids(focus: dict[str, Any], key: str) -> list[str]:
    values = focus.get(key)
    if not isinstance(values, list):
        return []
    return [
        str(value).strip()
        for value in values
        if isinstance(value, str) and value.strip()
    ]


def build_focus_objects_payload(
    casefile: dict[str, Any],
    focus: dict[str, Any],
    *,
    max_full_objects: int = _MAX_FULL_OBJECTS,
    max_neighbors: int = _MAX_NEIGHBORS,
) -> dict[str, Any]:
    """Expand focus references read-only; dangling references are reported, not repaired."""

    object_ids = list(dict.fromkeys(_focus_ids(focus, "object_ids")))
    event_ids = list(dict.fromkeys(_focus_ids(focus, "event_ids")))
    seeds = list(dict.fromkeys([*object_ids, *event_ids]))
    full_objects: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for seed in seeds[: max(1, max_full_objects)]:
        found = find_casefile_object(casefile, seed)
        if found is None:
            unresolved.append(seed)
            continue
        collection, item = found
        full_objects.append({"collection": collection, "object": item})
    expansion = related_casefile_objects(
        casefile,
        seeds,
        limit=max_neighbors,
    )
    for reference in expansion["unresolved_refs"]:
        if reference not in unresolved:
            unresolved.append(reference)
    return {
        "focus_object_ids": object_ids,
        "focus_event_ids": event_ids,
        "objects": full_objects,
        "neighbors": expansion["objects"],
        "unresolved_refs": sorted(set(unresolved)),
    }


@dataclass(slots=True)
class FocusObjectsStage:
    """Policy stage producing the ``focus_objects`` block."""

    name: str = "focus_objects_v1"
    version: str = "focus-objects-v1"
    capabilities: frozenset[str] = frozenset({"source", "chat", "focus", "deterministic"})

    def can_run(self, run: ContextRun) -> bool:
        return isinstance(run.frozen_input.get("casefile"), dict)

    def run(self, run: ContextRun) -> StageResult:
        config = run.policy_stage_config("focus_objects")
        casefile = run.frozen_input.get("casefile")
        focus = run.frozen_input.get("focus")
        if not isinstance(casefile, dict) or not isinstance(focus, dict):
            return StageResult()
        max_full = int(config.get("max_full_objects", _MAX_FULL_OBJECTS))
        max_neighbors = int(config.get("max_neighbors", _MAX_NEIGHBORS))
        payload = build_focus_objects_payload(
            casefile,
            focus,
            max_full_objects=max(1, max_full),
            max_neighbors=max(1, min(40, max_neighbors)),
        )
        decisions: list[ContextDecision] = []
        if payload["unresolved_refs"]:
            decisions.append(
                ContextDecision(
                    stage="focus_objects",
                    code="focus_dangling_reference_pruned",
                    detail=(
                        "focus references not present in the frozen CaseFile are kept "
                        "read-only and excluded from expansion: "
                        f"{payload['unresolved_refs']!r}"
                    ),
                )
            )
        return StageResult(
            added=(
                ContextBlock(
                    id="focus_objects",
                    kind="focus_objects",
                    payload=payload,
                    tokens=estimate_jsonable_tokens(payload, run.estimator),
                    metadata={
                        "full_object_count": len(payload["objects"]),
                        "neighbor_count": len(payload["neighbors"]),
                    },
                ),
            ),
            decisions=tuple(decisions),
            metrics={
                "full_object_count": len(payload["objects"]),
                "neighbor_count": len(payload["neighbors"]),
            },
        )


__all__ = [
    "FocusObjectsStage",
    "build_focus_objects_payload",
]
