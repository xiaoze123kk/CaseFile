"""Immutable inputs shared by versioned closure rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from casefile.domain.logical_mutation.closure.index import (
    ClosureIndex,
    build_closure_index,
)
from casefile.domain.logical_mutation.graph import LogicalGraph
from casefile.domain.logical_mutation.impact import analyze_impact
from casefile.domain.logical_mutation.models import ImpactCone, MutationSet


@dataclass(frozen=True, slots=True)
class ClosureContext:
    baseline: Mapping[str, Any]
    candidate: Mapping[str, Any]
    baseline_graph: LogicalGraph
    candidate_graph: LogicalGraph
    baseline_index: ClosureIndex
    candidate_index: ClosureIndex
    mutation_set: MutationSet
    impact: ImpactCone
    policy_version: str


def build_closure_context(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    baseline_graph: LogicalGraph,
    candidate_graph: LogicalGraph,
    mutation_set: MutationSet,
    *,
    policy_version: str,
) -> ClosureContext:
    return ClosureContext(
        baseline=baseline,
        candidate=candidate,
        baseline_graph=baseline_graph,
        candidate_graph=candidate_graph,
        baseline_index=build_closure_index(baseline),
        candidate_index=build_closure_index(candidate),
        mutation_set=mutation_set,
        impact=analyze_impact(
            baseline_graph,
            candidate_graph,
            mutation_set,
            policy_version=policy_version,
        ),
        policy_version=policy_version,
    )
