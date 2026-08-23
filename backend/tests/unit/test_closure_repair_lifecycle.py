from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from casefile.application.closure_repair import (
    closure_repair_envelope,
    primary_mutation_from_suggestions,
    validate_closure_repair_envelope,
)
from casefile.domain.logical_mutation.repair import (
    ClosureRepairContextV1,
    RepairProposal,
    RepairUpdateOperation,
    run_closure_repair,
)
from casefile.domain.verification_engine import VerificationEngine
from casefile.worker.runtime import WorkerConfig

ROOT = Path(__file__).resolve().parents[3]


class _StatusProposer:
    def propose(
        self, context: ClosureRepairContextV1, *, round_no: int
    ) -> RepairProposal:
        assert round_no == 1
        obligation = context.obligations[0]
        return RepairProposal(
            context.context_hash,
            (
                RepairUpdateOperation(
                    (obligation.obligation_key,),
                    obligation.subject_object_ids[0],
                    "/status",
                    "unresolved",
                    "让依赖目标回到未决状态。",
                ),
            ),
        )


def _document() -> dict[str, Any]:
    document = json.loads(
        (ROOT / "fixtures/casefiles/restart_loop.casefile.json").read_text(
            encoding="utf-8"
        )
    )
    template = document["claims"][0]
    prerequisite = deepcopy(template)
    prerequisite.update(
        id="claim_lifecycle_prerequisite",
        title="前置主张",
        statement="隔离的前置主张。",
        dependency_claim_refs=[],
    )
    subject = deepcopy(template)
    subject.update(
        id="claim_lifecycle_subject",
        title="目标主张",
        statement="依赖前置主张。",
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


def _repair_fixture() -> tuple[dict[str, Any], Any, Any]:
    document = _document()
    suggestions = [
        {
            "object_id": "claim_lifecycle_prerequisite",
            "path": "/status",
            "value": "unresolved",
            "reason": "调整前置主张。",
        }
    ]
    mutation = primary_mutation_from_suggestions(
        document,
        draft_id=7,
        base_revision=11,
        task_run_id=42,
        suggestions=suggestions,
    )
    simulation = VerificationEngine(
        closure_policy_version=mutation.closure_policy_version
    ).simulate_mutation_set(document, mutation)
    result = run_closure_repair(
        document,
        mutation,
        simulation,
        _StatusProposer(),
        original_intent="调整前置主张",
    )
    assert result.repaired
    return document, mutation, result


def test_worker_config_defaults_to_shadow_and_rejects_unknown_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CLOSURE_REPAIR_MODE", raising=False)
    assert WorkerConfig.from_environment().closure_repair_mode == "shadow"
    monkeypatch.setenv("CLOSURE_REPAIR_MODE", "suggest")
    assert WorkerConfig.from_environment().closure_repair_mode == "suggest"
    monkeypatch.setenv("CLOSURE_REPAIR_MODE", "automatic")
    with pytest.raises(ValueError, match="CLOSURE_REPAIR_MODE"):
        WorkerConfig.from_environment()


def test_shadow_replays_proof_without_exposing_companion_operations() -> None:
    document, mutation, result = _repair_fixture()
    validation = validate_closure_repair_envelope(
        document,
        mutation,
        closure_repair_envelope(mode="shadow", result=result),
        original_intent="调整前置主张",
    )
    assert validation.status == "repaired"
    assert validation.companion_operations == ()


def test_off_mode_keeps_primary_path_without_repair_envelope() -> None:
    document, mutation, _result = _repair_fixture()
    validation = validate_closure_repair_envelope(
        document,
        mutation,
        None,
        original_intent="调整前置主张",
    )
    assert validation.mode == "off"
    assert validation.companion_operations == ()


def test_suggest_replays_proof_and_preserves_companion_provenance() -> None:
    document, mutation, result = _repair_fixture()
    validation = validate_closure_repair_envelope(
        document,
        mutation,
        closure_repair_envelope(mode="suggest", result=result),
        original_intent="调整前置主张",
    )
    assert validation.status == "repaired"
    assert validation.companion_operations[0]["repair_round"] == 1
    assert validation.companion_operations[0]["obligation_keys"]


def test_application_rejects_tampered_repair_proof() -> None:
    document, mutation, result = _repair_fixture()
    envelope = closure_repair_envelope(mode="suggest", result=result)
    envelope["final_candidate_hash"] = "0" * 64
    with pytest.raises(ValueError, match="repair_envelope_replay_mismatch"):
        validate_closure_repair_envelope(
            document,
            mutation,
            envelope,
            original_intent="调整前置主张",
        )
