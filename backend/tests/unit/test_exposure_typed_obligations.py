"""Typed Exposure obligation contracts and historical projection compatibility."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from casefile.api.schemas import ExposurePlanEntryRequest
from casefile.data_postgres.compiler_repository import (
    CompilerRepository,
    FrozenExposureEntry,
    FrozenExposureObligation,
)


class _FakeSession:
    def __init__(self, payload_schema_id: str) -> None:
        self.payload_schema_id = payload_schema_id

    def get(self, _model: object, _identity: int) -> SimpleNamespace:
        return SimpleNamespace(payload_schema_id=self.payload_schema_id)


def _frozen_entry() -> FrozenExposureEntry:
    event = SimpleNamespace(object_type="event", object_id="evt_one")
    entity = SimpleNamespace(object_type="entity", object_id="ent_one")
    obligation = SimpleNamespace(
        obligation_kind="participant_coverage",
        obligation_key="obligation_participants",
        level="hard",
        min_distinct=1,
    )
    return FrozenExposureEntry(
        entry=SimpleNamespace(
            entry_key="exposure_one",
            sequence_no=1,
            title="First",
            note=None,
        ),
        refs=(event,),
        obligations=(FrozenExposureObligation(obligation=obligation, refs=(entity,)),),
    )


@pytest.mark.parametrize(
    ("payload_schema_id", "expected"),
    [
        (
            "casefile.exposure-plan.v1",
            {
                "entries": [
                    {
                        "entry_key": "exposure_one",
                        "sequence_no": 1,
                        "title": "First",
                        "note": None,
                        "refs": [{"object_type": "event", "object_id": "evt_one"}],
                    }
                ]
            },
        ),
        (
            "casefile.exposure-plan.v2",
            {
                "entries": [
                    {
                        "entry_key": "exposure_one",
                        "sequence_no": 1,
                        "title": "First",
                        "note": None,
                        "refs": [{"object_type": "event", "object_id": "evt_one"}],
                        "planning_obligations": [
                            {
                                "kind": "participant_coverage",
                                "obligation_key": "obligation_participants",
                                "level": "hard",
                                "min_distinct": 1,
                                "eligible_refs": [
                                    {"object_type": "entity", "object_id": "ent_one"}
                                ],
                            }
                        ],
                    }
                ]
            },
        ),
    ],
)
def test_exposure_projection_preserves_v1_shape_and_adds_obligations_only_in_v2(
    monkeypatch: pytest.MonkeyPatch,
    payload_schema_id: str,
    expected: dict[str, Any],
) -> None:
    repository = CompilerRepository(_FakeSession(payload_schema_id))  # type: ignore[arg-type]
    monkeypatch.setattr(
        CompilerRepository,
        "read_exposure_revision_entries",
        lambda _self, _revision_id: [_frozen_entry()],
    )

    assert repository.project_exposure_revision_payload(1) == expected


def test_hypothesis_obligation_rejects_non_hypothesis_refs() -> None:
    with pytest.raises(ValidationError):
        ExposurePlanEntryRequest.model_validate(
            {
                "entry_key": "exposure_one",
                "title": "First",
                "refs": [{"object_type": "event", "object_id": "evt_one"}],
                "planning_obligations": [
                    {
                        "kind": "hypothesis_coverage",
                        "obligation_key": "obligation_hypothesis",
                        "level": "hard",
                        "required_refs": [
                            {"object_type": "claim", "object_id": "claim_one"}
                        ],
                    }
                ],
            }
        )


def test_participant_obligation_rejects_impossible_minimum() -> None:
    with pytest.raises(ValidationError):
        ExposurePlanEntryRequest.model_validate(
            {
                "entry_key": "exposure_one",
                "title": "First",
                "refs": [{"object_type": "event", "object_id": "evt_one"}],
                "planning_obligations": [
                    {
                        "kind": "participant_coverage",
                        "obligation_key": "obligation_participants",
                        "level": "hard",
                        "eligible_refs": [
                            {"object_type": "entity", "object_id": "ent_one"}
                        ],
                        "min_distinct": 2,
                    }
                ],
            }
        )
