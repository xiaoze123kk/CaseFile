"""Safe Patch Registry compilation and deterministic materialization tests."""

from __future__ import annotations

from casefile.agent_runtime.chat_safe_patches import (
    compile_safe_patch_registry,
    materialize_unique_safe_patches,
)


def _entry(
    ordinal: int,
    *,
    value_json: str,
    valid: bool = True,
    new_count: int = 0,
) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "tool_name": "simulate_patch_application",
        "status": "ok",
        "sanitized_arguments": {
            "object_id": "ent_leader",
            "path": "/description",
            "value_json": value_json,
        },
        "bounded_result": {
            "valid": valid,
            "advice": "introduces_new_issues" if new_count else "safe_to_propose",
            "counts": {"new": new_count},
        },
    }


def test_registry_keeps_only_safe_simulations_and_checks_input_hash() -> None:
    ledger = {
        "input_hash": "frozen",
        "ledger_hash": "a" * 64,
        "entries": [
            _entry(1, value_json='"safe"'),
            _entry(2, value_json='"unsafe"', new_count=1),
            _entry(3, value_json='"invalid"', valid=False),
        ],
    }

    registry = compile_safe_patch_registry(ledger, expected_input_hash="frozen")
    rejected = compile_safe_patch_registry(ledger, expected_input_hash="other")

    assert [candidate.patch_id for candidate in registry.candidates] == ["P1"]
    assert rejected.candidates == ()


def test_materializer_normalizes_equivalent_json_without_changing_other_fields() -> None:
    ledger = {
        "input_hash": "frozen",
        "ledger_hash": "a" * 64,
        "entries": [_entry(1, value_json='{"b":2,"a":1}')],
    }
    registry = compile_safe_patch_registry(ledger)
    suggestions = [
        {
            "object_id": "ent_leader",
            "path": "/description",
            "value_json": '{ "a": 1, "b": 2 }',
            "reason": "keep",
            "finding_ref": "F1",
        }
    ]

    materialized, changes = materialize_unique_safe_patches(suggestions, registry)

    assert materialized[0]["value_json"] == '{"b":2,"a":1}'
    assert materialized[0]["reason"] == "keep"
    assert changes[0].reason == "canonical_match"
