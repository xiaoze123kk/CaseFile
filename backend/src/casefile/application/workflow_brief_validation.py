"""Brief validation rules required by the durable generation workflow."""

from __future__ import annotations

from typing import Any

from casefile_contracts import Brief as BriefContract
from pydantic import ValidationError

from casefile.application.errors import ApplicationError


def validate_brief(content: dict[str, Any]) -> dict[str, Any]:
    try:
        model = BriefContract.model_validate(content)
    except ValidationError as error:
        raise ApplicationError(
            "brief_invalid",
            "创作简报不符合当前内容要求。",
            status_code=422,
            details={"issues": error.errors(include_url=False)},
        ) from error
    validated = model.model_dump(mode="json", exclude_none=False)
    _validate_brief_semantics(validated)
    return validated


def _validate_brief_semantics(content: dict[str, Any]) -> None:
    text_fields = ("creative_intent", "reasoning_proposition")
    if any(not str(content[field]).strip() for field in text_fields):
        raise ApplicationError(
            "brief_invalid",
            "创作意图和推理命题不能为空。",
            status_code=422,
        )
    for field in ("author_answer", "boundary_text"):
        value = content[field]
        if value is not None and not str(value).strip():
            field_label = {"author_answer": "作者底牌", "boundary_text": "创作边界"}[field]
            raise ApplicationError(
                "brief_invalid",
                f"{field_label}必须为空或填写有效内容。",
                status_code=422,
            )
    source_record_ids = content["source_record_ids"]
    if len(source_record_ids) != len(set(source_record_ids)):
        raise ApplicationError(
            "brief_source_record_duplicate",
            "创作简报中的来源记录引用不能重复。",
            status_code=422,
        )
    resolution_mode = content["resolution_mode"]
    if resolution_mode == "author_anchored":
        if content["author_answer"] is None:
            raise ApplicationError(
                "brief_author_answer_required",
                "按作者底牌展开时必须填写作者底牌。",
                status_code=422,
            )
    elif content["author_answer"] is not None or content["author_anchors"]:
        raise ApplicationError(
            "brief_resolution_mode_conflict",
            "非作者底牌展开方式不能包含作者底牌或底牌原子项。",
            status_code=422,
        )
    anchor_ids = [item["anchor_id"] for item in content["author_anchors"]]
    constraint_ids = [item["constraint_id"] for item in content["creative_constraints"]]
    if len(anchor_ids) != len(set(anchor_ids)) or len(constraint_ids) != len(set(constraint_ids)):
        raise ApplicationError(
            "brief_atomic_id_duplicate",
            "同一组创作简报原子项的 ID 不能重复。",
            status_code=422,
        )
    statements = [
        *(item["statement"] for item in content["author_anchors"]),
        *(item["statement"] for item in content["creative_constraints"]),
    ]
    if any(not str(statement).strip() for statement in statements):
        raise ApplicationError(
            "brief_atomic_statement_blank",
            "创作简报原子项内容不能为空。",
            status_code=422,
        )


def require_confirmed_atomics(content: dict[str, Any]) -> None:
    if content["author_answer"] and not content["author_anchors"]:
        raise ApplicationError(
            "brief_author_anchors_required",
            "作者底牌至少要拆解为一个已确认的底牌原子项。",
            status_code=422,
        )
    if content["boundary_text"] and not content["creative_constraints"]:
        raise ApplicationError(
            "brief_creative_constraints_required",
            "创作边界至少要拆解为一个已确认的边界原子项。",
            status_code=422,
        )


__all__ = ["require_confirmed_atomics", "validate_brief"]
