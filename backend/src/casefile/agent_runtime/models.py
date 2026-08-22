"""Provider-neutral requests and results for durable CaseFile Agent tasks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, is_dataclass
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


#: The pre-context-pipeline policy frozen on existing chat TaskRuns. The
#: extensible context package re-exports this constant; unknown future policy
#: versions fall back to it instead of failing the task.
LEGACY_CONTEXT_POLICY_VERSION = "agent-focus-v1"


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
    referenced_object_ids: list[str] = Field(default_factory=list, max_length=50)
    referenced_event_ids: list[str] = Field(default_factory=list, max_length=50)
    referenced_validation_issue_ids: list[str] = Field(default_factory=list, max_length=50)
    suggested_view: str | None = Field(
        default=None,
        pattern=r"^(?:timeline|relations|reasoning|map|export|compile|evidence)$",
    )
    suggestions: list[CaseFileChatSuggestionCandidate] = Field(default_factory=list)


AuditFindingKind = Literal[
    "dangling_ref",
    "contradiction",
    "temporal",
    "motivation_gap",
    "scope_gap",
]
AuditFindingSeverity = Literal["S1", "S2", "S3"]


class CaseFileChatAuditFindingCandidate(StrictAgentOutput):
    """One evidence-backed logical hole found by the logic_audit executor."""

    finding_id: str = Field(min_length=1, max_length=32, pattern=r"^F[1-9][0-9]*$")
    kind: AuditFindingKind
    severity: AuditFindingSeverity
    title: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=4000)
    needs_manual_review: bool = False
    evidence_object_ids: list[str] = Field(default_factory=list, max_length=20)
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=20)
    evidence_validation_issue_ids: list[str] = Field(default_factory=list, max_length=20)


class CaseFileChatSuggestionCandidateV2(StrictAgentOutput):
    """v2 suggestion that may bind itself to one audit finding."""

    object_id: str = Field(min_length=1)
    path: str = Field(
        min_length=2,
        pattern=r"^/(?:[^/~]|~[01])+(?:/(?:[^/~]|~[01])+)*$",
    )
    value_json: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    finding_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        pattern=r"^F[1-9][0-9]*$",
    )


class CaseFileChatTargetLockedRepairOutput(StrictAgentOutput):
    """The only model-authored fields in a server-locked audit repair."""

    value_json: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class CaseFileChatCandidateV2(StrictAgentOutput):
    """v2 chat output: v1 fields plus the structured logic-audit findings."""

    answer: str = Field(min_length=1)
    referenced_object_ids: list[str] = Field(default_factory=list, max_length=50)
    referenced_event_ids: list[str] = Field(default_factory=list, max_length=50)
    referenced_validation_issue_ids: list[str] = Field(default_factory=list, max_length=50)
    suggested_view: str | None = Field(
        default=None,
        pattern=r"^(?:timeline|relations|reasoning|map|export|compile|evidence)$",
    )
    suggestions: list[CaseFileChatSuggestionCandidateV2] = Field(default_factory=list)
    audit_findings: list[CaseFileChatAuditFindingCandidate] = Field(
        default_factory=list, max_length=5
    )


IntentPrimaryLabel = Literal[
    "question",
    "analysis",
    "explain_issue",
    "logic_audit",
    "edit_request",
    "validate_request",
    "unsupported_action",
    "clarify",
    "out_of_scope",
]
IntentRiskLevel = Literal["low", "medium", "high"]
IntentComplexity = Literal["low", "medium", "high"]


class IntentEntityMention(StrictAgentOutput):
    """LLM may only emit the mention text; resolved_ref is filled by code."""

    text: str = Field(min_length=1, max_length=200)


class IntentEntitiesOutput(StrictAgentOutput):
    object_mentions: list[IntentEntityMention] = Field(default_factory=list, max_length=20)
    event_mentions: list[IntentEntityMention] = Field(default_factory=list, max_length=20)
    issue_mentions: list[IntentEntityMention] = Field(default_factory=list, max_length=20)
    temporal_mentions: list[str] = Field(default_factory=list, max_length=20)


class IntentConstraintsOutput(StrictAgentOutput):
    preserved_negations: list[str] = Field(default_factory=list, max_length=20)
    preserved_actions: list[str] = Field(default_factory=list, max_length=20)
    output_format: Literal["answer", "patch_proposal", "mixed"] = "answer"


class ChatTaskUnderstandingOutput(StrictAgentOutput):
    """One LLM intent call: Task State plus a conservative pre-route canonical."""

    original_query: str = Field(min_length=1, max_length=100_000)
    normalized_query: str = Field(min_length=1, max_length=100_000)
    primary_intent: IntentPrimaryLabel
    sub_intents: list[str] = Field(default_factory=list, max_length=20)
    entities: IntentEntitiesOutput = Field(default_factory=IntentEntitiesOutput)
    constraints: IntentConstraintsOutput = Field(default_factory=IntentConstraintsOutput)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    complexity: IntentComplexity = "low"
    multi_step: bool = False
    risk_level: IntentRiskLevel = "low"
    ambiguous: bool = False
    missing_info: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    canonical_query: str = Field(min_length=1, max_length=100_000)


class QueryRewriteOutput(StrictAgentOutput):
    """Route-specific LLM rewrite; only invoked for MULTI_QUERY/DECOMPOSE in R2."""

    canonical_query: str = Field(min_length=1, max_length=100_000)
    retrieval_queries: list[str] = Field(default_factory=list, max_length=10)
    rewrite_decision: Literal[
        "KEEP",
        "CONTEXTUALIZE",
        "EXPAND",
        "DECOMPOSE",
        "MULTI_QUERY",
    ]
    preservation_checks: dict[str, bool] = Field(default_factory=dict)


class ChatIntentRouterInputV1(StrictAgentOutput):
    """Prompt Package input contract for the v2 intent router."""

    input_hash: str = Field(min_length=1)
    author_message: str = Field(min_length=1, max_length=100_000)
    thread_history: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    focus: dict[str, Any] = Field(default_factory=dict)
    candidate_object_labels: dict[str, list[str]] = Field(default_factory=dict)
    validation_issues: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class ChatRewriteInputV1(StrictAgentOutput):
    """Prompt Package input contract for the v2 post-route rewriter."""

    input_hash: str = Field(min_length=1)
    original_query: str = Field(min_length=1, max_length=100_000)
    normalized_query: str = Field(min_length=1, max_length=100_000)
    conservative_canonical_query: str = Field(min_length=1, max_length=100_000)
    primary_intent: str = Field(min_length=1, max_length=64)
    sub_intents: list[str] = Field(default_factory=list, max_length=20)
    constraints: dict[str, Any] = Field(default_factory=dict)
    rewrite_strategy: str = Field(min_length=1, max_length=32)
    route_profile: str = Field(min_length=1, max_length=128)


class ChatExecutorInputV1(StrictAgentOutput):
    """Prompt Package input contract shared by every v2 chat executor component."""

    input_hash: str = Field(min_length=1)
    casefile: dict[str, Any]
    thread_history: list[dict[str, Any]] = Field(default_factory=list)
    author_message: str = Field(min_length=1, max_length=100_000)
    editable_fields_by_collection: dict[str, list[str]] = Field(default_factory=dict)
    focus: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    routing: dict[str, Any] | None = None


class ChatExecutorInputV2(StrictAgentOutput):
    """v2 input contract for the v4/v5 executors: skeleton plus expansions.

    ``casefile`` contains only id/collection/label/type skeletons; full record
    content is fetched with the read-only tools. ``focus_objects`` carries the
    bounded full-object and one-hop neighbor expansion selected by the context
    policy, Phase 3 policies additionally bind the rolling ``thread_memory``
    state, and Phase 4 bindings attach the read-only ``context_dashboard``.
    """

    input_hash: str = Field(min_length=1)
    casefile: dict[str, Any]
    focus_objects: dict[str, Any] = Field(default_factory=dict)
    thread_history: list[dict[str, Any]] = Field(default_factory=list)
    thread_memory: dict[str, Any] | None = None
    context_dashboard: dict[str, Any] | None = None
    author_message: str = Field(min_length=1, max_length=100_000)
    editable_fields_by_collection: dict[str, list[str]] = Field(default_factory=dict)
    focus: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    routing: dict[str, Any] | None = None


class ChatFinalizerInputV1(ChatExecutorInputV2):
    """Frozen v14 finalizer input: context plus a server-owned tool ledger."""

    tool_ledger: dict[str, Any] | None = None
    evidence_summary: str = Field(default="", max_length=20_000)
    previous_candidate: dict[str, Any] | None = None
    repair_plan: dict[str, Any] | None = None


class ChatFinalizerInputV2(ChatFinalizerInputV1):
    """v15 finalizer input before the server post-finalizer patch gate."""


class ChatEvidenceOutputV1(StrictAgentOutput):
    """Small tool-agent handoff; the server owns the authoritative ledger."""

    evidence_summary: str = Field(min_length=1, max_length=20_000)


@dataclass(frozen=True, slots=True)
class ChatTaskUnderstanding:
    """Semantic Task State; Routing Policy consumes this, not the raw user text."""

    primary_intent: str
    sub_intents: tuple[str, ...] = ()
    entities: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    complexity: str = "low"
    multi_step: bool = False
    risk_level: str = "low"
    ambiguous: bool = False
    missing_info: tuple[str, ...] = ()
    confidence: float = 1.0
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Policy decision produced by deterministic code; the model never picks routes."""

    router_version: str = "casefile-chat-router-v2"
    route_source: str = "llm"
    candidate_routes: tuple[dict[str, Any], ...] = ()
    routes: tuple[dict[str, Any], ...] = ()
    execution_mode: str = "serial"
    merge_strategy: str | None = None
    rewrite_strategy: str = "KEEP"
    execution_profile: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    confidence_margin: float = 0.0
    reason_codes: tuple[str, ...] = ()
    fallback: str = "question"
    route_hash: str = ""


@dataclass(frozen=True, slots=True)
class QueryRewriteResult:
    """Dual Representation: original stays authoritative, derived forms are per-executor."""

    original_query: str
    normalized_query: str
    canonical_query: str
    retrieval_queries: tuple[str, ...] = ()
    rewrite_decision: str = "KEEP"
    preservation_checks: dict[str, Any] = field(default_factory=dict)


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
    schema_version: str = "2.0"
    network_retries: int = 2
    repair_feedback: tuple[dict[str, Any], ...] = ()
    candidate_strategy: CandidateStrategy = CandidateStrategy.BALANCED
    candidate_strategy_version: str = CANDIDATE_STRATEGY_VERSION
    reusable_steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    agent_version: str | None = None
    toolset_version: str | None = None


ThreadEvidenceResolver = Callable[[str], dict[str, Any] | None]


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
    validation_issues: tuple[dict[str, Any], ...] = ()
    validation: dict[str, Any] = field(default_factory=dict)
    focus: dict[str, Any] = field(default_factory=dict)
    routing_hint: dict[str, Any] = field(default_factory=dict)
    task_understanding: ChatTaskUnderstanding | None = None
    route: RouteDecision | None = None
    rewrite: QueryRewriteResult | None = None
    network_retries: int = 2
    toolset_version: str = "casefile-chat-tools-v1"
    context_policy_version: str = LEGACY_CONTEXT_POLICY_VERSION
    assembled_input: dict[str, Any] | None = None
    thread_id: int | None = None
    thread_evidence_resolver: ThreadEvidenceResolver | None = None
    repair_feedback: tuple[str, ...] = ()
    frozen_tool_ledger: dict[str, Any] | None = None
    safe_patch_registry: dict[str, Any] | None = None
    previous_candidate: dict[str, Any] | None = None
    repair_plan: dict[str, Any] | None = None
    target_locked_repair: dict[str, Any] | None = None


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
    candidate: CaseFileChatCandidate | CaseFileChatCandidateV2
    usage: dict[str, Any]
    tools: ToolMetrics = field(default_factory=ToolMetrics)
    tool_ledger: dict[str, Any] | None = None
    safe_patch_registry: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class IntentUnderstandingResult:
    candidate: ChatTaskUnderstandingOutput
    usage: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RouteSpecificRewriteRequest:
    task_run_id: int
    prompt_version: str
    original_query: str
    normalized_query: str
    conservative_canonical_query: str
    primary_intent: str
    sub_intents: tuple[str, ...]
    constraints: dict[str, Any]
    rewrite_strategy: str
    route_profile: str
    input_hash: str
    model_id: str
    api_key: str | None
    max_turns: int
    emit: EventSink
    network_retries: int = 2


@dataclass(frozen=True, slots=True)
class RouteSpecificRewriteResult:
    candidate: QueryRewriteOutput
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
    preferences: dict[str, Any] | None = None


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
        "entity_alias",
        "event",
        "information_unit",
        "knowledge_state",
        "relationship_causality",
        "candidate_question",
        "candidate_conclusion",
    ]
    content: dict[str, Any]
    grading: Literal[
        "explicit",
        "inferred",
        "needs_confirmation",
        "conflicting",
        "missing_important",
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


def agent_state_to_jsonable(value: Any) -> Any:
    """Recursively convert routing state dataclasses/tuples into JSON-ready data."""

    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: agent_state_to_jsonable(getattr(value, field.name))
            for field in value.__dataclass_fields__.values()
        }
    if isinstance(value, tuple):
        return [agent_state_to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [agent_state_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: agent_state_to_jsonable(item) for key, item in value.items()}
    return value


def chat_state_as_dict(value: Any) -> dict[str, Any]:
    """Return one routing state dataclass as a plain dict (tuples become lists)."""

    converted = agent_state_to_jsonable(value)
    if not isinstance(converted, dict):
        raise TypeError(f"expected a routing state dataclass, got {type(value).__name__}")
    return converted


def chat_routing_payload_as_dict(request: CaseFileChatRequest) -> dict[str, Any] | None:
    """Serialize resolved chat routing state exactly like the legacy prompt payload.

    The prompt renderer and the context engine share this helper so the payload
    providers receive and the audited context manifest can never diverge.
    """

    if request.route is None:
        return None
    routing_payload: dict[str, Any] = {"route": chat_state_as_dict(request.route)}
    if request.task_understanding is not None:
        routing_payload["task_understanding"] = chat_state_as_dict(request.task_understanding)
    if request.rewrite is not None:
        routing_payload["rewrite"] = chat_state_as_dict(request.rewrite)
    return routing_payload


def _rate(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator
