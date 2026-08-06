"""Versioned deterministic tools exposed to the single CaseFile Agent."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agents import RunContextWrapper, Tool, function_tool

from casefile.agent_runtime.models import GenerationRequest, ToolMetrics
from casefile.contracts import (
    ContractValidationError,
    public_validation_issues,
    validate_casefile,
)

TOOLSET_VERSION = "casefile-generation-tools-v2"

_PREFIXES = {
    "resolution_specs": "res",
    "entities": "ent",
    "relationships": "rel",
    "locations": "loc",
    "events": "evt",
    "information_units": "info",
    "claims": "claim",
    "hypotheses": "hyp",
    "reasoning_paths": "path",
    "constraints": "con",
    "structure_locks": "lock",
}


@dataclass(slots=True)
class GenerationToolContext:
    request: GenerationRequest
    metrics: ToolMetrics = field(default_factory=ToolMetrics)
    plan_calls: int = 0
    validation_calls: int = 0


@function_tool
def plan_object_ids(
    wrapper: RunContextWrapper[GenerationToolContext],
    resolution_specs: int,
    entities: int,
    relationships: int,
    locations: int,
    events: int,
    information_units: int,
    claims: int,
    hypotheses: int,
    reasoning_paths: int,
    constraints: int,
    structure_locks: int,
) -> str:
    """Allocate stable object IDs before drafting the CaseFile candidate."""

    context = wrapper.context
    context.metrics.calls += 1
    context.metrics.valid_calls += 1
    counts = {
        "resolution_specs": resolution_specs,
        "entities": entities,
        "relationships": relationships,
        "locations": locations,
        "events": events,
        "information_units": information_units,
        "claims": claims,
        "hypotheses": hypotheses,
        "reasoning_paths": reasoning_paths,
        "constraints": constraints,
        "structure_locks": structure_locks,
    }
    if any(value < 0 or value > 100 for value in counts.values()):
        raise ValueError("Each object count must be between 0 and 100")
    token = f"t{context.request.task_run_id}"
    collections = {
        name: [f"{_PREFIXES[name]}_{token}_{index:02d}" for index in range(1, count + 1)]
        for name, count in counts.items()
    }
    planned_ids = {object_id for values in collections.values() for object_id in values}
    context.metrics.planned_object_ids = planned_ids
    context.metrics.successful_calls += 1
    context.plan_calls += 1
    context.request.emit(
        "tool.completed",
        "planning",
        {
            "tool": "plan_object_ids",
            "object_count": len(planned_ids),
            "collection_counts": counts,
        },
    )
    return json.dumps(
        {
            "casefile_id": context.request.casefile_id,
            "brief_id": context.request.brief_id,
            "version_id": context.request.version_id,
            "collections": collections,
        },
        ensure_ascii=False,
    )


@function_tool
def validate_casefile_candidate(
    wrapper: RunContextWrapper[GenerationToolContext], candidate_json: str
) -> str:
    """Run the deterministic v1 structural validator against a candidate JSON string."""

    context = wrapper.context
    context.metrics.calls += 1
    context.metrics.valid_calls += 1
    context.validation_calls += 1
    try:
        candidate: Any = json.loads(candidate_json)
        if not isinstance(candidate, dict):
            errors = [
                {
                    "code": "candidate_json_invalid",
                    "path": "",
                    "message": "candidate must be a JSON object",
                }
            ]
            raise ContractValidationError(errors)
        validate_casefile(candidate)
    except json.JSONDecodeError as error:
        errors = [
            {
                "code": "candidate_json_invalid",
                "path": "",
                "message": f"invalid JSON at line {error.lineno}, column {error.colno}",
            }
        ]
        context.request.emit(
            "tool.completed",
            "validating",
            {
                "tool": "validate_casefile_candidate",
                "valid": False,
                "issue_count": len(errors),
                "issues": public_validation_issues(errors),
            },
        )
        return json.dumps({"valid": False, "issues": errors}, ensure_ascii=False)
    except ContractValidationError as error:
        errors = error.errors
        context.request.emit(
            "tool.completed",
            "validating",
            {
                "tool": "validate_casefile_candidate",
                "valid": False,
                "issue_count": len(errors),
                "issues": public_validation_issues(errors),
            },
        )
        return json.dumps({"valid": False, "issues": errors}, ensure_ascii=False)
    context.metrics.successful_calls += 1
    context.request.emit(
        "tool.completed",
        "validating",
        {"tool": "validate_casefile_candidate", "valid": True},
    )
    return json.dumps({"valid": True}, ensure_ascii=False)


GENERATION_TOOLS: list[Tool] = [plan_object_ids, validate_casefile_candidate]

__all__ = ["GENERATION_TOOLS", "GenerationToolContext", "TOOLSET_VERSION"]
