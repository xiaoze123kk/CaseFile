"""Dynamic user-message renderers for the author-facing CaseFile Agent tasks."""

from __future__ import annotations

import json
from typing import Any

from casefile.agent_runtime.models import CaseFileChatRequest, GenerationRequest

AGENT_VERSION = "casefile-single-agent-v2"


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


def casefile_chat_input(request: CaseFileChatRequest) -> str:
    payload = {
        "input_hash": request.input_hash,
        "casefile": request.casefile,
        "thread_history": list(request.history),
        "author_message": request.message,
    }
    return (
        "Reply to the author and optionally propose reviewable field changes. "
        "The complete CaseFile below is frozen for this turn.\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


__all__ = [
    "AGENT_VERSION",
    "anchor_extract_input",
    "casefile_chat_input",
    "generation_input",
    "polish_input",
]
