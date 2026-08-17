"""Brief-to-draft runtime spec registry and plugin hook contract tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from casefile.agent_runtime.brief_to_draft_runtime import (
    BriefToDraftSpec,
    FeatureFlags,
    resolve_pipeline_spec,
    schema_id_for_component,
    supported_pipeline_versions,
)
from casefile.agent_runtime.brief_to_draft_v8.compiler import (
    compile_casefile,
    link_draft,
)
from casefile.agent_runtime.brief_to_draft_v8.ir import (
    CaseBlueprintV1,
    EvidenceLogicIRV1,
    ResolutionGovernanceIRV1,
    StoryWorldIRV1,
)
from casefile.agent_runtime.brief_to_draft_v8.workflow import (
    _build_context_pack,
    _with_temporal_plan,
    register_pipeline_stage,
    registered_pipeline_stage_ids,
    run_v8_generation,
)
from casefile.agent_runtime.models import CandidateStrategy, GenerationRequest
from casefile.agent_runtime.prompt import V12_GENERATION_AGENT_VERSION
from casefile.agent_runtime.providers import _add_fake_v10_matrix_plan, _fake_v8_output
from casefile.agent_runtime.tools import TOOLSET_VERSION

VERSIONS = {
    f"brief-to-draft-v{version}" for version in range(8, 16)
}


def test_all_component_versions_have_a_frozen_spec() -> None:
    assert supported_pipeline_versions() == VERSIONS


def test_unknown_pipeline_version_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unsupported brief-to-draft pipeline version"):
        resolve_pipeline_spec("brief-to-draft-v7")


def test_specs_bind_prompt_component_sets() -> None:
    base = {"planner", "story", "evidence", "governance"}
    temporal = base | {"temporal"}
    assert resolve_pipeline_spec("brief-to-draft-v8").prompt_components == base
    assert resolve_pipeline_spec("brief-to-draft-v9").prompt_components == base
    assert resolve_pipeline_spec("brief-to-draft-v10").prompt_components == base
    assert resolve_pipeline_spec("brief-to-draft-v11").prompt_components == base
    assert resolve_pipeline_spec("brief-to-draft-v12").prompt_components == temporal
    assert resolve_pipeline_spec("brief-to-draft-v13").prompt_components == temporal
    assert resolve_pipeline_spec("brief-to-draft-v14").prompt_components == temporal
    assert resolve_pipeline_spec("brief-to-draft-v15").prompt_components == temporal | {
        "matrix"
    }


def test_specs_bind_ordered_execution_graphs() -> None:
    legacy = (
        "context_pack",
        "blueprint_planner",
        "domain_draft",
        "compile_quality_gate",
    )
    temporal = (
        "context_pack",
        "blueprint_planner",
        "temporal_plan",
        "domain_draft",
        "compile_quality_gate",
    )
    v15 = (
        "context_pack",
        "blueprint_planner",
        "temporal_plan",
        "domain_draft",
        "resolution_governance",
        "compile_quality_gate",
    )
    for version in {
        "brief-to-draft-v8",
        "brief-to-draft-v9",
        "brief-to-draft-v10",
        "brief-to-draft-v11",
    }:
        assert resolve_pipeline_spec(version).stages == legacy
    for version in {
        "brief-to-draft-v12",
        "brief-to-draft-v13",
        "brief-to-draft-v14",
    }:
        assert resolve_pipeline_spec(version).stages == temporal
    assert resolve_pipeline_spec("brief-to-draft-v15").stages == v15


def test_feature_flags_translate_the_historical_version_branches() -> None:
    flags = {
        version: resolve_pipeline_spec(version).features for version in sorted(VERSIONS)
    }

    assert flags["brief-to-draft-v8"] == FeatureFlags()
    assert flags["brief-to-draft-v9"] == FeatureFlags()
    assert flags["brief-to-draft-v10"] == FeatureFlags(competition_matrix=True)
    assert flags["brief-to-draft-v11"] == FeatureFlags(
        v2_context=True, competition_matrix=True
    )
    assert flags["brief-to-draft-v12"] == FeatureFlags(
        v2_context=True, temporal_plan=True, competition_matrix=True
    )
    assert flags["brief-to-draft-v13"] == flags["brief-to-draft-v12"]
    assert flags["brief-to-draft-v14"] == FeatureFlags(
        v2_context=True,
        temporal_plan=True,
        competition_matrix=True,
        language_gate=True,
    )
    assert flags["brief-to-draft-v15"] == FeatureFlags(
        v2_context=True,
        temporal_plan=True,
        competition_matrix=True,
        governance_v2=True,
        matrix_evaluation=True,
        language_gate=True,
        explicit_targets=True,
        blueprint_repair_budget=2,
    )


def test_specs_bind_story_evidence_and_governance_schemas() -> None:
    assert resolve_pipeline_spec("brief-to-draft-v8").story_schema_id == "story-world-ir-v1"
    assert (
        resolve_pipeline_spec("brief-to-draft-v11").story_schema_id == "story-world-ir-v2"
    )
    assert (
        resolve_pipeline_spec("brief-to-draft-v12").story_schema_id == "story-world-ir-v3"
    )
    assert (
        resolve_pipeline_spec("brief-to-draft-v10").evidence_schema_id
        == "evidence-logic-ir-v2"
    )
    assert (
        resolve_pipeline_spec("brief-to-draft-v9").governance_schema_id
        == "resolution-governance-ir-v1"
    )
    assert (
        resolve_pipeline_spec("brief-to-draft-v15").governance_schema_id
        == "resolution-governance-ir-v2"
    )


def test_schema_id_for_component_uses_spec_bindings() -> None:
    v15 = resolve_pipeline_spec("brief-to-draft-v15")
    assert schema_id_for_component(v15, "story_world") == "story-world-ir-v3"
    assert schema_id_for_component(v15, "evidence_logic") == "evidence-logic-ir-v2"
    assert (
        schema_id_for_component(v15, "resolution_governance")
        == "resolution-governance-ir-v2"
    )
    assert schema_id_for_component(v15, "evidence_matrix") == "matrix-evaluation-v1"
    assert schema_id_for_component(v15, "case_blueprint_planner") is None


def test_context_pack_builder_uses_spec_context_types() -> None:
    expected_schema_ids = {
        "brief-to-draft-v8": "draft-context-pack-v1",
        "brief-to-draft-v9": "draft-context-pack-v1",
        "brief-to-draft-v10": "draft-context-pack-v1",
        "brief-to-draft-v11": "draft-context-pack-v2",
        "brief-to-draft-v12": "draft-context-pack-v3",
        "brief-to-draft-v13": "draft-context-pack-v3",
        "brief-to-draft-v14": "draft-context-pack-v4",
        "brief-to-draft-v15": "draft-context-pack-v5",
    }
    for version, schema_id in expected_schema_ids.items():
        context = _build_context_pack(
            _request(version),
            resolve_pipeline_spec(version),
        )
        assert context.schema_id == schema_id
        assert context.prompt_bundle_version == version


def test_repair_input_contract_preserved_for_matrix_versions() -> None:
    for version in sorted(VERSIONS - {"brief-to-draft-v8", "brief-to-draft-v9"}):
        spec: BriefToDraftSpec = resolve_pipeline_spec(version)
        assert spec.evidence_repair_input_contract_id == (
            "brief-to-draft-evidence-repair-input-v1"
        )
    assert resolve_pipeline_spec("brief-to-draft-v8").evidence_repair_input_contract_id is None
    assert resolve_pipeline_spec("brief-to-draft-v9").evidence_repair_input_contract_id is None


class RecordingStoryFeature:
    """Test StoryFeature that records every hook invocation."""

    feature_id = "test-story-feature"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def domain_input_fields(self, request: GenerationRequest) -> dict[str, Any]:
        self.calls.append("domain_input_fields")
        return {}

    def validate_story(
        self,
        story: Any,
        *,
        request: GenerationRequest,
    ) -> list[dict[str, Any]]:
        self.calls.append("validate_story")
        return []

    def with_temporal_plan(self, story: Any, plan: Any) -> Any:
        self.calls.append("with_temporal_plan")
        return story


class RecordingTemporalStoryFeature(RecordingStoryFeature):
    """Delegates to the legacy join so a plugin can replace it wholesale."""

    def with_temporal_plan(self, story: Any, plan: Any) -> Any:
        self.calls.append("with_temporal_plan")
        return _with_temporal_plan(story, plan)


class TitleCompilerFeature:
    feature_id = "test-compiler-feature"

    def __init__(self) -> None:
        self.calls = 0

    def compile_document(
        self,
        document: dict[str, Any],
        *,
        story: Any,
        linked: Any,
    ) -> None:
        self.calls += 1
        document["title"] = f"{document['title']}-plugin"


def _request(prompt_version: str) -> GenerationRequest:
    return GenerationRequest(
        task_run_id=1,
        prompt_version=prompt_version,
        brief={"creative_intent": "测试案件"},
        casefile_id="case_test",
        brief_id="brief_test",
        brief_version=1,
        version_id="draft_test",
        version_no=1,
        parent_version_id=None,
        model_id="fake",
        api_key=None,
        max_turns=8,
        emit=lambda *args: None,
        candidate_strategy=CandidateStrategy.BALANCED,
    )


async def _fake_call_component(
    _instructions: str,
    _input_text: str,
    output_type: type[Any],
    _stage: str,
    _component_id: str,
    _schema_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    return _fake_v8_output(output_type), {}


async def _fake_call_component_v12(
    _instructions: str,
    _input_text: str,
    output_type: type[Any],
    _stage: str,
    _component_id: str,
    _schema_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    output = _fake_v8_output(output_type)
    _add_fake_v10_matrix_plan(output_type, output)
    return output, {}


def test_compiler_plugin_mutates_document_before_validation() -> None:
    blueprint = CaseBlueprintV1.model_validate(_fake_v8_output(CaseBlueprintV1))
    story = StoryWorldIRV1.model_validate(_fake_v8_output(StoryWorldIRV1))
    evidence = EvidenceLogicIRV1.model_validate(_fake_v8_output(EvidenceLogicIRV1))
    governance = ResolutionGovernanceIRV1.model_validate(
        _fake_v8_output(ResolutionGovernanceIRV1)
    )
    linked = link_draft(
        blueprint,
        story,
        evidence,
        governance,
        task_run_id=1,
    )
    plugin = TitleCompilerFeature()

    candidate = compile_casefile(
        linked,
        casefile_id="case_test",
        brief_id="brief_test",
        brief_version=1,
        version_id="draft_test",
        version_no=1,
        parent_version_id=None,
        compiler_plugins=(plugin,),
    )

    assert plugin.calls == 1
    assert candidate["title"].endswith("-plugin")


def test_run_v8_generation_invokes_story_and_compiler_hooks() -> None:
    story_feature = RecordingStoryFeature()
    compiler_plugin = TitleCompilerFeature()
    spec = replace(
        resolve_pipeline_spec("brief-to-draft-v8"),
        story_feature=story_feature,
        compiler_plugins=(compiler_plugin,),
    )
    request = replace(
        _request("brief-to-draft-v8"),
        brief={"conclusion_mode": "unique"},
    )

    result = asyncio.run(
        run_v8_generation(request, call_component=_fake_call_component, spec=spec)
    )

    assert story_feature.calls == ["domain_input_fields", "validate_story"]
    assert compiler_plugin.calls == 1
    assert result.candidate["title"].endswith("-plugin")


def test_run_v12_generation_delegates_temporal_join_to_story_feature() -> None:
    story_feature = RecordingTemporalStoryFeature()
    spec = replace(
        resolve_pipeline_spec("brief-to-draft-v12"),
        story_feature=story_feature,
    )
    request = replace(
        _request("brief-to-draft-v12"),
        brief={"conclusion_mode": "unique"},
        agent_version=V12_GENERATION_AGENT_VERSION,
        toolset_version=TOOLSET_VERSION,
    )

    result = asyncio.run(
        run_v8_generation(
            request,
            call_component=_fake_call_component_v12,
            spec=spec,
        )
    )

    assert story_feature.calls == [
        "domain_input_fields",
        "with_temporal_plan",
        "validate_story",
    ]
    assert result.candidate["schema_version"] == request.schema_version


class RecordingStage:
    """Minimal PipelineStage plugin for graph-order tests."""

    def __init__(self, stage_id: str, calls: list[str]) -> None:
        self.stage_id = stage_id
        self.calls = calls

    async def run(self, ctx: Any) -> None:
        self.calls.append(self.stage_id)
        assert ctx.blueprint is not None


def test_registered_stage_runs_in_spec_graph_order() -> None:
    stage_id = f"probe_{uuid4().hex[:8]}"
    calls: list[str] = []
    register_pipeline_stage(RecordingStage(stage_id, calls))
    spec = replace(
        resolve_pipeline_spec("brief-to-draft-v8"),
        stages=(
            "context_pack",
            "blueprint_planner",
            stage_id,
            "domain_draft",
            "compile_quality_gate",
        ),
    )
    request = replace(
        _request("brief-to-draft-v8"),
        brief={"conclusion_mode": "unique"},
    )

    result = asyncio.run(
        run_v8_generation(request, call_component=_fake_call_component, spec=spec)
    )

    assert calls == [stage_id]
    assert result.candidate["title"] == "v8 可恢复生成样例"


def test_unknown_stage_fails_closed() -> None:
    spec = replace(
        resolve_pipeline_spec("brief-to-draft-v8"),
        stages=("context_pack", "not_registered_stage"),
    )
    request = replace(
        _request("brief-to-draft-v8"),
        brief={"conclusion_mode": "unique"},
    )

    with pytest.raises(RuntimeError, match="no brief-to-draft pipeline stage"):
        asyncio.run(
            run_v8_generation(request, call_component=_fake_call_component, spec=spec)
        )


def test_builtin_stage_registry_is_complete() -> None:
    assert {
        "context_pack",
        "blueprint_planner",
        "temporal_plan",
        "domain_draft",
        "resolution_governance",
        "compile_quality_gate",
    } <= registered_pipeline_stage_ids()
