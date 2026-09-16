"""Read-only evidence queries over one frozen CaseFile document."""

from typing import Any

from casefile.domain.logical_mutation.graph import compile_logical_graph
from casefile.domain.logical_mutation.policy import CLOSURE_POLICY_V2, propagating_relations


def query_modification_impact(document: dict[str, Any], object_id: str) -> dict[str, Any]:
    """Potential dependents of an object, not proof of a proposed patch's outcome."""
    graph = compile_logical_graph(document, policy_version=CLOSURE_POLICY_V2)
    if graph.object_type(object_id) is None:
        return {"error": "object_not_found"}
    relations = propagating_relations(CLOSURE_POLICY_V2)
    direct = set(graph.direct_dependents(object_id, relations=relations)) - {object_id}
    affected = set(graph.dependents(object_id, relations=relations)) - {object_id}
    return {
        "scope": "potential_dependencies_in_frozen_document",
        "policy_version": CLOSURE_POLICY_V2,
        "object_id": object_id,
        "results": [
            {
                "id": target,
                "object_type": graph.object_type(target),
                "distance": "direct" if target in direct else "transitive",
                "dependency_path": list(graph.paths(object_id, [target], relations=relations)[0]),
            }
            for target in sorted(affected)
        ],
        "total": len(affected),
    }


def query_character_knowledge(
    document: dict[str, Any], character_id: str, as_of_event_ref: str | None
) -> dict[str, Any]:
    """Return explicit snapshots only; never infer chronology or missing knowledge."""
    character = next(
        (item for item in document.get("entities", []) if item.get("id") == character_id), None
    )
    if character is None:
        return {"error": "character_not_found"}
    if "knowledge_states" not in character:
        return {"error": "character_knowledge_not_recorded"}
    if as_of_event_ref is not None and not any(
        item.get("id") == as_of_event_ref for item in document.get("events", [])
    ):
        return {"error": "event_not_found"}
    states = character["knowledge_states"]
    records = [
        {"source_path": f"/knowledge_states/{index}", **state}
        for index, state in enumerate(states)
        if as_of_event_ref is None
        or (
            isinstance(state.get("as_of_event_ref"), dict)
            and state["as_of_event_ref"].get("object_type") == "event"
            and state["as_of_event_ref"].get("object_id") == as_of_event_ref
        )
    ]
    return {
        "scope": "explicit_recorded_snapshots_only",
        "character_id": character_id,
        "as_of_event_ref": as_of_event_ref,
        "status": "recorded" if records else "not_recorded",
        "results": records,
        "total": len(records),
    }
