"""Versioned v11 Prompt Package inputs and Story World semantic IR."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from casefile.agent_runtime.brief_to_draft_v8.ir import (
    CaseBlueprintV1,
    KnowledgeStateIR,
    LocalKey,
    RelationshipIR,
    SemanticObjectIR,
)
from casefile.agent_runtime.models import StrictAgentOutput

WallClockTime = Annotated[
    str,
    Field(
        pattern=(
            r"^[0-9]{4}-[0-9]{2}-[0-9]{2}"
            r"(?:T[0-9]{2}(?::[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?)?)?$"
        )
    ),
]
WallClockPrecision = Literal["second", "minute", "hour", "day"]


def _wall_clock_value(value: str, precision: WallClockPrecision) -> datetime:
    patterns = {
        "day": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
        "hour": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}$",
        "minute": r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$",
        "second": (
            r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:"
            r"[0-9]{2}(?:\.[0-9]{1,6})?$"
        ),
    }
    if re.fullmatch(patterns[precision], value) is None:
        raise ValueError("wall-clock value does not match its declared precision")
    suffix = {
        "day": "T00:00:00",
        "hour": ":00:00",
        "minute": ":00",
        "second": "",
    }[precision]
    return datetime.fromisoformat(value + suffix)


class ExactTemporalPositionIRV2(StrictAgentOutput):
    kind: Literal["exact"] = "exact"
    value: WallClockTime
    precision: WallClockPrecision

    @model_validator(mode="after")
    def validate_precision(self) -> ExactTemporalPositionIRV2:
        _wall_clock_value(self.value, self.precision)
        return self


class ApproximateTemporalPositionIRV2(StrictAgentOutput):
    kind: Literal["approximate"] = "approximate"
    value: WallClockTime
    precision: WallClockPrecision

    @model_validator(mode="after")
    def validate_precision(self) -> ApproximateTemporalPositionIRV2:
        _wall_clock_value(self.value, self.precision)
        return self


class RangeTemporalPositionIRV2(StrictAgentOutput):
    kind: Literal["range"] = "range"
    start: WallClockTime
    end: WallClockTime
    precision: WallClockPrecision

    @model_validator(mode="after")
    def validate_range(self) -> RangeTemporalPositionIRV2:
        start = _wall_clock_value(self.start, self.precision)
        end = _wall_clock_value(self.end, self.precision)
        if end < start:
            raise ValueError("wall-clock range end must not precede start")
        return self


class RelativeTemporalPositionIRV2(StrictAgentOutput):
    kind: Literal["relative"] = "relative"
    anchor_event_key: LocalKey
    relation: Literal["before", "after", "same_time"]
    offset_minutes: float | None = Field(default=None, ge=0)


class UnknownTemporalPositionIRV2(StrictAgentOutput):
    kind: Literal["unknown"] = "unknown"


TemporalPositionIRV2 = (
    ExactTemporalPositionIRV2
    | ApproximateTemporalPositionIRV2
    | RangeTemporalPositionIRV2
    | RelativeTemporalPositionIRV2
    | UnknownTemporalPositionIRV2
)


class SchematicSpatialPositionIRV2(StrictAgentOutput):
    coordinate_system: Literal["schematic"] = "schematic"
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)


class Wgs84SpatialPositionIRV2(StrictAgentOutput):
    coordinate_system: Literal["wgs84"] = "wgs84"
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


SpatialPositionIRV2 = SchematicSpatialPositionIRV2 | Wgs84SpatialPositionIRV2


class CoordinatePairV1(StrictAgentOutput):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class DraftContextPackV2(StrictAgentOutput):
    schema_id: Literal["draft-context-pack-v2"] = "draft-context-pack-v2"
    task_run_id: int = Field(ge=1)
    casefile_schema_version: Literal["2.0"] = "2.0"
    prompt_bundle_version: Literal["brief-to-draft-v11"] = "brief-to-draft-v11"
    candidate_strategy: str = Field(min_length=1)
    candidate_strategy_version: str = Field(min_length=1)
    brief: dict[str, object]
    frozen_context: dict[str, object]
    budget: dict[str, int]


class PlannerInputV2(StrictAgentOutput):
    context_pack: DraftContextPackV2


class DomainDraftInputV2(StrictAgentOutput):
    context_pack: DraftContextPackV2
    blueprint: CaseBlueprintV1
    reference_directory: dict[str, list[str]]
    reference_contract: dict[str, list[str]]
    allowed_reference_values: dict[str, list[str]]
    allowed_wgs84_coordinates: list[CoordinatePairV1] = Field(default_factory=list)
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


class TravelTimeIRV2(StrictAgentOutput):
    to_key: LocalKey
    minutes: float = Field(ge=0)


class EntityIRV2(SemanticObjectIR):
    """Inline the stable entity enum for providers that do not resolve remote refs."""

    entity_type: Literal[
        "person",
        "organization",
        "object",
        "system",
        "faction",
        "rule_actor",
        "other",
    ]
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    traits: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    knowledge_states: list[KnowledgeStateIR] = Field(default_factory=list)


class LocationIRV2(SemanticObjectIR):
    name: str = Field(min_length=1)
    spatial_position: SpatialPositionIRV2 | None = None
    parent_key: LocalKey | None = None
    adjacency_keys: list[LocalKey] = Field(default_factory=list)
    access_rules: list[str] = Field(default_factory=list)
    travel_times: list[TravelTimeIRV2] = Field(default_factory=list)
    visibility_rules: list[str] = Field(default_factory=list)


class EventIRV2(SemanticObjectIR):
    title: str = Field(min_length=1)
    truth_status: Literal["canon_true", "reported", "disputed", "false_belief", "unknown"]
    time: TemporalPositionIRV2
    participant_keys: list[LocalKey] = Field(default_factory=list)
    location_key: LocalKey | None = None
    cause_keys: list[LocalKey] = Field(default_factory=list)
    effect_keys: list[LocalKey] = Field(default_factory=list)
    observed_by_keys: list[LocalKey] = Field(default_factory=list)


class StoryWorldIRV2(StrictAgentOutput):
    schema_id: Literal["story-world-ir-v2"] = "story-world-ir-v2"
    entities: list[EntityIRV2] = Field(default_factory=list)
    relationships: list[RelationshipIR] = Field(default_factory=list)
    locations: list[LocationIRV2] = Field(default_factory=list)
    events: list[EventIRV2] = Field(default_factory=list)


__all__ = [
    "ApproximateTemporalPositionIRV2",
    "CoordinatePairV1",
    "DomainDraftInputV2",
    "DraftContextPackV2",
    "EntityIRV2",
    "EventIRV2",
    "ExactTemporalPositionIRV2",
    "LocationIRV2",
    "PlannerInputV2",
    "RangeTemporalPositionIRV2",
    "RelativeTemporalPositionIRV2",
    "SpatialPositionIRV2",
    "StoryWorldIRV2",
    "TemporalPositionIRV2",
    "UnknownTemporalPositionIRV2",
    "Wgs84SpatialPositionIRV2",
]
