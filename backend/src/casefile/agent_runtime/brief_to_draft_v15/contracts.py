"""Immutable v15 inputs and governance output contracts.

The conclusion-bearing IR lives here so importing a historical v8-v14 output
model continues to expose exactly its released JSON Schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from casefile.agent_runtime.brief_to_draft_v8.ir import (
    CaseBlueprintV1,
    ConstraintIR,
    ContentNoticeIR,
    EvidenceLogicIRV2,
    LocalKey,
    ResolutionSpecIR,
    StructureLockIR,
)
from casefile.agent_runtime.brief_to_draft_v11.contracts import CoordinatePairV1
from casefile.agent_runtime.brief_to_draft_v12.contracts import TemporalPlanV1
from casefile.agent_runtime.models import StrictAgentOutput


class DraftContextPackV5(StrictAgentOutput):
    schema_id: Literal["draft-context-pack-v5"] = "draft-context-pack-v5"
    task_run_id: int = Field(ge=1)
    casefile_schema_version: Literal["2.0"] = "2.0"
    prompt_bundle_version: Literal["brief-to-draft-v15"] = "brief-to-draft-v15"
    candidate_strategy: str = Field(min_length=1)
    candidate_strategy_version: str = Field(min_length=1)
    brief: dict[str, object]
    frozen_context: dict[str, object]
    budget: dict[str, int]


class PlannerInputV5(StrictAgentOutput):
    context_pack: DraftContextPackV5
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


class TemporalPlannerInputV3(StrictAgentOutput):
    context_pack: DraftContextPackV5
    blueprint: CaseBlueprintV1
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


class DomainDraftInputV5(StrictAgentOutput):
    context_pack: DraftContextPackV5
    blueprint: CaseBlueprintV1
    temporal_plan: TemporalPlanV1
    reference_directory: dict[str, list[str]]
    reference_contract: dict[str, list[str]]
    allowed_reference_values: dict[str, list[str]]
    allowed_wgs84_coordinates: list[CoordinatePairV1] = Field(default_factory=list)
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


class GovernanceDraftInputV5(DomainDraftInputV5):
    """Governance runs after Evidence and must judge the actual matrix output."""

    evidence_logic: EvidenceLogicIRV2


class EvidenceRepairInputV1(DomainDraftInputV5):
    """Evidence repair must see its previous failed output to make a targeted fix.

    Reusing DomainDraftInputV5 left the repair model to re-draft the whole domain
    from scratch because it could never observe the objects it was told to
    "only fix". The previous output is required so the matrix repair can preserve
    untouched reasoning_paths and evidence_assessments while addressing the
    reported semantic issues.
    """

    previous_output: EvidenceLogicIRV2


class MatrixCellSpecIR(StrictAgentOutput):
    """One fixed matrix cell computed deterministically from reasoning paths."""

    hypothesis_key: LocalKey
    information_key: LocalKey


class MatrixAssessmentIR(MatrixCellSpecIR):
    """The model's judgment for one deterministic matrix cell."""

    effect: Literal["supports", "contradicts", "neutral"]
    strength: Literal["weak", "moderate", "strong"]
    rationale: str = Field(min_length=1)


class MatrixEvaluationOutputV1(StrictAgentOutput):
    schema_id: Literal["matrix-evaluation-v1"] = "matrix-evaluation-v1"
    assessments: list[MatrixAssessmentIR] = Field(default_factory=list)


class MatrixEvaluationInputV1(StrictAgentOutput):
    """The evaluator judges only the program-computed cells; it cannot alter them."""

    context_pack: DraftContextPackV5
    blueprint: CaseBlueprintV1
    evidence_graph: EvidenceLogicIRV2
    cells: list[MatrixCellSpecIR] = Field(default_factory=list)
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)
    previous_output: MatrixEvaluationOutputV1 | None = None


class ResolutionConclusionValueIR(StrictAgentOutput):
    slot_key: LocalKey
    value: str | int | float | bool | None = None
    value_key: LocalKey | None = None

    @model_validator(mode="after")
    def exactly_one_value_source(self) -> ResolutionConclusionValueIR:
        if (self.value is None) == (self.value_key is None):
            raise ValueError("exactly one of value or value_key is required")
        return self


class ResolutionConclusionIR(StrictAgentOutput):
    outcome: Literal["answer", "undetermined"]
    summary: str = Field(min_length=1)
    values: list[ResolutionConclusionValueIR] = Field(default_factory=list)
    selected_hypothesis_keys: list[LocalKey] = Field(default_factory=list)
    supporting_reasoning_path_keys: list[LocalKey] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    unresolved_gaps: list[str] = Field(default_factory=list)


class ResolutionSpecIRV2(ResolutionSpecIR):
    """v15 governance shape; conclusion is an AI proposal only."""

    conclusion: ResolutionConclusionIR


class ResolutionGovernanceIRV2(StrictAgentOutput):
    schema_id: Literal["resolution-governance-ir-v2"] = "resolution-governance-ir-v2"
    resolution_specs: list[ResolutionSpecIRV2] = Field(min_length=1)
    constraints: list[ConstraintIR] = Field(default_factory=list)
    structure_locks: list[StructureLockIR] = Field(default_factory=list)
    content_notices: list[ContentNoticeIR] = Field(default_factory=list)


__all__ = [
    "DomainDraftInputV5",
    "DraftContextPackV5",
    "EvidenceRepairInputV1",
    "GovernanceDraftInputV5",
    "MatrixAssessmentIR",
    "MatrixCellSpecIR",
    "MatrixEvaluationInputV1",
    "MatrixEvaluationOutputV1",
    "PlannerInputV5",
    "ResolutionConclusionIR",
    "ResolutionConclusionValueIR",
    "ResolutionGovernanceIRV2",
    "ResolutionSpecIRV2",
    "TemporalPlannerInputV3",
]
