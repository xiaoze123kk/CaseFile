"""Compile CaseFile documents into a semantic dependency graph."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from typing import Any

import networkx as nx

from casefile.domain.logical_mutation.models import LogicCycle, LogicEdge
from casefile.domain.logical_mutation.policy import (
    ACTIVE_APPLY_POLICY,
    CLOSURE_POLICY_V2,
    relation_policy,
    validate_closure_policy_version,
)

COLLECTION_BY_TYPE = {
    "resolution_spec": "resolution_specs",
    "entity": "entities",
    "relationship": "relationships",
    "location": "locations",
    "event": "events",
    "information_unit": "information_units",
    "claim": "claims",
    "hypothesis": "hypotheses",
    "reasoning_path": "reasoning_paths",
    "constraint": "constraints",
    "structure_lock": "structure_locks",
}


class LogicalGraph:
    """Small domain-facing wrapper around the graph implementation."""

    def __init__(self, edges: Iterable[LogicEdge], object_types: Mapping[str, str]) -> None:
        self._graph: nx.MultiDiGraph[str] = nx.MultiDiGraph()
        self._object_types = dict(object_types)
        for object_id, object_type in sorted(object_types.items()):
            self._graph.add_node(object_id, object_type=object_type)
        self._edges = tuple(
            sorted(
                edges,
                key=lambda item: (
                    item.prerequisite_id,
                    item.dependent_id,
                    item.relation,
                    item.source_path,
                ),
            )
        )
        for ordinal, edge in enumerate(self._edges):
            self._graph.add_edge(
                edge.prerequisite_id,
                edge.dependent_id,
                key=f"{edge.relation}:{ordinal}",
                relation=edge.relation,
            )

    @property
    def edges(self) -> tuple[LogicEdge, ...]:
        return self._edges

    def object_type(self, object_id: str) -> str | None:
        return self._object_types.get(object_id)

    def direct_dependents(
        self, object_id: str, *, relations: Collection[str] | None = None
    ) -> tuple[str, ...]:
        if object_id not in self._graph:
            return ()
        allowed = None if relations is None else set(relations)
        values = {
            target
            for _source, target, data in self._graph.out_edges(object_id, data=True)
            if allowed is None or data.get("relation") in allowed
        }
        return tuple(sorted(values))

    def dependents(
        self, object_id: str, *, relations: Collection[str] | None = None
    ) -> tuple[str, ...]:
        graph = self._filtered(relations)
        return tuple(sorted(nx.descendants(graph, object_id))) if object_id in graph else ()

    def dependencies(
        self, object_id: str, *, relations: Collection[str] | None = None
    ) -> tuple[str, ...]:
        graph = self._filtered(relations)
        return tuple(sorted(nx.ancestors(graph, object_id))) if object_id in graph else ()

    def paths(
        self,
        source_id: str,
        target_ids: Collection[str],
        *,
        relations: Collection[str] | None = None,
    ) -> tuple[tuple[str, ...], ...]:
        graph = self._filtered(relations)
        result: list[tuple[str, ...]] = []
        for target_id in sorted(set(target_ids)):
            if source_id not in graph or target_id not in graph:
                continue
            try:
                result.append(tuple(nx.shortest_path(graph, source_id, target_id)))
            except nx.NetworkXNoPath:
                continue
        return tuple(result)

    def cycles(self, relation: str) -> tuple[LogicCycle, ...]:
        graph = self._filtered({relation})
        result = []
        for component in nx.strongly_connected_components(graph):
            if len(component) > 1 or any(graph.has_edge(node, node) for node in component):
                result.append(LogicCycle(relation, tuple(sorted(component))))
        return tuple(sorted(result, key=lambda item: item.object_ids))

    def degree(self, object_id: str) -> int:
        return int(self._graph.degree(object_id)) if object_id in self._graph else 0

    def _filtered(self, relations: Collection[str] | None) -> nx.DiGraph[str]:
        result: nx.DiGraph[str] = nx.DiGraph()
        result.add_nodes_from(self._graph.nodes)
        allowed = None if relations is None else set(relations)
        for source, target, data in self._graph.edges(data=True):
            if allowed is None or data.get("relation") in allowed:
                result.add_edge(source, target)
        return result


def compile_logical_graph(
    document: Mapping[str, Any],
    *,
    policy_version: str = ACTIVE_APPLY_POLICY,
) -> LogicalGraph:
    policy_version = validate_closure_policy_version(policy_version)
    object_types: dict[str, str] = {}
    for object_type, collection in COLLECTION_BY_TYPE.items():
        for item in document.get(collection, []):
            if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                object_types[str(item["id"])] = object_type

    edges: list[LogicEdge] = []

    def add(
        prerequisite_id: str | None,
        dependent_id: str | None,
        relation: str,
        source_path: str,
    ) -> None:
        if prerequisite_id and dependent_id:
            policy = relation_policy(relation, policy_version)
            edges.append(
                LogicEdge(
                    prerequisite_id,
                    dependent_id,
                    relation,
                    policy.strength,
                    source_path,
                )
            )

    for unit in document.get("information_units", []):
        unit_id = str(unit["id"])
        add(
            _ref_id(unit.get("source_event_ref")),
            unit_id,
            "produced_by",
            "/source_event_ref",
        )
        for index, ref in enumerate(unit.get("supports_claim_refs", [])):
            add(
                unit_id,
                str(ref["object_id"]),
                "supports",
                f"/supports_claim_refs/{index}",
            )
        for index, ref in enumerate(unit.get("refutes_claim_refs", [])):
            add(
                unit_id,
                str(ref["object_id"]),
                "refutes",
                f"/refutes_claim_refs/{index}",
            )

    for claim in document.get("claims", []):
        claim_id = str(claim["id"])
        for index, ref in enumerate(claim.get("dependency_claim_refs", [])):
            add(
                str(ref["object_id"]),
                claim_id,
                "claim_dependency",
                f"/dependency_claim_refs/{index}",
            )

    for hypothesis in document.get("hypotheses", []):
        hypothesis_id = str(hypothesis["id"])
        add(
            hypothesis_id,
            _required_ref_id(hypothesis["target_resolution_ref"]),
            "targets_resolution",
            "/target_resolution_ref",
        )
        for index, ref in enumerate(hypothesis.get("required_claim_refs", [])):
            add(
                str(ref["object_id"]),
                hypothesis_id,
                "required_by_hypothesis",
                f"/required_claim_refs/{index}",
            )
        for index, ref in enumerate(hypothesis.get("competing_hypothesis_refs", [])):
            add(
                str(ref["object_id"]),
                hypothesis_id,
                "competes_with",
                f"/competing_hypothesis_refs/{index}",
            )
        if policy_version == CLOSURE_POLICY_V2:
            for index, assessment in enumerate(
                hypothesis.get("evidence_assessments", [])
            ):
                add(
                    _ref_id(assessment.get("information_ref")),
                    hypothesis_id,
                    "assessed_by_hypothesis",
                    f"/evidence_assessments/{index}/information_ref",
                )

    for resolution in document.get("resolution_specs", []):
        resolution_id = str(resolution["id"])
        for index, ref in enumerate(resolution.get("required_claim_refs", [])):
            add(
                str(ref["object_id"]),
                resolution_id,
                "required_by_resolution",
                f"/required_claim_refs/{index}",
            )
        conclusion = resolution.get("conclusion") or {}
        for index, ref in enumerate(conclusion.get("selected_hypothesis_refs", [])):
            add(
                str(ref["object_id"]),
                resolution_id,
                "selected_by_resolution",
                f"/conclusion/selected_hypothesis_refs/{index}",
            )
        for index, ref in enumerate(conclusion.get("supporting_reasoning_path_refs", [])):
            add(
                str(ref["object_id"]),
                resolution_id,
                "basis_of_resolution",
                f"/conclusion/supporting_reasoning_path_refs/{index}",
            )

    for path in document.get("reasoning_paths", []):
        path_id = str(path["id"])
        add(_required_ref_id(path["target_ref"]), path_id, "reasoning_target", "/target_ref")
        for step_index, step in enumerate(path.get("steps", [])):
            for ref_index, ref in enumerate(step.get("input_refs", [])):
                add(
                    str(ref["object_id"]),
                    path_id,
                    "reasoning_input",
                    f"/steps/{step_index}/input_refs/{ref_index}",
                )
            add(
                _required_ref_id(step["output_ref"]),
                path_id,
                "reasoning_output",
                f"/steps/{step_index}/output_ref",
            )

    for event in document.get("events", []):
        event_id = str(event["id"])
        time = event.get("time") or {}
        if time.get("kind") == "relative":
            add(
                _ref_id(time.get("anchor_event_ref")),
                event_id,
                "relative_time",
                "/time/anchor_event_ref",
            )
        add(_ref_id(event.get("location_ref")), event_id, "occurs_at", "/location_ref")
        for index, ref in enumerate(event.get("participant_refs", [])):
            add(
                str(ref["object_id"]),
                event_id,
                "participates_in",
                f"/participant_refs/{index}",
            )

    for relationship in document.get("relationships", []):
        relationship_id = str(relationship["id"])
        add(
            _required_ref_id(relationship["from_ref"]),
            relationship_id,
            "relationship_endpoint",
            "/from_ref",
        )
        add(
            _required_ref_id(relationship["to_ref"]),
            relationship_id,
            "relationship_endpoint",
            "/to_ref",
        )

    for entity in document.get("entities", []):
        entity_id = str(entity["id"])
        for state_index, state in enumerate(entity.get("knowledge_states", [])):
            add(
                _ref_id(state.get("as_of_event_ref")),
                entity_id,
                "knowledge_anchor",
                f"/knowledge_states/{state_index}/as_of_event_ref",
            )
            for field in ("knows_refs", "believes_refs", "false_belief_refs"):
                for ref_index, ref in enumerate(state.get(field, [])):
                    add(
                        str(ref["object_id"]),
                        entity_id,
                        "knowledge_state",
                        f"/knowledge_states/{state_index}/{field}/{ref_index}",
                    )

    for lock in document.get("structure_locks", []):
        add(
            _required_ref_id(lock["object_ref"]),
            str(lock["id"]),
            "protected_by_lock",
            "/object_ref",
        )

    return LogicalGraph(edges, object_types)


def _ref_id(value: Any) -> str | None:
    return (
        str(value["object_id"]) if isinstance(value, Mapping) and value.get("object_id") else None
    )


def _required_ref_id(value: Any) -> str | None:
    return _ref_id(value)
