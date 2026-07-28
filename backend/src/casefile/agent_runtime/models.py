"""Provider-neutral Agent generation request, result, and metric types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class EventSink(Protocol):
    def __call__(self, event_type: str, stage: str, payload: dict[str, Any]) -> None: ...


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    task_run_id: int
    brief: dict[str, Any]
    casefile_id: str
    brief_id: str
    brief_version: int
    project_profile: dict[str, Any]
    version_id: str
    version_no: int
    parent_version_id: str | None
    model_id: str
    api_key: str | None
    max_turns: int
    emit: EventSink
    repair_feedback: tuple[str, ...] = ()


@dataclass(slots=True)
class ToolMetrics:
    calls: int = 0
    valid_calls: int = 0
    successful_calls: int = 0
    adopted_results: int = 0
    planned_object_ids: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "valid_calls": self.valid_calls,
            "successful_calls": self.successful_calls,
            "adopted_results": self.adopted_results,
            "validity_rate": _rate(self.valid_calls, self.calls),
            "execution_success_rate": _rate(self.successful_calls, self.valid_calls),
            "result_adoption_rate": _rate(self.adopted_results, self.successful_calls),
        }


@dataclass(frozen=True, slots=True)
class GenerationResult:
    candidate: dict[str, Any]
    usage: dict[str, Any]
    tools: ToolMetrics


def _rate(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator
