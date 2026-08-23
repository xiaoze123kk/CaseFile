from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from casefile.domain.logical_mutation import (
    CLOSURE_POLICY_V1,
    CLOSURE_POLICY_V2,
    ClosureIssue,
    ClosureObjectRef,
    CreateObject,
    DeleteObject,
    MutationSet,
    assess_closure_repair,
)
from casefile.domain.logical_mutation.repair import (
    REPAIR_POLICY_V1,
    repair_policies,
    repair_policy,
)
from casefile.domain.verification_engine import (
    MutationSimulation,
    VerificationEngine,
    VerificationFinding,
)

ROOT = Path(__file__).resolve().parents[3]


def _mutation(*operations: object, actor: str = "agent") -> MutationSet:
    return MutationSet(
        mutation_set_id="repair_contract",
        base_draft_id=7,
        base_revision=11,
        operations=tuple(operations),  # type: ignore[arg-type]
        actor=actor,  # type: ignore[arg-type]
        closure_policy_version=CLOSURE_POLICY_V2,
    )


def _finding(
    rule_code: str,
    *,
    finding_key: str | None = None,
    level: str = "repair_required",
    repair_kinds: tuple[str, ...] = (),
    object_refs: tuple[tuple[str, str], ...] = (("claim_subject", "subject"),),
) -> VerificationFinding:
    return VerificationFinding(
        finding_key=finding_key or f"det:{rule_code}:stable",
        kind="deterministic",
        severity="blocker" if level == "hard_invariant" else "error",
        status="open",
        title=rule_code,
        message=rule_code,
        rule_code=rule_code,
        payload={
            "closure_level": level,
            "closure_policy_version": CLOSURE_POLICY_V2,
            "object_refs": [
                {"object_id": object_id, "role": role}
                for object_id, role in object_refs
            ],
            "repair_kinds": list(repair_kinds),
            "caused_by_operation_ids": [],
            "dependency_path": [],
        },
    )


def _simulation(
    *findings: VerificationFinding,
    candidate_hash: str = "b" * 64,
    introduced: tuple[str, ...] | None = None,
    authorized: tuple[str, ...] | None = None,
    valid: bool = True,
) -> MutationSimulation:
    keys = tuple(finding.finding_key for finding in findings)
    return MutationSimulation(
        valid=valid,
        can_apply=False,
        reason_code="repair_required",
        document={},
        normalized_mutation={} if valid else None,
        impact_cone=None,
        baseline_findings=(),
        final_findings=findings,
        fixed_finding_keys=(),
        introduced_finding_keys=keys if introduced is None else introduced,
        worsened_finding_keys=(),
        residual_target_finding_keys=(),
        authorization_required_finding_keys=(
            keys if authorized is None else authorized
        ),
        baseline_hash="a" * 64,
        candidate_hash=candidate_hash,
        closure_policy_version=CLOSURE_POLICY_V2,
    )


@pytest.mark.parametrize(
    ("rule_code", "repair_kinds", "object_refs"),
    (
        (
            "claim_supported_without_support",
            ("attach_support", "downgrade_claim_status"),
            (("claim_a", "subject"),),
        ),
        (
            "claim_refuted_without_refutation",
            ("attach_refutation", "change_claim_status"),
            (("claim_a", "subject"),),
        ),
        (
            "claim_dependency_incompatible",
            ("repair_dependency_claim", "change_claim_status"),
            (
                ("claim_a", "subject"),
                ("claim_b", "prerequisite"),
                ("claim_c", "prerequisite"),
            ),
        ),
    ),
)
def test_claim_rules_create_stable_agent_obligations(
    rule_code: str,
    repair_kinds: tuple[str, ...],
    object_refs: tuple[tuple[str, str], ...],
) -> None:
    finding = _finding(
        rule_code, repair_kinds=repair_kinds, object_refs=object_refs
    )
    mutation = _mutation()
    first = assess_closure_repair(mutation, _simulation(finding))
    second = assess_closure_repair(
        mutation, _simulation(finding, candidate_hash="c" * 64)
    )

    assert first.status == "eligible"
    assert first.agent_repair_allowed is True
    assert first.obligations[0].obligation_key == second.obligations[0].obligation_key
    assert first.obligations[0].candidate_hash != second.obligations[0].candidate_hash
    assert [ref.role for ref in first.obligations[0].object_refs] == [
        role for _object_id, role in object_refs
    ]
    assert first.obligations[0].allow_create is False
    assert first.obligations[0].allow_delete is False


def test_policy_registry_is_versioned_and_explicitly_classifies_known_rules() -> None:
    policies = repair_policies(version=REPAIR_POLICY_V1)
    assert policies == repair_policies(version=REPAIR_POLICY_V1)
    assert repair_policy(
        "claim_dependency_incompatible", "repair_required"
    ).automation == "agent"
    assert repair_policy(
        "resolution_basis_path_unhealthy", "repair_required"
    ).automation == "manual"
    assert repair_policy("structure_lock_conflict", "hard_invariant").automation == (
        "ineligible"
    )
    assert repair_policy("missing_evidence_assessment", "warning").automation == (
        "ineligible"
    )
    with pytest.raises(ValueError, match="repair_policy_unknown_rule"):
        repair_policy("future_unregistered_rule", "repair_required")


def test_closure_issue_rejects_role_projection_and_duplicate_drift() -> None:
    with pytest.raises(ValueError, match="closure_object_refs_mismatch"):
        ClosureIssue(
            "rule",
            "warning",
            "title",
            "message",
            ("claim_a",),
            (ClosureObjectRef("claim_b", "subject"),),
        )
    with pytest.raises(ValueError, match="closure_object_ref_duplicate"):
        ClosureIssue(
            "rule",
            "warning",
            "title",
            "message",
            ("claim_a", "claim_a"),
            (
                ClosureObjectRef("claim_a", "subject"),
                ClosureObjectRef("claim_a", "subject"),
            ),
        )


def test_manual_and_mixed_obligations_never_allow_partial_agent_repair() -> None:
    agent = _finding(
        "claim_supported_without_support",
        repair_kinds=("attach_support", "downgrade_claim_status"),
    )
    manual = _finding(
        "reasoning_required_path_without_information_input",
        repair_kinds=("attach_information_input", "make_path_optional"),
        object_refs=(("path_a", "path"),),
    )
    assessment = assess_closure_repair(_mutation(), _simulation(agent, manual))

    assert assessment.status == "manual_required"
    assert assessment.agent_repair_allowed is False
    assert assessment.manual_finding_keys == (manual.finding_key,)
    assert {item.automation for item in assessment.obligations} == {"agent", "manual"}


@pytest.mark.parametrize(
    ("rule_code", "repair_kinds", "object_refs"),
    (
        (
            "hypothesis_assessment_status_conflict",
            ("review_assessments", "change_hypothesis_status"),
            (("hypothesis_a", "subject"),),
        ),
        (
            "reasoning_required_path_without_information_input",
            ("attach_information_input", "make_path_optional"),
            (("path_a", "path"),),
        ),
        (
            "resolution_required_claim_incompatible",
            ("repair_required_claim", "make_resolution_undetermined"),
            (("resolution_a", "resolution"), ("claim_a", "prerequisite")),
        ),
        (
            "knowledge_state_available_before_source",
            (),
            (
                ("information_a", "evidence"),
                ("entity_a", "entity"),
                ("event_a", "event"),
            ),
        ),
        (
            "temporal_exclusivity_violation",
            (),
            (
                ("event_a", "event"),
                ("event_b", "event"),
                ("entity_a", "entity"),
            ),
        ),
    ),
)
def test_non_claim_repair_rules_are_explicitly_manual(
    rule_code: str,
    repair_kinds: tuple[str, ...],
    object_refs: tuple[tuple[str, str], ...],
) -> None:
    finding = _finding(
        rule_code, repair_kinds=repair_kinds, object_refs=object_refs
    )
    assessment = assess_closure_repair(_mutation(), _simulation(finding))

    assert assessment.status == "manual_required"
    assert assessment.obligations[0].automation == "manual"
    assert assessment.agent_repair_allowed is False


def test_hard_invariant_blocks_repair_before_agent_obligations_are_exposed() -> None:
    repair = _finding(
        "claim_supported_without_support",
        repair_kinds=("attach_support", "downgrade_claim_status"),
    )
    hard = _finding(
        "structure_lock_conflict",
        level="hard_invariant",
        object_refs=(("claim_a", "subject"), ("lock_a", "lock")),
    )
    assessment = assess_closure_repair(_mutation(), _simulation(repair, hard))

    assert assessment.status == "blocked"
    assert assessment.reason_code == "repair_hard_invariant_present"
    assert assessment.obligations == ()


@pytest.mark.parametrize(
    ("mutation", "simulation", "reason_code"),
    (
        (
            _mutation(actor="author"),
            _simulation(
                _finding(
                    "claim_supported_without_support",
                    repair_kinds=("attach_support", "downgrade_claim_status"),
                )
            ),
            "repair_actor_ineligible",
        ),
        (
            replace(_mutation(), closure_policy_version=CLOSURE_POLICY_V1),
            _simulation(
                _finding(
                    "claim_supported_without_support",
                    repair_kinds=("attach_support", "downgrade_claim_status"),
                )
            ),
            "repair_closure_policy_stale",
        ),
        (
            _mutation(),
            _simulation(
                _finding(
                    "claim_supported_without_support",
                    repair_kinds=("attach_support", "downgrade_claim_status"),
                ),
                introduced=(),
                authorized=(),
            ),
            "repair_no_eligible_blocking_finding",
        ),
    ),
)
def test_ineligible_actor_stale_policy_and_baseline_debt_are_not_applicable(
    mutation: MutationSet,
    simulation: MutationSimulation,
    reason_code: str,
) -> None:
    assessment = assess_closure_repair(mutation, simulation)
    assert assessment.status == "not_applicable"
    assert assessment.reason_code == reason_code


@pytest.mark.parametrize(
    ("finding", "reason_code"),
    (
        (
            _finding("future_unregistered_rule"),
            "repair_policy_unknown_rule",
        ),
        (
            _finding(
                "claim_supported_without_support",
                repair_kinds=("change_claim_status",),
            ),
            "repair_policy_kind_drift",
        ),
        (
            _finding(
                "claim_dependency_incompatible",
                repair_kinds=("repair_dependency_claim", "change_claim_status"),
                object_refs=(("claim_a", "subject"),),
            ),
            "repair_policy_required_role_missing",
        ),
    ),
)
def test_unknown_rule_and_finding_contract_drift_fail_closed(
    finding: VerificationFinding, reason_code: str
) -> None:
    assessment = assess_closure_repair(_mutation(), _simulation(finding))
    assert assessment.status == "blocked"
    assert assessment.reason_code == reason_code
    assert assessment.obligations == ()


def test_mechanical_reference_cleanup_can_create_obligation_without_direct_cause() -> None:
    document: dict[str, Any] = json.loads(
        (ROOT / "fixtures/casefiles/restart_loop.casefile.json").read_text(
            encoding="utf-8"
        )
    )
    mutation = _mutation(DeleteObject("delete_support", "info_restart_log"))
    simulation = VerificationEngine(
        closure_policy_version=CLOSURE_POLICY_V2
    ).simulate_mutation_set(document, mutation)
    assessment = assess_closure_repair(mutation, simulation)
    claim_obligation = next(
        item
        for item in assessment.obligations
        if item.rule_code == "claim_supported_without_support"
    )

    assert assessment.status == "manual_required"
    assert claim_obligation.automation == "agent"
    assert claim_obligation.caused_by_operation_ids == ()


def test_semantic_repair_findings_expose_role_aware_manual_contract() -> None:
    document: dict[str, Any] = json.loads(
        (ROOT / "fixtures/casefiles/m3_reasoning_closure.casefile.json").read_text(
            encoding="utf-8"
        )
    )
    event = dict(document["events"][0])
    event.update(
        id="evt_conflicting_copy",
        location_ref={"object_type": "location", "object_id": "loc_corridor"},
    )
    mutation = _mutation(CreateObject("create_parallel", "events", event))
    simulation = VerificationEngine(
        closure_policy_version=CLOSURE_POLICY_V2
    ).simulate_mutation_set(document, mutation)

    assessment = assess_closure_repair(mutation, simulation)
    temporal = next(
        item
        for item in assessment.obligations
        if item.rule_code == "temporal_exclusivity_violation"
    )

    assert assessment.status == "manual_required"
    assert temporal.automation == "manual"
    assert {ref.role for ref in temporal.object_refs} >= {"event", "entity"}
