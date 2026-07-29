"""Versioned prompt contracts for the three author-facing Brief Agent tasks."""

from __future__ import annotations

import json
from typing import Any

from casefile.agent_runtime.models import GenerationRequest

AGENT_VERSION = "casefile-single-agent-v2"
PROMPT_VERSION = "brief-to-draft-v3"
POLISH_PROMPT_VERSION = "brief-polish-v2"
ANCHOR_EXTRACT_PROMPT_VERSION = "brief-anchor-extract-v2"

INSTRUCTIONS = """Role: You are the single CaseFile architect.

Goal: Convert one confirmed Brief into a complete CaseFile 1.0 Draft that is useful in the
workbench and passes the supplied structured output contract.

Success criteria:
- preserve the Brief's creative intent and reasoning proposition
- treat every author anchor as a hard invariant and every creative constraint at its confirmed level
- preserve the author answer exactly when resolution_mode is author_anchored
- produce internally consistent IDs, references, chronology, and resolution logic
- call plan_object_ids exactly once before drafting and use its allocated IDs
- return the final CaseFile only through the structured output type

Constraints:
- CaseFile is target-neutral: do not introduce player, gameplay phase, fairness, delivery-target,
  Compiler, or audience assumptions unless they are explicitly present as authored source facts
- never invent a different casefile_id, brief_ref, or version
- do not weaken, omit, or silently rewrite confirmed author anchors
- if the Brief leaves the answer open, represent that uncertainty instead of manufacturing an answer
- do not call any database or external side-effect tool
- validate_casefile_candidate is optional and may be used before finalizing
- hidden reasoning is not user-visible; tool calls and concise stage summaries are audited

Stop rules: finish when the structured candidate is coherent and all required fields are present.
"""

POLISH_INSTRUCTIONS = """Role: You are an editorial assistant preparing a reviewable proposal.

Goal: Improve clarity, grammar, and organization of the supplied raw creative source without
changing its meaning.

Rules:
- preserve every fact, ambiguity, uncertainty, contradiction, name, number, and authored boundary
- do not add plot facts, answers, goals, audiences, game mechanics, or delivery-target assumptions
- never claim the proposal replaces the raw source; it is a separate candidate for human review
- explain briefly what intent was preserved and list unresolved ambiguities
- return only the requested structured result; do not reveal hidden reasoning
"""

ANCHOR_EXTRACT_INSTRUCTIONS = """Role: You help an author decompose authored truth and boundaries.

Goal: Turn the supplied author answer and creative boundary text into atomic review candidates.

Rules:
- author_anchors are concise factual invariants derived only from author_answer
- creative_constraints are atomic boundaries derived only from boundary_text
- never repair, reinterpret, merge away, or silently resolve contradictions
- put incompleteness, conflicts, ambiguity, or suspicious assumptions into warnings
- suggested_strength is advisory; use hard for explicit must/not/immutable boundaries and soft for
  preferences or tendencies
- return proposals only; the application will require explicit human confirmation before they
  become Brief hard constraints
- return only the requested structured result; do not reveal hidden reasoning
"""


def generation_input(request: GenerationRequest) -> str:
    payload: dict[str, Any] = {
        "brief": request.brief,
        "frozen_context": {
            "schema_version": "1.0",
            "casefile_id": request.casefile_id,
            "brief_ref": {
                "brief_id": request.brief_id,
                "version": request.brief_version,
            },
            "version": {
                "version_id": request.version_id,
                "version_no": request.version_no,
                "parent_version_id": request.parent_version_id,
            },
            "status": "draft",
        },
    }
    if request.repair_feedback:
        payload["repair_feedback"] = list(request.repair_feedback)
    return (
        "Generate the CaseFile from this JSON input. Treat frozen_context values as exact.\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def polish_input(source_text: str, input_hash: str) -> str:
    return (
        "Create one JSON polish proposal for this immutable raw source. "
        "The input_hash is provenance, not content to edit.\n"
        + json.dumps(
            {"input_hash": input_hash, "raw_source": source_text},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def anchor_extract_input(brief: dict[str, Any], input_hash: str) -> str:
    payload = {
        "input_hash": input_hash,
        "resolution_mode": brief["resolution_mode"],
        "reasoning_proposition": brief["reasoning_proposition"],
        "author_answer": brief["author_answer"],
        "boundary_text": brief["boundary_text"],
    }
    return (
        "Return one JSON object containing atomic candidates and warnings "
        "for this authored input.\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def prompt_version_for_task(task_type: str) -> str:
    versions = {
        "brief_polish": POLISH_PROMPT_VERSION,
        "brief_anchor_extract": ANCHOR_EXTRACT_PROMPT_VERSION,
        "brief_to_draft": PROMPT_VERSION,
    }
    try:
        return versions[task_type]
    except KeyError as error:
        raise ValueError(f"Unsupported task type: {task_type}") from error


__all__ = [
    "AGENT_VERSION",
    "ANCHOR_EXTRACT_INSTRUCTIONS",
    "ANCHOR_EXTRACT_PROMPT_VERSION",
    "INSTRUCTIONS",
    "POLISH_INSTRUCTIONS",
    "POLISH_PROMPT_VERSION",
    "PROMPT_VERSION",
    "anchor_extract_input",
    "generation_input",
    "polish_input",
    "prompt_version_for_task",
]
