"""Immutable v16 prompt inputs for semantic relationship coverage."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from casefile.agent_runtime.brief_to_draft_v8.ir import CaseBlueprintV1, EvidenceLogicIRV2
from casefile.agent_runtime.brief_to_draft_v11.contracts import CoordinatePairV1
from casefile.agent_runtime.brief_to_draft_v12.contracts import TemporalPlanV1
from casefile.agent_runtime.brief_to_draft_v15.contracts import (
    MatrixCellSpecIR,
    MatrixEvaluationOutputV1,
    ResolutionGovernanceIRV2,
)
from casefile.agent_runtime.models import StrictAgentOutput


class DraftContextPackV6(StrictAgentOutput):
    schema_id: Literal["draft-context-pack-v6"] = "draft-context-pack-v6"
    task_run_id: int = Field(ge=1)
    casefile_schema_version: Literal["2.0"] = "2.0"
    prompt_bundle_version: Literal["brief-to-draft-v16"] = "brief-to-draft-v16"
    candidate_strategy: str = Field(min_length=1)
    candidate_strategy_version: str = Field(min_length=1)
    brief: dict[str, object]
    frozen_context: dict[str, object]
    budget: dict[str, int]


class PlannerInputV6(StrictAgentOutput):
    context_pack: DraftContextPackV6
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)
    previous_output: CaseBlueprintV1 | None = None


class TemporalPlannerInputV4(StrictAgentOutput):
    context_pack: DraftContextPackV6
    blueprint: CaseBlueprintV1
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


class DomainDraftInputV6(StrictAgentOutput):
    context_pack: DraftContextPackV6
    blueprint: CaseBlueprintV1
    temporal_plan: TemporalPlanV1
    reference_directory: dict[str, list[str]]
    reference_contract: dict[str, list[str]]
    allowed_reference_values: dict[str, list[str]]
    allowed_wgs84_coordinates: list[CoordinatePairV1] = Field(default_factory=list)
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


class GovernanceDraftInputV6(DomainDraftInputV6):
    evidence_logic: EvidenceLogicIRV2


class EvidenceRepairInputV2(DomainDraftInputV6):
    previous_output: EvidenceLogicIRV2


class MatrixEvaluationInputV2(StrictAgentOutput):
    context_pack: DraftContextPackV6
    blueprint: CaseBlueprintV1
    evidence_graph: EvidenceLogicIRV2
    cells: list[MatrixCellSpecIR] = Field(default_factory=list)
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)
    previous_output: MatrixEvaluationOutputV1 | None = None


__all__ = [
    "DomainDraftInputV6",
    "DraftContextPackV6",
    "EvidenceRepairInputV2",
    "GovernanceDraftInputV6",
    "MatrixEvaluationInputV2",
    "PlannerInputV6",
    "ResolutionGovernanceIRV2",
    "TemporalPlannerInputV4",
]
