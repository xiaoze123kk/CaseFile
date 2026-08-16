"""Cross-language Brief Intake contract invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from casefile_contracts import (
    Brief,
    BriefIntakeCandidate,
    BriefIntakeQuestionSet,
    TaskRun,
)
from jsonschema import Draft202012Validator, ValidationError

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "contracts"
    / "schemas"
    / "brief-intake"
    / "brief-intake.schema.json"
)


def _intake_schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _candidate(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "concept": "一名档案员发现所有证词都指向一段不存在的时间。",
        "core_selling_points": ["证词时间互相咬合", "真相取决于作者确认的底牌"],
        "content_outline": ["建立不可能时间", "交叉验证证词", "揭示记录被改写"],
        "reasoning_goal": "解释同一事件为何在三份可靠记录中拥有不同发生时间。",
        "resolution_mode": "author_anchored",
        "conclusion_mode": "unique",
        "author_answer": "档案员本人曾为了保护证人改写主记录。",
        "constraints": [
            {
                "constraint_key": "constraint_keep_archive",
                "category": "must_keep",
                "statement": "必须保留档案被人为改写这一核心事实。",
                "strength": "hard",
                "confirmed": True,
                "source": "user_confirmed",
            }
        ],
        "pending_decisions": [
            {
                "decision_key": "decision_supporting_cast",
                "prompt": "次要证人是否合并？",
                "impact": "影响角色数量，但不改变核心解答。",
                "source": "unresolved",
            }
        ],
        "scope_estimate": "中篇，6 至 8 个主要场景，4 名核心角色。",
        "risk_notes": ["时间线信息密度较高，需要分段验证。"],
        "field_sources": {
            "concept": "user_original",
            "core_selling_points": "agent_suggestion",
            "content_outline": "agent_suggestion",
            "reasoning_goal": "user_confirmed",
            "resolution_mode": "user_confirmed",
            "conclusion_mode": "user_confirmed",
            "author_answer": "user_confirmed",
            "constraints": "user_confirmed",
            "scope_estimate": "agent_suggestion",
            "risk_notes": "agent_suggestion",
        },
    }
    value.update(overrides)
    return value


def test_brief_intake_candidate_round_trip_and_sources() -> None:
    candidate = BriefIntakeCandidate.model_validate(_candidate())

    assert candidate.model_dump(mode="json") == _candidate()
    assert candidate.field_sources.concept.value == "user_original"


def test_brief_intake_candidate_rejects_unanchored_author_answer() -> None:
    with pytest.raises(ValidationError):
        Draft202012Validator(_intake_schema()).validate(
            _candidate(resolution_mode="open", author_answer="不应被保留")
        )


def test_brief_intake_question_gate_allows_at_most_one_hard_question() -> None:
    valid = BriefIntakeQuestionSet.model_validate(
        {
            "questions": [
                {
                    "question_key": "question_author_truth",
                    "ordinal": 1,
                    "prompt": "作者是否已经确定真相？",
                    "impact": "决定结论模式和是否需要作者底牌。",
                    "required": True,
                    "suggestions": ["由作者确定", "由 Agent 提供候选"],
                },
                {
                    "question_key": "question_scope",
                    "ordinal": 2,
                    "prompt": "预计采用多大规模？",
                    "impact": "影响内容骨架，不改变核心解答。",
                    "required": False,
                    "suggestions": ["中篇"],
                },
            ]
        }
    )
    assert len(valid.questions) == 2

    root_schema = _intake_schema()
    question_schema = {
        "$schema": root_schema["$schema"],
        "$defs": root_schema["$defs"],
        "$ref": "#/$defs/BriefIntakeQuestionSet",
    }
    with pytest.raises(ValidationError):
        Draft202012Validator(question_schema).validate(
            {
                "questions": [
                    {
                        "question_key": f"question_hard_{index}",
                        "ordinal": index,
                        "prompt": "必须回答的问题",
                        "impact": "改变核心方向",
                        "required": True,
                        "suggestions": [],
                    }
                    for index in (1, 2)
                ]
            }
        )


def test_brief_optional_projection_fields_and_intake_task_shape() -> None:
    brief = Brief.model_validate(
        {
            "source_record_ids": [1],
            "creative_intent": "不可存在的时间记录",
            "reasoning_proposition": "谁改写了记录，为什么？",
            "resolution_mode": "open",
            "conclusion_mode": "open_interpretation",
            "author_answer": None,
            "author_anchors": [],
            "boundary_text": None,
            "creative_constraints": [],
            "core_selling_points": ["时间证词互锁"],
            "content_outline": ["建立矛盾", "验证来源"],
            "scope_estimate": "中篇",
            "risk_notes": ["避免一次暴露全部时间点"],
        }
    )
    assert brief.scope_estimate is not None
    assert brief.scope_estimate.root == "中篇"

    task = TaskRun.model_validate(
        {
            "task_run_id": 1,
            "project_id": 1,
            "task_type": "brief_intake_synthesize",
            "status": "queued",
            "stage": "queued",
            "provider": "openai",
            "model_id": "gpt-5.6-sol",
            "input_draft_revision": 1,
            "input_brief_revision": None,
            "input_source_record_id": 1,
            "input_brief_intake_id": 1,
            "input_brief_intake_revision": 3,
            "base_brief_intake_candidate_id": None,
            "agent_thread_id": None,
            "input_message_id": None,
            "output_message_id": None,
            "input_hash": "a" * 64,
            "attempt_count": 0,
            "usage": {},
            "result": None,
            "failure": None,
            "candidate_strategy": None,
            "component_steps": [],
        }
    )
    assert task.task_type.value == "brief_intake_synthesize"
