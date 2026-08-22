"""Single source of truth for logical-relation impact semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RelationStrength = Literal["hard", "conditional", "contextual"]
ClosurePolicyVersion = Literal["logical-mutation-v1", "logical-mutation-v2"]

CLOSURE_POLICY_V1: ClosurePolicyVersion = "logical-mutation-v1"
CLOSURE_POLICY_V2: ClosurePolicyVersion = "logical-mutation-v2"
ACTIVE_APPLY_POLICY: ClosurePolicyVersion = CLOSURE_POLICY_V1
SHADOW_POLICY: ClosurePolicyVersion = CLOSURE_POLICY_V2
SUPPORTED_CLOSURE_POLICY_VERSIONS = frozenset(
    {CLOSURE_POLICY_V1, CLOSURE_POLICY_V2}
)


@dataclass(frozen=True, slots=True)
class RelationPolicy:
    relation: str
    strength: RelationStrength
    propagates_to_dependents: bool
    cycle_rule_code: str | None = None
    cycle_title: str | None = None

    @property
    def detects_cycles(self) -> bool:
        return self.cycle_rule_code is not None


_BASE_RELATION_POLICIES: dict[str, RelationPolicy] = {
    policy.relation: policy
    for policy in (
        RelationPolicy("produced_by", "contextual", False),
        RelationPolicy("supports", "conditional", True),
        RelationPolicy("refutes", "conditional", True),
        RelationPolicy(
            "claim_dependency",
            "conditional",
            True,
            "claim_dependency_cycle",
            "Claim 依赖形成循环",
        ),
        RelationPolicy("targets_resolution", "hard", True),
        RelationPolicy("required_by_hypothesis", "conditional", True),
        RelationPolicy("competes_with", "contextual", False),
        RelationPolicy("required_by_resolution", "conditional", True),
        RelationPolicy("selected_by_resolution", "hard", True),
        RelationPolicy("basis_of_resolution", "hard", True),
        RelationPolicy("reasoning_target", "hard", True),
        RelationPolicy("reasoning_input", "conditional", True),
        RelationPolicy("reasoning_output", "conditional", True),
        RelationPolicy(
            "relative_time",
            "hard",
            True,
            "relative_time_cycle",
            "相对时间形成循环",
        ),
        RelationPolicy("occurs_at", "contextual", False),
        RelationPolicy("participates_in", "contextual", False),
        RelationPolicy("relationship_endpoint", "contextual", False),
        RelationPolicy("knowledge_anchor", "contextual", False),
        RelationPolicy("knowledge_state", "contextual", False),
        RelationPolicy("protected_by_lock", "hard", True),
    )
}

_V2_RELATION_POLICIES = {
    **_BASE_RELATION_POLICIES,
    "assessed_by_hypothesis": RelationPolicy(
        "assessed_by_hypothesis", "conditional", True
    ),
}

# Compatibility export: callers that do not select a policy continue to see v1.
RELATION_POLICIES = _BASE_RELATION_POLICIES


def validate_closure_policy_version(policy_version: str) -> ClosurePolicyVersion:
    if policy_version not in SUPPORTED_CLOSURE_POLICY_VERSIONS:
        raise ValueError(f"closure_policy_version_unsupported:{policy_version}")
    return policy_version


def relation_policies(
    policy_version: str = ACTIVE_APPLY_POLICY,
) -> dict[str, RelationPolicy]:
    version = validate_closure_policy_version(policy_version)
    return (
        _BASE_RELATION_POLICIES
        if version == CLOSURE_POLICY_V1
        else _V2_RELATION_POLICIES
    )


def relation_policy(
    relation: str, policy_version: str = ACTIVE_APPLY_POLICY
) -> RelationPolicy:
    try:
        return relation_policies(policy_version)[relation]
    except KeyError as error:
        raise ValueError(f"logical_relation_policy_missing:{relation}") from error


def propagating_relations(
    policy_version: str = ACTIVE_APPLY_POLICY,
) -> frozenset[str]:
    return frozenset(
        relation
        for relation, policy in relation_policies(policy_version).items()
        if policy.propagates_to_dependents
    )


def cycle_relations(policy_version: str = ACTIVE_APPLY_POLICY) -> frozenset[str]:
    return frozenset(
        relation
        for relation, policy in relation_policies(policy_version).items()
        if policy.detects_cycles
    )


def cycle_policies(
    policy_version: str = ACTIVE_APPLY_POLICY,
) -> tuple[RelationPolicy, ...]:
    return tuple(
        policy
        for _relation, policy in sorted(relation_policies(policy_version).items())
        if policy.detects_cycles
    )


_V2_SEMANTIC_FINDING_LEVELS = {
    "knowledge_state_available_before_source": "repair_required",
    "temporal_exclusivity_violation": "repair_required",
}


def semantic_finding_closure_level(
    rule_code: str, policy_version: str
) -> Literal["repair_required"] | None:
    version = validate_closure_policy_version(policy_version)
    if version == CLOSURE_POLICY_V1:
        return None
    return _V2_SEMANTIC_FINDING_LEVELS.get(rule_code)  # type: ignore[return-value]
