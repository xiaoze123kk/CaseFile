"""Logical impact-cone analysis across baseline and candidate graphs."""

from __future__ import annotations

from casefile.domain.logical_mutation.graph import LogicalGraph
from casefile.domain.logical_mutation.models import ImpactCone, MutationSet
from casefile.domain.logical_mutation.policy import cycle_relations, propagating_relations


def analyze_impact(
    baseline_graph: LogicalGraph,
    candidate_graph: LogicalGraph,
    mutation_set: MutationSet,
    *,
    policy_version: str | None = None,
) -> ImpactCone:
    version = policy_version or mutation_set.closure_policy_version
    propagation_relations = propagating_relations(version)
    roots = tuple(sorted({operation.object_id for operation in mutation_set.operations}))
    direct: set[str] = set()
    transitive: set[str] = set()
    affected_edges = set()
    paths: set[tuple[str, ...]] = set()
    resolutions: set[str] = set()
    for root in roots:
        for graph in (baseline_graph, candidate_graph):
            root_direct = set(graph.direct_dependents(root, relations=propagation_relations))
            root_all = set(graph.dependents(root, relations=propagation_relations))
            direct.update(root_direct)
            transitive.update(root_all - root_direct)
            for edge in graph.edges:
                if edge.prerequisite_id == root or edge.dependent_id in root_all:
                    affected_edges.add(edge)
            target_resolutions = {
                item for item in root_all if graph.object_type(item) == "resolution_spec"
            }
            resolutions.update(target_resolutions)
            paths.update(graph.paths(root, target_resolutions, relations=propagation_relations))
    cycles = tuple(
        sorted(
            {
                *(
                    cycle
                    for relation in cycle_relations(version)
                    for cycle in baseline_graph.cycles(relation)
                ),
                *(
                    cycle
                    for relation in cycle_relations(version)
                    for cycle in candidate_graph.cycles(relation)
                ),
            },
            key=lambda item: (item.relation, item.object_ids),
        )
    )
    return ImpactCone(
        root_object_ids=roots,
        direct_object_ids=tuple(sorted(direct - set(roots))),
        transitive_object_ids=tuple(sorted(transitive - direct - set(roots))),
        affected_edges=tuple(
            sorted(
                affected_edges,
                key=lambda edge: (
                    edge.prerequisite_id,
                    edge.dependent_id,
                    edge.relation,
                    edge.source_path,
                ),
            )
        ),
        affected_resolution_ids=tuple(sorted(resolutions)),
        dependency_paths=tuple(sorted(paths)),
        cycles=cycles,
    )
