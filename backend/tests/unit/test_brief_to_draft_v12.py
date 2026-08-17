"""v12 temporal planning, deterministic Story injection, and recovery contracts."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from casefile.agent_runtime import CandidateStrategy, FakeProvider, GenerationRequest
from casefile.agent_runtime.brief_to_draft_v8.ir import CaseBlueprintV1
from casefile.agent_runtime.brief_to_draft_v8.workflow import run_v8_generation
from casefile.agent_runtime.brief_to_draft_v12.contracts import (
    StoryWorldIRV3,
    TemporalPlanV1,
    temporal_plan_issues,
    temporal_story_issues,
)
from casefile.agent_runtime.prompt import V12_GENERATION_AGENT_VERSION
from casefile.agent_runtime.providers import _add_fake_v10_matrix_plan, _fake_v8_output
from casefile.agent_runtime.tools import TOOLSET_VERSION
from casefile.contracts import ContractValidationError, validate_casefile
from pydantic import BaseModel


def _blueprint() -> CaseBlueprintV1:
    payload = _fake_v8_output(CaseBlueprintV1)
    _add_fake_v10_matrix_plan(CaseBlueprintV1, payload)
    return CaseBlueprintV1.model_validate(payload)


def _plan(*, time: dict[str, object] | None = None) -> TemporalPlanV1:
    return TemporalPlanV1.model_validate(
        {
            "assignments": [
                {
                    "event_key": "discovery",
                    "time": time
                    or {
                        "kind": "exact",
                        "value": "2042-06-01T20:15",
                        "precision": "minute",
                    },
                    "basis": "design_anchor",
                    "basis_refs": [],
                }
            ]
        }
    )


def _two_event_blueprint() -> CaseBlueprintV1:
    payload = _fake_v8_output(CaseBlueprintV1)
    payload["events"].append(
        {
            "local_key": "follow_up",
            "title": "后续核验",
            "purpose": "验证时间因果。",
            "dependency_keys": ["discovery"],
        }
    )
    return CaseBlueprintV1.model_validate(payload)


def _request() -> GenerationRequest:
    return GenerationRequest(
        task_run_id=512,
        prompt_version="brief-to-draft-v12",
        brief={"conclusion_mode": "unique"},
        schema_version="2.0",
        casefile_id="case_v12",
        brief_id="brief_v12",
        brief_version=1,
        version_id="draft_v12",
        version_no=1,
        parent_version_id=None,
        model_id="fake-v12",
        api_key=None,
        max_turns=3,
        emit=lambda _event_type, _stage, _payload: None,
        candidate_strategy=CandidateStrategy.BALANCED,
        agent_version=V12_GENERATION_AGENT_VERSION,
        toolset_version=TOOLSET_VERSION,
    )


def test_v12_fake_provider_generates_complete_non_unknown_time_structure() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    request = replace(
        _request(),
        emit=lambda event_type, _stage, payload: events.append((event_type, payload)),
    )

    result = FakeProvider().generate(request)

    validate_casefile(result.candidate)
    assert result.candidate["events"][0]["time"] == {
        "kind": "exact",
        "value": "2026-08-08T08:00",
        "precision": "minute",
    }
    started = [payload for event_type, payload in events if event_type == "agent.step.started"]
    assert {payload["component_id"] for payload in started} >= {
        "temporal_structure_planner",
        "story_world",
    }
    assert any(payload["schema_id"] == "temporal-plan-v1" for payload in started)
    assert any(payload["schema_id"] == "story-world-ir-v3" for payload in started)


def test_v12_injects_temporal_plan_even_when_story_contains_no_time() -> None:
    blueprint = _blueprint()
    plan = _plan()
    story = StoryWorldIRV3.model_validate(_fake_v8_output(StoryWorldIRV3))

    assert temporal_plan_issues(plan, blueprint) == []
    assert temporal_story_issues(story, plan) == []
    assert "time" not in story.events[0].model_dump(mode="json")


@pytest.mark.parametrize(
    ("plan", "code"),
    [
        (
            {"assignments": []},
            "List should have at least 1 item",
        ),
        (
            {
                "assignments": [
                    {
                        "event_key": "discovery",
                        "time": {
                            "kind": "relative",
                            "anchor_event_key": "discovery",
                            "relation": "after",
                            "offset_minutes": None,
                        },
                        "basis": "design_relative",
                        "basis_refs": [],
                    }
                ]
            },
            "temporal_offset_missing",
        ),
    ],
)
def test_v12_rejects_missing_or_unresolvable_time_structure(
    plan: dict[str, object], code: str
) -> None:
    if code.startswith("List"):
        with pytest.raises(Exception, match=code):
            TemporalPlanV1.model_validate(plan)
        return
    issues = temporal_plan_issues(TemporalPlanV1.model_validate(plan), _blueprint())
    assert code in {issue["code"] for issue in issues}
    assert "temporal_anchor_missing" in {issue["code"] for issue in issues}


@pytest.mark.parametrize(
    ("assignments", "expected_codes"),
    [
        (
            [
                {
                    "event_key": "discovery",
                    "time": {
                        "kind": "exact",
                        "value": "2042-06-01T20:00",
                        "precision": "minute",
                    },
                    "basis": "design_anchor",
                    "basis_refs": [],
                }
            ],
            {"temporal_assignment_missing"},
        ),
        (
            [
                {
                    "event_key": "discovery",
                    "time": {
                        "kind": "relative",
                        "anchor_event_key": "follow_up",
                        "relation": "after",
                        "offset_minutes": 5,
                    },
                    "basis": "design_relative",
                    "basis_refs": [],
                },
                {
                    "event_key": "follow_up",
                    "time": {
                        "kind": "relative",
                        "anchor_event_key": "discovery",
                        "relation": "after",
                        "offset_minutes": 5,
                    },
                    "basis": "design_relative",
                    "basis_refs": [],
                },
            ],
            {"temporal_anchor_missing", "temporal_event_unresolved"},
        ),
    ],
)
def test_v12_reports_missing_assignment_and_relative_cycles(
    assignments: list[dict[str, object]], expected_codes: set[str]
) -> None:
    plan = TemporalPlanV1.model_validate({"assignments": assignments})
    codes = {issue["code"] for issue in temporal_plan_issues(plan, _two_event_blueprint())}

    assert expected_codes <= codes


def test_v12_rejects_story_causality_that_reverses_the_temporal_plan() -> None:
    story_payload = _fake_v8_output(StoryWorldIRV3)
    story_payload["events"].append(
        {
            **story_payload["events"][0],
            "local_key": "follow_up",
            "title": "后续核验",
            "cause_keys": [],
        }
    )
    story_payload["events"][0]["cause_keys"] = ["follow_up"]
    story = StoryWorldIRV3.model_validate(story_payload)
    plan = TemporalPlanV1.model_validate(
        {
            "assignments": [
                {
                    "event_key": "discovery",
                    "time": {
                        "kind": "exact",
                        "value": "2042-06-01T20:00",
                        "precision": "minute",
                    },
                    "basis": "design_anchor",
                    "basis_refs": [],
                },
                {
                    "event_key": "follow_up",
                    "time": {
                        "kind": "relative",
                        "anchor_event_key": "discovery",
                        "relation": "after",
                        "offset_minutes": 10,
                    },
                    "basis": "design_relative",
                    "basis_refs": [],
                },
            ]
        }
    )

    assert {issue["code"] for issue in temporal_story_issues(story, plan)} == {
        "temporal_causal_inversion"
    }


def test_v12_temporal_failure_stops_before_story_generation() -> None:
    calls: list[str] = []

    async def call_component(
        _instructions: str,
        _input_text: str,
        output_type: type[BaseModel],
        _stage: str,
        component_id: str,
        _schema_id: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        calls.append(component_id)
        if output_type is CaseBlueprintV1:
            output = _fake_v8_output(output_type)
            _add_fake_v10_matrix_plan(output_type, output)
            return output, {"requests": 1}
        if output_type is TemporalPlanV1:
            return {
                "assignments": [
                    {
                        "event_key": "discovery",
                        "time": {
                            "kind": "relative",
                            "anchor_event_key": "discovery",
                            "relation": "after",
                            "offset_minutes": None,
                        },
                        "basis": "design_relative",
                        "basis_refs": [],
                    }
                ]
            }, {"requests": 1}
        raise AssertionError(f"unexpected component {component_id}")

    with pytest.raises(ContractValidationError):
        asyncio.run(run_v8_generation(_request(), call_component=call_component))

    assert calls == ["case_blueprint_planner", "temporal_structure_planner"]
