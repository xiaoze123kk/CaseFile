"""Strict, provider-facing semantic IR for brief-to-draft v8.

The models deliberately contain no stable CaseFile IDs, ObjectRef object types,
CoreMetadata, CaseFile envelope, or extensions. References are local keys only.
"""

from __future__ import annotations

from typing import Annotated, Literal

from casefile_contracts import (
    ClaimType,
    Classification,
    ConclusionMode,
    Direction,
    EntityType,
    InformationType,
    Level,
    LockType,
    Materiality,
    Operation,
    PathType,
    Precision,
    QuestionType,
    Reliability,
    Status,
    Status1,
    TruthStatus,
    ValueType,
    Visibility,
)
from pydantic import AwareDatetime, Field, model_validator

from casefile.agent_runtime.models import StrictAgentOutput

LocalKey = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,79}$")]
JsonPointer = Annotated[str, Field(pattern=r"^(?:/(?:[^~/]|~[01])*)*$")]


class DraftContextPackV1(StrictAgentOutput):
    """Frozen, deterministic inputs consumed by every v8 model component."""

    schema_id: Literal["draft-context-pack-v1"] = "draft-context-pack-v1"
    task_run_id: int = Field(ge=1)
    casefile_schema_version: Literal["1.0"] = "1.0"
    prompt_bundle_version: str = Field(min_length=1)
    candidate_strategy: str = Field(min_length=1)
    candidate_strategy_version: str = Field(min_length=1)
    brief: dict[str, object]
    frozen_context: dict[str, object]
    budget: dict[str, int]


class BlueprintObjectV1(StrictAgentOutput):
    local_key: LocalKey
    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=500)
    dependency_keys: list[LocalKey] = Field(default_factory=list, max_length=30)


class CaseBlueprintV1(StrictAgentOutput):
    """Fixed-collection object plan; the model cannot invent collection names."""

    schema_id: Literal["case-blueprint-v1"] = "case-blueprint-v1"
    title: str = Field(min_length=1, max_length=300)
    resolution_specs: list[BlueprintObjectV1] = Field(min_length=1, max_length=8)
    entities: list[BlueprintObjectV1] = Field(default_factory=list, max_length=40)
    relationships: list[BlueprintObjectV1] = Field(default_factory=list, max_length=60)
    locations: list[BlueprintObjectV1] = Field(default_factory=list, max_length=30)
    events: list[BlueprintObjectV1] = Field(default_factory=list, max_length=60)
    information_units: list[BlueprintObjectV1] = Field(default_factory=list, max_length=80)
    claims: list[BlueprintObjectV1] = Field(default_factory=list, max_length=60)
    hypotheses: list[BlueprintObjectV1] = Field(default_factory=list, max_length=30)
    reasoning_paths: list[BlueprintObjectV1] = Field(default_factory=list, max_length=30)
    constraints: list[BlueprintObjectV1] = Field(default_factory=list, max_length=40)
    structure_locks: list[BlueprintObjectV1] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def validate_local_graph(self) -> CaseBlueprintV1:
        objects = [item for name in BLUEPRINT_COLLECTIONS for item in getattr(self, name)]
        keys = [item.local_key for item in objects]
        if len(keys) != len(set(keys)):
            raise ValueError("blueprint local_key values must be globally unique")
        known = set(keys)
        unknown = sorted(
            {key for item in objects for key in item.dependency_keys if key not in known}
        )
        if unknown:
            raise ValueError(f"blueprint contains unknown dependency keys: {unknown!r}")
        return self


BLUEPRINT_COLLECTIONS = (
    "resolution_specs",
    "entities",
    "relationships",
    "locations",
    "events",
    "information_units",
    "claims",
    "hypotheses",
    "reasoning_paths",
    "constraints",
    "structure_locks",
)


class SemanticObjectIR(StrictAgentOutput):
    local_key: LocalKey
    # Descriptions are a product requirement, not a best-effort embellishment.
    # Requiring them in the IR lets the structured-output retry repair the
    # offending domain before the final quality gate runs.
    description: str = Field(min_length=1, max_length=4000)
    tags: list[str] = Field(default_factory=list, max_length=20)


class KnowledgeStateIR(StrictAgentOutput):
    as_of_event_key: LocalKey | None = None
    knows_keys: list[LocalKey] = Field(default_factory=list)
    believes_keys: list[LocalKey] = Field(default_factory=list)
    false_belief_keys: list[LocalKey] = Field(default_factory=list)


class EntityIR(SemanticObjectIR):
    entity_type: EntityType
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    traits: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    knowledge_states: list[KnowledgeStateIR] = Field(default_factory=list)


class RelationshipIR(SemanticObjectIR):
    title: str = Field(min_length=1)
    from_key: LocalKey
    to_key: LocalKey
    relationship_type: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    direction: Direction
    truth_status: TruthStatus
    visibility: Visibility


class TravelTimeIR(StrictAgentOutput):
    to_key: LocalKey
    minutes: float = Field(ge=0)


class SpatialPositionIR(StrictAgentOutput):
    coordinate_system: Literal["schematic"] = "schematic"
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)


class LocationIR(SemanticObjectIR):
    name: str = Field(min_length=1)
    spatial_position: SpatialPositionIR | None = None
    parent_key: LocalKey | None = None
    adjacency_keys: list[LocalKey] = Field(default_factory=list)
    access_rules: list[str] = Field(default_factory=list)
    travel_times: list[TravelTimeIR] = Field(default_factory=list)
    visibility_rules: list[str] = Field(default_factory=list)


class TimeIR(StrictAgentOutput):
    start: AwareDatetime
    end: AwareDatetime | None = None
    precision: Precision


class EventIR(SemanticObjectIR):
    title: str = Field(min_length=1)
    truth_status: TruthStatus
    time: TimeIR
    participant_keys: list[LocalKey] = Field(default_factory=list)
    location_key: LocalKey | None = None
    cause_keys: list[LocalKey] = Field(default_factory=list)
    effect_keys: list[LocalKey] = Field(default_factory=list)
    observed_by_keys: list[LocalKey] = Field(default_factory=list)


class StoryWorldIRV1(StrictAgentOutput):
    schema_id: Literal["story-world-ir-v1"] = "story-world-ir-v1"
    entities: list[EntityIR] = Field(default_factory=list)
    relationships: list[RelationshipIR] = Field(default_factory=list)
    locations: list[LocationIR] = Field(default_factory=list)
    events: list[EventIR] = Field(default_factory=list)


class AvailabilityIR(StrictAgentOutput):
    perspective_keys: list[LocalKey] = Field(default_factory=list)
    acquisition_conditions: list[str] = Field(default_factory=list)
    alternative_path_keys: list[LocalKey] = Field(default_factory=list)


class InformationUnitIR(SemanticObjectIR):
    information_type: InformationType
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_event_key: LocalKey | None = None
    reliability: Reliability
    truth_status: TruthStatus
    supports_claim_keys: list[LocalKey] = Field(default_factory=list)
    refutes_claim_keys: list[LocalKey] = Field(default_factory=list)
    availability: AvailabilityIR
    classification: Classification


class ClaimIR(SemanticObjectIR):
    title: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    # Keep this aligned with the CaseFile enum so a bad model value is retried
    # by the structured-output gateway instead of escaping to the compiler.
    claim_type: ClaimType
    support_keys: list[LocalKey] = Field(default_factory=list)
    refute_keys: list[LocalKey] = Field(default_factory=list)
    dependency_claim_keys: list[LocalKey] = Field(default_factory=list)
    status: Status
    materiality: Materiality


class HypothesisIR(SemanticObjectIR):
    title: str = Field(min_length=1)
    proposition: str = Field(min_length=1)
    target_resolution_key: LocalKey
    required_claim_keys: list[LocalKey] = Field(default_factory=list)
    falsifier_keys: list[LocalKey] = Field(default_factory=list)
    competing_hypothesis_keys: list[LocalKey] = Field(default_factory=list)
    status: Status1
    score: float | None = Field(default=None, ge=0, le=1)


class EvidenceAssessmentIR(StrictAgentOutput):
    information_key: LocalKey
    effect: Literal["supports", "contradicts", "neutral"]
    strength: Literal["weak", "moderate", "strong"]
    rationale: str = Field(min_length=1)


class HypothesisIRV2(HypothesisIR):
    evidence_assessments: list[EvidenceAssessmentIR] = Field(default_factory=list)


class ReasoningStepIR(StrictAgentOutput):
    step_key: LocalKey
    input_keys: list[LocalKey]
    operation: Operation
    output_key: LocalKey


class ReasoningPathIR(SemanticObjectIR):
    title: str = Field(min_length=1)
    path_type: PathType
    target_key: LocalKey
    steps: list[ReasoningStepIR] = Field(min_length=1)
    required_for_resolution: bool
    alternative_path_keys: list[LocalKey] = Field(default_factory=list)


class EvidenceLogicIRBase(StrictAgentOutput):
    information_units: list[InformationUnitIR] = Field(default_factory=list)
    claims: list[ClaimIR] = Field(default_factory=list)
    reasoning_paths: list[ReasoningPathIR] = Field(default_factory=list)


class EvidenceLogicIRV1(EvidenceLogicIRBase):
    schema_id: Literal["evidence-logic-ir-v1"] = "evidence-logic-ir-v1"
    hypotheses: list[HypothesisIR] = Field(default_factory=list)


class EvidenceLogicIRV2(EvidenceLogicIRBase):
    schema_id: Literal["evidence-logic-ir-v2"] = "evidence-logic-ir-v2"
    hypotheses: list[HypothesisIRV2] = Field(default_factory=list)


EvidenceLogicIR = EvidenceLogicIRV1 | EvidenceLogicIRV2


class RequiredSlotIR(StrictAgentOutput):
    slot_key: LocalKey
    value_type: ValueType
    required: bool


class ResolutionSpecIR(SemanticObjectIR):
    title: str = Field(min_length=1)
    question_type: QuestionType
    reasoning_question: str = Field(min_length=1)
    conclusion_mode: ConclusionMode
    required_slots: list[RequiredSlotIR] = Field(default_factory=list)
    accepted_answer_texts: list[str] = Field(default_factory=list)
    accepted_answer_keys: list[LocalKey] = Field(default_factory=list)
    required_claim_keys: list[LocalKey] = Field(default_factory=list)


class ConstraintIR(SemanticObjectIR):
    title: str = Field(min_length=1)
    level: Level
    scope_keys: list[LocalKey] = Field(default_factory=list)
    statement: str = Field(min_length=1)
    rule_expression: str | None = None
    conflict_keys: list[LocalKey] = Field(default_factory=list)


class StructureLockIR(SemanticObjectIR):
    title: str = Field(min_length=1)
    lock_type: LockType
    object_key: LocalKey
    field_paths: list[JsonPointer] = Field(min_length=1)
    reason: str = Field(min_length=1)


class ContentNoticeIR(StrictAgentOutput):
    local_key: LocalKey
    category: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    severity: Literal["low", "medium", "high"]
    description: str = Field(min_length=1)


class ResolutionGovernanceIRV1(StrictAgentOutput):
    schema_id: Literal["resolution-governance-ir-v1"] = "resolution-governance-ir-v1"
    resolution_specs: list[ResolutionSpecIR] = Field(min_length=1)
    constraints: list[ConstraintIR] = Field(default_factory=list)
    structure_locks: list[StructureLockIR] = Field(default_factory=list)
    content_notices: list[ContentNoticeIR] = Field(default_factory=list)


DOMAIN_COLLECTIONS = {
    "story_world": ("entities", "relationships", "locations", "events"),
    "evidence_logic": (
        "information_units",
        "claims",
        "hypotheses",
        "reasoning_paths",
    ),
    "resolution_governance": (
        "resolution_specs",
        "constraints",
        "structure_locks",
    ),
}
