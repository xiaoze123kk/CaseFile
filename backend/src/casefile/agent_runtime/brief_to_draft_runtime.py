"""Versioned runtime specs and pluggable feature hooks for brief-to-draft.

Every component generation version (v8-v15) executes the same deterministic
graph in :mod:`casefile.agent_runtime.brief_to_draft_v8.workflow`. Historically
the differences between versions were expressed as `request.prompt_version`
literals scattered through that graph, so each new capability forced edits in
the shared controller.

This module centralizes those differences into a frozen spec registry:

- :class:`FeatureFlags` describe optional pipeline capabilities.
- :class:`BriefToDraftSpec` binds one prompt version to its input/output
  contracts, agent runtime, component set, and feature flags.
- :class:`StoryFeature` and :class:`CompilerFeature` are extension points that
  let a new capability own its prompt-input fields, story validation, temporal
  join, and document compilation without editing the shared graph.

Adding a future capability therefore means: define its IR, implement a feature
object, add one spec entry, and release a new immutable Prompt Package. Existing
frozen versions keep resolving to their original spec and behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType

from pydantic import BaseModel

from casefile.agent_runtime.brief_to_draft_features import (
    CompilerFeature,
    PipelineStage,
    StoryFeature,
)
from casefile.agent_runtime.brief_to_draft_v8.ir import (
    DraftContextPackV1,
    EvidenceLogicIRV1,
    EvidenceLogicIRV2,
    ResolutionGovernanceIRV1,
    StoryWorldIRV1,
)
from casefile.agent_runtime.brief_to_draft_v11.contracts import (
    DraftContextPackV2,
    StoryWorldIRV2,
)
from casefile.agent_runtime.brief_to_draft_v12.contracts import (
    DraftContextPackV3,
    StoryWorldIRV3,
)
from casefile.agent_runtime.brief_to_draft_v14.contracts import DraftContextPackV4
from casefile.agent_runtime.brief_to_draft_v15.contracts import (
    DraftContextPackV5,
    ResolutionGovernanceIRV2,
)
from casefile.agent_runtime.prompt import (
    V8_GENERATION_AGENT_VERSION,
    V9_GENERATION_AGENT_VERSION,
    V10_GENERATION_AGENT_VERSION,
    V11_GENERATION_AGENT_VERSION,
    V12_GENERATION_AGENT_VERSION,
    V13_GENERATION_AGENT_VERSION,
    V14_GENERATION_AGENT_VERSION,
    V15_GENERATION_AGENT_VERSION,
)

_V8_PROMPT_COMPONENTS = frozenset({"planner", "story", "evidence", "governance"})
_V12_PROMPT_COMPONENTS = frozenset(
    {"planner", "temporal", "story", "evidence", "governance"}
)
_V15_PROMPT_COMPONENTS = frozenset(
    {"planner", "temporal", "story", "evidence", "matrix", "governance"}
)

_STAGES_LEGACY = (
    "context_pack",
    "blueprint_planner",
    "domain_draft",
    "compile_quality_gate",
)
_STAGES_TEMPORAL = (
    "context_pack",
    "blueprint_planner",
    "temporal_plan",
    "domain_draft",
    "compile_quality_gate",
)
_STAGES_V15 = (
    "context_pack",
    "blueprint_planner",
    "temporal_plan",
    "domain_draft",
    "resolution_governance",
    "compile_quality_gate",
)

EvidenceOutputType = type[EvidenceLogicIRV1] | type[EvidenceLogicIRV2]
GovernanceOutputType = type[ResolutionGovernanceIRV1] | type[ResolutionGovernanceIRV2]
StoryOutputType = type[StoryWorldIRV1] | type[StoryWorldIRV2] | type[StoryWorldIRV3]


@dataclass(frozen=True, slots=True)
class FeatureFlags:
    """Optional capabilities a pipeline spec can opt into."""

    v2_context: bool = False
    temporal_plan: bool = False
    competition_matrix: bool = False
    governance_v2: bool = False
    matrix_evaluation: bool = False
    language_gate: bool = False
    explicit_targets: bool = False
    blueprint_repair_budget: int = 1


@dataclass(frozen=True, slots=True)
class BriefToDraftSpec:
    """Frozen runtime description for one brief-to-draft prompt version."""

    prompt_version: str
    agent_version: str
    context_pack_type: type[BaseModel]
    context_schema_id: str
    story_output_type: StoryOutputType
    story_schema_id: str
    evidence_output_type: EvidenceOutputType
    evidence_schema_id: str
    governance_output_type: GovernanceOutputType
    governance_schema_id: str
    prompt_components: frozenset[str]
    prompt_package: bool
    stages: tuple[str, ...] = _STAGES_LEGACY
    governance_runs_in_parallel: bool = True
    features: FeatureFlags = field(default_factory=FeatureFlags)
    evidence_repair_input_contract_id: str | None = None
    story_feature: StoryFeature | None = None
    compiler_plugins: tuple[CompilerFeature, ...] = ()


_PIPELINE_SPECS: dict[str, BriefToDraftSpec] = {
    "brief-to-draft-v8": BriefToDraftSpec(
        prompt_version="brief-to-draft-v8",
        agent_version=V8_GENERATION_AGENT_VERSION,
        context_pack_type=DraftContextPackV1,
        context_schema_id="draft-context-pack-v1",
        story_output_type=StoryWorldIRV1,
        story_schema_id="story-world-ir-v1",
        evidence_output_type=EvidenceLogicIRV1,
        evidence_schema_id="evidence-logic-ir-v1",
        governance_output_type=ResolutionGovernanceIRV1,
        governance_schema_id="resolution-governance-ir-v1",
        prompt_components=_V8_PROMPT_COMPONENTS,
        prompt_package=False,
    ),
    "brief-to-draft-v9": BriefToDraftSpec(
        prompt_version="brief-to-draft-v9",
        agent_version=V9_GENERATION_AGENT_VERSION,
        context_pack_type=DraftContextPackV1,
        context_schema_id="draft-context-pack-v1",
        story_output_type=StoryWorldIRV1,
        story_schema_id="story-world-ir-v1",
        evidence_output_type=EvidenceLogicIRV1,
        evidence_schema_id="evidence-logic-ir-v1",
        governance_output_type=ResolutionGovernanceIRV1,
        governance_schema_id="resolution-governance-ir-v1",
        prompt_components=_V8_PROMPT_COMPONENTS,
        prompt_package=True,
    ),
    "brief-to-draft-v10": BriefToDraftSpec(
        prompt_version="brief-to-draft-v10",
        agent_version=V10_GENERATION_AGENT_VERSION,
        context_pack_type=DraftContextPackV1,
        context_schema_id="draft-context-pack-v1",
        story_output_type=StoryWorldIRV1,
        story_schema_id="story-world-ir-v1",
        evidence_output_type=EvidenceLogicIRV2,
        evidence_schema_id="evidence-logic-ir-v2",
        governance_output_type=ResolutionGovernanceIRV1,
        governance_schema_id="resolution-governance-ir-v1",
        prompt_components=_V8_PROMPT_COMPONENTS,
        prompt_package=True,
        features=FeatureFlags(competition_matrix=True),
        evidence_repair_input_contract_id="brief-to-draft-evidence-repair-input-v1",
    ),
    "brief-to-draft-v11": BriefToDraftSpec(
        prompt_version="brief-to-draft-v11",
        agent_version=V11_GENERATION_AGENT_VERSION,
        context_pack_type=DraftContextPackV2,
        context_schema_id="draft-context-pack-v2",
        story_output_type=StoryWorldIRV2,
        story_schema_id="story-world-ir-v2",
        evidence_output_type=EvidenceLogicIRV2,
        evidence_schema_id="evidence-logic-ir-v2",
        governance_output_type=ResolutionGovernanceIRV1,
        governance_schema_id="resolution-governance-ir-v1",
        prompt_components=_V8_PROMPT_COMPONENTS,
        prompt_package=True,
        features=FeatureFlags(v2_context=True, competition_matrix=True),
        evidence_repair_input_contract_id="brief-to-draft-evidence-repair-input-v1",
    ),
    "brief-to-draft-v12": BriefToDraftSpec(
        prompt_version="brief-to-draft-v12",
        agent_version=V12_GENERATION_AGENT_VERSION,
        context_pack_type=DraftContextPackV3,
        context_schema_id="draft-context-pack-v3",
        story_output_type=StoryWorldIRV3,
        story_schema_id="story-world-ir-v3",
        evidence_output_type=EvidenceLogicIRV2,
        evidence_schema_id="evidence-logic-ir-v2",
        governance_output_type=ResolutionGovernanceIRV1,
        governance_schema_id="resolution-governance-ir-v1",
        prompt_components=_V12_PROMPT_COMPONENTS,
        prompt_package=True,
        stages=_STAGES_TEMPORAL,
        features=FeatureFlags(
            v2_context=True,
            temporal_plan=True,
            competition_matrix=True,
        ),
        evidence_repair_input_contract_id="brief-to-draft-evidence-repair-input-v1",
    ),
    "brief-to-draft-v13": BriefToDraftSpec(
        prompt_version="brief-to-draft-v13",
        agent_version=V13_GENERATION_AGENT_VERSION,
        context_pack_type=DraftContextPackV3,
        context_schema_id="draft-context-pack-v3",
        story_output_type=StoryWorldIRV3,
        story_schema_id="story-world-ir-v3",
        evidence_output_type=EvidenceLogicIRV2,
        evidence_schema_id="evidence-logic-ir-v2",
        governance_output_type=ResolutionGovernanceIRV1,
        governance_schema_id="resolution-governance-ir-v1",
        prompt_components=_V12_PROMPT_COMPONENTS,
        prompt_package=True,
        stages=_STAGES_TEMPORAL,
        features=FeatureFlags(
            v2_context=True,
            temporal_plan=True,
            competition_matrix=True,
        ),
        evidence_repair_input_contract_id="brief-to-draft-evidence-repair-input-v1",
    ),
    "brief-to-draft-v14": BriefToDraftSpec(
        prompt_version="brief-to-draft-v14",
        agent_version=V14_GENERATION_AGENT_VERSION,
        context_pack_type=DraftContextPackV4,
        context_schema_id="draft-context-pack-v4",
        story_output_type=StoryWorldIRV3,
        story_schema_id="story-world-ir-v3",
        evidence_output_type=EvidenceLogicIRV2,
        evidence_schema_id="evidence-logic-ir-v2",
        governance_output_type=ResolutionGovernanceIRV1,
        governance_schema_id="resolution-governance-ir-v1",
        prompt_components=_V12_PROMPT_COMPONENTS,
        prompt_package=True,
        stages=_STAGES_TEMPORAL,
        features=FeatureFlags(
            v2_context=True,
            temporal_plan=True,
            competition_matrix=True,
            language_gate=True,
        ),
        evidence_repair_input_contract_id="brief-to-draft-evidence-repair-input-v1",
    ),
    "brief-to-draft-v15": BriefToDraftSpec(
        prompt_version="brief-to-draft-v15",
        agent_version=V15_GENERATION_AGENT_VERSION,
        context_pack_type=DraftContextPackV5,
        context_schema_id="draft-context-pack-v5",
        story_output_type=StoryWorldIRV3,
        story_schema_id="story-world-ir-v3",
        evidence_output_type=EvidenceLogicIRV2,
        evidence_schema_id="evidence-logic-ir-v2",
        governance_output_type=ResolutionGovernanceIRV2,
        governance_schema_id="resolution-governance-ir-v2",
        prompt_components=_V15_PROMPT_COMPONENTS,
        prompt_package=True,
        stages=_STAGES_V15,
        governance_runs_in_parallel=False,
        features=FeatureFlags(
            v2_context=True,
            temporal_plan=True,
            competition_matrix=True,
            governance_v2=True,
            matrix_evaluation=True,
            language_gate=True,
            explicit_targets=True,
            blueprint_repair_budget=2,
        ),
        evidence_repair_input_contract_id="brief-to-draft-evidence-repair-input-v1",
    ),
}

_SPECS = MappingProxyType(_PIPELINE_SPECS)


def resolve_pipeline_spec(prompt_version: str) -> BriefToDraftSpec:
    """Resolve the frozen runtime spec for one prompt version."""

    try:
        return _SPECS[prompt_version]
    except KeyError as error:
        raise ValueError(
            f"Unsupported brief-to-draft pipeline version: {prompt_version!r}"
        ) from error


def supported_pipeline_versions() -> frozenset[str]:
    """Return every prompt version with a registered pipeline spec."""

    return frozenset(_SPECS)


def schema_id_for_component(
    spec: BriefToDraftSpec,
    component_id: str,
) -> str | None:
    """Resolve the structured-output schema bound to a pipeline component."""

    if component_id == "story_world":
        return spec.story_schema_id
    if component_id == "evidence_logic":
        return spec.evidence_schema_id
    if component_id == "resolution_governance":
        return spec.governance_schema_id
    if component_id == "evidence_matrix" and spec.features.matrix_evaluation:
        return "matrix-evaluation-v1"
    return None


__all__ = [
    "BriefToDraftSpec",
    "CompilerFeature",
    "EvidenceOutputType",
    "FeatureFlags",
    "GovernanceOutputType",
    "PipelineStage",
    "StoryFeature",
    "StoryOutputType",
    "resolve_pipeline_spec",
    "schema_id_for_component",
    "supported_pipeline_versions",
]
