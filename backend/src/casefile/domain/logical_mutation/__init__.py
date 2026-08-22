"""Pure logical-mutation contracts and deterministic analysis."""

from casefile.domain.logical_mutation.graph import LogicalGraph, compile_logical_graph
from casefile.domain.logical_mutation.impact import analyze_impact
from casefile.domain.logical_mutation.models import (
    CLOSURE_POLICY_VERSION,
    OLD_VALUE_UNSET,
    ClosureIssue,
    CreateObject,
    DeleteObject,
    ImpactCone,
    LogicCycle,
    LogicEdge,
    MechanicalOperation,
    MutationSet,
    NormalizedMutation,
    UpdateField,
)
from casefile.domain.logical_mutation.normalizer import (
    MutationNormalizationError,
    normalize_mutation,
)
from casefile.domain.logical_mutation.policy import (
    RELATION_POLICIES,
    RelationPolicy,
    cycle_policies,
    cycle_relations,
    relation_policy,
)
from casefile.domain.logical_mutation.rules import evaluate_closure_rules

__all__ = [
    "CLOSURE_POLICY_VERSION",
    "ClosureIssue",
    "CreateObject",
    "DeleteObject",
    "ImpactCone",
    "LogicCycle",
    "LogicEdge",
    "LogicalGraph",
    "MechanicalOperation",
    "MutationNormalizationError",
    "MutationSet",
    "NormalizedMutation",
    "OLD_VALUE_UNSET",
    "RELATION_POLICIES",
    "RelationPolicy",
    "UpdateField",
    "analyze_impact",
    "compile_logical_graph",
    "cycle_policies",
    "cycle_relations",
    "evaluate_closure_rules",
    "normalize_mutation",
    "relation_policy",
]
