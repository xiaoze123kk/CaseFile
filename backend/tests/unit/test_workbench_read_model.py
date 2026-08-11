"""Focused tests for deterministic workbench validation and source traces."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

from casefile.application.workbench_read_model import (
    WorkbenchReadModel,
    _contract_source_refs,
)
from casefile.contracts import ContractValidationError
from casefile.data_postgres.repositories import OwnedDraft
from sqlalchemy.orm import Session


def test_validation_failure_returns_stable_localized_issues() -> None:
    model = WorkbenchReadModel(Mock(spec=Session))
    owned = cast(
        OwnedDraft,
        cast(
            Any,
            SimpleNamespace(
                draft=SimpleNamespace(
                    brief_version_id=17,
                    schema_version="1.0",
                )
            ),
        ),
    )
    failure = ContractValidationError(
        [
            {
                "code": "missing_reference",
                "path": "/events/0/location_ref",
                "message": "object_id 'loc_missing' does not exist",
            }
        ]
    )

    with patch(
        "casefile.application.workbench_read_model.build_casefile_document",
        side_effect=failure,
    ):
        document, first = model._validation(owned)
        _document, second = model._validation(owned)

    assert document is None
    assert first == second
    assert first["status"] == "failed"
    assert first["issue_count"] == 1
    assert first["issues"][0]["message"] == "引用的对象不存在"
    assert first["issues"][0]["path"] == "/events/0/location_ref"
    assert first["issues"][0]["issue_id"].startswith("validator:")


def test_contract_source_refs_preserve_real_ids_and_json_pointer_paths() -> None:
    document = {
        "events": [
            {
                "source_refs": [
                    {
                        "object_type": "source_fragment",
                        "object_id": "src_gate_log",
                    }
                ]
            }
        ],
        "information_units": [
            {
                "source_refs": [
                    {
                        "object_type": "source_fragment",
                        "object_id": "src_gate_log",
                    },
                    {"object_type": "claim", "object_id": "claim_not_a_source"},
                ]
            }
        ],
    }

    assert _contract_source_refs(document) == [
        {
            "source_fragment_id": "src_gate_log",
            "paths": [
                "/events/0/source_refs/0",
                "/information_units/0/source_refs/0",
            ],
        }
    ]
