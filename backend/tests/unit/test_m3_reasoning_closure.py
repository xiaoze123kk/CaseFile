from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from casefile.contracts import validate_casefile
from casefile.domain.logical_mutation import (
    ACTIVE_APPLY_POLICY,
    CLOSURE_POLICY_V1,
    CLOSURE_POLICY_V2,
    CLOSURE_POLICY_VERSION,
    CreateObject,
    DeleteObject,
    MutationSet,
    UpdateField,
    compile_logical_graph,
)
from casefile.domain.logical_mutation.closure import build_closure_index
from casefile.domain.verification_engine import VerificationEngine

ROOT = Path(__file__).resolve().parents[3]


def _document() -> dict[str, Any]:
    document = json.loads(
        (ROOT / "fixtures/casefiles/m3_reasoning_closure.casefile.json").read_text(
            encoding="utf-8"
        )
    )
    validate_casefile(document)
    return document

def _mutation(*operations: object, actor: str = "agent") -> MutationSet:
    return MutationSet(
        mutation_set_id="m3_test",
        base_draft_id=1,
        base_revision=1,
        operations=tuple(operations),  # type: ignore[arg-type]
        actor=actor,  # type: ignore[arg-type]
        closure_policy_version=CLOSURE_POLICY_V2,
    )


def _simulate(document: dict[str, Any], *operations: object, actor: str = "agent"):
    return VerificationEngine(
        draft_revision=1, closure_policy_version=CLOSURE_POLICY_V2
    ).simulate_mutation_set(document, _mutation(*operations, actor=actor))


def _codes(simulation: object) -> set[str]:
    return {item.rule_code for item in simulation.final_findings}  # type: ignore[attr-defined]


def test_v2_is_active_and_v1_remains_explicitly_available() -> None:
    document = _document()
    assert ACTIVE_APPLY_POLICY == CLOSURE_POLICY_V2
    assert CLOSURE_POLICY_VERSION == CLOSURE_POLICY_V2
    assert MutationSet(
        mutation_set_id="active_default",
        base_draft_id=1,
        base_revision=1,
        operations=(),
    ).closure_policy_version == CLOSURE_POLICY_V2
    operation = UpdateField(
        "op_v1_parity",
        "res_shutdown_rule",
        "/title",
        "v1 parity title",
        document["resolution_specs"][1]["title"],
    )
    v1_mutation = MutationSet(
        mutation_set_id="v1_parity",
        base_draft_id=1,
        base_revision=1,
        operations=(operation,),
        actor="agent",
        closure_policy_version=CLOSURE_POLICY_V1,
    )
    v1_simulation = VerificationEngine(
        closure_policy_version=CLOSURE_POLICY_V1
    ).simulate_mutation_set(document, v1_mutation)
    assert v1_simulation.closure_policy_version == CLOSURE_POLICY_V1
    assert v1_simulation.reason_code is None
    assert not any(
        edge.relation == "assessed_by_hypothesis"
        for edge in compile_logical_graph(document, policy_version=CLOSURE_POLICY_V1).edges
    )
    v2_graph = compile_logical_graph(document, policy_version=CLOSURE_POLICY_V2)
    assert any(
        edge.prerequisite_id == "info_manual_trace"
        and edge.dependent_id == "hyp_automatic_restart"
        and edge.relation == "assessed_by_hypothesis"
        for edge in v2_graph.edges
    )
    simulation = VerificationEngine().simulate_mutation_set(
        document,
        MutationSet(
            mutation_set_id="active_v2_impact",
            base_draft_id=1,
            base_revision=1,
            operations=(
                UpdateField(
                    "op_content",
                    "info_manual_trace",
                    "/content",
                    "控制台人工操作痕迹已复核。",
                    document["information_units"][1]["content"],
                ),
            ),
        ),
    )
    assert simulation.closure_policy_version == CLOSURE_POLICY_V2
    assert simulation.impact_cone is not None
    assert "hyp_automatic_restart" in simulation.impact_cone.direct_object_ids
    assert "res_root_cause" in simulation.impact_cone.affected_resolution_ids


def test_claim_hypothesis_reasoning_and_resolution_rules() -> None:
    document = _document()
    supported = _simulate(
        document,
        UpdateField(
            "op_support",
            "claim_backup_trigger",
            "/support_refs",
            [],
            document["claims"][0]["support_refs"],
        ),
    )
    assert "claim_supported_without_support" in _codes(supported)

    conflict = _simulate(
        document,
        UpdateField(
            "op_assessments",
            "hyp_automatic_restart",
            "/evidence_assessments",
            [
                {
                    "information_ref": {
                        "object_type": "information_unit",
                        "object_id": "info_manual_trace",
                    },
                    "effect": "contradicts",
                    "strength": "strong",
                    "rationale": "只有强反证。",
                }
            ],
            document["hypotheses"][0]["evidence_assessments"],
        ),
    )
    assert "hypothesis_assessment_status_conflict" in _codes(conflict)

    ungrounded_steps = copy.deepcopy(document["reasoning_paths"][0]["steps"])
    ungrounded_steps[0]["input_refs"] = [
        {"object_type": "claim", "object_id": "claim_backup_trigger"}
    ]
    ungrounded = _simulate(
        document,
        UpdateField(
            "op_steps",
            "path_causal_restart",
            "/steps",
            ungrounded_steps,
            document["reasoning_paths"][0]["steps"],
        ),
    )
    assert "reasoning_required_path_without_information_input" in _codes(ungrounded)
    assert "resolution_basis_path_unhealthy" in _codes(ungrounded)

    incompatible = _simulate(
        document,
        UpdateField(
            "op_status",
            "claim_backup_trigger",
            "/status",
            "unsupported",
            "supported",
        ),
    )
    assert "resolution_required_claim_incompatible" in _codes(incompatible)


def test_required_reasoning_path_rejects_refuted_claim_input() -> None:
    document = _document()
    steps = copy.deepcopy(document["reasoning_paths"][0]["steps"])
    steps[0]["input_refs"].append(
        {"object_type": "claim", "object_id": "claim_manual_trigger"}
    )

    simulation = _simulate(
        document,
        UpdateField(
            "op_path_inputs",
            "path_causal_restart",
            "/steps",
            steps,
            document["reasoning_paths"][0]["steps"],
        ),
        UpdateField(
            "op_claim_status",
            "claim_manual_trigger",
            "/status",
            "refuted",
            "supported",
        ),
    )

    assert "reasoning_required_path_incompatible_claim_input" in _codes(simulation)
    assert "resolution_basis_path_unhealthy" in _codes(simulation)


def test_reasoning_claim_input_health_is_deduplicated_and_deterministic() -> None:
    document = _document()
    document["claims"][0]["status"] = "unsupported"
    document["claims"][1]["status"] = "disputed"
    path = document["reasoning_paths"][0]
    path["steps"] = [
        {
            "step_id": "step_read_inputs",
            "input_refs": [
                {"object_type": "information_unit", "object_id": "info_restart_log"},
                {"object_type": "claim", "object_id": "claim_manual_trigger"},
                {"object_type": "claim", "object_id": "claim_backup_trigger"},
            ],
            "operation": "infer",
            "output_ref": {
                "object_type": "claim",
                "object_id": "claim_backup_trigger",
            },
        },
        {
            "step_id": "step_repeat_input",
            "input_refs": [
                {"object_type": "claim", "object_id": "claim_manual_trigger"}
            ],
            "operation": "compare",
            "output_ref": {
                "object_type": "hypothesis",
                "object_id": "hyp_automatic_restart",
            },
        },
    ]

    first = build_closure_index(document).path_health_by_id["path_causal_restart"]
    second = build_closure_index(copy.deepcopy(document)).path_health_by_id[
        "path_causal_restart"
    ]

    assert first.incompatible_claim_input_ids == (
        "claim_backup_trigger",
        "claim_manual_trigger",
    )
    assert first == second
    assert first.healthy_for_resolution is False


def test_existing_incompatible_claim_input_debt_is_grandfathered() -> None:
    document = _document()
    document["claims"][1]["status"] = "unsupported"
    document["reasoning_paths"][0]["steps"][0]["input_refs"].append(
        {"object_type": "claim", "object_id": "claim_manual_trigger"}
    )

    simulation = _simulate(
        document,
        UpdateField(
            "op_unrelated_title",
            "res_shutdown_rule",
            "/title",
            "更新后的安全规则组合",
            document["resolution_specs"][1]["title"],
        ),
    )

    assert "reasoning_required_path_incompatible_claim_input" in _codes(simulation)
    assert simulation.can_apply is True
    assert not simulation.authorization_required_finding_keys


def test_claim_refutation_dependency_and_overlap_levels() -> None:
    document = _document()
    refuted = _simulate(
        document,
        UpdateField(
            "op_refuted",
            "claim_manual_trigger",
            "/status",
            "refuted",
            "supported",
        ),
    )
    assert "claim_refuted_without_refutation" in _codes(refuted)

    dependency = _simulate(
        document,
        UpdateField(
            "op_dependency",
            "claim_backup_trigger",
            "/dependency_claim_refs",
            [{"object_type": "claim", "object_id": "claim_manual_trigger"}],
            [],
        ),
        UpdateField(
            "op_dependency_status",
            "claim_manual_trigger",
            "/status",
            "unresolved",
            "supported",
        ),
    )
    assert "claim_dependency_incompatible" in _codes(dependency)

    overlap = _simulate(
        document,
        UpdateField(
            "op_overlap",
            "claim_backup_trigger",
            "/refute_refs",
            [
                {
                    "object_type": "information_unit",
                    "object_id": "info_restart_log",
                }
            ],
            [],
        ),
    )
    finding = next(
        item
        for item in overlap.final_findings
        if item.rule_code == "claim_support_refute_overlap"
    )
    assert finding.payload["closure_level"] == "warning"
    assert overlap.can_apply is True


def test_sparse_matrix_and_authored_view_expressions_do_not_block() -> None:
    document = _document()
    document["entities"][0]["knowledge_states"][0]["false_belief_refs"] = [
        {"object_type": "claim", "object_id": "claim_backup_trigger"}
    ]
    document["claims"][1]["status"] = "refuted"
    document["information_units"][1]["supports_claim_refs"] = []
    document["information_units"][1]["refutes_claim_refs"] = [
        {"object_type": "claim", "object_id": "claim_manual_trigger"}
    ]
    document["claims"][1]["support_refs"] = []
    document["claims"][1]["refute_refs"] = [
        {"object_type": "information_unit", "object_id": "info_manual_trace"}
    ]
    document["reasoning_paths"][1]["steps"][0]["operation"] = "eliminate"
    document["hypotheses"][0]["evidence_assessments"] = []
    validate_casefile(document)
    findings = VerificationEngine(
        closure_policy_version=CLOSURE_POLICY_V2
    ).evaluate_snapshot_closure(document)
    codes = {item.rule_code for item in findings}
    assert "missing_evidence_assessment" in codes
    assert "knowledge_state_belief_conflict" not in codes
    assert all(
        item.payload["closure_level"] == "warning"
        for item in findings
        if item.rule_code == "missing_evidence_assessment"
    )
    assert not any("refuted" in item.rule_code for item in findings)


def test_reasoning_target_and_step_output_types_are_hard_invariants() -> None:
    document = _document()
    path = document["reasoning_paths"][1]
    path["target_ref"] = {"object_type": "event", "object_id": "evt_restart_seven"}
    path["steps"][0]["output_ref"] = {
        "object_type": "event",
        "object_id": "evt_restart_seven",
    }
    findings = VerificationEngine(
        closure_policy_version=CLOSURE_POLICY_V2
    ).evaluate_snapshot_closure(document)
    by_code = {item.rule_code: item for item in findings}
    assert by_code["reasoning_path_target_type_invalid"].payload[
        "closure_level"
    ] == "hard_invariant"
    assert by_code["reasoning_step_output_type_invalid"].payload[
        "closure_level"
    ] == "hard_invariant"


def test_v2_semantic_bridge_grandfathers_existing_debt_and_requires_exact_authorization() -> None:
    document = _document()
    event = copy.deepcopy(document["events"][0])
    event.update(
        id="evt_parallel",
        title="同时发生的走廊事件",
        location_ref={"object_type": "location", "object_id": "loc_corridor"},
    )
    create = CreateObject("op_event", "events", event)
    agent = _simulate(document, create)
    semantic = [
        item
        for item in agent.final_findings
        if item.rule_code == "temporal_exclusivity_violation"
    ]
    assert len(semantic) == 1
    assert semantic[0].payload["closure_level"] == "repair_required"
    assert agent.can_apply is False
    agent_cannot_accept = VerificationEngine(
        closure_policy_version=CLOSURE_POLICY_V2
    ).simulate_mutation_set(
        document,
        _mutation(create),
        accepted_debt_finding_keys=agent.authorization_required_finding_keys,
        debt_acceptance_reason="Agent 不能代替作者授权。",
    )
    assert agent_cannot_accept.can_apply is False
    assert agent_cannot_accept.reason_code == "repair_required"

    author_mutation = _mutation(create, actor="author")
    engine = VerificationEngine(closure_policy_version=CLOSURE_POLICY_V2)
    author_preview = engine.simulate_mutation_set(document, author_mutation)
    accepted = engine.simulate_mutation_set(
        document,
        author_mutation,
        accepted_debt_finding_keys=author_preview.authorization_required_finding_keys,
        debt_acceptance_reason="作者明确接受该并发事件设定。",
    )
    assert accepted.can_apply is True

    baseline_with_debt = accepted.document
    unrelated = _simulate(
        baseline_with_debt,
        UpdateField(
            "op_title",
            "res_shutdown_rule",
            "/title",
            "更新后的安全规则组合",
            baseline_with_debt["resolution_specs"][1]["title"],
        ),
    )
    assert unrelated.can_apply is True
    assert not unrelated.authorization_required_finding_keys


def test_v2_grandfathers_existing_knowledge_timing_debt() -> None:
    document = _document()
    later_event = copy.deepcopy(document["events"][0])
    later_event.update(
        id="evt_later_source",
        title="稍后产生的信息来源",
        time={"kind": "exact", "value": "2042-06-01T21:00", "precision": "minute"},
    )
    document["events"].append(later_event)
    document["information_units"][0]["source_event_ref"] = {
        "object_type": "event",
        "object_id": "evt_later_source",
    }

    simulation = VerificationEngine().simulate_mutation_set(
        document,
        MutationSet(
            mutation_set_id="existing_knowledge_debt",
            base_draft_id=1,
            base_revision=1,
            operations=(
                UpdateField(
                    "op_unrelated_title",
                    "res_shutdown_rule",
                    "/title",
                    "更新后的安全规则组合",
                    document["resolution_specs"][1]["title"],
                ),
            ),
        ),
    )

    assert "knowledge_state_available_before_source" in _codes(simulation)
    assert simulation.can_apply is True
    assert not simulation.authorization_required_finding_keys


def test_new_object_typed_integration_and_repeatability() -> None:
    document = _document()
    info = copy.deepcopy(document["information_units"][0])
    info.update(
        id="info_unlinked_key",
        title="未接入的关键信息",
        supports_claim_refs=[],
        refutes_claim_refs=[],
    )
    operation = CreateObject("op_info", "information_units", info)
    first = _simulate(document, operation)
    second = _simulate(copy.deepcopy(document), operation)
    matching = [
        item
        for item in first.final_findings
        if item.rule_code == "new_information_unit_not_reasoning_integrated"
    ]
    assert len(matching) == 1
    assert matching[0].payload["closure_level"] == "repair_required"
    assert first.as_dict() == second.as_dict()

    authored_complete = _simulate(document, operation, actor="author")
    authored_complete_finding = next(
        item
        for item in authored_complete.final_findings
        if item.rule_code == "new_information_unit_not_reasoning_integrated"
    )
    assert authored_complete_finding.payload["closure_level"] == "repair_required"
    assert authored_complete.can_apply is False

    background = copy.deepcopy(info)
    background.update(id="info_unlinked_background", classification="background")
    authored_incomplete = _simulate(
        document,
        CreateObject("op_background", "information_units", background),
        actor="author",
    )
    authored_incomplete_finding = next(
        item
        for item in authored_incomplete.final_findings
        if item.rule_code == "new_information_unit_not_reasoning_integrated"
    )
    assert authored_incomplete_finding.payload["closure_level"] == "warning"
    assert authored_incomplete.can_apply is True

    constraint = copy.deepcopy(document["constraints"][0])
    constraint.update(id="con_unlinked_agent", scope_refs=[])
    legacy_degree = _simulate(
        document, CreateObject("op_constraint", "constraints", constraint)
    )
    legacy_finding = next(
        item
        for item in legacy_degree.final_findings
        if item.rule_code == "new_constraint_not_reasoning_integrated"
    )
    assert legacy_finding.payload["closure_level"] == "repair_required"


def test_explicit_one_way_travel_time_is_shadow_warning_only() -> None:
    document = _document()
    event = copy.deepcopy(document["events"][0])
    event.update(
        id="evt_corridor_arrival",
        title="备用系统到达走廊",
        time={"kind": "exact", "value": "2042-06-01T20:04", "precision": "minute"},
        location_ref={"object_type": "location", "object_id": "loc_corridor"},
    )
    simulation = _simulate(document, CreateObject("op_travel", "events", event))
    finding = next(
        item
        for item in simulation.final_findings
        if item.rule_code == "temporal_travel_time_violation"
    )
    assert finding.payload["closure_level"] == "warning"
    assert simulation.can_apply is True

    child_document = _document()
    child_document["locations"][1]["parent_ref"] = {
        "object_type": "location",
        "object_id": "loc_lab",
    }
    child_simulation = _simulate(
        child_document, CreateObject("op_child_travel", "events", event)
    )
    assert "temporal_travel_time_violation" not in _codes(child_simulation)


def test_v2_blocks_existing_hard_debt_unless_normalization_explicitly_allows_it() -> None:
    document = _document()
    document["claims"][1]["dependency_claim_refs"] = [
        {"object_type": "claim", "object_id": "claim_manual_trigger"}
    ]
    unrelated_operation = UpdateField(
        "op_unrelated",
        "res_shutdown_rule",
        "/title",
        "不相关标题修改",
        document["resolution_specs"][1]["title"],
    )
    unrelated = _simulate(document, unrelated_operation)
    assert unrelated.can_apply is False
    assert unrelated.reason_code == "hard_invariant_failed"

    v1_mutation = MutationSet(
        mutation_set_id="m3_v1_existing_hard",
        base_draft_id=1,
        base_revision=1,
        operations=(unrelated_operation,),
        actor="agent",
        closure_policy_version=CLOSURE_POLICY_V1,
    )
    v1_unrelated = VerificationEngine(
        closure_policy_version=CLOSURE_POLICY_V1
    ).simulate_mutation_set(document, v1_mutation)
    assert v1_unrelated.can_apply is False
    assert v1_unrelated.reason_code == "hard_invariant_failed"

    normalization = VerificationEngine(
        closure_policy_version=CLOSURE_POLICY_V2
    ).simulate_mutation_set(
        document,
        _mutation(unrelated_operation),
        allow_existing_hard_invariants=True,
    )
    assert normalization.can_apply is True

    valid_document = _document()
    new_hard = VerificationEngine(
        closure_policy_version=CLOSURE_POLICY_V2
    ).simulate_mutation_set(
        valid_document,
        _mutation(
            UpdateField(
                "op_new_cycle",
                "claim_manual_trigger",
                "/dependency_claim_refs",
                [{"object_type": "claim", "object_id": "claim_manual_trigger"}],
                valid_document["claims"][1]["dependency_claim_refs"],
            )
        ),
        allow_existing_hard_invariants=True,
    )
    assert new_hard.can_apply is False
    assert new_hard.reason_code == "hard_invariant_failed"


def test_v2_invalid_target_remains_a_hard_failure() -> None:
    valid_document = _document()
    invalid_target = _simulate(
        valid_document,
        UpdateField(
            "op_invalid_target",
            "path_manual_restart",
            "/target_ref",
            {"object_type": "event", "object_id": "evt_restart_seven"},
            valid_document["reasoning_paths"][1]["target_ref"],
        ),
        actor="author",
    )
    assert invalid_target.can_apply is False
    assert invalid_target.reason_code in {"hard_invariant_failed", "post_document_invalid"}


def test_pending_v1_mutation_is_stale_under_v2_engine() -> None:
    document = _document()
    mutation = MutationSet(
        mutation_set_id="pending_v1",
        base_draft_id=1,
        base_revision=1,
        operations=(
            UpdateField(
                "op_title",
                "res_shutdown_rule",
                "/title",
                "旧策略待处理修改",
                document["resolution_specs"][1]["title"],
            ),
        ),
        actor="agent",
        closure_policy_version=CLOSURE_POLICY_V1,
    )
    simulation = VerificationEngine(
        closure_policy_version=CLOSURE_POLICY_V2
    ).simulate_mutation_set(document, mutation)
    assert simulation.can_apply is False
    assert simulation.reason_code == "closure_policy_version_stale"


def test_v2_invalid_delete_candidates_report_instead_of_crashing() -> None:
    document = _document()
    resolution_delete = _simulate(
        document,
        DeleteObject("op_delete_resolution", "res_root_cause"),
        actor="author",
    )
    assert resolution_delete.can_apply is False
    assert resolution_delete.reason_code == "post_document_invalid"

    claim_delete = _simulate(
        document,
        DeleteObject("op_delete_claim", "claim_manual_trigger"),
        actor="author",
    )
    assert claim_delete.can_apply is False
    assert claim_delete.reason_code == "post_document_invalid"
