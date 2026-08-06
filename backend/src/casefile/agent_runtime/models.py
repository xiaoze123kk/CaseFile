"""Provider-neutral requests and results for durable CaseFile Agent tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from casefile_contracts import (
    BriefIntakeCandidate as BriefIntakeCandidateContract,
)
from casefile_contracts import (
    BriefIntakeQuestionSet as BriefIntakeQuestionSetContract,
)
from pydantic import BaseModel, ConfigDict, Field


class EventSink(Protocol):
    def __call__(self, event_type: str, stage: str, payload: dict[str, Any]) -> None: ...


class StrictAgentOutput(BaseModel):
    """Base class for provider output that must not silently accept extra fields."""

    model_config = ConfigDict(extra="forbid")


class BriefPolishCandidate(StrictAgentOutput):
    """Reviewable semantic-preserving polish proposal."""

    polished_text: str = Field(min_length=1)
    preserved_intent_summary: str = Field(min_length=1)
    ambiguities: list[str] = Field(default_factory=list)
    introduced_details: list[str] = Field(default_factory=list)


PolishMode = Literal["proofread", "rewrite", "narrative_enhance"]


class ExtractedAnchor(StrictAgentOutput):
    statement: str = Field(min_length=1)


class ExtractedConstraint(StrictAgentOutput):
    statement: str = Field(min_length=1)
    suggested_strength: str = Field(pattern=r"^(?:hard|soft)$")


class BriefAnchorExtractCandidate(StrictAgentOutput):
    """Atomic candidates that remain proposals until the author confirms them."""

    author_anchors: list[ExtractedAnchor] = Field(default_factory=list)
    creative_constraints: list[ExtractedConstraint] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CaseFileChatSuggestionCandidate(StrictAgentOutput):
    """One reviewable field-level change proposed against the frozen CaseFile."""

    object_id: str = Field(min_length=1)
    path: str = Field(
        min_length=2,
        pattern=r"^/(?:[^/~]|~[01])+(?:/(?:[^/~]|~[01])+)*$",
    )
    value_json: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class CaseFileChatCandidate(StrictAgentOutput):
    """An author-facing answer plus optional changes that still require approval."""

    answer: str = Field(min_length=1)
    referenced_object_ids: list[str] = Field(default_factory=list)
    suggestions: list[CaseFileChatSuggestionCandidate] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class BriefIntakeQuestionsRequest:
    task_run_id: int
    prompt_version: str
    source_text: str
    existing_questions: list[dict[str, Any]]
    mode: Literal["initial", "additional"]
    input_hash: str
    model_id: str
    api_key: str | None
    max_turns: int
    emit: EventSink
    network_retries: int = 2


@dataclass(frozen=True, slots=True)
class BriefIntakeSynthesizeRequest:
    task_run_id: int
    prompt_version: str
    input_data: dict[str, Any]
    input_hash: str
    model_id: str
    api_key: str | None
    max_turns: int
    emit: EventSink
    network_retries: int = 2


@dataclass(frozen=True, slots=True)
class BriefPolishRequest:
    task_run_id: int
    prompt_version: str
    source_text: str
    polish_mode: PolishMode
    input_hash: str
    model_id: str
    api_key: str | None
    max_turns: int
    emit: EventSink
    network_retries: int = 2


@dataclass(frozen=True, slots=True)
class BriefAnchorExtractRequest:
    task_run_id: int
    prompt_version: str
    brief: dict[str, Any]
    input_hash: str
    model_id: str
    api_key: str | None
    max_turns: int
    emit: EventSink
    network_retries: int = 2


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    task_run_id: int
    prompt_version: str
    brief: dict[str, Any]
    casefile_id: str
    brief_id: str
    brief_version: int
    version_id: str
    version_no: int
    parent_version_id: str | None
    model_id: str
    api_key: str | None
    max_turns: int
    emit: EventSink
    network_retries: int = 2
    repair_feedback: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class CaseFileChatRequest:
    task_run_id: int
    prompt_version: str
    casefile: dict[str, Any]
    history: tuple[dict[str, str], ...]
    message: str
    editable_fields_by_collection: dict[str, tuple[str, ...]]
    input_hash: str
    model_id: str
    api_key: str | None
    max_turns: int
    emit: EventSink
    network_retries: int = 2


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


@dataclass(frozen=True, slots=True)
class BriefPolishResult:
    candidate: BriefPolishCandidate
    usage: dict[str, Any]
    polish_mode: PolishMode


@dataclass(frozen=True, slots=True)
class BriefAnchorExtractResult:
    candidate: BriefAnchorExtractCandidate
    usage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CaseFileChatResult:
    candidate: CaseFileChatCandidate
    usage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BriefIntakeQuestionsResult:
    candidate: BriefIntakeQuestionSetContract
    usage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BriefIntakeSynthesizeResult:
    candidate: BriefIntakeCandidateContract
    usage: dict[str, Any]


def _rate(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator
