"""Runtime CaseFile v1 schema, reference, and canonical-hash tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from casefile.application.snapshot import casefile_content_hash
from casefile.contracts import (
    ContractValidationError,
    load_casefile_schema,
    public_validation_issues,
    validate_casefile,
)

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
    invalid_time["events"][0]["time"]["end"] = "2042-06-01T19:59:00+08:00"
    assert "invalid_time_range" in _error_codes(invalid_time)


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
