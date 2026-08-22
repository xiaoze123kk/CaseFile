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
    ACTIVE_APPLY_POLICY,
    CLOSURE_POLICY_V1,
    CLOSURE_POLICY_V2,
    RELATION_POLICIES,
    SHADOW_POLICY,
    SUPPORTED_CLOSURE_POLICY_VERSIONS,
    RelationPolicy,
    cycle_policies,
    cycle_relations,
    relation_policy,
    semantic_finding_closure_level,
    validate_closure_policy_version,
)
from casefile.domain.logical_mutation.rules import evaluate_closure_rules

__all__ = [
    "ACTIVE_APPLY_POLICY",
    "CLOSURE_POLICY_V1",
    "CLOSURE_POLICY_V2",
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
    "SHADOW_POLICY",
    "SUPPORTED_CLOSURE_POLICY_VERSIONS",
    "RelationPolicy",
    "UpdateField",
    "analyze_impact",
    "compile_logical_graph",
    "cycle_policies",
    "cycle_relations",
    "evaluate_closure_rules",
    "normalize_mutation",
    "relation_policy",
    "semantic_finding_closure_level",
    "validate_closure_policy_version",
]
