"""v11 temporal, spatial, competition-matrix, and compiler contracts."""

from __future__ import annotations

import pytest
from casefile.agent_runtime.brief_to_draft_v8.compiler import compile_casefile, link_draft
from casefile.agent_runtime.brief_to_draft_v8.ir import (
    BlueprintObjectV1,
    CaseBlueprintV1,
    EvidenceLogicIRV2,
    ResolutionGovernanceIRV1,
)
from casefile.agent_runtime.brief_to_draft_v8.workflow import (
    _evidence_assessment_issues,
    _extract_allowed_wgs84_coordinates,
    _v11_story_issues,
)
from casefile.agent_runtime.brief_to_draft_v11.contracts import (
    CoordinatePairV1,
    EventIRV2,
    RangeTemporalPositionIRV2,
    RelativeTemporalPositionIRV2,
    StoryWorldIRV2,
    Wgs84SpatialPositionIRV2,
)
from casefile.agent_runtime.providers import _add_fake_v10_matrix_plan, _fake_v8_output
from casefile.agent_runtime.structured_output import compile_deepseek_strict_schema
from casefile.contracts import validate_casefile
from pydantic import ValidationError


def _v11_parts() -> tuple[
    CaseBlueprintV1,
    StoryWorldIRV2,
    EvidenceLogicIRV2,
    ResolutionGovernanceIRV1,
]:
    blueprint_payload = _fake_v8_output(CaseBlueprintV1)
    _add_fake_v10_matrix_plan(CaseBlueprintV1, blueprint_payload)
    return (
        CaseBlueprintV1.model_validate(blueprint_payload),
        StoryWorldIRV2.model_validate(_fake_v8_output(StoryWorldIRV2)),
        EvidenceLogicIRV2.model_validate(_fake_v8_output(EvidenceLogicIRV2)),
        ResolutionGovernanceIRV1.model_validate(
            _fake_v8_output(ResolutionGovernanceIRV1)
        ),
    )


def test_story_world_v2_compiles_for_deepseek_strict() -> None:
    schema = compile_deepseek_strict_schema(StoryWorldIRV2)

    assert schema["type"] == "object"
    assert "oneOf" not in str(schema)
    assert "events" in schema["properties"]
    assert schema["$defs"]["EntityIRV2"]["properties"]["entity_type"]["enum"] == [
        "person",
        "organization",
        "object",
        "system",
        "faction",
        "rule_actor",
        "other",
    ]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"kind": "range", "start": "2026-08-09", "end": "2026-08-08", "precision": "day"},
            "must not precede",
        ),
        (
            {
                "kind": "range",
                "start": "2026-08-08T08:00Z",
                "end": "2026-08-08T09:00Z",
                "precision": "minute",
            },
            "string_pattern_mismatch",
        ),
        (
            {
                "kind": "range",
                "start": "2026-08-08T08",
                "end": "2026-08-08T09",
                "precision": "minute",
            },
            "declared precision",
        ),
    ],
)
def test_temporal_ir_rejects_fabricated_precision_and_timezone(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        RangeTemporalPositionIRV2.model_validate(payload)


@pytest.mark.parametrize("offset_minutes", [10, None])
def test_v11_compiler_preserves_relative_time_as_event_reference(
    offset_minutes: int | None,
) -> None:
    blueprint, story, evidence, governance = _v11_parts()
    blueprint.events.append(
        BlueprintObjectV1(
            local_key="follow_up",
            title="后续核验",
            purpose="在发现记录后十分钟发生。",
            dependency_keys=["discovery"],
        )
    )
    source = story.events[0]
    story.events.append(
        EventIRV2.model_validate(
            {
                **source.model_dump(mode="json"),
                "local_key": "follow_up",
                "title": "后续核验",
                "time": {
                    "kind": "relative",
                    "anchor_event_key": "discovery",
                    "relation": "after",
                    "offset_minutes": offset_minutes,
                },
                "cause_keys": ["discovery"],
            }
        )
    )

    candidate = compile_casefile(
        link_draft(blueprint, story, evidence, governance, task_run_id=401),
        casefile_id="case_v11_relative",
        brief_id="brief_v11",
        brief_version=1,
        version_id="draft_v11",
        version_no=1,
        parent_version_id=None,
        schema_version="2.0",
    )

    validate_casefile(candidate)
    assert candidate["events"][1]["time"] == {
        "kind": "relative",
        "anchor_event_ref": {"object_type": "event", "object_id": "evt_t401_001"},
        "relation": "after",
        "offset_minutes": None if offset_minutes is None else 10.0,
    }


def test_wgs84_allowlist_is_derived_only_from_explicit_brief_coordinates() -> None:
    pairs = _extract_allowed_wgs84_coordinates(
        {
            "notes": "坐标：31.2304, 121.4737；另一处只有上海这个地名。",
            "structured": {"latitude": 30.1, "longitude": 120.2},
        }
    )

    assert [item.model_dump() for item in pairs] == [
        {"latitude": 30.1, "longitude": 120.2},
        {"latitude": 31.2304, "longitude": 121.4737},
    ]


def test_story_gate_rejects_unlisted_wgs84_and_relative_cycles() -> None:
    _blueprint, story, _evidence, _governance = _v11_parts()
    story.locations[0].spatial_position = Wgs84SpatialPositionIRV2(
        latitude=31.2304,
        longitude=121.4737,
    )
    story.events[0].time = RelativeTemporalPositionIRV2(
        anchor_event_key="discovery",
        relation="same_time",
        offset_minutes=None,
    )

    codes = {
        issue["code"]
        for issue in _v11_story_issues(
            story,
            [CoordinatePairV1(latitude=30, longitude=120)],
        )
    }

    assert "wgs84_not_explicit_in_brief" in codes
    assert "relative_time_self_anchor" in codes
    assert "relative_time_cycle" in codes


def test_v11_matrix_gate_requires_exact_peer_and_information_sets() -> None:
    _blueprint, _story, evidence, _governance = _v11_parts()
    evidence.hypotheses[0].competing_hypothesis_keys = []
    evidence.hypotheses[1].evidence_assessments = []

    codes = {
        issue["code"]
        for issue in _evidence_assessment_issues(evidence, strict_competition=True)
    }

    assert codes == {
        "competing_hypothesis_group_incomplete",
        "missing_evidence_assessment",
    }


def test_v11_matrix_gate_rejects_cross_resolution_competitor_reference() -> None:
    _blueprint, _story, evidence, _governance = _v11_parts()
    evidence.hypotheses[1].target_resolution_key = "other_resolution"

    codes = {
        issue["code"]
        for issue in _evidence_assessment_issues(evidence, strict_competition=True)
    }

    assert codes == {"competing_hypothesis_group_incomplete"}
