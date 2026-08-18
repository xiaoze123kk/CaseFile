"""v15 deterministic competition-matrix scope, evaluation, and scoped repair."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from casefile.agent_runtime import CandidateStrategy, GenerationRequest
from casefile.agent_runtime.brief_to_draft_v8.ir import EvidenceLogicIRV2
from casefile.agent_runtime.brief_to_draft_v8.workflow import (
    _hypotheses_by_resolution,
    _used_information_by_hypothesis,
    _v15_story_person_name_issues,
    run_v8_generation,
)
from casefile.agent_runtime.brief_to_draft_v11.contracts import EntityIRV2
from casefile.agent_runtime.brief_to_draft_v12.contracts import StoryWorldIRV3
from casefile.agent_runtime.brief_to_draft_v15.contracts import (
    MatrixAssessmentIR,
    MatrixEvaluationOutputV1,
)
from casefile.agent_runtime.brief_to_draft_v15.matrix import (
    derive_matrix_cells,
    join_matrix_assessments,
    matrix_evaluation_issues,
)
from casefile.agent_runtime.prompt import V15_GENERATION_AGENT_VERSION
from casefile.agent_runtime.providers import (
    _add_fake_v10_matrix_plan,
    _fake_matrix_evaluation_output,
    _fake_v8_output,
)
from casefile.agent_runtime.tools import TOOLSET_VERSION
from casefile.contracts import validate_casefile
from pydantic import BaseModel


def _v15_evidence() -> EvidenceLogicIRV2:
    return EvidenceLogicIRV2.model_validate(_fake_v8_output(EvidenceLogicIRV2))


def _v15_story_with_entity(name: str, *, entity_type: str = "person") -> StoryWorldIRV3:
    return StoryWorldIRV3(
        schema_id="story-world-ir-v3",
        entities=[
            EntityIRV2(
                local_key="person_a",
                entity_type=entity_type,
                name=name,
                aliases=[],
                traits=[],
                goals=[],
                secrets=[],
                capabilities=[],
                knowledge_states=[],
                description="人物实体。",
                tags=[],
            )
        ],
        relationships=[],
        locations=[],
        events=[],
    )


def test_v15_person_name_gate_rejects_role_labels() -> None:
    for label in ("主角", "嫌疑人", "真正主谋", "表层黑手", "幕后黑手"):
        issues = _v15_story_person_name_issues(_v15_story_with_entity(label))

        assert [issue["code"] for issue in issues] == ["person_name_role_label"]
        assert issues[0]["path"] == "/entities/person_a/name"
        assert issues[0]["component_id"] == "story_world"
        assert issues[0]["failure_layer"] == "naming_contract"
        assert issues[0]["ir_path"] == "/entities/person_a"


def test_v15_person_name_gate_accepts_names_and_non_person_entities() -> None:
    assert _v15_story_person_name_issues(_v15_story_with_entity("林晚")) == []
    assert _v15_story_person_name_issues(_v15_story_with_entity("顾铮")) == []
    assert (
        _v15_story_person_name_issues(
            _v15_story_with_entity("主角", entity_type="organization")
        )
        == []
    )


def test_derive_matrix_cells_uses_exact_path_scope() -> None:
    evidence = _v15_evidence()
    cells = derive_matrix_cells(
        _hypotheses_by_resolution(evidence),
        _used_information_by_hypothesis(evidence),
    )

    assert [(cell.hypothesis_key, cell.information_key) for cell in cells] == [
        ("alternative_hypothesis", "record"),
        ("hypothesis", "record"),
    ]


def test_matrix_evaluation_issues_rejects_missing_duplicate_and_unscoped_cells() -> None:
    evidence = _v15_evidence()
    cells = derive_matrix_cells(
        _hypotheses_by_resolution(evidence),
        _used_information_by_hypothesis(evidence),
    )

    assert matrix_evaluation_issues(
        MatrixEvaluationOutputV1(
            assessments=[
                MatrixAssessmentIR(
                    hypothesis_key="hypothesis",
                    information_key="record",
                    effect="supports",
                    strength="strong",
                    rationale="与命题一致。",
                ),
                MatrixAssessmentIR(
                    hypothesis_key="alternative_hypothesis",
                    information_key="record",
                    effect="contradicts",
                    strength="moderate",
                    rationale="与命题冲突。",
                ),
            ]
        ),
        cells,
    ) == []
    broken = MatrixEvaluationOutputV1(
        assessments=[
            MatrixAssessmentIR(
                hypothesis_key="hypothesis",
                information_key="record",
                effect="supports",
                strength="strong",
                rationale="与命题一致。",
            ),
            MatrixAssessmentIR(
                hypothesis_key="hypothesis",
                information_key="record",
                effect="neutral",
                strength="weak",
                rationale="重复判定。",
            ),
            MatrixAssessmentIR(
                hypothesis_key="hypothesis",
                information_key="extra",
                effect="neutral",
                strength="weak",
                rationale="越界判定。",
            ),
        ]
    )

    assert {
        issue["code"] for issue in matrix_evaluation_issues(broken, cells)
    } == {
        "duplicate_matrix_assessment",
        "unscoped_matrix_assessment",
        "missing_matrix_assessment",
    }


def test_join_matrix_assessments_attaches_cells_in_canonical_order() -> None:
    evidence = _v15_evidence()
    cells = derive_matrix_cells(
        _hypotheses_by_resolution(evidence),
        _used_information_by_hypothesis(evidence),
    )
    output = MatrixEvaluationOutputV1(
        assessments=[
            MatrixAssessmentIR(
                hypothesis_key=cell.hypothesis_key,
                information_key=cell.information_key,
                effect=("supports", "contradicts")[index % 2],
                strength="moderate",
                rationale=f"第 {index + 1} 格判定。",
            )
            for index, cell in enumerate(cells)
        ]
    )

    joined = join_matrix_assessments(evidence, output, cells)

    assert [
        [item.information_key for item in h.evidence_assessments] for h in joined.hypotheses
    ] == [
        ["record"],
        ["record"],
    ]
    # Cells derive in sorted hypothesis order, so "hypothesis" gets the second cell.
    assert [item.rationale for item in joined.hypotheses[0].evidence_assessments] == [
        "第 2 格判定。"
    ]
    assert [item.rationale for item in joined.hypotheses[1].evidence_assessments] == [
        "第 1 格判定。"
    ]


def test_v15_matrix_evaluation_failure_is_repaired_with_previous_output() -> None:
    calls: list[str] = []
    matrix_inputs: list[dict[str, Any]] = []
    matrix_calls = 0
    request = GenerationRequest(
        task_run_id=330,
        prompt_version="brief-to-draft-v15",
        brief={"conclusion_mode": "unique"},
        schema_version="2.0",
        casefile_id="case_demo_v15_matrix",
        brief_id="brief_demo",
        brief_version=1,
        version_id="draft_demo_v15_matrix",
        version_no=1,
        parent_version_id=None,
        model_id="fake-v15-matrix",
        api_key=None,
        max_turns=3,
        emit=lambda *_args: None,
        candidate_strategy=CandidateStrategy.BALANCED,
        agent_version=V15_GENERATION_AGENT_VERSION,
        toolset_version=TOOLSET_VERSION,
    )

    async def call_component(
        _instructions: str,
        input_text: str,
        output_type: type[BaseModel],
        _stage: str,
        component_id: str,
        _schema_id: str,
    ) -> tuple[dict[str, object], dict[str, int]]:
        nonlocal matrix_calls
        calls.append(component_id)
        if output_type.__name__ == "MatrixEvaluationOutputV1":
            matrix_calls += 1
            payload = json.loads(input_text)
            matrix_inputs.append(payload)
            if matrix_calls == 1:
                return {"assessments": []}, {"requests": 1}
            return _fake_matrix_evaluation_output(payload), {"requests": 1}
        output = _fake_v8_output(output_type)
        _add_fake_v10_matrix_plan(output_type, output)
        return output, {"requests": 1}

    result = asyncio.run(run_v8_generation(request, call_component=call_component))

    validate_casefile(result.candidate)
    assert calls.count("evidence_matrix") == 2
    assert [cell["information_key"] for cell in matrix_inputs[0]["cells"]] == ["record", "record"]
    repair_input = matrix_inputs[1]
    assert repair_input["previous_output"]["assessments"] == []
    assert [issue["code"] for issue in repair_input["targeted_repair_issues"]] == [
        "missing_matrix_assessment",
        "missing_matrix_assessment",
    ]
    assessments = result.candidate["hypotheses"][0]["evidence_assessments"]
    assert {item["information_ref"]["object_id"] for item in assessments}
    assert len(result.candidate["hypotheses"][1]["evidence_assessments"]) == 1


def test_v15_person_name_role_label_is_repaired_before_compile() -> None:
    story_calls = 0
    repair_inputs: list[dict[str, Any]] = []
    request = GenerationRequest(
        task_run_id=331,
        prompt_version="brief-to-draft-v15",
        brief={"conclusion_mode": "unique"},
        schema_version="2.0",
        casefile_id="case_demo_v15_naming",
        brief_id="brief_demo",
        brief_version=1,
        version_id="draft_demo_v15_naming",
        version_no=1,
        parent_version_id=None,
        model_id="fake-v15-naming",
        api_key=None,
        max_turns=3,
        emit=lambda *_args: None,
        candidate_strategy=CandidateStrategy.BALANCED,
        agent_version=V15_GENERATION_AGENT_VERSION,
        toolset_version=TOOLSET_VERSION,
    )

    async def call_component(
        _instructions: str,
        input_text: str,
        output_type: type[BaseModel],
        _stage: str,
        _component_id: str,
        _schema_id: str,
    ) -> tuple[dict[str, object], dict[str, int]]:
        nonlocal story_calls
        if output_type.__name__ == "MatrixEvaluationOutputV1":
            return _fake_matrix_evaluation_output(json.loads(input_text)), {"requests": 1}
        if output_type.__name__ == "StoryWorldIRV3":
            story_calls += 1
            output = _fake_v8_output(output_type)
            if story_calls == 1:
                output["entities"][0]["name"] = "主角"
                output["entities"][0]["traits"] = ["谨慎"]
            else:
                repair_inputs.append(json.loads(input_text))
                output["entities"][0]["name"] = "林晚"
                output["entities"][0]["traits"] = ["主角", "谨慎"]
            return output, {"requests": 1}
        output = _fake_v8_output(output_type)
        _add_fake_v10_matrix_plan(output_type, output)
        return output, {"requests": 1}

    result = asyncio.run(run_v8_generation(request, call_component=call_component))

    validate_casefile(result.candidate)
    assert story_calls == 2
    assert len(repair_inputs) == 1
    repair_issues = repair_inputs[0]["targeted_repair_issues"]
    assert [issue["code"] for issue in repair_issues] == ["person_name_role_label"]
    assert repair_issues[0]["path"] == "/entities/author/name"
    assert repair_issues[0]["component_id"] == "story_world"
    assert result.candidate["entities"][0]["name"] == "林晚"
    assert result.candidate["entities"][0]["traits"] == ["主角", "谨慎"]


def test_v15_matrix_component_binds_strict_schema() -> None:
    from casefile.agent_runtime.prompt_package import output_type_for_component
    from casefile.agent_runtime.prompt_repository import load_prompt

    definition = load_prompt("brief_to_draft", "brief-to-draft-v15")
    assert definition.package is not None

    assert output_type_for_component(definition.package, "matrix") is MatrixEvaluationOutputV1
    assert definition.package.components["matrix"].output_schema_id == "matrix-evaluation-v1"
