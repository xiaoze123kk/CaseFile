from casefile.application.v1_editing import _casefile_semantically_equal
from casefile.benchmark.general_mutation_backend_executor import (
    PostgresBackendReleaseExecutor,
    _semantic_delta,
    _semantic_hash,
)
from casefile.benchmark.general_mutation_backend_release import ReleaseTask


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

    assert _casefile_semantically_equal(left, right)


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

    assert row.classification == "capability_failure"
    assert row.failure_stage == "route"
    assert row.infrastructure_failure is None
