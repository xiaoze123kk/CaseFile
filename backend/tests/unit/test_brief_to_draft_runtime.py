"""Brief-to-draft runtime spec registry contract tests."""

from __future__ import annotations

import pytest

from casefile.agent_runtime.brief_to_draft_runtime import (
    BriefToDraftSpec,
    FeatureFlags,
    resolve_pipeline_spec,
    schema_id_for_component,
    supported_pipeline_versions,
)
from casefile.agent_runtime.brief_to_draft_v8.workflow import _build_context_pack
from casefile.agent_runtime.models import CandidateStrategy, GenerationRequest

VERSIONS = {
    f"brief-to-draft-v{version}" for version in range(8, 16)
}


def _request(prompt_version: str) -> GenerationRequest:
    return GenerationRequest(
        task_run_id=1,
        prompt_version=prompt_version,
        brief={"creative_intent": "测试案件"},
        casefile_id="case_test",
        brief_id="brief_test",
        brief_version=1,
        version_id="version_test",
        version_no=1,
        parent_version_id=None,
        model_id="fake",
        api_key=None,
        max_turns=8,
        emit=lambda *args: None,
        candidate_strategy=CandidateStrategy.BALANCED,
    )


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
