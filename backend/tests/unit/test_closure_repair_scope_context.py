from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from casefile.domain.logical_mutation import (
    CLOSURE_POLICY_V2,
    ClosureObjectRef,
    CreateObject,
    DeleteObject,
    ImpactCone,
    LogicEdge,
    MutationSet,
    UpdateField,
    assess_closure_repair,
    build_closure_repair_context,
    build_repair_scope,
)
from casefile.domain.logical_mutation.repair import (
    REPAIR_POLICY_V1,
    ClosureObligation,
    ClosureRepairAssessment,
    RepairContextError,
    RepairScopeError,
)
from casefile.domain.verification_engine import MutationSimulation, VerificationFinding


def _claim(object_id: str, **values: Any) -> dict[str, Any]:
    return {"id": object_id, "status": "supported", **values}


def _mutation(*operations: Any) -> MutationSet:
    return MutationSet(
        "scope-test",
        7,
        11,
        tuple(operations),
        actor="agent",
        closure_policy_version=CLOSURE_POLICY_V2,
    )


def _finding(
    rule_code: str,
    repair_kinds: tuple[str, ...],
    refs: tuple[tuple[str, str], ...],
    *,
    dependency_path: tuple[str, ...] = (),
) -> VerificationFinding:
    return VerificationFinding(
        finding_key=f"det:{rule_code}:{refs[0][0]}",
        kind="deterministic",
        severity="error",
        status="open",
        title=rule_code,
        message=rule_code,
        rule_code=rule_code,
        payload={
            "closure_level": "repair_required",
            "closure_policy_version": CLOSURE_POLICY_V2,
            "object_refs": [
                {"object_id": object_id, "role": role}
                for object_id, role in refs
            ],
            "repair_kinds": list(repair_kinds),
            "caused_by_operation_ids": [],
            "dependency_path": list(dependency_path),
        },
    )


def _simulation(
    document: dict[str, Any],
    *findings: VerificationFinding,
    mechanical: tuple[dict[str, Any], ...] = (),
    impact: ImpactCone | None = None,
    candidate_hash: str = "b" * 64,
) -> MutationSimulation:
    keys = tuple(item.finding_key for item in findings)
    return MutationSimulation(
        valid=True,
        can_apply=False,
        reason_code="repair_required",
        document=document,
        normalized_mutation={"mechanical_operations": list(mechanical)},
        impact_cone=impact,
        baseline_findings=(),
        final_findings=findings,
        fixed_finding_keys=(),
        introduced_finding_keys=keys,
        worsened_finding_keys=(),
        residual_target_finding_keys=(),
        authorization_required_finding_keys=keys,
        baseline_hash="a" * 64,
        candidate_hash=candidate_hash,
        closure_policy_version=CLOSURE_POLICY_V2,
    )


def _eligible(
    mutation: MutationSet, simulation: MutationSimulation
) -> ClosureRepairAssessment:
    result = assess_closure_repair(mutation, simulation)
    assert result.status == "eligible"
    return result


@pytest.mark.parametrize(
    ("rule_code", "repair_kinds", "expected_kinds", "expected_paths"),
    (
        (
            "claim_supported_without_support",
            ("attach_support", "downgrade_claim_status"),
            ("downgrade_claim_status",),
            ("/status",),
        ),
        (
            "claim_refuted_without_refutation",
            ("attach_refutation", "change_claim_status"),
            ("change_claim_status",),
            ("/status",),
        ),
        (
            "claim_dependency_incompatible",
            ("repair_dependency_claim", "change_claim_status"),
            ("repair_dependency_claim", "change_claim_status"),
            ("/dependency_claim_refs", "/status"),
        ),
    ),
)
def test_claim_rules_have_strict_local_scope(
    rule_code: str,
    repair_kinds: tuple[str, ...],
    expected_kinds: tuple[str, ...],
    expected_paths: tuple[str, ...],
) -> None:
    refs = (("claim_a", "subject"),)
    document = {"claims": [_claim("claim_a")]}
    if rule_code == "claim_dependency_incompatible":
        refs = (("claim_a", "subject"), ("claim_b", "prerequisite"))
        document["claims"].append(_claim("claim_b"))
    finding = _finding(rule_code, repair_kinds, refs)
    mutation = _mutation()
    simulation = _simulation(document, finding)

    scope = build_repair_scope(mutation, simulation, _eligible(mutation, simulation))

    assert scope.read_write_object_ids == ("claim_a",)
    assert scope.read_only_object_ids == (() if len(refs) == 1 else ("claim_b",))
    assert scope.obligations[0].effective_repair_kinds == expected_kinds
    assert scope.allowed_paths_for("claim_a") == expected_paths


def test_primary_status_requires_intent_revision_for_support_repair() -> None:
    finding = _finding(
        "claim_supported_without_support",
        ("attach_support", "downgrade_claim_status"),
        (("claim_a", "subject"),),
    )
    mutation = _mutation(UpdateField("primary", "claim_a", "/status", "supported"))
    simulation = _simulation({"claims": [_claim("claim_a")]}, finding)

    with pytest.raises(RepairScopeError, match="repair_requires_intent_revision"):
        build_repair_scope(mutation, simulation, _eligible(mutation, simulation))


@pytest.mark.parametrize(
    ("protected_path", "remaining_path", "remaining_kind"),
    (
        ("/status", "/dependency_claim_refs", "repair_dependency_claim"),
        ("/dependency_claim_refs", "/status", "change_claim_status"),
        ("/dependency_claim_refs/0", "/status", "change_claim_status"),
    ),
)
def test_dependency_scope_removes_primary_path_overlap(
    protected_path: str, remaining_path: str, remaining_kind: str
) -> None:
    finding = _finding(
        "claim_dependency_incompatible",
        ("repair_dependency_claim", "change_claim_status"),
        (("claim_a", "subject"), ("claim_b", "prerequisite")),
    )
    mutation = _mutation(UpdateField("primary", "claim_a", protected_path, None))
    simulation = _simulation(
        {"claims": [_claim("claim_a"), _claim("claim_b")]}, finding
    )

    scope = build_repair_scope(mutation, simulation, _eligible(mutation, simulation))

    assert scope.allowed_paths_for("claim_a") == (remaining_path,)
    assert scope.obligations[0].effective_repair_kinds == (remaining_kind,)


@pytest.mark.parametrize(
    "operation",
    (
        CreateObject("create", "claims", _claim("claim_a")),
        DeleteObject("delete", "claim_a"),
    ),
)
def test_create_and_delete_protect_whole_subject(operation: Any) -> None:
    finding = _finding(
        "claim_supported_without_support",
        ("attach_support", "downgrade_claim_status"),
        (("claim_a", "subject"),),
    )
    mutation = _mutation(operation)
    simulation = _simulation({"claims": [_claim("claim_a")]}, finding)

    with pytest.raises(RepairScopeError, match="repair_requires_intent_revision"):
        build_repair_scope(mutation, simulation, _eligible(mutation, simulation))


def test_mechanical_path_cannot_be_overwritten() -> None:
    finding = _finding(
        "claim_supported_without_support",
        ("attach_support", "downgrade_claim_status"),
        (("claim_a", "subject"),),
    )
    mutation = _mutation()
    simulation = _simulation(
        {"claims": [_claim("claim_a")]},
        finding,
        mechanical=(
            {
                "operation_id": "mechanical-1",
                "object_id": "claim_a",
                "field_path": "/status",
            },
        ),
    )

    with pytest.raises(RepairScopeError, match="repair_requires_intent_revision"):
        build_repair_scope(mutation, simulation, _eligible(mutation, simulation))


@pytest.mark.parametrize("locked_path", ("/status", "", "/status/value"))
def test_matching_structure_lock_blocks_overlapping_path(locked_path: str) -> None:
    finding = _finding(
        "claim_supported_without_support",
        ("attach_support", "downgrade_claim_status"),
        (("claim_a", "subject"),),
    )
    document = {
        "claims": [_claim("claim_a")],
        "structure_locks": [
            {
                "id": "lock_a",
                "object_ref": {"object_type": "claim", "object_id": "claim_a"},
                "field_paths": [locked_path],
            }
        ],
    }
    mutation = _mutation()
    simulation = _simulation(document, finding)

    with pytest.raises(RepairScopeError, match="repair_requires_intent_revision"):
        build_repair_scope(mutation, simulation, _eligible(mutation, simulation))


def test_unrelated_lock_is_not_in_scope() -> None:
    finding = _finding(
        "claim_supported_without_support",
        ("attach_support", "downgrade_claim_status"),
        (("claim_a", "subject"),),
    )
    document = {
        "claims": [_claim("claim_a")],
        "structure_locks": [
            {
                "id": "lock_a",
                "object_ref": {"object_type": "claim", "object_id": "claim_a"},
                "field_paths": ["/title"],
            }
        ],
    }
    mutation = _mutation()
    simulation = _simulation(document, finding)
    scope = build_repair_scope(mutation, simulation, _eligible(mutation, simulation))

    assert scope.structure_lock_ids == ()
    assert "lock_a" not in scope.read_only_object_ids


def test_matching_lock_is_read_only_when_another_repair_path_remains() -> None:
    finding = _finding(
        "claim_dependency_incompatible",
        ("repair_dependency_claim", "change_claim_status"),
        (("claim_a", "subject"), ("claim_b", "prerequisite")),
    )
    document = {
        "claims": [_claim("claim_a"), _claim("claim_b")],
        "structure_locks": [
            {
                "id": "lock_a",
                "object_ref": {"object_type": "claim", "object_id": "claim_a"},
                "field_paths": ["/status"],
            }
        ],
    }
    mutation = _mutation()
    simulation = _simulation(document, finding)
    assessment = _eligible(mutation, simulation)

    scope = build_repair_scope(mutation, simulation, assessment)
    context = build_closure_repair_context(
        mutation, simulation, assessment, scope, original_intent="修正依赖"
    )

    assert scope.allowed_paths_for("claim_a") == ("/dependency_claim_refs",)
    assert scope.structure_lock_ids == ("lock_a",)
    assert scope.read_only_object_ids == ("claim_b", "lock_a")
    lock = next(item for item in context.objects if item.object_id == "lock_a")
    assert lock.access == "read_only"


def test_context_excludes_unscoped_impact_objects_and_edges() -> None:
    finding = _finding(
        "claim_dependency_incompatible",
        ("repair_dependency_claim", "change_claim_status"),
        (("claim_a", "subject"), ("claim_b", "prerequisite")),
        dependency_path=("claim_a", "claim_b"),
    )
    impact = ImpactCone(
        ("claim_a",),
        ("claim_b", "claim_noise"),
        (),
        (
            LogicEdge("claim_b", "claim_a", "dependency", "hard", "/refs"),
            LogicEdge("claim_noise", "claim_a", "dependency", "hard", "/refs"),
        ),
        (),
        (),
        (),
    )
    document = {
        "claims": [
            _claim(
                "claim_a",
                dependency_claim_refs=[
                    {"object_type": "claim", "object_id": "claim_b"},
                    {"object_type": "claim", "object_id": "claim_noise"},
                ],
            ),
            _claim("claim_b"),
            _claim("claim_noise"),
        ]
    }
    mutation = _mutation()
    simulation = _simulation(document, finding, impact=impact)
    assessment = _eligible(mutation, simulation)
    scope = build_repair_scope(mutation, simulation, assessment)

    context = build_closure_repair_context(
        mutation, simulation, assessment, scope, original_intent="修正依赖"
    )

    assert [item.object_id for item in context.objects] == ["claim_a", "claim_b"]
    assert len(context.relevant_edges) == 1
    assert context.relevant_edges[0]["prerequisite_id"] == "claim_b"
    assert context.relevant_edges[0]["dependent_id"] == "claim_a"
    assert context.dependency_paths == (("claim_a", "claim_b"),)
    assert [
        (item.object_id, item.field_path) for item in context.allowed_writes
    ] == [
        ("claim_a", "/dependency_claim_refs"),
        ("claim_a", "/status"),
    ]
    dependency_write = context.allowed_writes[0]
    assert dependency_write.obligation_keys == (context.obligations[0].obligation_key,)
    assert dependency_write.current_value == [
        {"object_type": "claim", "object_id": "claim_b"},
        {"object_type": "claim", "object_id": "claim_noise"},
    ]
    assert dependency_write.value_schema["items"]["properties"]["object_id"][
        "enum"
    ] == ["claim_b"]
    status_write = context.allowed_writes[1]
    assert status_write.value_schema == {
        "type": "string",
        "enum": [
            "unsupported",
            "partially_supported",
            "supported",
            "refuted",
            "disputed",
            "unresolved",
        ],
    }


def test_context_hash_is_stable_and_binds_candidate_and_intent() -> None:
    finding = _finding(
        "claim_supported_without_support",
        ("attach_support", "downgrade_claim_status"),
        (("claim_a", "subject"),),
    )
    mutation = _mutation()
    first_simulation = _simulation(
        {"claims": [{"status": "supported", "id": "claim_a"}]}, finding
    )
    first_assessment = _eligible(mutation, first_simulation)
    first_scope = build_repair_scope(mutation, first_simulation, first_assessment)
    first = build_closure_repair_context(
        mutation, first_simulation, first_assessment, first_scope, original_intent="修正"
    )
    repeated = build_closure_repair_context(
        mutation, first_simulation, first_assessment, first_scope, original_intent="修正"
    )
    reordered_simulation = _simulation(
        {"claims": [_claim("claim_a")]}, finding
    )
    reordered_assessment = _eligible(mutation, reordered_simulation)
    reordered_scope = build_repair_scope(
        mutation, reordered_simulation, reordered_assessment
    )
    reordered = build_closure_repair_context(
        mutation,
        reordered_simulation,
        reordered_assessment,
        reordered_scope,
        original_intent="修正",
    )
    changed_simulation = _simulation(
        {"claims": [_claim("claim_a")]}, finding, candidate_hash="c" * 64
    )
    changed_assessment = _eligible(mutation, changed_simulation)
    changed_scope = build_repair_scope(mutation, changed_simulation, changed_assessment)
    candidate_changed = build_closure_repair_context(
        mutation,
        changed_simulation,
        changed_assessment,
        changed_scope,
        original_intent="修正",
    )
    intent_changed = build_closure_repair_context(
        mutation, first_simulation, first_assessment, first_scope, original_intent="另一意图"
    )

    assert first.context_hash == repeated.context_hash
    assert first.context_hash == reordered.context_hash
    assert first.context_hash != candidate_changed.context_hash
    assert first.context_hash != intent_changed.context_hash


def test_missing_object_and_scope_drift_fail_closed() -> None:
    finding = _finding(
        "claim_supported_without_support",
        ("attach_support", "downgrade_claim_status"),
        (("claim_a", "subject"),),
    )
    mutation = _mutation()
    missing_simulation = _simulation({"claims": []}, finding)
    missing_assessment = _eligible(mutation, missing_simulation)
    with pytest.raises(RepairScopeError, match="repair_scope_object_missing"):
        build_repair_scope(mutation, missing_simulation, missing_assessment)

    simulation = _simulation({"claims": [_claim("claim_a")]}, finding)
    assessment = _eligible(mutation, simulation)
    scope = build_repair_scope(mutation, simulation, assessment)
    with pytest.raises(RepairContextError, match="repair_context_scope_invalid"):
        build_closure_repair_context(
            mutation,
            simulation,
            assessment,
            replace(scope, candidate_hash="c" * 64),
            original_intent="修正",
        )


def _obligation(index: int, refs: tuple[ClosureObjectRef, ...]) -> ClosureObligation:
    return ClosureObligation(
        obligation_key=f"obligation-{index}",
        source_finding_key=f"finding-{index}",
        rule_code="claim_dependency_incompatible",
        closure_policy_version=CLOSURE_POLICY_V2,
        repair_policy_version=REPAIR_POLICY_V1,
        base_draft_id=7,
        base_revision=11,
        candidate_hash="b" * 64,
        object_refs=refs,
        caused_by_operation_ids=(),
        dependency_path=(),
        allowed_repair_kinds=("repair_dependency_claim", "change_claim_status"),
        automation="agent",
        max_operations=4,
        allow_create=False,
        allow_delete=False,
    )


def test_scope_object_and_operation_budgets_fail_closed() -> None:
    mutation = _mutation()
    context_refs = (ClosureObjectRef("subject", "subject"),) + tuple(
        ClosureObjectRef(f"prerequisite-{index}", "prerequisite")
        for index in range(24)
    )
    context_document = {
        "claims": [_claim(ref.object_id) for ref in context_refs]
    }
    context_assessment = ClosureRepairAssessment(
        "eligible", "repair_agent_eligible", (_obligation(0, context_refs),)
    )
    context_simulation = _simulation(context_document)
    with pytest.raises(RepairScopeError, match="repair_scope_too_large"):
        build_repair_scope(mutation, context_simulation, context_assessment)

    write_obligations = tuple(
        _obligation(
            index,
            (
                ClosureObjectRef(f"subject-{index}", "subject"),
                ClosureObjectRef("p", "prerequisite"),
            ),
        )
        for index in range(7)
    )
    write_assessment = ClosureRepairAssessment(
        "eligible", "repair_agent_eligible", write_obligations
    )
    write_simulation = _simulation(
        {
            "claims": [
                *(_claim(f"subject-{index}") for index in range(7)),
                _claim("p"),
            ]
        }
    )
    with pytest.raises(RepairScopeError, match="repair_scope_too_large"):
        build_repair_scope(mutation, write_simulation, write_assessment)

    many = tuple(
        replace(
            _obligation(
                index,
                (
                    ClosureObjectRef("subject", "subject"),
                    ClosureObjectRef("p", "prerequisite"),
                ),
            ),
            source_finding_key=f"finding-{index}",
        )
        for index in range(9)
    )
    operation_assessment = ClosureRepairAssessment(
        "eligible", "repair_agent_eligible", many
    )
    operation_simulation = _simulation(
        {"claims": [_claim("subject"), _claim("p")]}
    )
    with pytest.raises(RepairScopeError, match="repair_operation_budget_exceeded"):
        build_repair_scope(mutation, operation_simulation, operation_assessment)


def test_manual_assessment_and_policy_drift_are_rejected() -> None:
    mutation = _mutation()
    simulation = _simulation({"claims": [_claim("subject"), _claim("p")]})
    obligation = _obligation(
        0,
        (
            ClosureObjectRef("subject", "subject"),
            ClosureObjectRef("p", "prerequisite"),
        ),
    )
    manual = ClosureRepairAssessment("manual_required", "repair_manual_required")
    with pytest.raises(RepairScopeError, match="repair_scope_assessment_ineligible"):
        build_repair_scope(mutation, simulation, manual)

    drifted = ClosureRepairAssessment(
        "eligible",
        "repair_agent_eligible",
        (replace(obligation, repair_policy_version="future-policy"),),
    )
    with pytest.raises(RepairScopeError, match="repair_scope_policy_mismatch"):
        build_repair_scope(mutation, simulation, drifted)
