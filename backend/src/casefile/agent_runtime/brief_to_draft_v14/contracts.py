"""v14 input contracts without mutating historical Prompt Package releases."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from casefile.agent_runtime.brief_to_draft_v8.ir import CaseBlueprintV1
from casefile.agent_runtime.brief_to_draft_v11.contracts import CoordinatePairV1
from casefile.agent_runtime.brief_to_draft_v12.contracts import (
    EventNarrativeIRV3,
    StoryWorldIRV3,
    TemporalAssignmentV1,
    TemporalPlanV1,
    TemporalPositionIRV2,
    temporal_plan_issues,
    temporal_story_issues,
)
from casefile.agent_runtime.models import StrictAgentOutput


class DraftContextPackV4(StrictAgentOutput):
    schema_id: Literal["draft-context-pack-v4"] = "draft-context-pack-v4"
    task_run_id: int = Field(ge=1)
    casefile_schema_version: Literal["2.0"] = "2.0"
    prompt_bundle_version: Literal["brief-to-draft-v14"] = "brief-to-draft-v14"
    candidate_strategy: str = Field(min_length=1)
    candidate_strategy_version: str = Field(min_length=1)
    brief: dict[str, object]
    frozen_context: dict[str, object]
    budget: dict[str, int]


class PlannerInputV4(StrictAgentOutput):
    context_pack: DraftContextPackV4
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


class TemporalPlannerInputV2(StrictAgentOutput):
    context_pack: DraftContextPackV4
    blueprint: CaseBlueprintV1
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


class DomainDraftInputV4(StrictAgentOutput):
    context_pack: DraftContextPackV4
    blueprint: CaseBlueprintV1
    temporal_plan: TemporalPlanV1
    reference_directory: dict[str, list[str]]
    reference_contract: dict[str, list[str]]
    allowed_reference_values: dict[str, list[str]]
    allowed_wgs84_coordinates: list[CoordinatePairV1] = Field(default_factory=list)
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


__all__ = [
    "DomainDraftInputV4",
    "DraftContextPackV4",
    "EventNarrativeIRV3",
    "PlannerInputV4",
    "StoryWorldIRV3",
    "TemporalAssignmentV1",
    "TemporalPlanV1",
    "TemporalPlannerInputV2",
    "TemporalPositionIRV2",
    "temporal_plan_issues",
    "temporal_story_issues",
]
