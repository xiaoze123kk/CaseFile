"""v11 temporal, spatial, competition-matrix, and compiler contracts."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from casefile.agent_runtime import CandidateStrategy, GenerationRequest
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
    _quality_gate,
    _v11_blueprint_issues,
    _v11_story_issues,
    run_v8_generation,
)
from casefile.agent_runtime.brief_to_draft_v11.contracts import (
    CoordinatePairV1,
    EventIRV2,
    RangeTemporalPositionIRV2,
    RelativeTemporalPositionIRV2,
    StoryWorldIRV2,
    Wgs84SpatialPositionIRV2,
)
from casefile.agent_runtime.prompt import V11_GENERATION_AGENT_VERSION
from casefile.agent_runtime.providers import _add_fake_v10_matrix_plan, _fake_v8_output
from casefile.agent_runtime.structured_output import compile_deepseek_strict_schema
from casefile.agent_runtime.tools import TOOLSET_VERSION
from casefile.contracts import ContractValidationError, validate_casefile


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


def test_v11_blueprint_gate_requires_one_dedicated_path_per_competing_hypothesis() -> None:
    blueprint, _story, _evidence, _governance = _v11_parts()
    blueprint.reasoning_paths = blueprint.reasoning_paths[:1]
    blueprint.reasoning_paths[0].dependency_keys = [
        "record",
        "claim",
        "hypothesis",
        "alternative_hypothesis",
    ]

    issues = _v11_blueprint_issues(blueprint)

    assert {
        (issue["code"], issue["component_id"], issue["failure_layer"])
        for issue in issues
    } == {
        (
            "competing_hypothesis_path_plan_missing",
            "case_blueprint_planner",
            "blueprint_semantics",
        )
    }
    assert {issue["ir_path"] for issue in issues} == {
        "/hypotheses/hypothesis",
        "/hypotheses/alternative_hypothesis",
    }


def test_v11_fake_blueprint_passes_competition_semantics() -> None:
    blueprint, _story, _evidence, _governance = _v11_parts()

    assert _v11_blueprint_issues(blueprint) == []


def test_quality_gate_failure_closes_its_started_step() -> None:
    blueprint, story, evidence, governance = _v11_parts()
    candidate = compile_casefile(
        link_draft(blueprint, story, evidence, governance, task_run_id=410),
        casefile_id="case_v11_quality_step",
        brief_id="brief_v11_quality_step",
        brief_version=1,
        version_id="draft_v11_quality_step",
        version_no=1,
        parent_version_id=None,
        schema_version="2.0",
    )
    candidate.pop("title")
    events: list[tuple[str, str, dict[str, Any]]] = []
    request = GenerationRequest(
        task_run_id=410,
        prompt_version="brief-to-draft-v11",
        brief={"conclusion_mode": "unique"},
        schema_version="2.0",
        casefile_id="case_v11_quality_step",
        brief_id="brief_v11_quality_step",
        brief_version=1,
        version_id="draft_v11_quality_step",
        version_no=1,
        parent_version_id=None,
        model_id="fake-v11",
        api_key=None,
        max_turns=3,
        emit=lambda event_type, stage, payload: events.append(
            (event_type, stage, payload)
        ),
    )

    with pytest.raises(ContractValidationError):
        _quality_gate(request, candidate, recoverable=False)

    assert [event_type for event_type, _stage, _payload in events] == [
        "agent.step.started",
        "agent.step.failed",
    ]
    assert events[-1][2]["recoverable"] is False


def test_v11_planner_repair_feedback_and_semantic_failure_are_observable() -> None:
    events: list[tuple[str, str, dict[str, Any]]] = []
    planner_inputs: list[dict[str, Any]] = []
    component_calls: list[str] = []
    blueprint_payload = _fake_v8_output(CaseBlueprintV1)
    _add_fake_v10_matrix_plan(CaseBlueprintV1, blueprint_payload)
    blueprint_payload["reasoning_paths"] = blueprint_payload["reasoning_paths"][:1]
    blueprint_payload["reasoning_paths"][0]["dependency_keys"] = [
        "record",
        "claim",
        "hypothesis",
        "alternative_hypothesis",
    ]
    repair_issue = {
        "code": "competing_hypothesis_path_plan_missing",
        "path": "/reasoning_paths/alternative_hypothesis",
        "message": "需要独立推理路径",
        "component_id": "case_blueprint_planner",
        "failure_layer": "blueprint_semantics",
        "schema_id": "case-blueprint-v1",
    }
    request = GenerationRequest(
        task_run_id=411,
        prompt_version="brief-to-draft-v11",
        brief={"conclusion_mode": "unique"},
        schema_version="2.0",
        casefile_id="case_v11_planner_gate",
        brief_id="brief_v11_planner_gate",
        brief_version=1,
        version_id="draft_v11_planner_gate",
        version_no=1,
        parent_version_id=None,
        model_id="fake-v11",
        api_key=None,
        max_turns=3,
        emit=lambda event_type, stage, payload: events.append(
            (event_type, stage, payload)
        ),
        repair_feedback=({"repair_no": 1, "issues": [repair_issue]},),
        candidate_strategy=CandidateStrategy.BALANCED,
        agent_version=V11_GENERATION_AGENT_VERSION,
        toolset_version=TOOLSET_VERSION,
    )

    async def call_component(
        _instructions: str,
        input_text: str,
        output_type: type[BaseModel],
        _stage: str,
        component_id: str,
        _schema_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        component_calls.append(component_id)
        assert output_type is CaseBlueprintV1
        planner_inputs.append(json.loads(input_text))
        return blueprint_payload, {"requests": 1}

    with pytest.raises(ContractValidationError) as captured:
        asyncio.run(run_v8_generation(request, call_component=call_component))

    assert component_calls == ["case_blueprint_planner"]
    assert planner_inputs[0]["targeted_repair_issues"] == [repair_issue]
    assert {
        issue["code"] for issue in captured.value.errors
    } == {"competing_hypothesis_path_plan_missing"}
    quality_events = [
        event_type
        for event_type, _stage, payload in events
        if payload.get("component_id") == "quality_repair_gate"
    ]
    assert quality_events == ["agent.step.started", "agent.step.failed"]
