from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from casefile.domain.logical_mutation import (
    CLOSURE_POLICY_V2,
    DeleteObject,
    MutationSet,
    RepairProposal,
    RepairUpdateOperation,
    UpdateField,
    run_closure_repair,
)
from casefile.domain.logical_mutation.repair import engine as repair_engine
from casefile.domain.logical_mutation.repair.models import ClosureRepairContextV1
from casefile.domain.verification_engine import MutationSimulation, VerificationEngine

ROOT = Path(__file__).resolve().parents[3]


class _QueueProposer:
    def __init__(
        self,
        *factories: Callable[[ClosureRepairContextV1], RepairProposal],
    ) -> None:
        self.factories = factories
        self.contexts: list[ClosureRepairContextV1] = []

    def propose(
        self, context: ClosureRepairContextV1, *, round_no: int
    ) -> RepairProposal:
        self.contexts.append(context)
        return self.factories[round_no - 1](context)


class _FailingProposer:
    def propose(
        self, context: ClosureRepairContextV1, *, round_no: int
    ) -> RepairProposal:
        del context, round_no
        raise RuntimeError("provider unavailable")


def _document() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(
        (ROOT / "fixtures/casefiles/restart_loop.casefile.json").read_text(
            encoding="utf-8"
        )
    )
    template = document["claims"][0]
    prerequisite = deepcopy(template)
    prerequisite.update(
        id="claim_repair_prerequisite",
        title="修复前置主张",
        statement="这是隔离的前置主张。",
        dependency_claim_refs=[],
    )
    subject = deepcopy(template)
    subject.update(
        id="claim_repair_subject",
        title="修复目标主张",
        statement="这是依赖前置主张的目标。",
        dependency_claim_refs=[
            {"object_type": "claim", "object_id": prerequisite["id"]}
        ],
    )
    document["claims"].extend((prerequisite, subject))
    document["information_units"][0]["supports_claim_refs"].extend(
        (
            {"object_type": "claim", "object_id": prerequisite["id"]},
            {"object_type": "claim", "object_id": subject["id"]},
        )
    )
    return document


def _mutation(*operations: Any) -> MutationSet:
    return MutationSet(
        "repair-engine-test",
        7,
        11,
        tuple(operations),
        actor="agent",
        closure_policy_version=CLOSURE_POLICY_V2,
    )


def _dependency_mutation() -> MutationSet:
    return _mutation(
        UpdateField(
            "primary_status",
            "claim_repair_prerequisite",
            "/status",
            "unresolved",
        )
    )


def _simulate(
    document: dict[str, Any], mutation: MutationSet
) -> MutationSimulation:
    return VerificationEngine(
        closure_policy_version=CLOSURE_POLICY_V2
    ).simulate_mutation_set(document, mutation)


def _status(value: str) -> Callable[[ClosureRepairContextV1], RepairProposal]:
    def factory(context: ClosureRepairContextV1) -> RepairProposal:
        obligation = context.obligations[0]
        return RepairProposal(
            context.context_hash,
            (
                RepairUpdateOperation(
                    (obligation.obligation_key,),
                    obligation.subject_object_ids[0],
                    "/status",
                    value,
                    f"将状态调整为 {value}",
                ),
            ),
        )

    return factory


def _dependency_refs(
    value: list[dict[str, str]],
) -> Callable[[ClosureRepairContextV1], RepairProposal]:
    def factory(context: ClosureRepairContextV1) -> RepairProposal:
        obligation = context.obligations[0]
        return RepairProposal(
            context.context_hash,
            (
                RepairUpdateOperation(
                    (obligation.obligation_key,),
                    obligation.subject_object_ids[0],
                    "/dependency_claim_refs",
                    value,
                    "调整依赖",
                ),
            ),
        )

    return factory


def _run(
    document: dict[str, Any],
    mutation: MutationSet,
    proposer: Any,
    *,
    simulation: MutationSimulation | None = None,
):
    original = simulation or _simulate(document, mutation)
    return run_closure_repair(
        document,
        mutation,
        original,
        proposer,
        original_intent="修改前置主张",
    )


def test_one_round_repair_is_rebased_and_fully_proven() -> None:
    document = _document()
    mutation = _dependency_mutation()

    result = _run(document, mutation, _QueueProposer(_status("unresolved")))

    assert result.status == "repaired"
    assert result.reason_code == "repair_succeeded"
    assert len(result.rounds) == 1
    assert result.rounds[0].outcome == "repaired"
    assert result.final_simulation is not None
    assert result.final_simulation.can_apply is True
    assert result.final_mutation_set is not None
    assert result.final_simulation.document["claims"] != document["claims"]
    assert result.companion_operations[0].object_id == "claim_repair_subject"


@pytest.mark.parametrize(
    ("starting_status", "claim_field", "information_field"),
    (
        ("supported", "support_refs", "supports_claim_refs"),
        ("refuted", "refute_refs", "refutes_claim_refs"),
    ),
)
def test_support_and_refutation_findings_are_repaired_deterministically(
    starting_status: str,
    claim_field: str,
    information_field: str,
) -> None:
    document = _document()
    suffix = "support" if starting_status == "supported" else "refutation"
    information = deepcopy(document["information_units"][0])
    information.update(
        id=f"info_isolated_{suffix}",
        title=f"隔离{suffix}",
        supports_claim_refs=[],
        refutes_claim_refs=[],
    )
    claim = deepcopy(document["claims"][0])
    claim.update(
        id=f"claim_isolated_{suffix}",
        title=f"隔离{suffix}主张",
        statement=f"该主张只有一项{suffix}。",
        support_refs=[],
        refute_refs=[],
        dependency_claim_refs=[],
        status=starting_status,
        materiality="minor",
    )
    reference_to_claim = {"object_type": "claim", "object_id": claim["id"]}
    reference_to_information = {
        "object_type": "information_unit",
        "object_id": information["id"],
    }
    information[information_field] = [reference_to_claim]
    claim[claim_field] = [reference_to_information]
    document["information_units"].append(information)
    document["claims"].append(claim)
    mutation = _mutation(
        UpdateField(
            f"remove_{suffix}",
            claim["id"],
            f"/{claim_field}",
            [],
        )
    )

    result = _run(
        document, mutation, _QueueProposer(_status("unresolved"))
    )

    assert result.status == "repaired"
    assert result.final_simulation is not None
    repaired_claim = next(
        item
        for item in result.final_simulation.document["claims"]
        if item["id"] == claim["id"]
    )
    assert repaired_claim[claim_field] == []
    assert repaired_claim["status"] == "unresolved"


def test_two_round_repair_collapses_same_path_to_final_value() -> None:
    document = _document()
    mutation = _dependency_mutation()

    result = _run(
        document,
        mutation,
        _QueueProposer(_status("refuted"), _status("unresolved")),
    )

    assert result.status == "repaired"
    assert len(result.rounds) == 2
    assert result.rounds[0].obligation_keys_before != (
        result.rounds[0].obligation_keys_after
    )
    assert len(result.companion_operations) == 1
    companion = result.companion_operations[0]
    assert companion.repair_round == 2
    assert companion.new_value == "unresolved"


def test_no_progress_stops_after_first_round() -> None:
    result = _run(
        _document(),
        _dependency_mutation(),
        _QueueProposer(_status("partially_supported")),
    )

    assert result.status == "no_progress"
    assert result.reason_code == "repair_no_progress"
    assert len(result.rounds) == 1


def test_candidate_cycle_stops_when_second_round_restores_first_candidate() -> None:
    result = _run(
        _document(),
        _dependency_mutation(),
        _QueueProposer(_status("refuted"), _status("supported")),
    )

    assert result.status == "cycle_detected"
    assert result.reason_code == "repair_cycle_detected"
    assert len(result.rounds) == 2
    assert result.rounds[-1].candidate_hash_after == (
        result.original_simulation.candidate_hash
    )
    assert result.companion_operations == ()


def test_two_progressing_rounds_that_remain_blocked_are_exhausted() -> None:
    result = _run(
        _document(),
        _dependency_mutation(),
        _QueueProposer(_status("refuted"), _status("partially_supported")),
    )

    assert result.status == "exhausted"
    assert result.reason_code == "repair_exhausted"
    assert len(result.rounds) == 2


@pytest.mark.parametrize(
    ("factory", "reason_code"),
    (
        (
            lambda context: RepairProposal(
                context.context_hash,
                (
                    RepairUpdateOperation(
                        (context.obligations[0].obligation_key,),
                        "claim_backup_trigger",
                        "/status",
                        "unresolved",
                        "越界对象",
                    ),
                ),
            ),
            "repair_scope_violation",
        ),
        (
            lambda context: RepairProposal(
                context.context_hash,
                (
                    RepairUpdateOperation(
                        (context.obligations[0].obligation_key,),
                        "claim_repair_subject",
                        "/title",
                        "越界字段",
                        "越界字段",
                    ),
                ),
            ),
            "repair_scope_violation",
        ),
        (
            lambda context: RepairProposal(
                context.context_hash,
                (
                    RepairUpdateOperation(
                        ("unknown-obligation",),
                        "claim_repair_subject",
                        "/status",
                        "unresolved",
                        "未知义务",
                    ),
                ),
            ),
            "repair_proposal_obligation_unknown",
        ),
        (
            lambda context: RepairProposal(
                context.context_hash,
                (
                    RepairUpdateOperation(
                        (context.obligations[0].obligation_key,),
                        "claim_repair_subject",
                        "/status",
                        42,
                        "非法值",
                    ),
                ),
            ),
            "repair_proposal_value_invalid",
        ),
        (
            lambda context: RepairProposal(
                "f" * 64,
                (
                    RepairUpdateOperation(
                        (context.obligations[0].obligation_key,),
                        "claim_repair_subject",
                        "/status",
                        "unresolved",
                        "过期上下文",
                    ),
                ),
            ),
            "repair_proposal_context_stale",
        ),
    ),
)
def test_proposal_contract_violations_reject_the_whole_round(
    factory: Callable[[ClosureRepairContextV1], RepairProposal],
    reason_code: str,
) -> None:
    result = _run(
        _document(), _dependency_mutation(), _QueueProposer(factory)
    )

    assert result.status == "proposal_rejected"
    assert result.reason_code == reason_code
    assert result.rounds == ()
    assert result.companion_operations == ()


def test_unknown_dependency_reference_is_rejected() -> None:
    result = _run(
        _document(),
        _dependency_mutation(),
        _QueueProposer(
            _dependency_refs(
                [{"object_type": "claim", "object_id": "claim_unknown"}]
            )
        ),
    )

    assert result.status == "proposal_rejected"
    assert result.reason_code == "repair_proposal_reference_invalid"


def test_round_and_operation_budgets_fail_closed() -> None:
    context_holder: list[ClosureRepairContextV1] = []

    def too_many(context: ClosureRepairContextV1) -> RepairProposal:
        context_holder.append(context)
        operation = RepairUpdateOperation(
            (context.obligations[0].obligation_key,),
            "claim_repair_subject",
            "/status",
            "unresolved",
            "超出预算",
        )
        return RepairProposal(context.context_hash, (operation,) * 9)

    result = _run(
        _document(), _dependency_mutation(), _QueueProposer(too_many)
    )

    assert context_holder[0].max_operations == 4
    assert result.status == "proposal_rejected"
    assert result.reason_code == "repair_operation_budget_exceeded"
    with pytest.raises(ValueError, match="repair_round_budget_invalid"):
        run_closure_repair(
            _document(),
            _dependency_mutation(),
            _simulate(_document(), _dependency_mutation()),
            _QueueProposer(_status("unresolved")),
            original_intent="修改",
            max_rounds=3,
        )


def test_proposer_failure_is_a_stable_rejection() -> None:
    result = _run(_document(), _dependency_mutation(), _FailingProposer())

    assert result.status == "proposal_rejected"
    assert result.reason_code == "repair_proposer_failed"


def test_manual_and_hard_initial_findings_never_call_proposer() -> None:
    manual_document = _document()
    manual_mutation = _mutation(
        UpdateField(
            "weaken_required_claim",
            "claim_backup_trigger",
            "/status",
            "unresolved",
        )
    )
    manual_proposer = _QueueProposer(_status("unresolved"))
    manual = _run(manual_document, manual_mutation, manual_proposer)
    assert manual.status == "manual_required"
    assert manual_proposer.contexts == []

    hard_document = _document()
    hard_mutation = _mutation(
        UpdateField(
            "self_dependency",
            "claim_repair_subject",
            "/dependency_claim_refs",
            [
                {
                    "object_type": "claim",
                    "object_id": "claim_repair_subject",
                }
            ],
        )
    )
    hard_proposer = _QueueProposer(_status("unresolved"))
    hard = _run(hard_document, hard_mutation, hard_proposer)
    assert hard.status == "blocked"
    assert hard.reason_code == "repair_hard_invariant_present"
    assert hard_proposer.contexts == []


def test_proposal_that_creates_manual_obligation_stops_immediately() -> None:
    document = _document()
    document["hypotheses"][0]["required_claim_refs"].append(
        {"object_type": "claim", "object_id": "claim_repair_subject"}
    )

    result = _run(
        document,
        _dependency_mutation(),
        _QueueProposer(_status("unresolved")),
    )

    assert result.status == "manual_required"
    assert result.reason_code == "repair_manual_required"
    assert len(result.rounds) == 1


def test_protected_original_path_requires_intent_revision() -> None:
    document = _document()
    lonely = deepcopy(document["claims"][0])
    lonely.update(
        id="claim_lonely",
        title="无支撑主张",
        statement="该主张没有支撑。",
        support_refs=[],
        refute_refs=[],
        dependency_claim_refs=[],
        status="unresolved",
    )
    document["claims"].append(lonely)
    mutation = _mutation(
        UpdateField("assert_lonely", "claim_lonely", "/status", "supported")
    )
    proposer = _QueueProposer(_status("unresolved"))

    result = _run(document, mutation, proposer)

    assert result.status == "intent_revision_required"
    assert result.reason_code == "repair_requires_intent_revision"
    assert proposer.contexts == []


def test_delete_projection_is_regenerated_by_document_diff_rebase() -> None:
    document = _document()
    information = deepcopy(document["information_units"][0])
    information.update(
        id="info_repair_isolated",
        title="隔离支撑",
        supports_claim_refs=[
            {"object_type": "claim", "object_id": "claim_repair_isolated"}
        ],
    )
    claim = deepcopy(document["claims"][0])
    claim.update(
        id="claim_repair_isolated",
        title="隔离主张",
        statement="该主张只依赖隔离支撑。",
        support_refs=[
            {
                "object_type": "information_unit",
                "object_id": information["id"],
            }
        ],
        dependency_claim_refs=[],
    )
    document["information_units"].append(information)
    document["claims"].append(claim)
    mutation = _mutation(DeleteObject("delete_support", information["id"]))

    result = _run(
        document, mutation, _QueueProposer(_status("unresolved"))
    )

    assert result.status == "repaired"
    assert result.final_mutation_set is not None
    assert any(
        isinstance(operation, DeleteObject)
        for operation in result.final_mutation_set.operations
    )
    assert result.final_simulation is not None
    repaired_claim = next(
        item
        for item in result.final_simulation.document["claims"]
        if item["id"] == claim["id"]
    )
    assert repaired_claim["support_refs"] == []
    assert repaired_claim["status"] == "unresolved"


def test_original_simulation_and_final_rebase_mismatch_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = _document()
    mutation = _dependency_mutation()
    simulation = _simulate(document, mutation)
    stale = replace(simulation, candidate_hash="f" * 64)
    mismatch = _run(
        document,
        mutation,
        _QueueProposer(_status("unresolved")),
        simulation=stale,
    )
    assert mismatch.status == "blocked"
    assert mismatch.reason_code == "repair_original_simulation_mismatch"

    monkeypatch.setattr(
        repair_engine,
        "build_mutation_from_document_diff",
        lambda *_args, **_kwargs: mutation,
    )
    rebase = _run(
        document, mutation, _QueueProposer(_status("unresolved"))
    )
    assert rebase.status == "rebase_mismatch"
    assert rebase.reason_code == "repair_rebase_mismatch"
