"""Immutable v18 Plan-Execute prompt contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from casefile.agent_runtime.brief_to_draft_v8.ir import CaseBlueprintV1, EvidenceLogicIRV2
from casefile.agent_runtime.brief_to_draft_v11.contracts import CoordinatePairV1
from casefile.agent_runtime.brief_to_draft_v12.contracts import StoryWorldIRV3, TemporalPlanV1
from casefile.agent_runtime.brief_to_draft_v15.contracts import (
    MatrixCellSpecIR,
    MatrixEvaluationOutputV1,
    ResolutionGovernanceIRV2,
)
from casefile.agent_runtime.models import StrictAgentOutput
from casefile.agent_runtime.plan_execute import (
    ExecutionGoalCandidate,
    ExecutionPlan,
    ExecutionPlanCandidate,
    PlanContext,
    PlanReconciliationReport,
)


class BriefExecutionGoalCandidate(ExecutionGoalCandidate):
    owner_branch: Literal["story_world", "evidence_logic"]


class BriefExecutionPlanCandidate(StrictAgentOutput):
    schema_id: Literal["casefile.execution-plan-candidate.v1"] = (
        "casefile.execution-plan-candidate.v1"
    )
    goals: list[BriefExecutionGoalCandidate] = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def validate_graph(self) -> BriefExecutionPlanCandidate:
        ExecutionPlanCandidate.model_validate(self.model_dump(mode="json"))
        return self


class DraftContextPackV8(StrictAgentOutput):
    schema_id: Literal["draft-context-pack-v8"] = "draft-context-pack-v8"
    task_run_id: int = Field(ge=1)
    casefile_schema_version: Literal["2.0"] = "2.0"
    prompt_bundle_version: Literal["brief-to-draft-v18"] = "brief-to-draft-v18"
    candidate_strategy: str = Field(min_length=1)
    candidate_strategy_version: str = Field(min_length=1)
    brief: dict[str, object]
    frozen_context: dict[str, object]
    budget: dict[str, int]


class BlueprintPlanOutputV1(StrictAgentOutput):
    schema_id: Literal["brief-to-draft-blueprint-plan-output-v1"] = (
        "brief-to-draft-blueprint-plan-output-v1"
    )
    blueprint: CaseBlueprintV1
    execution_plan: BriefExecutionPlanCandidate


class StoryPlanOutputV1(StrictAgentOutput):
    schema_id: Literal["brief-to-draft-story-plan-output-v1"] = (
        "brief-to-draft-story-plan-output-v1"
    )
    artifact: StoryWorldIRV3
    plan_checkin: dict[str, object] | None = None


class EvidencePlanOutputV1(StrictAgentOutput):
    schema_id: Literal["brief-to-draft-evidence-plan-output-v1"] = (
        "brief-to-draft-evidence-plan-output-v1"
    )
    artifact: EvidenceLogicIRV2
    plan_checkin: dict[str, object] | None = None


class PlannerInputV8(StrictAgentOutput):
    context_pack: DraftContextPackV8
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)
    previous_output: CaseBlueprintV1 | None = None
    previous_execution_plan: BriefExecutionPlanCandidate | None = None


class TemporalPlannerInputV6(StrictAgentOutput):
    context_pack: DraftContextPackV8
    blueprint: CaseBlueprintV1
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


class DomainDraftInputV8(StrictAgentOutput):
    context_pack: DraftContextPackV8
    blueprint: CaseBlueprintV1
    temporal_plan: TemporalPlanV1
    plan_context: PlanContext | None = None
    reference_directory: dict[str, list[str]]
    reference_contract: dict[str, list[str]]
    allowed_reference_values: dict[str, list[str]]
    allowed_wgs84_coordinates: list[CoordinatePairV1] = Field(default_factory=list)
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


class GovernanceDraftInputV8(DomainDraftInputV8):
    evidence_logic: EvidenceLogicIRV2


class EvidenceRepairInputV4(DomainDraftInputV8):
    previous_output: EvidenceLogicIRV2


class MatrixEvaluationInputV4(StrictAgentOutput):
    context_pack: DraftContextPackV8
    blueprint: CaseBlueprintV1
    evidence_graph: EvidenceLogicIRV2
    cells: list[MatrixCellSpecIR] = Field(default_factory=list)
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)
    previous_output: MatrixEvaluationOutputV1 | None = None


class PlanReconciliationInputV1(StrictAgentOutput):
    execution_plan: ExecutionPlan
    final_candidate: dict[str, object]
    checkins: list[dict[str, object]] = Field(default_factory=list, max_length=256)


__all__ = [
    "BriefExecutionGoalCandidate",
    "BriefExecutionPlanCandidate",
    "BlueprintPlanOutputV1",
    "DomainDraftInputV8",
    "DraftContextPackV8",
    "EvidencePlanOutputV1",
    "EvidenceRepairInputV4",
    "GovernanceDraftInputV8",
    "MatrixEvaluationInputV4",
    "PlanReconciliationInputV1",
    "PlanReconciliationReport",
    "PlannerInputV8",
    "ResolutionGovernanceIRV2",
    "StoryPlanOutputV1",
    "TemporalPlannerInputV6",
]
