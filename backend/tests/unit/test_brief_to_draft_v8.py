"""Unit coverage for v8 semantic IR, linking, and deterministic compilation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from casefile.agent_runtime.brief_to_draft_v8.compiler import (
    LinkerValidationError,
    compile_casefile,
    link_draft,
)
from casefile.agent_runtime.brief_to_draft_v8.ir import (
    BlueprintObjectV1,
    CaseBlueprintV1,
    EvidenceLogicIRV1,
    EvidenceLogicIRV2,
    KnowledgeStateIR,
    LocationIR,
    ResolutionGovernanceIRV1,
    StoryWorldIRV1,
    TimeIR,
)
from casefile.agent_runtime.brief_to_draft_v8.workflow import (
    _evidence_assessment_issues,
    _merge_usage,
    _safe_diagnostic_message,
)
from casefile.agent_runtime.models import GenerationRequest
from casefile.agent_runtime.prompt_repository import (
    component_prompt_for_task,
    load_prompt,
)
from casefile.agent_runtime.providers import FakeProvider
from casefile.agent_runtime.structured_output import compile_deepseek_strict_schema
from casefile.contracts import validate_casefile


def _blueprint() -> CaseBlueprintV1:
    return CaseBlueprintV1.model_validate(
        {
            "title": "失踪的航海日志",
            "resolution_specs": [
                {
                    "local_key": "main_resolution",
                    "title": "核心解答",
                    "purpose": "回答日志为何失踪",
                    "dependency_keys": ["main_claim"],
                }
            ],
            "entities": [
                {
                    "local_key": "captain",
                    "title": "船长",
                    "purpose": "核心行动者",
                    "dependency_keys": [],
                }
            ],
            "information_units": [
                {
                    "local_key": "wet_log",
                    "title": "潮湿纸页",
                    "purpose": "支持核心主张",
                    "dependency_keys": ["main_claim"],
                }
            ],
            "claims": [
                {
                    "local_key": "main_claim",
                    "title": "日志被主动藏匿",
                    "purpose": "核心待证主张",
                    "dependency_keys": ["wet_log"],
                }
            ],
        }
    )


def _story() -> StoryWorldIRV1:
    return StoryWorldIRV1.model_validate(
        {
            "entities": [
                {
                    "local_key": "captain",
                    "description": "掌握航线秘密、拒绝公开最后一页日志的船长。",
                    "tags": ["核心角色"],
                    "entity_type": "person",
                    "name": "林船长",
                    "aliases": [],
                    "traits": ["谨慎"],
                    "goals": ["保护船员"],
                    "secrets": [],
                    "capabilities": ["航海"],
                    "knowledge_states": [],
                }
            ]
        }
    )


def _evidence() -> EvidenceLogicIRV1:
    return EvidenceLogicIRV1.model_validate(
        {
            "information_units": [
                {
                    "local_key": "wet_log",
                    "description": "纸页只在边缘受潮，说明它曾被短暂藏在甲板夹层。",
                    "tags": ["物证"],
                    "information_type": "evidence",
                    "title": "潮湿纸页",
                    "content": "最后一页边缘残留与甲板夹层一致的盐渍。",
                    "source_event_key": None,
                    "reliability": "high",
                    "truth_status": "canon_true",
                    "supports_claim_keys": ["main_claim"],
                    "refutes_claim_keys": [],
                    "availability": {
                        "perspective_keys": ["captain"],
                        "acquisition_conditions": ["检查甲板夹层"],
                        "alternative_path_keys": [],
                    },
                    "classification": "key",
                }
            ],
            "claims": [
                {
                    "local_key": "main_claim",
                    "description": "日志并非落海，而是被知情者主动藏入夹层。",
                    "tags": ["核心主张"],
                    "title": "日志被主动藏匿",
                    "statement": "最后一页日志被主动藏入甲板夹层。",
                    "claim_type": "fact",
                    "support_keys": ["wet_log"],
                    "refute_keys": [],
                    "dependency_claim_keys": [],
                    "status": "supported",
                    "materiality": "critical",
                }
            ],
        }
    )


def _governance() -> ResolutionGovernanceIRV1:
    return ResolutionGovernanceIRV1.model_validate(
        {
            "resolution_specs": [
                {
                    "local_key": "main_resolution",
                    "description": "通过盐渍位置还原日志被藏匿的过程。",
                    "tags": ["解答"],
                    "title": "核心解答",
                    "question_type": "fact_reconstruction",
                    "reasoning_question": "日志为何会从船长室消失？",
                    "conclusion_mode": "unique",
                    "required_slots": [],
                    "accepted_answer_texts": ["日志被主动藏进甲板夹层。"],
                    "accepted_answer_keys": [],
                    "required_claim_keys": ["main_claim"],
                }
            ]
        }
    )


def _v10_evidence() -> EvidenceLogicIRV2:
    payload = _evidence().model_dump(mode="json")
    payload["schema_id"] = "evidence-logic-ir-v2"
    payload["hypotheses"] = [
        {
            "local_key": "captain_explanation",
            "description": "船长为保护船员而藏起日志。",
            "tags": [],
            "title": "船长藏匿日志",
            "proposition": "船长将最后一页日志藏在甲板夹层。",
            "target_resolution_key": "main_resolution",
            "required_claim_keys": ["main_claim"],
            "falsifier_keys": [],
            "competing_hypothesis_keys": ["storm_explanation"],
            "evidence_assessments": [
                {
                    "information_key": "wet_log",
                    "effect": "supports",
                    "strength": "strong",
                    "rationale": "盐渍位置与主动藏匿的路径吻合。",
                }
            ],
            "status": "supported",
            "score": 0.8,
        },
        {
            "local_key": "storm_explanation",
            "description": "风暴使日志意外掉入夹层。",
            "tags": [],
            "title": "风暴遗失日志",
            "proposition": "日志在风暴中意外落入甲板夹层。",
            "target_resolution_key": "main_resolution",
            "required_claim_keys": ["main_claim"],
            "falsifier_keys": [],
            "competing_hypothesis_keys": ["captain_explanation"],
            "evidence_assessments": [
                {
                    "information_key": "wet_log",
                    "effect": "contradicts",
                    "strength": "moderate",
                    "rationale": "受潮只在边缘，不像风暴导致的随机遗失。",
                }
            ],
            "status": "undetermined",
            "score": 0.3,
        },
    ]
    payload["reasoning_paths"] = [
        {
            "local_key": "captain_path",
            "description": "以纸页盐渍推断主动藏匿。",
            "tags": [],
            "title": "主动藏匿路径",
            "path_type": "proof",
            "target_key": "captain_explanation",
            "steps": [
                {
                    "step_key": "salt_trace",
                    "input_keys": ["wet_log"],
                    "operation": "infer",
                    "output_key": "main_claim",
                }
            ],
            "required_for_resolution": True,
            "alternative_path_keys": ["storm_path"],
        },
        {
            "local_key": "storm_path",
            "description": "检验风暴遗失这个替代解释。",
            "tags": [],
            "title": "风暴遗失路径",
            "path_type": "proof",
            "target_key": "storm_explanation",
            "steps": [
                {
                    "step_key": "storm_check",
                    "input_keys": ["wet_log"],
                    "operation": "compare",
                    "output_key": "main_claim",
                }
            ],
            "required_for_resolution": True,
            "alternative_path_keys": ["captain_path"],
        },
    ]
    return EvidenceLogicIRV2.model_validate(payload)


def test_v8_output_schemas_compile_for_deepseek_strict() -> None:
    for output_type in (
        CaseBlueprintV1,
        StoryWorldIRV1,
        EvidenceLogicIRV1,
        EvidenceLogicIRV2,
        ResolutionGovernanceIRV1,
    ):
        schema = compile_deepseek_strict_schema(output_type)
        assert schema["type"] == "object"
        assert "additionalProperties" in schema


def test_time_ir_rejects_ambiguous_natural_language_time() -> None:
    with pytest.raises(ValueError):
        TimeIR.model_validate(
            {
                "start": "午夜",
                "end": None,
                "precision": "hour",
            }
        )


def test_linker_and_compiler_inject_ids_metadata_and_object_types() -> None:
    linked = link_draft(
        _blueprint(),
        _story(),
        _evidence(),
        _governance(),
        task_run_id=123,
    )
    candidate = compile_casefile(
        linked,
        casefile_id="case_demo",
        brief_id="brief_demo",
        brief_version=2,
        version_id="draft_demo",
        version_no=3,
        parent_version_id=None,
        updated_at=datetime(2026, 8, 8, tzinfo=UTC),
    )

    validate_casefile(candidate)
    assert candidate["entities"][0]["id"] == "ent_t123_001"
    assert candidate["entities"][0]["created_by"] == {
        "actor_type": "agent",
        "actor_id": "agent_brief_to_draft",
    }
    assert candidate["information_units"][0]["supports_claim_refs"] == [
        {"object_type": "claim", "object_id": "claim_t123_001"}
    ]
    assert candidate["extensions"] == {}
    assert candidate["version"]["parent_version_id"] is None


def test_v10_compiler_preserves_explicit_assessments_and_matrix_gate() -> None:
    blueprint = _blueprint().model_copy(deep=True)
    blueprint.hypotheses = [
        BlueprintObjectV1(
            local_key="captain_explanation",
            title="船长藏匿日志",
            purpose="第一个竞争解释。",
        ),
        BlueprintObjectV1(
            local_key="storm_explanation",
            title="风暴遗失日志",
            purpose="第二个竞争解释。",
        ),
    ]
    blueprint.reasoning_paths = [
        BlueprintObjectV1(
            local_key="captain_path",
            title="主动藏匿路径",
            purpose="支持第一个解释。",
        ),
        BlueprintObjectV1(
            local_key="storm_path",
            title="风暴遗失路径",
            purpose="检验第二个解释。",
        ),
    ]
    evidence = _v10_evidence()

    assert _evidence_assessment_issues(evidence) == []
    linked = link_draft(blueprint, _story(), evidence, _governance(), task_run_id=125)
    candidate = compile_casefile(
        linked,
        casefile_id="case_demo",
        brief_id="brief_demo",
        brief_version=2,
        version_id="draft_demo",
        version_no=3,
        parent_version_id=None,
        updated_at=datetime(2026, 8, 8, tzinfo=UTC),
    )

    validate_casefile(candidate)
    assert candidate["hypotheses"][0]["evidence_assessments"] == [
        {
            "information_ref": {"object_type": "information_unit", "object_id": "info_t125_001"},
            "effect": "supports",
            "strength": "strong",
            "rationale": "盐渍位置与主动藏匿的路径吻合。",
        }
    ]

    incomplete = evidence.model_copy(deep=True)
    incomplete.hypotheses[1].evidence_assessments = []
    assert {issue["code"] for issue in _evidence_assessment_issues(incomplete)} == {
        "missing_evidence_assessment"
    }


def test_compiler_omits_optional_nulls_and_preserves_required_nulls() -> None:
    blueprint = _blueprint().model_copy(deep=True)
    blueprint.locations.append(
        BlueprintObjectV1(
            local_key="dock",
            title="码头",
            purpose="没有可靠坐标的进入地点",
        )
    )
    story = _story().model_copy(deep=True)
    story.locations.append(
        LocationIR.model_validate(
            {
                "local_key": "dock",
                "description": "没有可靠坐标的进入地点。",
                "name": "旧码头",
                "parent_key": None,
                "adjacency_keys": [],
                "access_rules": [],
                "travel_times": [],
                "visibility_rules": [],
            }
        )
    )
    linked = link_draft(
        blueprint,
        story,
        _evidence(),
        _governance(),
        task_run_id=124,
    )

    candidate = compile_casefile(
        linked,
        casefile_id="case_demo",
        brief_id="brief_demo",
        brief_version=2,
        version_id="draft_demo",
        version_no=3,
        parent_version_id=None,
        updated_at=datetime(2026, 8, 8, tzinfo=UTC),
    )

    location = candidate["locations"][0]
    assert "spatial_position" not in location
    assert location["parent_ref"] is None
    assert location["confidence"] is None
    validate_casefile(candidate)


def test_linker_rejects_missing_and_cross_type_local_keys() -> None:
    evidence = _evidence().model_copy(deep=True)
    evidence.information_units[0].availability.perspective_keys = ["main_claim"]
    evidence.claims = []

    with pytest.raises(LinkerValidationError) as captured:
        link_draft(
            _blueprint(),
            _story(),
            evidence,
            _governance(),
            task_run_id=123,
        )

    codes = {issue["code"] for issue in captured.value.errors}
    assert "planned_object_missing" in codes
    assert "local_key_type_mismatch" in codes
    assert all(issue["component_id"] == "evidence_logic" for issue in captured.value.errors)


def test_linker_rejects_casefile_specific_reference_types() -> None:
    evidence = _evidence().model_copy(deep=True)
    evidence.claims[0].support_keys = ["main_claim"]
    story = _story().model_copy(deep=True)
    story.entities[0].knowledge_states = [
        KnowledgeStateIR(
            as_of_event_key=None,
            knows_keys=["main_claim"],
            believes_keys=[],
            false_belief_keys=[],
        )
    ]

    with pytest.raises(LinkerValidationError) as captured:
        link_draft(
            _blueprint(),
            story,
            evidence,
            _governance(),
            task_run_id=123,
        )

    mismatches = [
        issue for issue in captured.value.errors if issue["code"] == "local_key_type_mismatch"
    ]
    assert {issue["path"] for issue in mismatches} == {
        "/claims/main_claim/support_keys/0",
        "/entities/captain/knowledge_states/knows_keys/0",
    }


def test_v8_prompt_bundle_loads_component_hashes_without_changing_v7() -> None:
    v7 = load_prompt("brief_to_draft", "brief-to-draft-v7")
    v8 = load_prompt("brief_to_draft", "brief-to-draft-v8")

    assert not v7.component_prompts
    assert set(v8.component_prompts) == {"planner", "story", "evidence", "governance"}
    assert len(v8.component_sha256["planner"]) == 64
    assert "CaseBlueprintV1" in component_prompt_for_task(
        "brief_to_draft", "brief-to-draft-v8", "planner"
    )


def test_fake_provider_uses_the_same_v8_component_pipeline() -> None:
    events: list[tuple[str, str, dict[str, object]]] = []
    request = GenerationRequest(
        task_run_id=321,
        prompt_version="brief-to-draft-v8",
        brief={"conclusion_mode": "unique"},
        casefile_id="case_demo",
        brief_id="brief_demo",
        brief_version=1,
        version_id="draft_demo",
        version_no=1,
        parent_version_id=None,
        model_id="fake-v8",
        api_key=None,
        max_turns=3,
        emit=lambda event_type, stage, payload: events.append((event_type, stage, payload)),
    )

    result = FakeProvider().generate(request)

    validate_casefile(result.candidate)
    completed = {
        str(payload["component_id"])
        for event_type, _stage, payload in events
        if event_type == "agent.step.completed"
    }
    assert completed == {
        "context_pack_builder",
        "case_blueprint_planner",
        "story_world",
        "evidence_logic",
        "resolution_governance",
        "reference_linker",
        "casefile_compiler",
        "quality_repair_gate",
    }
    assert result.candidate["events"]
    assert result.candidate["reasoning_paths"]


def test_v8_diagnostics_redact_full_and_provider_masked_api_keys() -> None:
    message = "api key: ****3548 invalid; supplied sk-secret-value-123"

    redacted = _safe_diagnostic_message(message, ("sk-secret-value-123",))

    assert "3548" not in redacted
    assert "sk-secret" not in redacted
    assert redacted.count("[REDACTED]") == 2


def test_v8_usage_merge_preserves_retry_cache_and_reasoning_totals() -> None:
    merged = _merge_usage(
        [
            {
                "requests": 2,
                "input_tokens": 100,
                "output_tokens": 30,
                "total_tokens": 130,
                "cached_tokens": 40,
                "reasoning_tokens": 10,
            },
            {
                "requests": 1,
                "input_tokens": 20,
                "output_tokens": 5,
                "total_tokens": 25,
                "cached_tokens": 15,
                "reasoning_tokens": 3,
            },
        ]
    )

    assert merged == {
        "requests": 3,
        "input_tokens": 120,
        "output_tokens": 35,
        "total_tokens": 155,
        "cached_tokens": 55,
        "reasoning_tokens": 13,
    }
