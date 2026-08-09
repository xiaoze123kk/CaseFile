"""Strict input contracts for brief-to-draft v9 Prompt Package components."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from casefile.agent_runtime.brief_to_draft_v8.ir import CaseBlueprintV1, DraftContextPackV1
from casefile.agent_runtime.models import StrictAgentOutput


class PlannerInputV1(StrictAgentOutput):
    context_pack: DraftContextPackV1


class DomainDraftInputV1(StrictAgentOutput):
    context_pack: DraftContextPackV1
    blueprint: CaseBlueprintV1
    reference_directory: dict[str, list[str]]
    reference_contract: dict[str, list[str]]
    allowed_reference_values: dict[str, list[str]]
    targeted_repair_issues: list[dict[str, Any]] | None = Field(default=None, max_length=50)


__all__ = ["DomainDraftInputV1", "PlannerInputV1"]
