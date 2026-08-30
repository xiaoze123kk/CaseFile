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
    GoalAmendmentObligationDraft,
    GoalAmendmentOutput,
    GoalCompletionDecision,
    GoalDecisionOutput,
    GoalObligation,
    GoalObligationDraft,
    GoalObservation,
    GoalPlanItem,
    GoalUnderstandingOutput,
    InvokeCapabilityAction,
)
from casefile.agent_runtime.models import StrictAgentOutput

GOAL_RUNTIME_VERSION = "casefile-chat-goal-runtime-v2"
GOAL_POLICY_VERSION = "casefile-chat-goal-policy-v4"
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
    policy_version: Literal["casefile-chat-goal-policy-v4"] = "casefile-chat-goal-policy-v4"
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
    drafts = _normalize_initial_obligations(output.obligations, source_message)
    obligations: list[GoalObligation] = []
    normalized_message = " ".join(source_message.split())
    for index, draft in enumerate(drafts, start=1):
        normalized_excerpt = " ".join(draft.source_excerpt.split())
        source_excerpt = (
            normalized_excerpt if normalized_excerpt in normalized_message else normalized_message
        )
        if not source_excerpt:
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
                source_excerpt=source_excerpt,
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
    deterministically_ambiguous = _has_unresolved_reference(frozen)
    if output.ambiguous or deterministically_ambiguous:
        reasons.append("goal_ambiguous")
    # ``missing_info`` is model-authored explanatory text, not an authoritative
    # stop signal. Only the typed ambiguous flag and the later deterministic
    # mutation preflight may reject an otherwise self-contained Goal.
    if (output.missing_info and output.ambiguous) or deterministically_ambiguous:
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


def apply_goal_amendment(
    current: FrozenGoal,
    amendment: GoalAmendmentOutput,
    source_message: str,
    *,
    budget: GoalBudget,
) -> FrozenGoal:
    """Validate a model amendment and assign stable server-owned obligation keys."""

    amendment = normalize_goal_amendment(current, amendment, source_message)

    existing = {item.obligation_id: item for item in current.obligations}
    refs = [item.obligation_ref for item in amendment.obligations]
    ref_set = set(refs)
    existing_refs = {ref for ref in refs if ref.startswith("obl_")}
    new_refs = [ref for ref in refs if ref.startswith("new_")]
    unknown_existing = existing_refs - set(existing)
    if unknown_existing:
        raise GoalPolicyError("goal_amendment_obligation_unknown")
    removed = set(existing) - existing_refs
    if amendment.amendment_kind in {"refine", "add_constraint"} and (removed or new_refs):
        raise GoalPolicyError("goal_amendment_shape_invalid")
    if amendment.amendment_kind == "add_obligation" and (removed or not new_refs):
        raise GoalPolicyError("goal_amendment_shape_invalid")
    if amendment.amendment_kind == "remove_obligation" and (not removed or new_refs):
        raise GoalPolicyError("goal_amendment_shape_invalid")
    if len(amendment.obligations) > budget.max_plan_items:
        raise GoalPolicyError("goal_plan_budget_exceeded")
    if any(
        dependency not in ref_set
        for item in amendment.obligations
        for dependency in item.depends_on
    ):
        raise GoalPolicyError("goal_amendment_dependency_unknown")
    if any(item.obligation_ref in item.depends_on for item in amendment.obligations):
        raise GoalPolicyError("goal_amendment_dependency_cycle")

    normalized_message = " ".join(source_message.split())
    if amendment.removal_source_excerpt is not None and (
        " ".join(amendment.removal_source_excerpt.split()) not in normalized_message
    ):
        raise GoalPolicyError("goal_amendment_excerpt_invalid")
    for draft in amendment.obligations:
        prior = existing.get(draft.obligation_ref)
        changed = prior is None or (
            prior.kind != draft.kind
            or prior.target_state != draft.target_state
            or prior.source_excerpt != draft.source_excerpt
        )
        if changed and " ".join(draft.source_excerpt.split()) not in normalized_message:
            raise GoalPolicyError("goal_amendment_excerpt_invalid")

    max_existing = max(int(key.removeprefix("obl_")) for key in existing)
    assigned = {ref: f"obl_{max_existing + offset}" for offset, ref in enumerate(new_refs, start=1)}
    assigned.update({key: key for key in existing_refs})
    obligations = [
        GoalObligation(
            obligation_id=assigned[draft.obligation_ref],
            kind=draft.kind,
            target_state=draft.target_state,
            source_excerpt=draft.source_excerpt,
            depends_on=[assigned[key] for key in draft.depends_on],
        )
        for draft in amendment.obligations
    ]
    _validate_dag(obligations)
    if sum(item.kind == "mutation_proposal" for item in obligations) > (
        budget.max_mutation_proposals
    ):
        raise GoalPolicyError("goal_mutation_budget_exceeded")
    if any(
        item.target_state == "candidate" and not _has_mutation_ancestor(item, obligations)
        for item in obligations
    ):
        raise GoalPolicyError("goal_candidate_before_mutation")
    payload = [item.model_dump(mode="json") for item in obligations]
    return FrozenGoal(
        goal=amendment.goal,
        obligations=obligations,
        source_message_hash=stable_hash(source_message),
        obligations_hash=stable_hash(payload),
    )


def normalize_goal_amendment(
    current: FrozenGoal,
    amendment: GoalAmendmentOutput,
    source_message: str,
) -> GoalAmendmentOutput:
    """Canonicalize safe projection details before strict policy validation.

    The projection owns structural intent. A remove label with no removed
    obligation is therefore a refine, while a paraphrased excerpt on an
    otherwise unchanged obligation can safely point at the complete author
    message. New obligations and capability/target changes remain strict.
    """

    normalized_message = " ".join(source_message.split())
    if normalized_message and len(normalized_message) <= 2_000:
        if (
            _requests_no_mutation(normalized_message)
            and not any(item.kind == "mutation_proposal" for item in current.obligations)
            and amendment.amendment_kind == "remove_obligation"
        ):
            return _canonical_amendment(
                current,
                current.obligations,
                amendment_kind="refine",
                source_message=normalized_message,
                rebind_excerpts=True,
            )
        if _requests_no_mutation(normalized_message) and any(
            item.kind == "mutation_proposal" or item.target_state == "candidate"
            for item in current.obligations
        ):
            retained = [
                item
                for item in current.obligations
                if item.kind != "mutation_proposal" and item.target_state != "candidate"
            ]
            if retained:
                return _canonical_amendment(
                    current,
                    retained,
                    amendment_kind="remove_obligation",
                    source_message=normalized_message,
                    removal_source_excerpt=normalized_message,
                )
        if _is_scope_refine_message(normalized_message):
            return _canonical_amendment(
                current,
                current.obligations,
                amendment_kind="refine",
                source_message=normalized_message,
                rebind_excerpts=True,
            )
        if _is_constraint_message(normalized_message):
            return _canonical_amendment(
                current,
                current.obligations,
                amendment_kind="add_constraint",
                source_message=normalized_message,
            )

    existing = {item.obligation_id: item for item in current.obligations}
    refs = [item.obligation_ref for item in amendment.obligations]
    existing_refs = {ref for ref in refs if ref.startswith("obl_")}
    new_refs = [ref for ref in refs if ref.startswith("new_")]
    removed = set(existing) - existing_refs
    normalized = amendment
    same_projection = not removed and not new_refs
    if (
        normalized.amendment_kind == "refine"
        and same_projection
        and _is_constraint_message(source_message)
    ):
        normalized = normalized.model_copy(update={"amendment_kind": "add_constraint"})
    removal_excerpt = amendment.removal_source_excerpt or ""
    removes_absent_mutation = (
        amendment.amendment_kind == "remove_obligation"
        and not removed
        and not new_refs
        and all(item.kind != "mutation_proposal" for item in current.obligations)
        and any(marker in removal_excerpt for marker in ("修改", "改动", "补丁", "Patch"))
    )
    if removes_absent_mutation:
        normalized = amendment.model_copy(
            update={"amendment_kind": "refine", "removal_source_excerpt": None}
        )

    if (
        normalized.amendment_kind not in {"refine", "add_constraint"}
        or not normalized_message
        or len(normalized_message) > 2_000
    ):
        return normalized

    obligations: list[GoalAmendmentObligationDraft] = []
    changed = False
    scope_refine = normalized.amendment_kind == "refine" and _is_scope_refine_message(
        normalized_message
    )
    for draft in normalized.obligations:
        prior = existing.get(draft.obligation_ref)
        excerpt_is_grounded = " ".join(draft.source_excerpt.split()) in normalized_message
        if (
            normalized.amendment_kind == "add_constraint"
            and prior is not None
            and prior.kind == draft.kind
            and prior.target_state == draft.target_state
        ):
            obligations.append(
                draft.model_copy(
                    update={
                        "source_excerpt": prior.source_excerpt,
                        "depends_on": prior.depends_on,
                    }
                )
            )
            changed = changed or (
                draft.source_excerpt != prior.source_excerpt or draft.depends_on != prior.depends_on
            )
        elif (
            scope_refine
            and prior is not None
            and prior.kind == draft.kind
            and prior.target_state == draft.target_state
        ):
            obligations.append(
                draft.model_copy(
                    update={
                        "source_excerpt": normalized_message,
                        "depends_on": prior.depends_on,
                    }
                )
            )
            changed = changed or (
                draft.source_excerpt != normalized_message or draft.depends_on != prior.depends_on
            )
        if (not obligations or obligations[-1].obligation_ref != draft.obligation_ref) and (
            prior is not None
            and prior.kind == draft.kind
            and prior.target_state == draft.target_state
            and prior.source_excerpt != draft.source_excerpt
            and not excerpt_is_grounded
        ):
            obligations.append(draft.model_copy(update={"source_excerpt": normalized_message}))
            changed = True
        elif not obligations or obligations[-1].obligation_ref != draft.obligation_ref:
            obligations.append(draft)
    return normalized.model_copy(update={"obligations": obligations}) if changed else normalized


def _canonical_amendment(
    current: FrozenGoal,
    obligations: list[GoalObligation],
    *,
    amendment_kind: Literal["refine", "add_constraint", "remove_obligation"],
    source_message: str,
    removal_source_excerpt: str | None = None,
    rebind_excerpts: bool = False,
) -> GoalAmendmentOutput:
    retained_ids = {item.obligation_id for item in obligations}
    return GoalAmendmentOutput(
        amendment_kind=amendment_kind,
        goal=f"{current.goal}；{source_message}",
        obligations=[
            GoalAmendmentObligationDraft(
                obligation_ref=item.obligation_id,
                kind=item.kind,
                target_state=item.target_state,
                source_excerpt=source_message if rebind_excerpts else item.source_excerpt,
                depends_on=[
                    dependency for dependency in item.depends_on if dependency in retained_ids
                ],
            )
            for item in obligations
        ],
        removal_source_excerpt=removal_source_excerpt,
    )


def _normalize_initial_obligations(
    drafts: list[GoalObligationDraft],
    source_message: str,
) -> list[GoalObligationDraft]:
    """Add the grounding step required by an explicitly review-gated edit Goal."""

    if not _requires_analysis_before_review_gated_mutation(source_message):
        return list(drafts)
    normalized_message = " ".join(source_message.split())
    if not normalized_message:
        return list(drafts)
    normalized = list(drafts)
    if not any(item.kind == "audit" for item in normalized) and "审计" in normalized_message:
        if len(normalized) >= 6:
            return normalized
        normalized.append(
            GoalObligationDraft(
                kind="audit",
                target_state="baseline",
                source_excerpt=normalized_message,
                depends_on=[],
            )
        )
    if not any(item.kind == "mutation_proposal" for item in normalized):
        if len(normalized) >= 6 or not _is_explicit_mutation_request(normalized_message):
            return normalized
        normalized.append(
            GoalObligationDraft(
                kind="mutation_proposal",
                target_state="baseline",
                source_excerpt=normalized_message,
                depends_on=[],
            )
        )
    if not any(item.kind == "analysis" for item in normalized):
        if len(normalized) >= 6:
            return normalized
        normalized.insert(
            0,
            GoalObligationDraft(
                kind="analysis",
                target_state="baseline",
                source_excerpt=normalized_message,
                depends_on=[],
            ),
        )
        old_indexes: list[int | None] = [None, *range(1, len(normalized))]
    else:
        first_analysis = next(
            index for index, item in enumerate(normalized, start=1) if item.kind == "analysis"
        )
        reordered_indexes = [
            first_analysis,
            *(index for index in range(1, len(normalized) + 1) if index != first_analysis),
        ]
        normalized = [normalized[index - 1] for index in reordered_indexes]
        old_indexes = list(reordered_indexes)

    new_index_by_old = {
        old_index: new_index
        for new_index, old_index in enumerate(old_indexes, start=1)
        if old_index is not None
    }
    remapped: list[GoalObligationDraft] = []
    for new_index, (_old_index, draft) in enumerate(
        zip(old_indexes, normalized, strict=True), start=1
    ):
        dependencies = sorted(
            {
                new_index_by_old[dependency]
                for dependency in draft.depends_on
                if dependency in new_index_by_old
                and new_index_by_old[dependency] < new_index
            }
        )
        if draft.kind == "audit" and new_index != 1 and 1 not in dependencies:
            dependencies.insert(0, 1)
        if draft.kind == "mutation_proposal":
            audit_indexes = [
                index
                for index, prior in enumerate(remapped, start=1)
                if prior.kind == "audit"
            ]
            if audit_indexes and audit_indexes[-1] not in dependencies:
                dependencies.append(audit_indexes[-1])
        remapped.append(draft.model_copy(update={"depends_on": sorted(set(dependencies))}))
    return remapped


def _has_unresolved_reference(frozen: FrozenGoal) -> bool:
    source = " ".join(item.source_excerpt for item in frozen.obligations)
    return any(marker in source for marker in ("那个事件", "那个人", "那条信息"))


def _requests_no_mutation(source_message: str) -> bool:
    return any(
        marker in source_message
        for marker in (
            "不做修改",
            "不要修改",
            "不修改",
            "不删除",
            "不新增",
            "不创建",
            "不要提出修改",
            "只分析并审计",
            "只分析和审计",
        )
    )


def _is_explicit_mutation_request(source_message: str) -> bool:
    return any(
        marker in source_message
        for marker in ("改成", "修改", "调整", "删除", "新增", "创建", "处理掉")
    )


def _requires_analysis_before_review_gated_mutation(source_message: str) -> bool:
    normalized = " ".join(source_message.split())
    return any(
        marker in normalized
        for marker in (
            "必须等待作者确认",
            "等待作者确认后",
            "经作者确认后",
        )
    )


def _is_constraint_message(source_message: str) -> bool:
    normalized = " ".join(source_message.split())
    if _is_scope_refine_message(normalized):
        return False
    return any(
        marker in normalized
        for marker in (
            "不得",
            "不要",
            "不可",
            "禁止",
            "只给出",
            "只完成",
            "必须",
            "不用确认",
            "自动应用",
        )
    )


def _is_scope_refine_message(source_message: str) -> bool:
    return any(
        marker in source_message
        for marker in (
            "只聚焦",
            "仅聚焦",
            "只关注",
            "其他结论不",
            "不展开",
            "具体强化",
            "进一步强化",
        )
    )


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
    """Rebuild the next action and plan display from frozen server facts."""

    completed = _completed_ids(observations)
    known_ids = {item.obligation_id for item in frozen.obligations}
    if completed == known_ids:
        action: FinishGoalAction | InvokeCapabilityAction = FinishGoalAction()
    elif not isinstance(decision.action, InvokeCapabilityAction):
        action = decision.action
    elif isinstance(decision.action, InvokeCapabilityAction) and _action_is_authorized(
        frozen, decision.action, completed
    ):
        action = decision.action
    else:
        ready = next(
            (
                obligation
                for obligation in frozen.obligations
                if obligation.obligation_id not in completed
                and set(obligation.depends_on).issubset(completed)
            ),
            None,
        )
        if ready is None:
            action = decision.action
        else:
            capability = next(
                definition.capability
                for definition in CAPABILITY_REGISTRY.values()
                if definition.obligation_kind == ready.kind
            )
            action = InvokeCapabilityAction(
                capability=capability,
                obligation_ids=[ready.obligation_id],
                target_state=ready.target_state,
            )
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


def _action_is_authorized(
    frozen: FrozenGoal,
    action: InvokeCapabilityAction,
    completed: set[str],
) -> bool:
    by_id = {item.obligation_id: item for item in frozen.obligations}
    definition = CAPABILITY_REGISTRY[action.capability]
    return bool(action.obligation_ids) and all(
        obligation_id not in completed
        and (obligation := by_id.get(obligation_id)) is not None
        and obligation.kind == definition.obligation_kind
        and obligation.target_state == action.target_state
        and action.target_state in definition.allowed_target_states
        and set(obligation.depends_on).issubset(completed)
        for obligation_id in action.obligation_ids
    )


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
    "apply_goal_amendment",
    "complete_goal",
    "freeze_goal",
    "goal_capability_message",
    "normalize_decision_plan",
    "normalize_goal_amendment",
    "qualify_goal",
    "stable_hash",
    "validate_decision",
]
