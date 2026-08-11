"""Runtime versioned CaseFile schema, reference, and canonical-hash tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from casefile.application.casefile_v1 import prepare_generation_candidate
from casefile.application.snapshot import casefile_content_hash
from casefile.contracts import (
    ContractValidationError,
    load_casefile_schema,
    public_validation_issues,
    validate_casefile,
)
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPO_ROOT / "fixtures" / "casefiles"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _error_codes(document: dict[str, Any]) -> set[str]:
    with pytest.raises(ContractValidationError) as caught:
        validate_casefile(document)
    return {error["code"] for error in caught.value.errors}


def test_schema_is_valid_and_all_three_product_shapes_pass() -> None:
    Draft202012Validator.check_schema(load_casefile_schema())
    for fixture in (
        "fractured_alliance.casefile.json",
        "restart_loop.casefile.json",
        "vanishing_route.casefile.json",
    ):
        validate_casefile(_load(fixture))


def test_historical_v1_document_remains_readable() -> None:
    legacy = _load("restart_loop.casefile.json")
    legacy["schema_version"] = "1.0"
    legacy["events"][0]["time"] = {
        "start": "2042-06-01T20:00:00+08:00",
        "end": "2042-06-01T20:03:00+08:00",
        "precision": "minute",
    }

    Draft202012Validator.check_schema(load_casefile_schema("1.0"))
    validate_casefile(legacy)


def test_v1_candidate_upgrade_does_not_mutate_historical_payload() -> None:
    legacy = _load("restart_loop.casefile.json")
    legacy["schema_version"] = "1.0"
    legacy["events"][0]["time"] = {
        "start": "2042-06-01T20:00:00+08:00",
        "end": None,
        "precision": "unknown",
    }
    frozen = copy.deepcopy(legacy)

    upgraded = prepare_generation_candidate(
        SimpleNamespace(draft=SimpleNamespace(schema_version="2.0")),  # type: ignore[arg-type]
        legacy,
    )

    assert legacy == frozen
    assert upgraded["schema_version"] == "2.0"
    assert upgraded["events"][0]["time"] == {"kind": "unknown"}
    validate_casefile(upgraded)


def test_runtime_rejects_dangling_and_wrong_type_references() -> None:
    missing = _load("restart_loop.casefile.json")
    missing["events"][0]["location_ref"]["object_id"] = "loc_missing"
    assert "missing_reference" in _error_codes(missing)

    wrong_type = _load("restart_loop.casefile.json")
    wrong_type["events"][0]["location_ref"] = {
        "object_type": "entity",
        "object_id": "ent_researcher",
    }
    assert "reference_type_mismatch" in _error_codes(wrong_type)


def test_runtime_rejects_deterministic_semantic_invariants() -> None:
    duplicate_step = _load("restart_loop.casefile.json")
    duplicate_step["reasoning_paths"][0]["steps"].append(
        copy.deepcopy(duplicate_step["reasoning_paths"][0]["steps"][0])
    )
    assert "duplicate_key" in _error_codes(duplicate_step)

    self_adjacent = _load("restart_loop.casefile.json")
    self_adjacent["locations"][0]["adjacency_refs"][0]["object_id"] = "loc_lab"
    assert "self_reference" in _error_codes(self_adjacent)

    invalid_time = _load("restart_loop.casefile.json")
    invalid_time["events"][0]["time"]["end"] = "2042-06-01T19:59"
    assert "invalid_time_range" in _error_codes(invalid_time)


def test_temporal_position_v2_supports_all_five_semantics() -> None:
    document = _load("restart_loop.casefile.json")
    event = document["events"][0]

    for time in (
        {"kind": "exact", "value": "2042-06-01T20:00", "precision": "minute"},
        {
            "kind": "approximate",
            "value": "2042-06-01T20",
            "precision": "hour",
        },
        {
            "kind": "range",
            "start": "2042-06-01T20:00",
            "end": "2042-06-01T20:03",
            "precision": "minute",
        },
        {"kind": "unknown"},
    ):
        candidate = copy.deepcopy(document)
        candidate["events"][0]["time"] = time
        validate_casefile(candidate)

    relative = copy.deepcopy(document)
    anchor = copy.deepcopy(event)
    anchor["id"] = "evt_restart_anchor"
    anchor["time"] = {"kind": "exact", "value": "2042-06-01T19:00", "precision": "minute"}
    relative["events"].append(anchor)
    relative["events"][0]["time"] = {
        "kind": "relative",
        "anchor_event_ref": {"object_type": "event", "object_id": "evt_restart_anchor"},
        "relation": "after",
        "offset_minutes": 60,
    }
    validate_casefile(relative)


def test_temporal_position_v2_rejects_fabricated_unknown_and_timezone_values() -> None:
    fabricated_unknown = _load("restart_loop.casefile.json")
    fabricated_unknown["events"][0]["time"] = {
        "kind": "unknown",
        "value": "2042-06-01T20:00",
    }
    assert "schema_invalid" in _error_codes(fabricated_unknown)

    timezone_value = _load("restart_loop.casefile.json")
    timezone_value["events"][0]["time"] = {
        "kind": "exact",
        "value": "2042-06-01T20:00+08:00",
        "precision": "minute",
    }
    assert "schema_invalid" in _error_codes(timezone_value)

    precision_mismatch = _load("restart_loop.casefile.json")
    precision_mismatch["events"][0]["time"] = {
        "kind": "exact",
        "value": "2042-06-01T20:00",
        "precision": "hour",
    }
    assert "time_precision_mismatch" in _error_codes(precision_mismatch)


def test_evidence_assessments_are_backward_compatible_and_semantically_checked() -> None:
    legacy = _load("restart_loop.casefile.json")
    validate_casefile(legacy)

    sparse = _load("restart_loop.casefile.json")
    sparse["hypotheses"][0]["evidence_assessments"] = [
        {
            "information_ref": {
                "object_type": "information_unit",
                "object_id": "info_restart_log",
            },
            "effect": "supports",
            "strength": "strong",
            "rationale": "重启日志直接记录了触发条件。",
        }
    ]
    validate_casefile(sparse)

    wrong_type = copy.deepcopy(sparse)
    wrong_type["hypotheses"][0]["evidence_assessments"][0]["information_ref"] = {
        "object_type": "claim",
        "object_id": "claim_backup_trigger",
    }
    assert "reference_type_mismatch" in _error_codes(wrong_type)

    missing_information = copy.deepcopy(sparse)
    missing_information["hypotheses"][0]["evidence_assessments"][0]["information_ref"][
        "object_id"
    ] = "info_missing"
    assert "missing_reference" in _error_codes(missing_information)

    duplicate = copy.deepcopy(sparse)
    duplicate["hypotheses"][0]["evidence_assessments"].append(
        copy.deepcopy(duplicate["hypotheses"][0]["evidence_assessments"][0])
    )
    assert "duplicate_key" in _error_codes(duplicate)

    illegal_enum = copy.deepcopy(sparse)
    illegal_enum["hypotheses"][0]["evidence_assessments"][0]["effect"] = "maybe"
    assert "schema_invalid" in _error_codes(illegal_enum)

    empty_rationale = copy.deepcopy(sparse)
    empty_rationale["hypotheses"][0]["evidence_assessments"][0]["rationale"] = ""
    assert "schema_invalid" in _error_codes(empty_rationale)


def test_rfc8785_hash_is_stable_across_object_key_order() -> None:
    document = _load("restart_loop.casefile.json")
    reordered = json.loads(json.dumps(document, ensure_ascii=False, sort_keys=True))
    assert casefile_content_hash(document) == casefile_content_hash(reordered)
    assert len(casefile_content_hash(document)) == 64


def test_public_validation_issues_keep_paths_without_candidate_values() -> None:
    secret_value = "author-secret-value"
    issues = public_validation_issues(
        [
            {
                "code": "schema_invalid",
                "path": "/events/0/time",
                "message": f"{secret_value!r} is not of type 'object'",
            },
            {
                "code": "missing_reference",
                "path": "/claims/0/support_refs/0",
                "message": "object_id 'secret_reference' does not exist",
            },
        ]
    )

    assert issues == [
        {
            "code": "schema_invalid",
            "path": "/events/0/time",
            "message": "字段类型应为 'object'",
        },
        {
            "code": "missing_reference",
            "path": "/claims/0/support_refs/0",
            "message": "引用的对象不存在",
        },
    ]
    assert secret_value not in repr(issues)
    assert "secret_reference" not in repr(issues)
