"""V4 delegation contracts and deterministic scope policy, separate from orchestration."""

from __future__ import annotations

import re
from hashlib import sha256
from importlib.resources import files
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

POLICY_VERSION = "casefile-chat-subagents-v4"
TOOLSET_VERSION = "casefile-chat-tools-v9"
SKILL_PATH = "skills/chat_delegation_v4/SKILL.md"
MAX_GATHER_TURNS = 5
MAX_FINALIZE_TURNS = 1
MAX_TOOL_CALLS = 10
MAX_SCOPE_RECORDS = 16
PLANNER_REVISION = "target-first-repair-v1"
MAX_PLANNER_READS = 6


def skill_content() -> str:
    return files("casefile.agent_runtime").joinpath(SKILL_PATH).read_text(encoding="utf-8")


def runtime_manifest() -> dict[str, Any]:
    return {
        "policy_version": POLICY_VERSION,
        "planner_revision": PLANNER_REVISION,
        "max_planner_reads": MAX_PLANNER_READS,
        "max_plan_repairs": 1,
        "skill_path": SKILL_PATH,
        "skill_sha256": sha256(skill_content().encode()).hexdigest(),
        "max_tasks": 2,
        "max_gather_turns": MAX_GATHER_TURNS,
        "max_finalize_turns": MAX_FINALIZE_TURNS,
        "max_tool_calls_per_task": MAX_TOOL_CALLS,
        "max_scope_records": MAX_SCOPE_RECORDS,
        "scope_hops": 1,
    }


class DelegationTask(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=1000)
    scope: str = Field(min_length=1, max_length=1000)
    object_ids: list[str] = Field(min_length=1, max_length=3)
    evidence_gap: str = Field(min_length=1, max_length=1000)
    completion_criteria: str = Field(min_length=1, max_length=1000)
    independence_reason: str = Field(min_length=1, max_length=1000)


class DelegationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["delegate", "single"]
    reason: str = Field(min_length=1, max_length=1000)
    tasks: list[DelegationTask] = Field(max_length=2)

    @model_validator(mode="after")
    def check_decision(self) -> DelegationPlan:
        if (self.decision == "delegate") != bool(self.tasks):
            raise ValueError("delegate requires tasks; single requires no tasks")
        questions = [task.question.casefold() for task in self.tasks]
        if len(questions) != len(set(questions)):
            raise ValueError("duplicate delegated questions")
        return self


def objects(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        record["id"]: record
        for records in document.values()
        if isinstance(records, list)
        for record in records
        if isinstance(record, dict) and isinstance(record.get("id"), str)
    }


def _references(value: Any, known: set[str]) -> set[str]:
    if isinstance(value, str):
        return {value} & known
    if isinstance(value, dict):
        return set().union(*(_references(v, known) for k, v in value.items() if k != "id"))
    if isinstance(value, list):
        return set().union(*(_references(v, known) for v in value))
    return set()


def scoped_document(
    document: dict[str, Any], anchors: list[str]
) -> tuple[dict[str, Any], list[str], bool]:
    """Only anchor records and directly connected records; never a recursive crawl."""
    indexed = objects(document)
    roots = set(anchors) & indexed.keys()
    adjacent = set().union(*(_references(indexed[k], set(indexed)) for k in roots))
    adjacent.update(k for k, record in indexed.items() if _references(record, roots))
    ordered = sorted(roots) + sorted(adjacent - roots)
    included = set(ordered[:MAX_SCOPE_RECORDS])
    bounded = {
        key: [item for item in value if isinstance(item, dict) and item.get("id") in included]
        if isinstance(value, list)
        else value
        for key, value in document.items()
    }
    return bounded, sorted(included), len(ordered) > MAX_SCOPE_RECORDS


def orientation_records(
    document: dict[str, Any], message: str, focus: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Bounded orientation only; the model decides questions, not this ranking."""
    indexed = objects(document)
    explicit = {
        object_id
        for object_id in indexed
        if re.search(r"(?<![A-Za-z0-9_-])" + re.escape(object_id) + r"(?![A-Za-z0-9_-])", message)
    }
    focused = _references(focus or {}, set(indexed))
    roots = explicit | focused
    related = set().union(*(_references(indexed[key], set(indexed)) for key in roots))
    related.update(key for key, record in indexed.items() if _references(record, roots))
    terms = {message[i : i + 2] for i in range(max(0, len(message) - 1))}

    def score(record: dict[str, Any]) -> int:
        label = " ".join(str(record.get(k, "")) for k in ("id", "name", "title", "statement"))
        return sum(term in label for term in terms)

    records: list[dict[str, Any]] = []
    size = 0
    for record in sorted(
        indexed.values(),
        key=lambda record: (
            record["id"] in explicit,
            record["id"] in focused,
            record["id"] in related,
            score(record),
        ),
        reverse=True,
    ):
        length = len(str(record))
        if size + length > 12000:
            continue
        records.append(record)
        size += length
        if len(records) == MAX_SCOPE_RECORDS:
            break
    return records
