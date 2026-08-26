"""Bounded structural repair behavior for Story Planner."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from casefile.agent_runtime.story_planner import (
    STORY_PLANNER_PROMPT_VERSION,
    StoryPlannerPatchProviderResult,
    StoryPlannerPatchRequest,
    StoryPlannerProviderResult,
    StoryPlannerRequest,
    execute_story_planner,
)
from casefile.agent_runtime.story_planner_prompt import (
    render_story_planner_patch_prompt,
    render_story_planner_prompt,
)
from casefile.domain.narrative_compiler import CompilerContractError


class SequenceProvider:
    def __init__(
        self,
        values: list[dict[str, Any]],
        patches: list[dict[str, Any]] | None = None,
    ) -> None:
        self.values = values
        self.patches = patches or []
        self.requests: list[StoryPlannerRequest] = []
        self.patch_requests: list[StoryPlannerPatchRequest] = []

    def plan_story(self, request: StoryPlannerRequest) -> StoryPlannerProviderResult:
        self.requests.append(request)
        return StoryPlannerProviderResult(candidate=self.values.pop(0), usage={})

    def patch_story(
        self, request: StoryPlannerPatchRequest
    ) -> StoryPlannerPatchProviderResult:
        self.patch_requests.append(request)
        return StoryPlannerPatchProviderResult(patch=self.patches.pop(0), usage={})


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


def test_provider_input_survives_structural_repair_and_is_rendered_instead_of_audit_input() -> None:
    provider = SequenceProvider([{}, _valid()])
    request = replace(
        _request(),
        planner_input={"schema_id": "audit"},
        provider_input={"schema_id": "compiler.story-planner-model-view.v3"},
        provider_input_hash="b" * 64,
    )

    execute_story_planner(provider, request)
    _, rendered, _ = render_story_planner_prompt(provider.requests[1])

    assert provider.requests[1].provider_input == request.provider_input
    assert provider.requests[1].provider_input_hash == "b" * 64
    assert "compiler.story-planner-model-view.v3" in rendered
    assert '"schema_id":"audit"' not in rendered


def test_scene_purpose_error_uses_typed_patch_and_preserves_other_fields() -> None:
    invalid = _valid()
    invalid["scenes"][0]["purpose"] = "opening"
    provider = SequenceProvider(
        [invalid],
        [
            {
                "schema_id": "compiler.story-plan-structural-patch.v1",
                "patches": [
                    {
                        "op": "replace_scene_purpose",
                        "scene_id": "scene_one",
                        "purpose": "hook",
                    }
                ],
            }
        ],
    )

    result = execute_story_planner(provider, _request())

    assert result.candidate == _valid()
    assert len(provider.requests) == 1
    assert len(provider.patch_requests) == 1
    request = provider.patch_requests[0]
    assert request.expected_scene_ids == ("scene_one",)
    system, rendered, _ = render_story_planner_patch_prompt(request)
    assert "compiler.story-plan-structural-patch.v1" in system
    assert "scene_one" in rendered


def test_invalid_purpose_patch_is_bounded_without_full_candidate_regeneration() -> None:
    invalid = _valid()
    invalid["scenes"][0]["purpose"] = "opening"
    provider = SequenceProvider([invalid], [{}, {}, {}])

    with pytest.raises(CompilerContractError) as captured:
        execute_story_planner(provider, _request())

    assert captured.value.reason_code == "compiler_story_planner_structural_repair_exhausted"
    assert len(provider.requests) == 1
    assert len(provider.patch_requests) == 3


def test_purpose_patch_rejects_scope_expansion() -> None:
    invalid = _valid()
    invalid["scenes"][0]["purpose"] = "opening"
    provider = SequenceProvider(
        [invalid],
        [
            {
                "schema_id": "compiler.story-plan-structural-patch.v1",
                "patches": [
                    {
                        "op": "replace_scene_purpose",
                        "scene_id": "scene_other",
                        "purpose": "hook",
                    }
                ],
            },
            {},
            {},
        ],
    )

    with pytest.raises(CompilerContractError):
        execute_story_planner(provider, _request())

    assert provider.patch_requests[1].previous_patch_errors[0]["code"] == (
        "story_planner_structural_patch_scope_mismatch"
    )


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


def test_current_prompt_makes_resolution_closure_algorithm_explicit() -> None:
    system_prompt, _, _ = render_story_planner_prompt(
        replace(_request(), prompt_version=STORY_PLANNER_PROMPT_VERSION)
    )

    assert STORY_PLANNER_PROMPT_VERSION == "story-planner-v3"
    assert "narrative_ir.objects.resolution_specs" in system_prompt
    assert "每个这样的 resolution_spec 恰好出现一次" in system_prompt
    assert "resolve 或 intentionally_unresolved" in system_prompt
    assert "Resolution ID 集合完全相等" in system_prompt
