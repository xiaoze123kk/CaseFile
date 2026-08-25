"""Bounded structural repair behavior for Story Planner."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from casefile.agent_runtime.story_planner import (
    StoryPlannerProviderResult,
    StoryPlannerRequest,
    execute_story_planner,
)
from casefile.domain.narrative_compiler import CompilerContractError


class SequenceProvider:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values
        self.requests: list[StoryPlannerRequest] = []

    def plan_story(self, request: StoryPlannerRequest) -> StoryPlannerProviderResult:
        self.requests.append(request)
        return StoryPlannerProviderResult(candidate=self.values.pop(0), usage={})


def _request() -> StoryPlannerRequest:
    return StoryPlannerRequest(
        task_run_id=1,
        prompt_version="story-planner-v1",
        planner_input={},
        input_hash="a" * 64,
        model_id="fake-pro",
        api_key="secret",
    )


def _valid() -> dict[str, Any]:
    return {
        "schema_id": "compiler.novel-plan-candidate.v1",
        "chapters": [{"chapter_id": "chapter_one", "ordinal": 1, "act_ordinal": 1, "title": "一"}],
        "scenes": [
            {
                "scene_id": "scene_one",
                "chapter_id": "chapter_one",
                "discourse_order": 1,
                "purpose": "hook",
                "intent": "开场",
                "presentation_mode": "linear",
                "pov_ref": None,
                "participant_refs": [],
                "location_ref": None,
                "event_refs": [],
                "story_time_refs": [],
                "basis_refs": [{"object_type": "casefile", "object_id": "case_root"}],
                "exposure": [],
                "resolutions": [],
                "prerequisite_scene_ids": [],
            }
        ],
    }


def test_structural_error_is_repaired_with_previous_errors() -> None:
    provider = SequenceProvider([{}, _valid()])
    result = execute_story_planner(provider, _request())
    assert result.candidate == _valid()
    assert len(result.rounds) == 2
    assert provider.requests[1].repair_errors


def test_repairs_are_bounded_to_three() -> None:
    provider = SequenceProvider([{}, {}, {}, {}])
    with pytest.raises(CompilerContractError) as captured:
        execute_story_planner(provider, replace(_request()))
    assert captured.value.reason_code == "compiler_story_planner_structural_repair_exhausted"
    assert len(provider.requests) == 4


def test_runtime_identity_fails_immediately_without_repair() -> None:
    candidate = _valid()
    candidate["scenes"][0]["task_run_id"] = 99
    provider = SequenceProvider([candidate])
    with pytest.raises(CompilerContractError) as captured:
        execute_story_planner(provider, _request())
    assert captured.value.reason_code == "compiler_story_plan_runtime_identity_forbidden"
    assert len(provider.requests) == 1
