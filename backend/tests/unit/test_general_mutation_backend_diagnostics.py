import json
from pathlib import Path

from casefile.application.agent_patch_mutation import (
    exact_history_restore_authorization,
    mutation_from_document_history,
)
from casefile.application.v1_editing import casefile_semantically_equal
from casefile.benchmark.general_mutation_backend_executor import (
    PostgresBackendReleaseExecutor,
    _semantic_delta,
    _semantic_hash,
)
from casefile.benchmark.general_mutation_backend_release import ReleaseTask
from casefile.domain.verification_engine import VerificationEngine


def test_semantic_hash_ignores_object_collection_order() -> None:
    before = {
        "entities": [
            {"id": "ent_a", "name": "A", "revision": 1},
            {"id": "ent_b", "name": "B", "revision": 1},
        ]
    }
    restored = {
        "entities": [
            {"id": "ent_b", "name": "B", "revision": 2},
            {"id": "ent_a", "name": "A", "revision": 2},
        ]
    }

    assert _semantic_hash(before) == _semantic_hash(restored)
    assert _semantic_delta(before, restored)["changed_top_level_fields"] == []


def test_semantic_hash_preserves_real_object_changes() -> None:
    before = {"entities": [{"id": "ent_a", "name": "A"}]}
    changed = {"entities": [{"id": "ent_a", "name": "Changed"}]}

    assert _semantic_hash(before) != _semantic_hash(changed)
    assert _semantic_delta(before, changed)["changed_top_level_fields"] == ["entities"]


def test_persistence_projection_accepts_collection_reordering_only() -> None:
    left = {"entities": [{"id": "ent_a", "name": "A"}, {"id": "ent_b", "name": "B"}]}
    right = {"entities": [{"id": "ent_b", "name": "B"}, {"id": "ent_a", "name": "A"}]}

    assert casefile_semantically_equal(left, right)


def test_exact_atomic_history_restore_authorizes_only_the_saved_document() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[3]
        / "fixtures"
        / "casefiles"
        / "general_mutation_dev_v2.casefile.json"
    )
    target = json.loads(fixture_path.read_text(encoding="utf-8"))
    current = {
        **target,
        "information_units": [
            item for item in target["information_units"] if item["id"] != "info_orphan_note"
        ],
    }
    mutation = mutation_from_document_history(
        current,
        target,
        mutation_set_id="test_history_restore",
        draft_id=1,
        base_revision=2,
    )
    engine = VerificationEngine(profile="fast")
    blocked = engine.simulate_mutation_set(current, mutation)

    finding_keys, reason = exact_history_restore_authorization(blocked, target)

    assert blocked.reason_code == "repair_required"
    assert finding_keys == blocked.authorization_required_finding_keys
    assert reason
    accepted = engine.simulate_mutation_set(
        current,
        mutation,
        accepted_debt_finding_keys=finding_keys,
        debt_acceptance_reason=reason,
        allow_author_debt_acceptance=True,
    )
    assert accepted.can_apply is True
    altered_target = {
        **target,
        "information_units": [
            {**item, "title": "偏离历史的标题"} if item["id"] == "info_orphan_note" else item
            for item in target["information_units"]
        ],
    }
    assert exact_history_restore_authorization(blocked, altered_target) == ((), None)


def _task(expectation: str = "apply") -> ReleaseTask:
    return ReleaseTask(
        task_id="test-task",
        family="abstention_neighbor" if expectation == "abstain" else "existing_update",
        expectation=expectation,  # type: ignore[arg-type]
        fixture="unused.json",
        message="test",
        oracle={},
    )


def test_deterministic_abstention_accepts_zero_model_calls_and_uses_null_na_fields() -> None:
    executor = object.__new__(PostgresBackendReleaseExecutor)
    row = executor._abstention_evidence(
        _task("abstain"),
        1,
        passed=True,
        worker_claimed=True,
        persisted={
            "task_succeeded": True,
            "route_lineage_continuous": True,
            "step_run_persisted": True,
            "model_call_persisted": False,
            "exact_model_observed": False,
            "model_call_count": 0,
            "revision": 2,
            "route_source": "rule_safety",
            "primary_intent": "clarify",
        },
        base_revision=2,
    )

    assert row.classification == "safe_block"
    assert row.patch_set_count == row.model_call_count == 0
    assert row.exact_model_observed is None
    assert row.apply_verified is row.undo_verified is row.redo_verified is None


def test_route_rejection_without_patch_is_not_infrastructure() -> None:
    executor = object.__new__(PostgresBackendReleaseExecutor)
    row = executor._terminal_failure(
        _task(),
        1,
        "pending_patch_missing:rule_safety:general_mutation_target_ambiguous",
        worker_claimed=True,
        persisted={
            "task_succeeded": True,
            "route_lineage_continuous": True,
            "step_run_persisted": True,
            "model_call_persisted": False,
            "exact_model_observed": False,
            "model_call_count": 0,
            "revision": 2,
            "route_source": "rule_safety",
            "primary_intent": "clarify",
            "transport_error_class": None,
        },
        base_revision=2,
    )

    assert row.classification == "routing_failure"
    assert row.failure_stage == "route"
    assert row.infrastructure_failure is None


def test_max_turn_exhaustion_is_protocol_not_lifecycle() -> None:
    executor = object.__new__(PostgresBackendReleaseExecutor)
    row = executor._terminal_failure(
        _task(),
        1,
        "pending_patch_missing:max_turns_exceeded",
        worker_claimed=True,
        persisted={
            "task_succeeded": False,
            "route_lineage_continuous": True,
            "step_run_persisted": False,
            "model_call_persisted": False,
            "exact_model_observed": False,
            "model_call_count": 0,
            "revision": 2,
            "route_source": "rule_capability",
            "primary_intent": "edit_request",
            "transport_error_class": None,
        },
        base_revision=2,
    )

    assert row.classification == "protocol_failure"
    assert row.failure_stage == "model_protocol"
    assert row.infrastructure_failure is None
