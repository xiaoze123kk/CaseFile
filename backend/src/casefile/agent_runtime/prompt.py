"""Versioned prompt contract for Brief-to-Draft generation."""

from __future__ import annotations

import json
from typing import Any

from casefile.agent_runtime.models import GenerationRequest

AGENT_VERSION = "casefile-single-agent-v1"
PROMPT_VERSION = "brief-to-draft-v1"

INSTRUCTIONS = """Role: You are the single CaseFile architect.

Goal: Convert one confirmed Brief into a complete CaseFile 1.0 Draft that is useful in the
workbench and passes the supplied structured output contract.

Success criteria:
- preserve the Brief's creative intent and frozen project profile
- produce internally consistent IDs, references, chronology, and resolution logic
- call plan_object_ids exactly once before drafting and use its allocated IDs
- return the final CaseFile only through the structured output type

Constraints:
- never invent a different casefile_id, brief_ref, version, or project_profile
- do not call any database or external side-effect tool
- validate_casefile_candidate is optional and may be used before finalizing
- hidden reasoning is not user-visible; tool calls and concise stage summaries are audited

Stop rules: finish when the structured candidate is coherent and all required fields are present.
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
            "project_profile": request.project_profile,
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


__all__ = ["AGENT_VERSION", "INSTRUCTIONS", "PROMPT_VERSION", "generation_input"]
