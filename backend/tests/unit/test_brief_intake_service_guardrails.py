"""Deterministic Brief Intake guardrails outside a database session."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from casefile.application.brief_intake_service import (
    deduplicate_additional_questions,
)
from casefile.data_postgres.models import BriefIntakeQuestion


def _existing(prompt: str) -> BriefIntakeQuestion:
    return cast(BriefIntakeQuestion, SimpleNamespace(prompt=prompt))


def _question(
    key: str,
    prompt: str,
    *,
    ordinal: int,
) -> dict[str, Any]:
    return {
        "question_key": key,
        "ordinal": ordinal,
        "prompt": prompt,
        "impact": "影响后续内容组织。",
        "required": False,
        "suggestions": [],
    }


def test_additional_questions_drop_exact_prompt_duplicates() -> None:
    kept = deduplicate_additional_questions(
        [_existing("你希望核心推理目标是什么？")],
        [
            _question("question_duplicate", "你希望核心推理目标是什么？", ordinal=1),
            _question("question_scope", "你预计采用多大规模？", ordinal=2),
        ],
    )

    assert kept == [_question("question_scope", "你预计采用多大规模？", ordinal=1)]


def test_additional_questions_ignore_punctuation_and_whitespace_drift() -> None:
    kept = deduplicate_additional_questions(
        [_existing("你希望 一晚完成，还是持续扩展？")],
        [
            _question("question_duplicate", "你希望一晚完成还是持续扩展?", ordinal=1),
        ],
    )

    assert kept == []


def test_additional_questions_keep_distinct_new_angles() -> None:
    existing = [_existing("你希望核心推理目标是什么？")]
    first = _question("question_cast", "次要证人需要独立承担线索吗？", ordinal=1)
    second = _question("question_pacing", "希望叙事节奏更快还是更克制？", ordinal=2)

    kept = deduplicate_additional_questions(existing, [first, second])

    assert kept == [first, second]
