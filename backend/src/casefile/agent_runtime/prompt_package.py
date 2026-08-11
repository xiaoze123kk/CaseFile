"""Typed, deterministic compiler for immutable Prompt Package releases."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ValidationError

from casefile.agent_runtime.brief_to_draft_v8.ir import (
    CaseBlueprintV1,
    EvidenceLogicIRV1,
    EvidenceLogicIRV2,
    ResolutionGovernanceIRV1,
    StoryWorldIRV1,
)
from casefile.agent_runtime.brief_to_draft_v9.contracts import (
    DomainDraftInputV1,
    PlannerInputV1,
)
from casefile.agent_runtime.tools import TOOLSET_VERSION


class PromptPackageError(RuntimeError):
    """A Prompt Package cannot be validated or rendered safely."""


@dataclass(frozen=True, slots=True)
class PromptFragment:
    fragment_id: str
    file: str
    content: str
    sha256: str


@dataclass(frozen=True, slots=True)
class PromptComponent:
    component_id: str
    instruction_fragments: tuple[str, ...]
    input_contract_id: str
    output_schema_id: str
    tool_policy_id: str


@dataclass(frozen=True, slots=True)
class PromptPackage:
    agent_id: str
    version: str
    previous_version: str | None
    change_summary: str
    runtime_agent_version: str
    runtime_toolset_version: str
    fragments: Mapping[str, PromptFragment]
    components: Mapping[str, PromptComponent]


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    package_version: str
    component_id: str
    instructions: str
    input_text: str
    prompt_sha256: str
    input_sha256: str
    input_contract_id: str
    output_schema_id: str
    tool_policy_id: str
    runtime_agent_version: str
    runtime_toolset_version: str


INPUT_CONTRACTS: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        "brief-to-draft-planner-input-v1": PlannerInputV1,
        "brief-to-draft-domain-input-v1": DomainDraftInputV1,
    }
)
OUTPUT_SCHEMAS: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        "case-blueprint-v1": CaseBlueprintV1,
        "story-world-ir-v1": StoryWorldIRV1,
        "evidence-logic-ir-v1": EvidenceLogicIRV1,
        "evidence-logic-ir-v2": EvidenceLogicIRV2,
        "resolution-governance-ir-v1": ResolutionGovernanceIRV1,
    }
)
TOOL_POLICIES: Mapping[str, frozenset[str]] = MappingProxyType(
    {"no-tools-v1": frozenset()}
)
RUNTIME_COMPATIBILITY: frozenset[tuple[str, str]] = frozenset(
    {
        ("brief-to-draft-pipeline-v9", TOOLSET_VERSION),
        ("brief-to-draft-pipeline-v10", TOOLSET_VERSION),
    }
)


def validate_prompt_package_bindings(package: PromptPackage) -> None:
    """Fail closed when a package references an unknown runtime contract."""

    runtime = (package.runtime_agent_version, package.runtime_toolset_version)
    if runtime not in RUNTIME_COMPATIBILITY:
        raise PromptPackageError(
            "Prompt Package runtime is not supported: "
            f"agent_version={runtime[0]!r}, toolset_version={runtime[1]!r}"
        )
    for component in package.components.values():
        if component.input_contract_id not in INPUT_CONTRACTS:
            raise PromptPackageError(
                f"Unknown Prompt Package input contract: {component.input_contract_id}"
            )
        if component.output_schema_id not in OUTPUT_SCHEMAS:
            raise PromptPackageError(
                f"Unknown Prompt Package output schema: {component.output_schema_id}"
            )
        if component.tool_policy_id not in TOOL_POLICIES:
            raise PromptPackageError(
                f"Unknown Prompt Package tool policy: {component.tool_policy_id}"
            )


def render_prompt_package(
    package: PromptPackage,
    component_id: str,
    input_value: BaseModel | Mapping[str, Any],
    *,
    agent_version: str,
    toolset_version: str,
) -> RenderedPrompt:
    """Render one component without interpolating untrusted data into instructions."""

    validate_prompt_package_bindings(package)
    if agent_version != package.runtime_agent_version:
        raise PromptPackageError(
            "Prompt Package agent version mismatch: "
            f"expected={package.runtime_agent_version!r}, actual={agent_version!r}"
        )
    if toolset_version != package.runtime_toolset_version:
        raise PromptPackageError(
            "Prompt Package toolset version mismatch: "
            f"expected={package.runtime_toolset_version!r}, actual={toolset_version!r}"
        )
    try:
        component = package.components[component_id]
    except KeyError as error:
        raise PromptPackageError(
            f"Prompt Package {package.version} has no component {component_id!r}"
        ) from error

    contract = INPUT_CONTRACTS[component.input_contract_id]
    try:
        validated_input = contract.model_validate(input_value)
    except ValidationError as error:
        raise PromptPackageError(
            f"Prompt Package input does not satisfy {component.input_contract_id}"
        ) from error

    instruction_parts = [
        package.fragments[fragment_id].content.rstrip("\n")
        for fragment_id in component.instruction_fragments
    ]
    instructions = "\n\n".join(instruction_parts) + "\n"
    input_json = json.dumps(
        validated_input.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    input_text = input_json
    return RenderedPrompt(
        package_version=package.version,
        component_id=component_id,
        instructions=instructions,
        input_text=input_text,
        prompt_sha256=sha256(instructions.encode("utf-8")).hexdigest(),
        input_sha256=sha256(input_json.encode("utf-8")).hexdigest(),
        input_contract_id=component.input_contract_id,
        output_schema_id=component.output_schema_id,
        tool_policy_id=component.tool_policy_id,
        runtime_agent_version=package.runtime_agent_version,
        runtime_toolset_version=package.runtime_toolset_version,
    )


def output_type_for_component(package: PromptPackage, component_id: str) -> type[BaseModel]:
    """Resolve the Pydantic output model bound by one package component."""

    validate_prompt_package_bindings(package)
    try:
        component = package.components[component_id]
    except KeyError as error:
        raise PromptPackageError(
            f"Prompt Package {package.version} has no component {component_id!r}"
        ) from error
    return OUTPUT_SCHEMAS[component.output_schema_id]


__all__ = [
    "INPUT_CONTRACTS",
    "OUTPUT_SCHEMAS",
    "RUNTIME_COMPATIBILITY",
    "TOOL_POLICIES",
    "PromptComponent",
    "PromptFragment",
    "PromptPackage",
    "PromptPackageError",
    "RenderedPrompt",
    "output_type_for_component",
    "render_prompt_package",
    "validate_prompt_package_bindings",
]
