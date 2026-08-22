"""Single source of truth for logical-relation impact semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

RelationStrength = Literal["hard", "conditional", "contextual"]


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


RELATION_POLICIES: dict[str, RelationPolicy] = {
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


def relation_policy(relation: str) -> RelationPolicy:
    try:
        return RELATION_POLICIES[relation]
    except KeyError as error:
        raise ValueError(f"logical_relation_policy_missing:{relation}") from error


def propagating_relations() -> frozenset[str]:
    return frozenset(
        relation
        for relation, policy in RELATION_POLICIES.items()
        if policy.propagates_to_dependents
    )


def cycle_relations() -> frozenset[str]:
    return frozenset(
        relation for relation, policy in RELATION_POLICIES.items() if policy.detects_cycles
    )


def cycle_policies() -> tuple[RelationPolicy, ...]:
    return tuple(
        policy
        for _relation, policy in sorted(RELATION_POLICIES.items())
        if policy.detects_cycles
    )
