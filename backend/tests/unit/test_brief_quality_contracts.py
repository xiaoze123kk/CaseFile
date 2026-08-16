"""Brief quality-requirement contracts and their repair paths."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from casefile.agent_runtime.brief_to_draft_runtime import resolve_pipeline_spec
from casefile.agent_runtime.brief_to_draft_v8.ir import CaseBlueprintV1
from casefile.agent_runtime.brief_to_draft_v8.workflow import (
    _affected_domain_components,
    _brief_quality_requirement_issues,
    run_v8_generation,
)
from casefile.agent_runtime.brief_to_draft_v12.contracts import StoryWorldIRV3
from casefile.agent_runtime.models import CandidateStrategy, GenerationRequest
from casefile.agent_runtime.prompt import V12_GENERATION_AGENT_VERSION
from casefile.agent_runtime.providers import _add_fake_v10_matrix_plan, _fake_v8_output
from casefile.agent_runtime.tools import TOOLSET_VERSION
from casefile.contracts import ContractValidationError


def _request(
    prompt_version: str,
    quality_requirements: dict[str, Any],
) -> GenerationRequest:
    return GenerationRequest(
        task_run_id=1,
        prompt_version=prompt_version,
        brief={
            "conclusion_mode": "unique",
            "quality_requirements": quality_requirements,
        },
        schema_version="2.0",
        casefile_id="case_quality",
        brief_id="brief_quality",
        brief_version=1,
        version_id="draft_quality",
        version_no=1,
        parent_version_id=None,
        model_id="fake-quality",
        api_key=None,
        max_turns=8,
        emit=lambda *args: None,
        candidate_strategy=CandidateStrategy.BALANCED,
        agent_version=(
            V12_GENERATION_AGENT_VERSION
            if prompt_version == "brief-to-draft-v12"
            else None
        ),
        toolset_version=(
            TOOLSET_VERSION if prompt_version == "brief-to-draft-v12" else None
        ),
    )


def test_brief_quality_requirement_issues_detect_missing_time_kinds() -> None:
    brief = {"quality_requirements": {"temporal_time_kinds": ["exact", "range"]}}
    candidate = {
        "events": [
            {"time": {"kind": "exact"}},
        ]
    }

    issues = _brief_quality_requirement_issues(brief, candidate, schema_id="casefile-v2")

    assert [issue["code"] for issue in issues] == ["frozen_temporal_time_kinds_missing"]
    assert issues[0]["component_id"] == "temporal_structure_planner"
    assert issues[0]["failure_layer"] == "temporal_grounding"


def test_brief_quality_requirement_issues_accept_complete_time_kinds() -> None:
    brief = {"quality_requirements": {"temporal_time_kinds": ["exact", "range"]}}
    candidate = {
        "events": [
            {"time": {"kind": "exact"}},
            {"time": {"kind": "range"}},
        ]
    }

    assert (
        _brief_quality_requirement_issues(brief, candidate, schema_id="casefile-v2")
        == []
    )


def test_brief_quality_requirement_issues_detect_missing_scene_topology() -> None:
    brief = {"quality_requirements": {"spatial_scene_topology": True}}
    candidate = {
        "locations": [
            {"spatial_position": {"coordinate_system": "wgs84"}},
        ]
    }

    issues = _brief_quality_requirement_issues(brief, candidate, schema_id="casefile-v2")

    assert [issue["code"] for issue in issues] == [
        "frozen_spatial_scene_topology_missing"
    ]
    assert issues[0]["component_id"] == "story_world"
    assert issues[0]["failure_layer"] == "spatial_grounding"


def test_brief_quality_requirement_issues_accept_schematic_topology() -> None:
    brief = {"quality_requirements": {"spatial_scene_topology": True}}
    candidate = {
        "locations": [
            {
                "spatial_position": {"coordinate_system": "schematic"},
                "travel_times": [{"to_ref": {"object_id": "loc_t1_002"}}],
            },
        ]
    }

    assert (
        _brief_quality_requirement_issues(brief, candidate, schema_id="casefile-v2")
        == []
    )


def test_affected_domain_components_routes_temporal_contract_issues() -> None:
    error = ContractValidationError(
        [
            {
                "code": "frozen_temporal_time_kinds_missing",
                "component_id": "temporal_structure_planner",
                "failure_layer": "temporal_grounding",
            }
        ]
    )

    assert _affected_domain_components(error) == {"temporal_structure_planner"}


def _two_event_blueprint_payload() -> dict[str, Any]:
    payload = _fake_v8_output(CaseBlueprintV1)
    _add_fake_v10_matrix_plan(CaseBlueprintV1, payload)
    payload["events"].append(
        {
            "local_key": "outage",
            "title": "监控中断",
            "purpose": "验证区间时间。",
            "dependency_keys": ["discovery"],
        }
    )
    return payload


def _two_event_story_payload() -> dict[str, Any]:
    payload = _fake_v8_output(StoryWorldIRV3)
    payload["events"].append(
        {
            "local_key": "outage",
            "description": "用于验证区间时间。",
            "tags": [],
            "title": "监控中断",
            "truth_status": "reported",
            "participant_keys": ["author"],
            "location_key": "archive",
            "cause_keys": ["discovery"],
            "effect_keys": [],
            "observed_by_keys": ["author"],
        }
    )
    return payload


async def _temporal_repair_call_component(
    _instructions: str,
    input_text: str,
    output_type: type[Any],
    _stage: str,
    _component_id: str,
    _schema_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if output_type.__name__ == "TemporalPlanV1":
        if "targeted_repair_issues" in input_text:
            return (
                {
                    "assignments": [
                        {
                            "event_key": "discovery",
                            "time": {
                                "kind": "exact",
                                "value": "2026-08-08T08:00",
                                "precision": "minute",
                            },
                            "basis": "design_anchor",
                            "basis_refs": [],
                        },
                        {
                            "event_key": "outage",
                            "time": {
                                "kind": "range",
                                "start": "2026-08-08T08:15",
                                "end": "2026-08-08T08:25",
                                "precision": "minute",
                            },
                            "basis": "brief_range",
                            "basis_refs": [],
                        },
                    ]
                },
                {},
            )
        return (
            {
                "assignments": [
                    {
                        "event_key": "discovery",
                        "time": {
                            "kind": "exact",
                            "value": "2026-08-08T08:00",
                            "precision": "minute",
                        },
                        "basis": "design_anchor",
                        "basis_refs": [],
                    },
                    {
                        "event_key": "outage",
                        "time": {
                            "kind": "exact",
                            "value": "2026-08-08T08:20",
                            "precision": "minute",
                        },
                        "basis": "design_anchor",
                        "basis_refs": [],
                    },
                ]
            },
            {},
        )
    if output_type is CaseBlueprintV1:
        return _two_event_blueprint_payload(), {}
    if output_type is StoryWorldIRV3:
        return _two_event_story_payload(), {}
    output = _fake_v8_output(output_type)
    return output, {}


def test_temporal_time_kinds_contract_triggers_temporal_repair() -> None:
    spec = resolve_pipeline_spec("brief-to-draft-v12")
    request = _request(
        "brief-to-draft-v12",
        {"temporal_time_kinds": ["exact", "range"]},
    )

    result = asyncio.run(
        run_v8_generation(
            request,
            call_component=_temporal_repair_call_component,
            spec=spec,
        )
    )

    kinds = {event["time"]["kind"] for event in result.candidate["events"]}
    assert kinds == {"exact", "range"}


def _two_location_blueprint_payload() -> dict[str, Any]:
    payload = _fake_v8_output(CaseBlueprintV1)
    _add_fake_v10_matrix_plan(CaseBlueprintV1, payload)
    payload["locations"].append(
        {
            "local_key": "control_room",
            "title": "控制室",
            "purpose": "验证示意坐标与拓扑关系。",
            "dependency_keys": ["archive"],
        }
    )
    return payload


def _two_location_story_payload(
    *,
    repaired: bool,
) -> dict[str, Any]:
    payload = _fake_v8_output(StoryWorldIRV3)
    for location in payload["locations"]:
        location["spatial_position"] = None
        location["parent_key"] = None
        location["adjacency_keys"] = []
        location["travel_times"] = []
    payload["locations"].append(
        {
            "local_key": "control_room",
            "description": "用于验证场景拓扑。",
            "tags": [],
            "name": "控制室",
            "spatial_position": (
                {"coordinate_system": "schematic", "x": 20, "y": 20}
                if repaired
                else None
            ),
            "parent_key": None,
            "adjacency_keys": [],
            "access_rules": [],
            "travel_times": [],
            "visibility_rules": [],
        }
    )
    if repaired:
        for location in payload["locations"]:
            if location["local_key"] == "archive":
                location["spatial_position"] = {
                    "coordinate_system": "schematic",
                    "x": 80,
                    "y": 80,
                }
                location["travel_times"] = [
                    {"to_key": "control_room", "minutes": 3}
                ]
    return payload


async def _spatial_repair_call_component(
    _instructions: str,
    input_text: str,
    output_type: type[Any],
    _stage: str,
    _component_id: str,
    _schema_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if output_type is CaseBlueprintV1:
        return _two_location_blueprint_payload(), {}
    if output_type is StoryWorldIRV3:
        return (
            _two_location_story_payload(
                repaired="targeted_repair_issues" in input_text
            ),
            {},
        )
    return _fake_v8_output(output_type), {}


def test_scene_topology_contract_triggers_story_repair() -> None:
    spec = resolve_pipeline_spec("brief-to-draft-v12")
    request = _request(
        "brief-to-draft-v12",
        {"spatial_scene_topology": True},
    )

    result = asyncio.run(
        run_v8_generation(
            request,
            call_component=_spatial_repair_call_component,
            spec=spec,
        )
    )

    positions = {
        location["spatial_position"]["coordinate_system"]
        for location in result.candidate["locations"]
        if location.get("spatial_position")
    }
    assert "schematic" in positions
    assert any(bool(location.get("travel_times")) for location in result.candidate["locations"])
