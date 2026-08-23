"""Provider-facing contracts and adapter for bounded closure repair."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal, Protocol

from casefile_contracts import Status as ClaimStatus
from pydantic import Field, model_validator

from casefile.agent_runtime.models import EventSink, StrictAgentOutput
from casefile.domain.logical_mutation.repair.models import (
    ClosureRepairContextV1,
    ClosureRepairContextV2,
    ClosureRepairContextV3,
    RepairProposal,
    RepairUpdateOperation,
)

CLOSURE_REPAIR_PROMPT_VERSION = "closure-repair-v3"
CLOSURE_REPAIR_AGENT_VERSION = "closure-repair-agent-v3"
CLOSURE_REPAIR_TOOLSET_VERSION = "closure-repair-tools-v1"
CLOSURE_REPAIR_COMPONENT_ID = "repair"
CLOSURE_REPAIR_SCHEMA_ID = "closure-repair-output-v3"

_SCHEMA_BY_PROMPT_VERSION = {
    "closure-repair-v1": "closure-repair-output-v1",
    "closure-repair-v2": "closure-repair-output-v2",
    "closure-repair-v3": "closure-repair-output-v3",
}


def closure_repair_schema_id(prompt_version: str) -> str:
    try:
        return _SCHEMA_BY_PROMPT_VERSION[prompt_version]
    except KeyError as error:
        raise ValueError("closure_repair_prompt_version_unsupported") from error


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
        if not isinstance(self.context.get("obligations"), list) or not self.context["obligations"]:
            raise ValueError("closure repair obligations are missing")
        return self


class ClosureRepairPromptInputV2(StrictAgentOutput):
    """V2 frozen context includes exact server-authored write affordances."""

    context: dict[str, Any]
    round_no: int = Field(ge=1, le=2)

    @model_validator(mode="after")
    def validates_frozen_context(self) -> ClosureRepairPromptInputV2:
        context_hash = self.context.get("context_hash")
        if self.context.get("context_version") != "closure-repair-context-v2":
            raise ValueError("closure repair context version is invalid")
        if (
            not isinstance(context_hash, str)
            or len(context_hash) != 64
            or any(value not in "0123456789abcdef" for value in context_hash)
        ):
            raise ValueError("closure repair context hash is invalid")
        if not isinstance(self.context.get("obligations"), list) or not self.context["obligations"]:
            raise ValueError("closure repair obligations are missing")
        if (
            not isinstance(self.context.get("allowed_writes"), list)
            or not self.context["allowed_writes"]
        ):
            raise ValueError("closure repair allowed writes are missing")
        return self


class ClosureRepairPromptInputV3(StrictAgentOutput):
    """V3 exposes only server-proved semantic alternatives."""

    context: dict[str, Any]
    round_no: int = Field(ge=1, le=2)

    @model_validator(mode="after")
    def validates_frozen_context(self) -> ClosureRepairPromptInputV3:
        context_hash = self.context.get("context_hash")
        if self.context.get("context_version") != "closure-repair-context-v3":
            raise ValueError("closure repair context version is invalid")
        if (
            not isinstance(context_hash, str)
            or len(context_hash) != 64
            or any(value not in "0123456789abcdef" for value in context_hash)
        ):
            raise ValueError("closure repair context hash is invalid")
        alternatives = self.context.get("repair_alternatives")
        if not isinstance(alternatives, list) or not alternatives:
            raise ValueError("closure repair alternatives are missing")
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


class ClosureRepairClaimRefOutputV2(StrictAgentOutput):
    object_type: Literal["claim"]
    object_id: str = Field(min_length=5, max_length=64)


class ClaimStatusRepairOutputV2(StrictAgentOutput):
    operation_type: Literal["claim_status"]
    obligation_keys: list[str] = Field(min_length=1, max_length=8)
    object_id: str = Field(min_length=1, max_length=200)
    field_path: Literal["/status"]
    value: ClaimStatus
    reason: str = Field(min_length=1, max_length=2_000)


class ClaimDependenciesRepairOutputV2(StrictAgentOutput):
    operation_type: Literal["claim_dependencies"]
    obligation_keys: list[str] = Field(min_length=1, max_length=8)
    object_id: str = Field(min_length=1, max_length=200)
    field_path: Literal["/dependency_claim_refs"]
    value: list[ClosureRepairClaimRefOutputV2]
    reason: str = Field(min_length=1, max_length=2_000)


ClosureRepairOperationOutputV2 = ClaimStatusRepairOutputV2 | ClaimDependenciesRepairOutputV2


class ClosureRepairOutputV2(StrictAgentOutput):
    """Typed model output for one atomic repair proposal round."""

    operations: list[ClosureRepairOperationOutputV2] = Field(min_length=1, max_length=8)


class ClosureRepairOutputV3(StrictAgentOutput):
    """Selection-only output; operation identity and values remain server-owned."""

    selected_alternative_id: str = Field(pattern=r"^alt_[0-9a-f]{24}$")
    reason: str = Field(min_length=1, max_length=2_000)


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

    @property
    def component_id(self) -> str:
        return f"closure_repair_round_{self.round_no}"


@dataclass(frozen=True, slots=True)
class ClosureRepairProviderResult:
    candidate: ClosureRepairOutputV1 | ClosureRepairOutputV2 | ClosureRepairOutputV3
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
        prompt_version = self.prompt_version
        if (
            context.context_version == "closure-repair-context-v1"
            and prompt_version == CLOSURE_REPAIR_PROMPT_VERSION
        ):
            prompt_version = "closure-repair-v1"
        elif isinstance(context, ClosureRepairContextV2) and (
            prompt_version == CLOSURE_REPAIR_PROMPT_VERSION
        ):
            prompt_version = "closure-repair-v2"
        request = ClosureRepairRequest(
            prompt_version=prompt_version,
            context=context.as_dict(),
            round_no=round_no,
            model_id=self.model_id,
            api_key=self.api_key,
            max_turns=self.max_turns,
            emit=self.emit,
            network_retries=self.network_retries,
        )
        request.emit(
            "agent.step.started",
            "closure_repair",
            {
                "component_id": request.component_id,
                "component_version": request.prompt_version,
                "schema_id": closure_repair_schema_id(request.prompt_version),
                "input_hash": context.context_hash,
                "upstream_hashes": {"candidate_hash": context.candidate_hash},
            },
        )
        try:
            result = self.provider.repair_closure(request)
        except Exception:
            request.emit(
                "agent.step.failed",
                "closure_repair",
                {
                    "component_id": request.component_id,
                    "schema_id": closure_repair_schema_id(request.prompt_version),
                    "input_hash": context.context_hash,
                    "error_code": "closure_repair_provider_failed",
                    "failure_layer": "provider",
                },
            )
            raise
        try:
            operations: list[RepairUpdateOperation] = []
            selected_alternative_id: str | None = None
            if isinstance(result.candidate, ClosureRepairOutputV3):
                if not isinstance(context, ClosureRepairContextV3):
                    raise ValueError("repair_alternative_context_invalid")
                selected_alternative_id = result.candidate.selected_alternative_id
                selected = next(
                    (
                        item
                        for item in context.repair_alternatives
                        if item.alternative_id == selected_alternative_id
                    ),
                    None,
                )
                if selected is None:
                    raise ValueError("repair_alternative_selection_unknown")
                operations.extend(
                    RepairUpdateOperation(
                        obligation_keys=item.obligation_keys,
                        object_id=item.object_id,
                        field_path=item.field_path,
                        new_value=deepcopy(item.new_value),
                        reason=result.candidate.reason,
                    )
                    for item in selected.operations
                )
            else:
                for item in result.candidate.operations:
                    if isinstance(item, ClosureRepairOperationOutputV1):
                        new_value = json.loads(item.value_json)
                    elif isinstance(item, ClaimStatusRepairOutputV2):
                        new_value = item.value.value
                    else:
                        new_value = [value.model_dump(mode="json") for value in item.value]
                    operations.append(
                        RepairUpdateOperation(
                            obligation_keys=tuple(item.obligation_keys),
                            object_id=item.object_id,
                            field_path=item.field_path,
                            new_value=new_value,
                            reason=item.reason,
                        )
                    )
            proposal = RepairProposal(
                context.context_hash,
                tuple(operations),
                selected_alternative_id=selected_alternative_id,
            )
        except (json.JSONDecodeError, ValueError) as error:
            request.emit(
                "agent.step.failed",
                "closure_repair",
                {
                    "component_id": request.component_id,
                    "schema_id": closure_repair_schema_id(request.prompt_version),
                    "input_hash": context.context_hash,
                    "error_code": "repair_proposal_invalid",
                    "failure_layer": "domain_validation",
                },
            )
            if isinstance(error, json.JSONDecodeError):
                raise ValueError("repair_proposal_value_json_invalid") from error
            raise
        self.results.append(result)
        output_hash = sha256(
            json.dumps(
                proposal.as_dict(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        request.emit(
            "agent.step.completed",
            "closure_repair",
            {
                "component_id": request.component_id,
                "schema_id": closure_repair_schema_id(request.prompt_version),
                "input_hash": context.context_hash,
                "output_hash": output_hash,
                "usage": result.usage,
                "_artifact": proposal.as_dict(),
            },
        )
        return proposal


__all__ = [
    "CLOSURE_REPAIR_AGENT_VERSION",
    "CLOSURE_REPAIR_COMPONENT_ID",
    "CLOSURE_REPAIR_PROMPT_VERSION",
    "CLOSURE_REPAIR_SCHEMA_ID",
    "CLOSURE_REPAIR_TOOLSET_VERSION",
    "ClosureRepairOperationOutputV1",
    "ClosureRepairOperationOutputV2",
    "ClosureRepairOutputV1",
    "ClosureRepairOutputV2",
    "ClosureRepairOutputV3",
    "ClosureRepairPromptInputV1",
    "ClosureRepairPromptInputV2",
    "ClosureRepairPromptInputV3",
    "ClaimDependenciesRepairOutputV2",
    "ClaimStatusRepairOutputV2",
    "ClosureRepairClaimRefOutputV2",
    "ClosureRepairProvider",
    "ClosureRepairProviderResult",
    "ClosureRepairRequest",
    "ProviderRepairProposer",
    "closure_repair_schema_id",
]
