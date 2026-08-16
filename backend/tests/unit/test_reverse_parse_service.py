"""Pure-logic unit tests for ReverseParseService._assemble_brief_content.

The assembly method only reads ImportedDocument/ParseItem attributes, so both ORM
objects are instantiated directly via constructors (no session, no database round
trip). DB-level CHECK constraints are not enforced at construction time.
"""

from __future__ import annotations

from typing import Any

import pytest
from casefile.application.reverse_parse_service import ReverseParseService
from casefile.data_postgres.models import ImportedDocument, ParseItem


def _document(*block_texts: str) -> ImportedDocument:
    return ImportedDocument(
        project_id=1,
        filename="case.txt",
        media_type="text/plain",
        original_bytes=b"x",
        extracted_text="",
        blocks_jsonb=[
            {"block_no": index + 1, "text": text} for index, text in enumerate(block_texts)
        ],
        parse_status="succeeded",
        created_by_user_id=1,
    )


def _item(
    item_type: str,
    content: dict[str, Any],
    *,
    confirm_status: str = "confirmed",
    grading: str = "explicit",
) -> ParseItem:
    return ParseItem(
        project_id=1,
        document_id=1,
        item_type=item_type,
        content_jsonb=content,
        grading=grading,
        source_block_refs=[1],
        source_quote="原文片段",
        confirm_status=confirm_status,
    )


def test_concept_comes_from_first_block_truncated_to_1000_chars() -> None:
    first_block_text = "谜" * 1500
    document = _document(first_block_text, "第二块文本")

    content = ReverseParseService._assemble_brief_content(document, [])

    assert content["concept"] == first_block_text[:1000]
    assert len(content["concept"]) == 1000


def test_confirmed_events_enter_content_outline_sorted_by_order_index() -> None:
    items = [
        _item("event", {"title": "第三事件", "order_index": 3}),
        _item("event", {"title": "第一事件", "order_index": 1}),
        _item("event", {"title": "第二事件", "order_index": 2}),
    ]
    document = _document("开头")

    content = ReverseParseService._assemble_brief_content(document, items)

    assert content["content_outline"] == ["1. 第一事件", "2. 第二事件", "3. 第三事件"]


def test_core_selling_points_take_first_three_confirmed_entities() -> None:
    items = [
        _item("entity_alias", {"name": "老李", "description": "门卫"}),
        _item("entity_alias", {"name": "阿珍", "description": "会计"}),
        _item("entity_alias", {"name": "码头", "description": "案发现场"}),
        _item("entity_alias", {"name": "第四者", "description": "被忽略"}),
    ]
    document = _document("开头")

    content = ReverseParseService._assemble_brief_content(document, items)

    assert content["core_selling_points"] == ["老李：门卫", "阿珍：会计", "码头：案发现场"]


def test_reasoning_goal_uses_first_confirmed_candidate_question() -> None:
    items = [
        _item("candidate_question", {"question": "谁改写了航海记录？"}),
        _item("candidate_question", {"question": "回航为何触发？"}),
    ]
    document = _document("开头")

    content = ReverseParseService._assemble_brief_content(document, items)

    assert content["reasoning_goal"] == "谁改写了航海记录？"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("unique", "unique"),
        ("multiple", "finite_multiple"),
        ("open", "open_interpretation"),
        ("weird", "undetermined"),
    ],
)
def test_conclusion_mode_mapping(mode: str, expected: str) -> None:
    items = [_item("candidate_conclusion", {"conclusion": "答案", "mode": mode})]
    document = _document("开头")

    content = ReverseParseService._assemble_brief_content(document, items)

    assert content["conclusion_mode"] == expected


def test_only_confirmed_items_participate_in_assembly() -> None:
    items = [
        _item("candidate_question", {"question": "已确认问题"}),
        _item("candidate_question", {"question": "被驳回问题"}, confirm_status="rejected"),
        _item("candidate_question", {"question": "未确认问题"}, confirm_status="unconfirmed"),
        _item("event", {"title": "被驳回事件", "order_index": 9}, confirm_status="rejected"),
        _item("entity_alias", {"name": "未确认实体"}, confirm_status="unconfirmed"),
    ]
    document = _document("开头")

    content = ReverseParseService._assemble_brief_content(document, items)

    assert content["reasoning_goal"] == "已确认问题"
    assert content["content_outline"] == []
    assert content["core_selling_points"] == []


def test_conclusion_mode_is_undetermined_without_confirmed_conclusion() -> None:
    items = [
        _item(
            "candidate_conclusion",
            {"conclusion": "答案", "mode": "unique"},
            confirm_status="unconfirmed",
        ),
        _item("candidate_question", {"question": "问题"}),
    ]
    document = _document("开头")

    content = ReverseParseService._assemble_brief_content(document, items)

    assert content["conclusion_mode"] == "undetermined"
