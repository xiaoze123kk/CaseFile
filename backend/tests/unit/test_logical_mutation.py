from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from casefile.contracts import validate_casefile
from casefile.domain.logical_mutation import (
    CreateObject,
    DeleteObject,
    MutationNormalizationError,
    MutationSet,
    UpdateField,
    analyze_impact,
    compile_logical_graph,
    evaluate_closure_rules,
    normalize_mutation,
)
from casefile.domain.verification_engine import VerificationEngine

ROOT = Path(__file__).resolve().parents[3]


def _restart_loop() -> dict[str, object]:
    return json.loads(
        (ROOT / "fixtures/casefiles/restart_loop.casefile.json").read_text(encoding="utf-8")
    )


def _mutation(*operations: object, actor: str = "agent", mode: str = "normal") -> MutationSet:
    return MutationSet(
        mutation_set_id="mutation_test",
        base_draft_id=1,
        base_revision=1,
        operations=tuple(operations),  # type: ignore[arg-type]
        actor=actor,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
    )


def test_graph_compilation_and_delete_impact_are_deterministic() -> None:
    document = _restart_loop()
    first = compile_logical_graph(document)
    second = compile_logical_graph(copy.deepcopy(document))

    assert first.edges == second.edges
    assert set(
        first.dependents(
            "info_restart_log",
            relations={
                "supports",
                "required_by_hypothesis",
                "required_by_resolution",
                "targets_resolution",
            },
        )
    ) == {
        "claim_backup_trigger",
        "hyp_automatic_restart",
        "res_root_cause",
        "res_shutdown_rule",
    }

    mutation = _mutation(DeleteObject("op_delete_info", "info_restart_log"))
    normalized = normalize_mutation(document, mutation)
    candidate_graph = compile_logical_graph(normalized.candidate_document)
    impact = analyze_impact(first, candidate_graph, mutation)

    assert impact.direct_object_ids == ("claim_backup_trigger", "path_causal_restart")
    assert impact.affected_resolution_ids == ("res_root_cause", "res_shutdown_rule")
    assert ("info_restart_log", "claim_backup_trigger", "res_root_cause") in impact.dependency_paths


def test_delete_only_support_produces_repair_required_issue() -> None:
    document = _restart_loop()
    mutation = _mutation(DeleteObject("op_delete_info", "info_restart_log"))
    normalized = normalize_mutation(document, mutation)
    baseline_graph = compile_logical_graph(document)
    candidate_graph = compile_logical_graph(normalized.candidate_document)

    issues = evaluate_closure_rules(
        document,
        normalized.candidate_document,
        baseline_graph,
        candidate_graph,
        mutation,
    )

    assert "critical_claim_support_lost" in {issue.rule_code for issue in issues}
    assert normalized.candidate_document["claims"][0]["support_refs"] == []


def test_create_replacement_evidence_and_delete_old_support_closes() -> None:
    document = _restart_loop()
    replacement = copy.deepcopy(document["information_units"][0])
    replacement["id"] = "info_restart_log_replacement"
    replacement["title"] = "替代重启日志"
    mutation = _mutation(
        DeleteObject("op_delete_info", "info_restart_log"),
        CreateObject("op_create_info", "information_units", replacement),
    )

    normalized = normalize_mutation(document, mutation)
    validate_casefile(dict(normalized.candidate_document))
    issues = evaluate_closure_rules(
        document,
        normalized.candidate_document,
        compile_logical_graph(document),
        compile_logical_graph(normalized.candidate_document),
        mutation,
    )

    assert "critical_claim_support_lost" not in {issue.rule_code for issue in issues}
    assert normalized.candidate_document["claims"][0]["support_refs"] == [
        {
            "object_type": "information_unit",
            "object_id": "info_restart_log_replacement",
        }
    ]
    assert {item.reason_code for item in normalized.mechanical_operations} >= {
        "deleted_reference_removed",
        "reciprocal_relation_added",
    }


def test_normal_mode_blocks_root_delete_but_restructure_allows_it() -> None:
    document = _restart_loop()
    operation = DeleteObject("op_delete_resolution", "res_root_cause")
    normal = _mutation(operation)
    restructured = _mutation(operation, actor="author", mode="restructure")

    normal_candidate = normalize_mutation(document, normal).candidate_document
    restructure_candidate = normalize_mutation(document, restructured).candidate_document
    normal_codes = {
        issue.rule_code
        for issue in evaluate_closure_rules(
            document,
            normal_candidate,
            compile_logical_graph(document),
            compile_logical_graph(normal_candidate),
            normal,
        )
    }
    restructure_codes = {
        issue.rule_code
        for issue in evaluate_closure_rules(
            document,
            restructure_candidate,
            compile_logical_graph(document),
            compile_logical_graph(restructure_candidate),
            restructured,
        )
    }

    assert "root_structure_delete_requires_explicit_restructure" in normal_codes
    assert "root_structure_delete_requires_explicit_restructure" not in restructure_codes


def test_claim_and_relative_time_cycles_are_reported() -> None:
    document = _restart_loop()
    claim = copy.deepcopy(document["claims"][0])
    claim["dependency_claim_refs"] = [{"object_type": "claim", "object_id": "claim_backup_trigger"}]
    document["claims"][0] = claim
    event = document["events"][0]
    event["time"] = {
        "kind": "relative",
        "anchor_event_ref": {"object_type": "event", "object_id": event["id"]},
        "relation": "after",
        "offset_minutes": 1,
    }
    graph = compile_logical_graph(document)

    assert graph.cycles("claim_dependency")[0].object_ids == ("claim_backup_trigger",)
    assert graph.cycles("relative_time")[0].object_ids == (event["id"],)


def test_operation_dependency_cycle_and_policy_drift_fail_closed() -> None:
    document = _restart_loop()
    stale = MutationSet(
        mutation_set_id="mutation_stale",
        base_draft_id=1,
        base_revision=1,
        operations=(),
        closure_policy_version="logical-mutation-old",
    )
    with pytest.raises(MutationNormalizationError, match="closure_policy_version_stale"):
        normalize_mutation(document, stale)

    create = copy.deepcopy(document["information_units"][0])
    create["id"] = "info_cycle"
    cyclic = _mutation(
        CreateObject("op_create", "information_units", create),
        DeleteObject("op_delete", "info_cycle"),
        UpdateField("op_update", "info_cycle", "/title", "新标题", "替代重启日志"),
    )
    with pytest.raises(MutationNormalizationError, match="operation_dependency_cycle"):
        normalize_mutation(document, cyclic)


def test_mutation_simulation_requires_exact_author_debt_acceptance() -> None:
    document = _restart_loop()
    operation = DeleteObject("op_delete_info", "info_restart_log")
    agent_mutation = _mutation(operation)
    engine = VerificationEngine(draft_revision=1)

    agent_preview = engine.simulate_mutation_set(document, agent_mutation)
    assert agent_preview.can_apply is False
    assert agent_preview.reason_code == "repair_required"
    assert len(agent_preview.authorization_required_finding_keys) == 1
    assert agent_preview.impact_cone is not None
    assert agent_preview.candidate_hash != agent_preview.baseline_hash

    author_mutation = _mutation(operation, actor="author")
    author_preview = engine.simulate_mutation_set(document, author_mutation)
    accepted = engine.simulate_mutation_set(
        document,
        author_mutation,
        accepted_debt_finding_keys=author_preview.authorization_required_finding_keys,
        debt_acceptance_reason="作者接受该 Claim 暂时失去支撑。",
    )
    assert accepted.can_apply is True
    assert accepted.reason_code is None

    invalid = engine.simulate_mutation_set(
        document,
        author_mutation,
        accepted_debt_finding_keys=("det:unrelated",),
        debt_acceptance_reason="错误授权",
    )
    assert invalid.can_apply is False
    assert invalid.reason_code == "debt_acceptance_invalid"


def test_immutable_metadata_and_structure_locks_fail_closed() -> None:
    document = _restart_loop()
    with pytest.raises(MutationNormalizationError, match="immutable_field_update"):
        normalize_mutation(
            document,
            _mutation(
                UpdateField(
                    "op_identity",
                    "res_root_cause",
                    "/id",
                    "res_rewritten",
                    "res_root_cause",
                )
            ),
        )

    mutation = _mutation(
        UpdateField(
            "op_locked_answer",
            "res_root_cause",
            "/accepted_answers",
            [],
            document["resolution_specs"][0]["accepted_answers"],
        ),
        actor="author",
        mode="restructure",
    )
    simulation = VerificationEngine().simulate_mutation_set(document, mutation)
    assert simulation.can_apply is False
    assert simulation.reason_code == "hard_invariant_failed"
    assert "structure_lock_conflict" in {
        finding.rule_code for finding in simulation.final_findings
    }
