"""Deterministic policy, budgets, hashing and completion for M3.7."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field

from casefile.agent_runtime.goal.contracts import (
    FinishGoalAction,
    FrozenGoal,
    GoalCompletionDecision,
    GoalDecisionOutput,
    GoalObligation,
    GoalObservation,
    GoalPlanItem,
    GoalUnderstandingOutput,
    InvokeCapabilityAction,
)
from casefile.agent_runtime.models import StrictAgentOutput

GOAL_RUNTIME_VERSION = "casefile-chat-goal-runtime-v2"
GOAL_POLICY_VERSION = "casefile-chat-goal-policy-v2"
GOAL_CAPABILITY_REGISTRY_VERSION = "casefile-chat-goal-capabilities-v2"
GOAL_QUALIFICATION_CONFIDENCE = 0.80


class GoalPolicyError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GoalBudget(StrictAgentOutput):
    max_plan_items: int = Field(default=6, ge=1, le=6)
    max_capability_actions: int = Field(default=4, ge=1, le=4)
    max_decision_calls: int = Field(default=6, ge=1, le=6)
    max_completion_retries: int = Field(default=1, ge=0, le=1)
    max_mutation_proposals: int = Field(default=1, ge=0, le=1)
    max_candidate_audits: int = Field(default=2, ge=0, le=2)
    max_total_tool_calls: int = Field(default=48, ge=0, le=48)
    max_provider_operations: int = Field(default=14, ge=1, le=14)
    max_observation_chars: int = Field(default=4_000, ge=1, le=4_000)
    max_total_observation_chars: int = Field(default=20_000, ge=1, le=20_000)


class GoalRuntimeConfig(StrictAgentOutput):
    mode: Literal["shadow", "active"]
    runtime_version: Literal["casefile-chat-goal-runtime-v2"] = "casefile-chat-goal-runtime-v2"
    policy_version: Literal["casefile-chat-goal-policy-v2"] = "casefile-chat-goal-policy-v2"
    capability_registry_version: Literal["casefile-chat-goal-capabilities-v2"] = (
        "casefile-chat-goal-capabilities-v2"
    )
    budget: GoalBudget = Field(default_factory=GoalBudget)


class CapabilityDefinition(StrictAgentOutput):
    capability: Literal["analyze", "audit", "propose_mutation"]
    obligation_kind: Literal["analysis", "audit", "mutation_proposal"]
    allowed_target_states: tuple[Literal["baseline", "candidate"], ...]
    effect: Literal["read", "propose_mutation"]


CAPABILITY_REGISTRY: dict[str, CapabilityDefinition] = {
    "analyze": CapabilityDefinition(
        capability="analyze",
        obligation_kind="analysis",
        allowed_target_states=("baseline", "candidate"),
        effect="read",
    ),
    "audit": CapabilityDefinition(
        capability="audit",
        obligation_kind="audit",
        allowed_target_states=("baseline", "candidate"),
        effect="read",
    ),
    "propose_mutation": CapabilityDefinition(
        capability="propose_mutation",
        obligation_kind="mutation_proposal",
        allowed_target_states=("baseline",),
        effect="propose_mutation",
    ),
}


@dataclass(frozen=True, slots=True)
class GoalQualification:
    qualified: bool
    reason_codes: tuple[str, ...]


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def freeze_goal(output: GoalUnderstandingOutput, source_message: str) -> FrozenGoal:
    obligations: list[GoalObligation] = []
    for index, draft in enumerate(output.obligations, start=1):
        if draft.source_excerpt not in source_message:
            raise GoalPolicyError("goal_source_excerpt_invalid")
        dependencies: list[str] = []
        for dependency in draft.depends_on:
            if dependency < 1 or dependency >= index:
                raise GoalPolicyError("goal_dependency_invalid")
            dependencies.append(f"obl_{dependency}")
        obligations.append(
            GoalObligation(
                obligation_id=f"obl_{index}",
                kind=draft.kind,
                target_state=draft.target_state,
                source_excerpt=draft.source_excerpt,
                depends_on=dependencies,
            )
        )
    _validate_dag(obligations)
    payload = [item.model_dump(mode="json") for item in obligations]
    return FrozenGoal(
        goal=output.goal,
        obligations=obligations,
        source_message_hash=stable_hash(source_message),
        obligations_hash=stable_hash(payload),
    )


def qualify_goal(
    output: GoalUnderstandingOutput,
    frozen: FrozenGoal,
    *,
    budget: GoalBudget,
) -> GoalQualification:
    reasons: list[str] = []
    if len(frozen.obligations) < 2:
        reasons.append("goal_too_few_obligations")
    if output.confidence < GOAL_QUALIFICATION_CONFIDENCE:
        reasons.append("goal_confidence_low")
    if output.ambiguous:
        reasons.append("goal_ambiguous")
    # ``missing_info`` is model-authored explanatory text, not an authoritative
    # stop signal. Only the typed ambiguous flag and the later deterministic
    # mutation preflight may reject an otherwise self-contained Goal.
    if output.missing_info and output.ambiguous:
        reasons.append("goal_missing_info")
    if len(frozen.obligations) > budget.max_plan_items:
        reasons.append("goal_plan_budget_exceeded")
    mutation_count = sum(item.kind == "mutation_proposal" for item in frozen.obligations)
    candidate_audits = sum(
        item.kind == "audit" and item.target_state == "candidate" for item in frozen.obligations
    )
    if mutation_count > budget.max_mutation_proposals:
        reasons.append("goal_mutation_budget_exceeded")
    if candidate_audits > budget.max_candidate_audits:
        reasons.append("goal_candidate_audit_budget_exceeded")
    if any(
        item.target_state == "candidate" and not _has_mutation_ancestor(item, frozen.obligations)
        for item in frozen.obligations
    ):
        reasons.append("goal_candidate_before_mutation")
    if len(frozen.obligations) > budget.max_capability_actions:
        reasons.append("goal_capability_budget_exceeded")
    return GoalQualification(qualified=not reasons, reason_codes=tuple(reasons))


def validate_decision(
    frozen: FrozenGoal,
    decision: GoalDecisionOutput,
    observations: tuple[GoalObservation, ...],
) -> None:
    known = {item.obligation_id: item for item in frozen.obligations}
    plan_ids = {item.obligation_id for item in decision.plan_items}
    if plan_ids != set(known):
        raise GoalPolicyError("goal_action_invalid")
    action = decision.action
    if not isinstance(action, InvokeCapabilityAction):
        return
    if any(item not in known for item in action.obligation_ids):
        raise GoalPolicyError("goal_action_invalid")
    capability = CAPABILITY_REGISTRY[action.capability]
    for obligation_id in action.obligation_ids:
        obligation = known[obligation_id]
        if obligation.kind != capability.obligation_kind:
            raise GoalPolicyError("goal_capability_blocked")
        if obligation.target_state != action.target_state:
            raise GoalPolicyError("goal_capability_blocked")
        if action.target_state not in capability.allowed_target_states:
            raise GoalPolicyError("goal_capability_blocked")
        completed = _completed_ids(observations)
        if not set(obligation.depends_on).issubset(completed):
            raise GoalPolicyError("goal_capability_blocked")


def normalize_decision_plan(
    frozen: FrozenGoal,
    decision: GoalDecisionOutput,
    observations: tuple[GoalObservation, ...],
) -> GoalDecisionOutput:
    """Rebuild non-authoritative plan display from frozen server facts."""

    completed = _completed_ids(observations)
    known_ids = {item.obligation_id for item in frozen.obligations}
    action = FinishGoalAction() if completed == known_ids else decision.action
    in_progress = (
        set(action.obligation_ids) if isinstance(action, InvokeCapabilityAction) else set()
    )
    plan_items = [
        GoalPlanItem(
            obligation_id=obligation.obligation_id,
            status=(
                "in_progress"
                if obligation.obligation_id in in_progress
                else "completed"
                if obligation.obligation_id in completed
                else "pending"
            ),
        )
        for obligation in frozen.obligations
    ]
    return decision.model_copy(update={"plan_items": plan_items, "action": action})


def goal_capability_message(
    frozen: FrozenGoal,
    action: InvokeCapabilityAction,
) -> str:
    """Project one authorized action to only its verbatim obligation text."""

    selected = set(action.obligation_ids)
    if not selected:
        raise GoalPolicyError("goal_action_invalid")
    excerpts = [
        obligation.source_excerpt.strip()
        for obligation in frozen.obligations
        if obligation.obligation_id in selected
    ]
    if len(excerpts) != len(selected) or any(not excerpt for excerpt in excerpts):
        raise GoalPolicyError("goal_action_invalid")
    return "；".join(excerpts)


def complete_goal(
    frozen: FrozenGoal,
    observations: tuple[GoalObservation, ...],
    *,
    expected_obligations_hash: str | None = None,
) -> GoalCompletionDecision:
    reasons: list[str] = []
    if (
        expected_obligations_hash is not None
        and expected_obligations_hash != frozen.obligations_hash
    ):
        reasons.append("goal_obligations_hash_mismatch")
    completed = _completed_ids(observations)
    missing = [
        item.obligation_id for item in frozen.obligations if item.obligation_id not in completed
    ]
    if missing:
        reasons.append("goal_obligation_incomplete")
    by_id = {item.obligation_id: item for item in frozen.obligations}
    mutation_obligations = {
        item.obligation_id for item in frozen.obligations if item.kind == "mutation_proposal"
    }
    for observation in observations:
        for obligation_id in observation.obligation_ids:
            obligation = by_id.get(obligation_id)
            if obligation is None:
                reasons.append("goal_observation_unknown_obligation")
                continue
            if observation.target_state != obligation.target_state:
                reasons.append("goal_observation_target_mismatch")
            if observation.target_state == "candidate" and not observation.candidate_hash:
                reasons.append("goal_candidate_hash_missing")
    for obligation_id in mutation_obligations & completed:
        matching = [
            item
            for item in observations
            if obligation_id in item.obligation_ids and item.status == "completed"
        ]
        if not matching or not matching[-1].mutation_proof_ref or not matching[-1].candidate_hash:
            reasons.append("goal_mutation_proof_missing")
    candidate_hashes = {
        item.candidate_hash
        for item in observations
        if item.target_state == "candidate" and item.status == "completed"
    }
    mutation_hashes = {
        item.candidate_hash
        for item in observations
        if any(obligation_id in mutation_obligations for obligation_id in item.obligation_ids)
        and item.status == "completed"
    }
    if candidate_hashes and (len(candidate_hashes) != 1 or candidate_hashes != mutation_hashes):
        reasons.append("goal_candidate_hash_mismatch")
    state_payload = {
        "obligations_hash": frozen.obligations_hash,
        "observations": [item.model_dump(mode="json") for item in observations],
    }
    return GoalCompletionDecision(
        allowed=not reasons and not missing,
        missing_obligation_ids=missing,
        reason_codes=sorted(set(reasons)),
        state_hash=stable_hash(state_payload),
    )


def _completed_ids(observations: tuple[GoalObservation, ...]) -> set[str]:
    return {
        obligation_id
        for observation in observations
        if observation.status == "completed"
        for obligation_id in observation.obligation_ids
    }


def _has_mutation_ancestor(item: GoalObligation, obligations: list[GoalObligation]) -> bool:
    by_id = {obligation.obligation_id: obligation for obligation in obligations}
    pending = list(item.depends_on)
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        dependency = by_id[current]
        if dependency.kind == "mutation_proposal":
            return True
        pending.extend(dependency.depends_on)
    return False


def _validate_dag(obligations: list[GoalObligation]) -> None:
    known = {item.obligation_id for item in obligations}
    edges: dict[str, list[str]] = defaultdict(list)
    indegree = {item.obligation_id: 0 for item in obligations}
    for item in obligations:
        for dependency in item.depends_on:
            if dependency not in known:
                raise GoalPolicyError("goal_dependency_unknown")
            edges[dependency].append(item.obligation_id)
            indegree[item.obligation_id] += 1
    queue = deque(sorted(key for key, degree in indegree.items() if degree == 0))
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for target in edges[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(obligations):
        raise GoalPolicyError("goal_dependency_cycle")


__all__ = [
    "CAPABILITY_REGISTRY",
    "GOAL_CAPABILITY_REGISTRY_VERSION",
    "GOAL_POLICY_VERSION",
    "GOAL_RUNTIME_VERSION",
    "CapabilityDefinition",
    "GoalBudget",
    "GoalPolicyError",
    "GoalQualification",
    "GoalRuntimeConfig",
    "complete_goal",
    "freeze_goal",
    "goal_capability_message",
    "normalize_decision_plan",
    "qualify_goal",
    "stable_hash",
    "validate_decision",
]
