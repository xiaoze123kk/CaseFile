"""Provider-neutral requests and results for durable CaseFile Agent tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from casefile_contracts import (
    BriefIntakeCandidate as BriefIntakeCandidateContract,
)
from casefile_contracts import (
    BriefIntakeQuestionSet as BriefIntakeQuestionSetContract,
)


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


class CandidateStrategy(StrEnum):
    """Frozen strategy labels for one Brief-to-Draft candidate run."""

    BALANCED = "balanced"
    STRUCTURE_FIRST = "structure_first"
    ATMOSPHERE_FIRST = "atmosphere_first"
    REASONING_FIRST = "reasoning_first"


CANDIDATE_STRATEGY_VERSION = "candidate-strategy-v1"
CANDIDATE_STRATEGY_LABELS: dict[CandidateStrategy, str] = {
    CandidateStrategy.BALANCED: "常规候选",
    CandidateStrategy.STRUCTURE_FIRST: "结构优先",
    CandidateStrategy.ATMOSPHERE_FIRST: "氛围优先",
    CandidateStrategy.REASONING_FIRST: "推理优先",
}

SelectableCandidateStrategy = Literal[
    "structure_first",
    "atmosphere_first",
    "reasoning_first",
]

GenerationCollection = Literal[
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
]


class GenerationPlanObject(StrictAgentOutput):
    local_key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    collection: GenerationCollection
    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=400)
    referenced_keys: list[str] = Field(default_factory=list, max_length=20)


class GenerationPlan(StrictAgentOutput):
    title: str = Field(min_length=1, max_length=300)
    objects: list[GenerationPlanObject] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validates_local_graph(self) -> GenerationPlan:
        keys = [item.local_key for item in self.objects]
        if len(keys) != len(set(keys)):
            raise ValueError("generation plan local_key values must be unique")
        known = set(keys)
        unknown = {ref for item in self.objects for ref in item.referenced_keys if ref not in known}
        if unknown:
            raise ValueError(f"generation plan references unknown keys: {sorted(unknown)!r}")
        if not any(item.collection == "resolution_specs" for item in self.objects):
            raise ValueError("generation plan requires at least one resolution spec")
        return self


class BriefStrategyOption(StrictAgentOutput):
    """One Brief-specific direction shown before expensive Draft generation."""

    strategy: SelectableCandidateStrategy
    direction: str = Field(min_length=1, max_length=600)
    focus: str = Field(min_length=1, max_length=300)
    strengths: list[str] = Field(min_length=2, max_length=3)
    tradeoffs: list[str] = Field(min_length=1, max_length=2)
    brief_fit: str = Field(min_length=1, max_length=400)


class BriefStrategyOptionsCandidate(StrictAgentOutput):
    """Exactly three tailored directions and one non-binding recommendation."""

    strategy_version: Literal["candidate-strategy-v1"] = "candidate-strategy-v1"
    options: list[BriefStrategyOption] = Field(min_length=3, max_length=3)
    recommended_strategy: SelectableCandidateStrategy
    recommendation_reason: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def contains_each_strategy_once(self) -> BriefStrategyOptionsCandidate:
        expected = {"structure_first", "atmosphere_first", "reasoning_first"}
        actual = [option.strategy for option in self.options]
        if len(set(actual)) != len(actual) or set(actual) != expected:
            raise ValueError("options must contain each selectable strategy exactly once")
        if self.recommended_strategy not in actual:
            raise ValueError("recommended_strategy must reference one option")
        return self


class ExtractedAnchor(StrictAgentOutput):
    statement: str = Field(min_length=1)


class ExtractedConstraint(StrictAgentOutput):
    statement: str = Field(min_length=1)
    suggested_strength: str = Field(pattern=r"^(?:hard|soft)$")


class BriefAnchorExtractCandidate(StrictAgentOutput):
    """Atomic candidates that remain proposals until the author confirms them."""

    suggested_author_answer: str | None = Field(default=None, min_length=1, max_length=20_000)
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
    mode: Literal["extract", "suggest_author_answer"] = "extract"


@dataclass(frozen=True, slots=True)
class BriefStrategyOptionsRequest:
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
    candidate_strategy: CandidateStrategy = CandidateStrategy.BALANCED
    candidate_strategy_version: str = CANDIDATE_STRATEGY_VERSION
    reusable_steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    agent_version: str | None = None
    toolset_version: str | None = None


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
class BriefStrategyOptionsResult:
    candidate: BriefStrategyOptionsCandidate
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


class IdeaCandidateModel(StrictAgentOutput):
    """One creative direction for Path B (帮我想一个)."""

    concept: str = Field(min_length=1, max_length=200)
    core_suspense: str = Field(min_length=1, max_length=300)
    reasoning_type: Literal["deductive", "inductive", "abductive", "hybrid"]
    conclusion_mode: Literal["author_anchored", "agent_proposed", "open"]
    target_experience: str = Field(min_length=1, max_length=300)
    design_risk: str = Field(min_length=1, max_length=300)
    scale_estimate: str = Field(min_length=1, max_length=160)


class IdeaCandidateSet(StrictAgentOutput):
    """Exactly three creative directions."""

    candidates: list[IdeaCandidateModel] = Field(min_length=3, max_length=3)


@dataclass(frozen=True, slots=True)
class IdeaGenerationRequest:
    task_run_id: int
    prompt_version: str
    regenerate: bool
    existing_concepts: tuple[str, ...]
    input_hash: str
    model_id: str
    api_key: str | None
    max_turns: int
    emit: EventSink
    network_retries: int = 2


@dataclass(frozen=True, slots=True)
class IdeaGenerationResult:
    candidate: IdeaCandidateSet
    usage: dict[str, Any]


REVERSE_PARSE_ITEM_TYPES = (
    "entity_alias",
    "event",
    "information_unit",
    "knowledge_state",
    "relationship_causality",
    "candidate_question",
    "candidate_conclusion",
)
REVERSE_PARSE_GRADINGS = (
    "explicit",
    "inferred",
    "needs_confirmation",
    "conflicting",
    "missing_important",
)


class ReverseParseItem(StrictAgentOutput):
    """One extracted item with grading and source block evidence."""

    item_type: Literal[
        "entity_alias", "event", "information_unit", "knowledge_state",
        "relationship_causality", "candidate_question", "candidate_conclusion",
    ]
    content: dict[str, Any]
    grading: Literal[
        "explicit", "inferred", "needs_confirmation", "conflicting", "missing_important",
    ]
    source_block_refs: list[int] = Field(default_factory=list)
    source_quote: str = Field(min_length=1, max_length=800)


class ReverseParseCandidate(StrictAgentOutput):
    """Complete structured extraction of one document."""

    items: list[ReverseParseItem] = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class ReverseParseRequest:
    task_run_id: int
    prompt_version: str
    blocks: list[dict[str, Any]]
    input_hash: str
    model_id: str
    api_key: str | None
    max_turns: int
    emit: EventSink
    network_retries: int = 2


@dataclass(frozen=True, slots=True)
class ReverseParseResult:
    candidate: ReverseParseCandidate
    usage: dict[str, Any]


def _rate(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator
