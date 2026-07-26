"""CaseFile 0.1.0 JSON Schema, reference, and canonical-hash tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from casefile.application.snapshot import casefile_content_hash
from casefile.contracts import (
    ContractValidationError,
    load_casefile_schema,
    validate_casefile,
)
from jsonschema import Draft202012Validator

CONTRACT_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "contracts"


def _load(kind: str, name: str) -> dict[str, Any]:
    return json.loads((CONTRACT_FIXTURES / kind / name).read_text(encoding="utf-8"))


def test_schema_is_valid_and_all_three_product_shapes_pass() -> None:
    Draft202012Validator.check_schema(load_casefile_schema())
    for fixture in ("unique_causal.json", "open_interpretation.json", "path_exploration.json"):
        validate_casefile(_load("valid", fixture))


@pytest.mark.parametrize(
    ("fixture", "expected_code"),
    [
        ("invalid_object_id.json", "schema_invalid"),
        ("dangling_reference.json", "missing_reference"),
        ("wrong_reference_type.json", "reference_type_mismatch"),
        ("duplicate_order.json", "duplicate_order"),
        ("unknown_structural_field.json", "schema_invalid"),
    ],
)
def test_invalid_contract_fixtures_have_stable_error_codes(
    fixture: str, expected_code: str
) -> None:
    with pytest.raises(ContractValidationError) as caught:
        validate_casefile(_load("invalid", fixture))
    assert expected_code in {error["code"] for error in caught.value.errors}


def test_rfc8785_hash_is_stable_across_object_key_order() -> None:
    document = _load("valid", "unique_causal.json")
    reordered = json.loads(json.dumps(document, ensure_ascii=False, sort_keys=True))
    assert casefile_content_hash(document) == casefile_content_hash(reordered)
    assert len(casefile_content_hash(document)) == 64
