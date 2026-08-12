"""v12 contracts for a complete, auditable CaseFile temporal structure."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field, model_validator

from casefile.agent_runtime.brief_to_draft_v8.ir import (
    CaseBlueprintV1,
    LocalKey,
    RelationshipIR,
    SemanticObjectIR,
)
from casefile.agent_runtime.brief_to_draft_v11.contracts import (
    CoordinatePairV1,
    EntityIRV2,
    LocationIRV2,
    SpatialPositionIRV2,
    TemporalPositionIRV2,
    TravelTimeIRV2,
    _wall_clock_value,
)
from casefile.agent_runtime.models import StrictAgentOutput

TemporalBasis = Literal[
    "brief_absolute",
    "brief_approximate",
    "brief_range",
    "brief_relation",
    "blueprint_precedence",
    "design_anchor",
    "design_relative",
]


class DraftContextPackV3(StrictAgentOutput):
    """Frozen v12 context, separated from the immutable v11 package."""

    schema_id: Literal["draft-context-pack-v3"] = "draft-context-pack-v3"
    task_run_id: int = Field(ge=1)
    casefile_schema_version: Literal["2.0"] = "2.0"
    prompt_bundle_version: Literal["brief-to-draft-v12", "brief-to-draft-v13"] = (
        "brief-to-draft-v12"
    )
    candidate_strategy: str = Field(min_length=1)
    candidate_strategy_version: str = Field(min_length=1)
    brief: dict[str, object]
    frozen_context: dict[str, object]
    budget: dict[str, int]


class PlannerInputV3(StrictAgentOutput):
    context_pack: DraftContextPackV3
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


class TemporalAssignmentV1(StrictAgentOutput):
    """One event time and the non-user-visible basis used to choose it."""

    event_key: LocalKey
    time: TemporalPositionIRV2
    basis: TemporalBasis
    basis_refs: list[str] = Field(default_factory=list, max_length=20)


class TemporalPlanV1(StrictAgentOutput):
    schema_id: Literal["temporal-plan-v1"] = "temporal-plan-v1"
    assignments: list[TemporalAssignmentV1] = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def validate_assignments(self) -> TemporalPlanV1:
        keys = [assignment.event_key for assignment in self.assignments]
        if len(keys) != len(set(keys)):
            raise ValueError("temporal assignments must contain each event_key once")
        if any(assignment.time.kind == "unknown" for assignment in self.assignments):
            raise ValueError("v12 temporal assignments must not use unknown time")
        return self


class TemporalPlannerInputV1(StrictAgentOutput):
    context_pack: DraftContextPackV3
    blueprint: CaseBlueprintV1
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


class DomainDraftInputV3(StrictAgentOutput):
    context_pack: DraftContextPackV3
    blueprint: CaseBlueprintV1
    temporal_plan: TemporalPlanV1
    reference_directory: dict[str, list[str]]
    reference_contract: dict[str, list[str]]
    allowed_reference_values: dict[str, list[str]]
    allowed_wgs84_coordinates: list[CoordinatePairV1] = Field(default_factory=list)
    targeted_repair_issues: list[dict[str, object]] | None = Field(default=None, max_length=50)


class EventNarrativeIRV3(SemanticObjectIR):
    """Story-owned event fields. Time is injected from TemporalPlanV1 server-side."""

    title: str = Field(min_length=1)
    truth_status: Literal["canon_true", "reported", "disputed", "false_belief", "unknown"]
    participant_keys: list[LocalKey] = Field(default_factory=list)
    location_key: LocalKey | None = None
    cause_keys: list[LocalKey] = Field(default_factory=list)
    effect_keys: list[LocalKey] = Field(default_factory=list)
    observed_by_keys: list[LocalKey] = Field(default_factory=list)


class StoryWorldIRV3(StrictAgentOutput):
    schema_id: Literal["story-world-ir-v3"] = "story-world-ir-v3"
    entities: list[EntityIRV2] = Field(default_factory=list)
    relationships: list[RelationshipIR] = Field(default_factory=list)
    locations: list[LocationIRV2] = Field(default_factory=list)
    events: list[EventNarrativeIRV3] = Field(default_factory=list)


def temporal_plan_issues(
    plan: TemporalPlanV1,
    blueprint: CaseBlueprintV1,
) -> list[dict[str, object]]:
    """Validate v12 coverage and ensure every relation resolves to a wall clock."""

    expected = {item.local_key for item in blueprint.events}
    assignments = {item.event_key: item for item in plan.assignments}
    issues: list[dict[str, object]] = []
    for event_key in sorted(expected - set(assignments)):
        issues.append(
            _temporal_issue("temporal_assignment_missing", event_key, "每个事件都必须有时间规划。")
        )
    for event_key in sorted(set(assignments) - expected):
        issues.append(
            _temporal_issue(
                "temporal_assignment_unplanned", event_key, "时间规划引用了蓝图外事件。"
            )
        )
    if issues:
        return issues

    absolute = {
        key
        for key, assignment in assignments.items()
        if assignment.time.kind in {"exact", "approximate", "range"}
    }
    if not absolute:
        issues.append(
            {
                "code": "temporal_anchor_missing",
                "path": "/assignments",
                "message": "时间线至少需要一个作品内绝对时间锚点。",
                "component_id": "temporal_structure_planner",
                "failure_layer": "temporal_grounding",
                "schema_id": "temporal-plan-v1",
            }
        )

    for event_key, assignment in assignments.items():
        time = assignment.time
        if time.kind != "relative":
            continue
        if time.anchor_event_key not in assignments:
            issues.append(
                _temporal_issue(
                    "relative_time_anchor_unknown",
                    event_key,
                    "相对时间锚点必须属于同一时间规划。",
                )
            )
        elif time.anchor_event_key == event_key:
            issues.append(
                _temporal_issue(
                    "relative_time_self_anchor", event_key, "事件不能以自身作为时间锚点。"
                )
            )
        if time.relation in {"before", "after"} and time.offset_minutes is None:
            issues.append(
                _temporal_issue(
                    "temporal_offset_missing",
                    event_key,
                    "前后关系必须给出设计或原稿支持的分钟偏移。",
                )
            )
        if time.relation == "same_time" and time.offset_minutes not in {None, 0}:
            issues.append(
                _temporal_issue("invalid_relative_time", event_key, "同时发生不能携带非零偏移。")
            )

    for event_key in sorted(assignments):
        if event_key not in _resolved_temporal_positions(assignments):
            issues.append(
                _temporal_issue(
                    "temporal_event_unresolved",
                    event_key,
                    "事件无法沿相对关系解析到绝对时间锚点。",
                )
            )
    return _unique_issues(issues)


def temporal_story_issues(
    story: StoryWorldIRV3,
    plan: TemporalPlanV1,
) -> list[dict[str, object]]:
    """Reject story causality that contradicts the already-resolved temporal plan."""

    assignments = {item.event_key: item for item in plan.assignments}
    positions = _resolved_temporal_positions(assignments)
    issues: list[dict[str, object]] = []
    for event in story.events:
        event_position = positions.get(event.local_key)
        if event_position is None:
            continue
        for cause_key in event.cause_keys:
            cause_position = positions.get(cause_key)
            if cause_position is not None and cause_position[0] > event_position[0]:
                issues.append(
                    _temporal_issue(
                        "temporal_causal_inversion",
                        event.local_key,
                        "原因事件不能晚于其结果事件。",
                        "/cause_keys",
                    )
                )
        for effect_key in event.effect_keys:
            effect_position = positions.get(effect_key)
            if effect_position is not None and event_position[0] > effect_position[0]:
                issues.append(
                    _temporal_issue(
                        "temporal_causal_inversion",
                        event.local_key,
                        "结果事件不能早于其原因事件。",
                        "/effect_keys",
                    )
                )
    return _unique_issues(issues)


def _resolved_temporal_positions(
    assignments: dict[str, TemporalAssignmentV1],
) -> dict[str, tuple[datetime, datetime]]:
    resolved: dict[str, tuple[datetime, datetime]] = {}
    visiting: set[str] = set()

    def resolve(key: str) -> tuple[datetime, datetime] | None:
        if key in resolved:
            return resolved[key]
        if key in visiting:
            return None
        assignment = assignments.get(key)
        if assignment is None:
            return None
        visiting.add(key)
        time = assignment.time
        position: tuple[datetime, datetime] | None
        if time.kind == "exact" or time.kind == "approximate":
            value = _wall_clock_value(time.value, time.precision)
            position = (value, value)
        elif time.kind == "range":
            position = (
                _wall_clock_value(time.start, time.precision),
                _wall_clock_value(time.end, time.precision),
            )
        elif time.kind == "relative":
            anchor = resolve(time.anchor_event_key)
            if anchor is None:
                position = None
            elif time.relation == "before" and time.offset_minutes is not None:
                value = anchor[0] - timedelta(minutes=time.offset_minutes)
                position = (value, value)
            elif time.relation == "after" and time.offset_minutes is not None:
                value = anchor[1] + timedelta(minutes=time.offset_minutes)
                position = (value, value)
            elif time.relation == "same_time" and time.offset_minutes in {None, 0}:
                position = (anchor[0], anchor[0])
            else:
                position = None
        else:
            position = None
        visiting.remove(key)
        if position is not None:
            resolved[key] = position
        return position

    for key in assignments:
        resolve(key)
    return resolved


def _temporal_issue(
    code: str,
    event_key: str,
    message: str,
    suffix: str = "",
) -> dict[str, object]:
    return {
        "code": code,
        "path": f"/assignments/{event_key}{suffix}",
        "message": message,
        "component_id": "temporal_structure_planner",
        "failure_layer": "temporal_grounding",
        "schema_id": "temporal-plan-v1",
        "ir_path": f"/events/{event_key}/time",
    }


def _unique_issues(issues: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[tuple[object, object]] = set()
    for issue in issues:
        key = (issue["code"], issue["path"])
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return result


__all__ = [
    "CoordinatePairV1",
    "DomainDraftInputV3",
    "DraftContextPackV3",
    "EventNarrativeIRV3",
    "PlannerInputV3",
    "SpatialPositionIRV2",
    "StoryWorldIRV3",
    "TemporalAssignmentV1",
    "TemporalPlanV1",
    "TemporalPlannerInputV1",
    "TemporalPositionIRV2",
    "TravelTimeIRV2",
    "temporal_plan_issues",
    "temporal_story_issues",
]
