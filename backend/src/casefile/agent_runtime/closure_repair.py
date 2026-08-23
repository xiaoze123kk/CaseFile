"""Provider-facing contracts and adapter for bounded closure repair."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import Field, model_validator

from casefile.agent_runtime.models import EventSink, StrictAgentOutput
from casefile.domain.logical_mutation.repair.models import (
    ClosureRepairContextV1,
    RepairProposal,
    RepairUpdateOperation,
)

CLOSURE_REPAIR_PROMPT_VERSION = "closure-repair-v1"
CLOSURE_REPAIR_AGENT_VERSION = "closure-repair-agent-v1"
CLOSURE_REPAIR_TOOLSET_VERSION = "closure-repair-tools-v1"
CLOSURE_REPAIR_COMPONENT_ID = "repair"
CLOSURE_REPAIR_SCHEMA_ID = "closure-repair-output-v1"


class ClosureRepairPromptInputV1(StrictAgentOutput):
    """Frozen context and round number sent as one JSON user message."""

    context: dict[str, Any]
    round_no: int = Field(ge=1, le=2)

    @model_validator(mode="after")
    def validates_frozen_context(self) -> ClosureRepairPromptInputV1:
        context_hash = self.context.get("context_hash")
        if self.context.get("context_version") != "closure-repair-context-v1":
            raise ValueError("closure repair context version is invalid")
        if (
            not isinstance(context_hash, str)
            or len(context_hash) != 64
            or any(value not in "0123456789abcdef" for value in context_hash)
        ):
            raise ValueError("closure repair context hash is invalid")
        if not isinstance(self.context.get("obligations"), list) or not self.context[
            "obligations"
        ]:
            raise ValueError("closure repair obligations are missing")
        return self


class ClosureRepairOperationOutputV1(StrictAgentOutput):
    """One model-authored UPDATE candidate; authorization remains server-owned."""

    obligation_keys: list[str] = Field(min_length=1, max_length=8)
    object_id: str = Field(min_length=1, max_length=200)
    field_path: str = Field(
        min_length=2,
        max_length=500,
        pattern=r"^/(?:[^/~]|~[01])+(?:/(?:[^/~]|~[01])+)*$",
    )
    value_json: str = Field(min_length=1, max_length=100_000)
    reason: str = Field(min_length=1, max_length=2_000)


class ClosureRepairOutputV1(StrictAgentOutput):
    """Strict model output for one atomic repair proposal round."""

    operations: list[ClosureRepairOperationOutputV1] = Field(min_length=1, max_length=8)


@dataclass(frozen=True, slots=True)
class ClosureRepairRequest:
    prompt_version: str
    context: dict[str, Any]
    round_no: int
    model_id: str
    api_key: str | None
    max_turns: int
    emit: EventSink
    network_retries: int = 2


@dataclass(frozen=True, slots=True)
class ClosureRepairProviderResult:
    candidate: ClosureRepairOutputV1
    usage: dict[str, Any]


class ClosureRepairProvider(Protocol):
    def repair_closure(
        self,
        request: ClosureRepairRequest,
    ) -> ClosureRepairProviderResult: ...


@dataclass(slots=True)
class ProviderRepairProposer:
    """Bridge an AgentProvider repair call into the pure RepairProposer port."""

    provider: ClosureRepairProvider
    model_id: str
    api_key: str | None
    emit: EventSink
    prompt_version: str = CLOSURE_REPAIR_PROMPT_VERSION
    max_turns: int = 1
    network_retries: int = 2
    results: list[ClosureRepairProviderResult] = field(default_factory=list, init=False)

    def propose(
        self,
        context: ClosureRepairContextV1,
        *,
        round_no: int,
    ) -> RepairProposal:
        result = self.provider.repair_closure(
            ClosureRepairRequest(
                prompt_version=self.prompt_version,
                context=context.as_dict(),
                round_no=round_no,
                model_id=self.model_id,
                api_key=self.api_key,
                max_turns=self.max_turns,
                emit=self.emit,
                network_retries=self.network_retries,
            )
        )
        self.results.append(result)
        operations: list[RepairUpdateOperation] = []
        for item in result.candidate.operations:
            try:
                new_value = json.loads(item.value_json)
            except json.JSONDecodeError as error:
                raise ValueError("repair_proposal_value_json_invalid") from error
            operations.append(
                RepairUpdateOperation(
                    obligation_keys=tuple(item.obligation_keys),
                    object_id=item.object_id,
                    field_path=item.field_path,
                    new_value=new_value,
                    reason=item.reason,
                )
            )
        return RepairProposal(context.context_hash, tuple(operations))


__all__ = [
    "CLOSURE_REPAIR_AGENT_VERSION",
    "CLOSURE_REPAIR_COMPONENT_ID",
    "CLOSURE_REPAIR_PROMPT_VERSION",
    "CLOSURE_REPAIR_SCHEMA_ID",
    "CLOSURE_REPAIR_TOOLSET_VERSION",
    "ClosureRepairOperationOutputV1",
    "ClosureRepairOutputV1",
    "ClosureRepairPromptInputV1",
    "ClosureRepairProvider",
    "ClosureRepairProviderResult",
    "ClosureRepairRequest",
    "ProviderRepairProposer",
]
