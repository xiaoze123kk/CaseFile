"""v16 semantic relationship coverage and labeling gates."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from casefile.agent_runtime import CandidateStrategy, GenerationRequest
from casefile.agent_runtime.brief_to_draft_runtime import resolve_pipeline_spec
from casefile.agent_runtime.brief_to_draft_v8.ir import CaseBlueprintV1
from casefile.agent_runtime.brief_to_draft_v8.validation import (
    _v16_blueprint_relationship_coverage_issues,
    _v16_story_relationship_coverage_issues,
)
from casefile.agent_runtime.brief_to_draft_v8.workflow import (
    PipelineContext,
    _BlueprintPlannerStage,
    _build_context_pack,
)
from casefile.agent_runtime.brief_to_draft_v12.contracts import StoryWorldIRV3
from casefile.agent_runtime.prompt import V16_GENERATION_AGENT_VERSION
from casefile.agent_runtime.tools import TOOLSET_VERSION
from pydantic import BaseModel


def _entity(local_key: str, name: str) -> dict[str, object]:
    return {
        "local_key": local_key,
        "entity_type": "person",
        "name": name,
        "aliases": [],
        "traits": [],
        "goals": [],
        "secrets": [],
        "capabilities": [],
        "knowledge_states": [],
        "description": f"{name}的人物设定。",
        "tags": [],
    }


def _event(*participant_keys: str) -> dict[str, object]:
    return {
        "local_key": "confrontation",
        "title": "实验室对峙",
        "truth_status": "canon_true",
        "participant_keys": list(participant_keys),
        "location_key": None,
        "cause_keys": [],
        "effect_keys": [],
        "observed_by_keys": [],
        "description": "三人在实验室正面对峙。",
        "tags": [],
    }


def _relationship(
    local_key: str, from_key: str, to_key: str, title: str, relationship_type: str
) -> dict[str, object]:
    return {
        "local_key": local_key,
        "title": title,
        "from_key": from_key,
        "to_key": to_key,
        "relationship_type": relationship_type,
        "direction": "directed",
        "truth_status": "canon_true",
        "visibility": "public",
        "description": f"{from_key}与{to_key}的具体关系。",
        "tags": [],
    }


def _story(relationships: list[dict[str, object]]) -> StoryWorldIRV3:
    return StoryWorldIRV3.model_validate(
        {
            "entities": [
                _entity("protagonist", "林晚"),
                _entity("victim", "周衡"),
                _entity("mastermind", "顾铮"),
            ],
            "relationships": relationships,
            "locations": [],
            "events": [_event("protagonist", "victim", "mastermind")],
        }
    )


def _blueprint(relationships: list[dict[str, object]]) -> CaseBlueprintV1:
    def node(
        local_key: str,
        title: str,
        dependency_keys: list[str] | None = None,
    ) -> dict[str, object]:
        return {
            "local_key": local_key,
            "title": title,
            "purpose": f"规划{title}。",
            "dependency_keys": dependency_keys or [],
        }

    return CaseBlueprintV1.model_validate(
        {
            "title": "关系覆盖样例",
            "resolution_specs": [node("resolution", "还原真相")],
            "entities": [
                node("protagonist", "林晚"),
                node("victim", "周衡"),
                node("mastermind", "顾铮"),
            ],
            "relationships": relationships,
            "locations": [],
            "events": [
                node(
                    "confrontation",
                    "实验室对峙",
                    ["protagonist", "victim", "mastermind"],
                )
            ],
            "information_units": [],
            "claims": [],
            "hypotheses": [node("hypothesis", "主谋实施身份替换", ["resolution"])],
            "reasoning_paths": [],
            "constraints": [],
            "structure_locks": [],
        }
    )


def test_v16_blueprint_requires_relationship_plans_for_key_event_entities() -> None:
    issues = _v16_blueprint_relationship_coverage_issues(_blueprint([]))

    assert [issue["code"] for issue in issues].count("relationship_plan_missing") == 3
    assert [issue["code"] for issue in issues].count(
        "isolated_entity_relationship_plan"
    ) == 3


def test_v16_blueprint_accepts_a_concrete_connected_relationship_plan() -> None:
    relationships = [
        {
            "local_key": "rel_protagonist_victim",
            "title": "保护并调查",
            "purpose": "林晚保护周衡并调查其身份遭窃原因。",
            "dependency_keys": ["protagonist", "victim"],
        },
        {
            "local_key": "rel_protagonist_mastermind",
            "title": "追查",
            "purpose": "林晚追查顾铮。",
            "dependency_keys": ["protagonist", "mastermind"],
        },
        {
            "local_key": "rel_mastermind_victim",
            "title": "操控并加害",
            "purpose": "顾铮操控并加害周衡。",
            "dependency_keys": ["mastermind", "victim"],
        },
    ]

    assert _v16_blueprint_relationship_coverage_issues(
        _blueprint(relationships)
    ) == []


def test_v16_requires_semantic_edges_for_entities_in_the_same_key_event() -> None:
    issues = _v16_story_relationship_coverage_issues(_story([]))

    assert [issue["code"] for issue in issues].count("missing_semantic_relationship") == 3


def test_v16_rejects_generic_relationship_titles() -> None:
    story = _story(
        [
            _relationship("rel_a", "protagonist", "victim", "主角与案件有关联", "related_to"),
            _relationship("rel_b", "protagonist", "mastermind", "追查", "investigates"),
            _relationship("rel_c", "mastermind", "victim", "操控并加害", "controls"),
        ]
    )

    issues = _v16_story_relationship_coverage_issues(story)

    assert [issue["code"] for issue in issues] == ["generic_relationship_title"]
    assert issues[0]["path"] == "/relationships/rel_a/title"


def test_v16_accepts_concrete_relationship_network() -> None:
    story = _story(
        [
            _relationship("rel_a", "protagonist", "victim", "保护并调查", "investigates"),
            _relationship("rel_b", "protagonist", "mastermind", "追查", "investigates"),
            _relationship("rel_c", "mastermind", "victim", "操控并加害", "controls"),
        ]
    )

    assert _v16_story_relationship_coverage_issues(story) == []


def test_v16_planner_repairs_receive_the_latest_previous_blueprint() -> None:
    relationships = [
        {
            "local_key": "rel_protagonist_victim",
            "title": "保护并调查",
            "purpose": "林晚保护周衡并调查其身份遭窃原因。",
            "dependency_keys": ["protagonist", "victim"],
        },
        {
            "local_key": "rel_protagonist_mastermind",
            "title": "追查",
            "purpose": "林晚追查顾铮。",
            "dependency_keys": ["protagonist", "mastermind"],
        },
        {
            "local_key": "rel_mastermind_victim",
            "title": "操控并加害",
            "purpose": "顾铮操控并加害周衡。",
            "dependency_keys": ["mastermind", "victim"],
        },
    ]
    outputs = [
        _blueprint([]).model_dump(mode="json"),
        _blueprint(relationships[:1]).model_dump(mode="json"),
        _blueprint(relationships).model_dump(mode="json"),
    ]
    planner_inputs: list[dict[str, Any]] = []
    request = GenerationRequest(
        task_run_id=416,
        prompt_version="brief-to-draft-v16",
        brief={"creative_intent": "验证定向修复"},
        schema_version="2.0",
        casefile_id="case_v16_planner_repair",
        brief_id="brief_v16_planner_repair",
        brief_version=1,
        version_id="draft_v16_planner_repair",
        version_no=1,
        parent_version_id=None,
        model_id="fake-v16",
        api_key=None,
        max_turns=3,
        emit=lambda *_args: None,
        candidate_strategy=CandidateStrategy.BALANCED,
        agent_version=V16_GENERATION_AGENT_VERSION,
        toolset_version=TOOLSET_VERSION,
    )

    async def call_component(
        _instructions: str,
        input_text: str,
        output_type: type[BaseModel],
        _stage: str,
        component_id: str,
        _schema_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        assert component_id == "case_blueprint_planner"
        assert output_type is CaseBlueprintV1
        planner_inputs.append(json.loads(input_text))
        return outputs[len(planner_inputs) - 1], {"requests": 1}

    spec = resolve_pipeline_spec(request.prompt_version)
    context = PipelineContext(request=request, call_component=call_component, spec=spec)
    context.context_pack = _build_context_pack(request, spec)

    asyncio.run(_BlueprintPlannerStage().run(context))

    assert len(planner_inputs) == 3
    assert planner_inputs[0]["previous_output"] is None
    assert planner_inputs[1]["previous_output"] == outputs[0]
    assert planner_inputs[2]["previous_output"] == outputs[1]
    assert context.blueprint == _blueprint(relationships)
