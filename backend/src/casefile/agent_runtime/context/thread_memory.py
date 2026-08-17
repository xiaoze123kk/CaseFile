"""Structured Thread Memory contract and the replaceable Compactor protocol.

Compaction never deletes evidence: raw messages stay in ``agent_messages`` and
the state only carries structured summaries plus stable evidence pointers.
A compactor is deliberately not allowed to recurse on its own output — the
merge input is always ``old_state + new raw turns`` (never summary + summary).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator

from casefile.agent_runtime.context.evidence import EvidenceRef
from casefile.agent_runtime.models import EventSink, StrictAgentOutput

THREAD_MEMORY_STATE_KIND = "thread_memory"
DEFAULT_THREAD_MEMORY_COMPACTOR = "thread-memory-compactor-v1"
_MAX_STRING_ITEMS = 50
_MAX_FACT_ITEMS = 200


class ThreadMemoryDecision(StrictAgentOutput):
    """One DB-fact patch decision carried verbatim across compactions."""

    decision: Literal["accepted", "rejected"]
    object_id: str = Field(min_length=1, max_length=200)
    field_path: str = Field(min_length=1, max_length=512)
    reason: str = Field(min_length=1, max_length=2000)
    patch_set_id: int = Field(ge=1)
    thread_ref: str = Field(min_length=1, max_length=300)

    @field_validator("thread_ref")
    @classmethod
    def _thread_ref_is_pointer(cls, value: str) -> str:
        if EvidenceRef.parse(value) is None:
            raise ValueError("thread_ref must be a resolvable evidence pointer")
        return value


class ThreadMemoryVerifiedFact(StrictAgentOutput):
    """One verified fact with the raw message it was extracted from."""

    fact: str = Field(min_length=1, max_length=1000)
    source_message_id: int = Field(ge=1)


class ThreadMemoryDelta(StrictAgentOutput):
    """Compactor output; ``last_compacted_message_seq`` is set by the merger."""

    topics: list[str] = Field(default_factory=list, max_length=_MAX_STRING_ITEMS)
    constraints: list[str] = Field(default_factory=list, max_length=_MAX_STRING_ITEMS)
    decisions: list[ThreadMemoryDecision] = Field(
        default_factory=list,
        max_length=_MAX_FACT_ITEMS,
    )
    verified_facts: list[ThreadMemoryVerifiedFact] = Field(
        default_factory=list,
        max_length=_MAX_FACT_ITEMS,
    )
    failed_hypotheses: list[str] = Field(default_factory=list, max_length=_MAX_STRING_ITEMS)
    unresolved_questions: list[str] = Field(
        default_factory=list,
        max_length=_MAX_STRING_ITEMS,
    )
    next_actions: list[str] = Field(default_factory=list, max_length=_MAX_STRING_ITEMS)
    evidence_refs: list[str] = Field(default_factory=list, max_length=_MAX_FACT_ITEMS)

    @field_validator("evidence_refs")
    @classmethod
    def _evidence_refs_are_pointers(cls, values: list[str]) -> list[str]:
        for value in values:
            if EvidenceRef.parse(value) is None:
                raise ValueError(f"invalid evidence reference: {value!r}")
        return values


class ChatThreadMemoryState(ThreadMemoryDelta):
    """The persisted, versioned state frozen into later chat requests."""

    last_compacted_message_seq: int = Field(ge=0, default=0)


class ThreadCompactionInputV1(StrictAgentOutput):
    """Typed input contract for the ``context_compactor`` Prompt Package."""

    input_hash: str = Field(min_length=1, max_length=64)
    from_message_seq: int = Field(ge=1)
    to_message_seq: int = Field(ge=1)
    old_state: dict[str, Any]
    new_turns: list[dict[str, Any]]
    db_decisions: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("new_turns")
    @classmethod
    def _new_turns_are_messages(cls, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for item in values:
            if item.get("role") not in {"user", "assistant"} or not isinstance(
                item.get("content"),
                str,
            ):
                raise ValueError("new_turns entries must be user/assistant messages")
        return values


@dataclass(frozen=True, slots=True)
class ThreadCompactionRequest:
    """Provider-neutral rolling compaction call."""

    task_run_id: int
    prompt_version: str
    input_hash: str
    input_data: dict[str, Any]
    model_id: str
    api_key: str | None
    network_retries: int
    max_turns: int
    emit: EventSink


@dataclass(frozen=True, slots=True)
class ThreadCompactionResult:
    """Compactor delta plus structured-output usage."""

    candidate: ThreadMemoryDelta
    usage: dict[str, Any]


@runtime_checkable
class ThreadMemoryCompactor(Protocol):
    """Replaceable compaction strategy.

    ``build_input`` prepares the deterministic input document (and hash),
    ``merge`` is the deterministic carry-forward step, and ``validate``
    performs post-merge State Validation before the state may be persisted.
    The LLM call itself is provider-owned; the protocol never touches the
    network.
    """

    name: str
    version: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]

    def build_input(
        self,
        *,
        old_state: ChatThreadMemoryState,
        new_turns: list[dict[str, str]],
        db_decisions: list[dict[str, Any]],
        from_message_seq: int,
        to_message_seq: int,
    ) -> dict[str, Any]: ...

    def merge(
        self,
        old_state: ChatThreadMemoryState,
        delta: ThreadMemoryDelta,
        *,
        db_decisions: list[dict[str, Any]] = ...,
        last_compacted_message_seq: int,
    ) -> ChatThreadMemoryState: ...

    def validate(self, state: ChatThreadMemoryState) -> list[str]: ...


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.strip()
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def thread_memory_state_to_jsonable(state: ChatThreadMemoryState) -> dict[str, Any]:
    return state.model_dump(mode="json")


def thread_memory_state_from_jsonable(raw: dict[str, Any]) -> ChatThreadMemoryState:
    return ChatThreadMemoryState.model_validate(raw)


def thread_memory_input_hash(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ThreadMemoryCompactorV1:
    """Default compactor: verbatim carry-forward plus deterministic dedupe merge."""

    name: str = DEFAULT_THREAD_MEMORY_COMPACTOR
    version: str = "1"
    input_schema: type[BaseModel] = ThreadCompactionInputV1
    output_schema: type[BaseModel] = ThreadMemoryDelta

    def build_input(
        self,
        *,
        old_state: ChatThreadMemoryState,
        new_turns: list[dict[str, str]],
        db_decisions: list[dict[str, Any]],
        from_message_seq: int,
        to_message_seq: int,
    ) -> dict[str, Any]:
        payload = {
            "from_message_seq": from_message_seq,
            "to_message_seq": to_message_seq,
            "old_state": thread_memory_state_to_jsonable(old_state),
            "new_turns": [
                {"role": item["role"], "content": item["content"]} for item in new_turns
            ],
            "db_decisions": [dict(item) for item in db_decisions],
        }
        validated = self.input_schema.model_validate(
            {**payload, "input_hash": thread_memory_input_hash(payload)}
        )
        return validated.model_dump(mode="json")

    def merge(
        self,
        old_state: ChatThreadMemoryState,
        delta: ThreadMemoryDelta,
        *,
        db_decisions: list[dict[str, Any]] | None = None,
        last_compacted_message_seq: int,
    ) -> ChatThreadMemoryState:
        merged_decisions = list(old_state.decisions)
        decision_keys = {
            (item.patch_set_id, item.field_path, item.decision)
            for item in merged_decisions
        }
        for raw in db_decisions or []:
            try:
                item = ThreadMemoryDecision.model_validate(raw)
            except ValueError:
                continue
            key = (item.patch_set_id, item.field_path, item.decision)
            if key not in decision_keys:
                decision_keys.add(key)
                merged_decisions.append(item)
        for item in delta.decisions:
            key = (item.patch_set_id, item.field_path, item.decision)
            if key not in decision_keys:
                decision_keys.add(key)
                merged_decisions.append(item)

        facts_by_source: dict[int, ThreadMemoryVerifiedFact] = {
            item.source_message_id: item for item in old_state.verified_facts
        }
        for fact in delta.verified_facts:
            facts_by_source[fact.source_message_id] = fact

        return ChatThreadMemoryState(
            topics=_dedupe_preserving_order([*old_state.topics, *delta.topics]),
            constraints=_dedupe_preserving_order(
                [*old_state.constraints, *delta.constraints]
            ),
            decisions=merged_decisions,
            verified_facts=sorted(
                facts_by_source.values(),
                key=lambda item: item.source_message_id,
            ),
            failed_hypotheses=_dedupe_preserving_order(
                [*old_state.failed_hypotheses, *delta.failed_hypotheses]
            ),
            unresolved_questions=_dedupe_preserving_order(
                [*old_state.unresolved_questions, *delta.unresolved_questions]
            ),
            next_actions=_dedupe_preserving_order(
                [*old_state.next_actions, *delta.next_actions]
            ),
            evidence_refs=_dedupe_preserving_order(
                [*old_state.evidence_refs, *delta.evidence_refs]
            ),
            last_compacted_message_seq=max(
                old_state.last_compacted_message_seq,
                last_compacted_message_seq,
            ),
        )

    def validate(self, state: ChatThreadMemoryState) -> list[str]:
        errors: list[str] = []
        for item in state.verified_facts:
            if item.source_message_id > state.last_compacted_message_seq:
                errors.append(
                    f"verified fact source_message_id {item.source_message_id} "
                    f"exceeds last_compacted_message_seq {state.last_compacted_message_seq}"
                )
        for value in state.evidence_refs:
            if EvidenceRef.parse(value) is None:
                errors.append(f"invalid evidence reference: {value!r}")
        if state.last_compacted_message_seq < 0:
            errors.append("last_compacted_message_seq must be >= 0")
        return errors


def preservation_errors(
    old_state: ChatThreadMemoryState,
    new_state: ChatThreadMemoryState,
) -> list[str]:
    """Verify carry-forward survival after a merge."""

    errors: list[str] = []
    old_constraints = set(old_state.constraints)
    new_constraints = set(new_state.constraints)
    for constraint in old_constraints - new_constraints:
        errors.append(f"constraint lost during compaction: {constraint!r}")
    old_decisions = {
        (item.patch_set_id, item.field_path, item.decision) for item in old_state.decisions
    }
    new_decisions = {
        (item.patch_set_id, item.field_path, item.decision) for item in new_state.decisions
    }
    for decision in old_decisions - new_decisions:
        errors.append(f"decision lost during compaction: {decision!r}")
    if new_state.last_compacted_message_seq < old_state.last_compacted_message_seq:
        errors.append(
            "last_compacted_message_seq regressed from "
            f"{old_state.last_compacted_message_seq} to "
            f"{new_state.last_compacted_message_seq}"
        )
    return errors


def empty_thread_memory_state() -> ChatThreadMemoryState:
    return ChatThreadMemoryState()


def register_compactor(
    compactor: ThreadMemoryCompactor,
    registry: dict[str, ThreadMemoryCompactor],
) -> None:
    existing = registry.get(compactor.name)
    if existing is not None and existing.version != compactor.version:
        raise ValueError(
            f"Thread Memory compactor {compactor.name!r} is already registered at "
            f"version {existing.version!r}; cannot register {compactor.version!r}"
        )
    registry[compactor.name] = compactor


def default_compactor_registry() -> dict[str, ThreadMemoryCompactor]:
    registry: dict[str, ThreadMemoryCompactor] = {}
    register_compactor(ThreadMemoryCompactorV1(), registry)
    return registry


__all__ = [
    "ChatThreadMemoryState",
    "DEFAULT_THREAD_MEMORY_COMPACTOR",
    "THREAD_MEMORY_STATE_KIND",
    "ThreadCompactionInputV1",
    "ThreadCompactionRequest",
    "ThreadCompactionResult",
    "ThreadMemoryCompactor",
    "ThreadMemoryCompactorV1",
    "ThreadMemoryDecision",
    "ThreadMemoryDelta",
    "ThreadMemoryVerifiedFact",
    "default_compactor_registry",
    "empty_thread_memory_state",
    "preservation_errors",
    "register_compactor",
    "thread_memory_input_hash",
    "thread_memory_state_from_jsonable",
    "thread_memory_state_to_jsonable",
]
