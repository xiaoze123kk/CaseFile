"""Evidence binding, author requirements and the final revision boundary."""

import pytest

from casefile.agent_runtime.novel_chapter_review import validate_chapter_review
from casefile.agent_runtime.novel_collaboration import collaboration_prompt, prepare_context


def test_requirements_are_frozen_without_inventing_constraints():
    requirements = {"preserve": "保留门开了这一动作", "allow_changes": "允许调整对白"}
    request = {
        "mode": "rewrite",
        "scope": "chapter_rewrite",
        "chapter_id": "c1",
        "anchor": None,
        "instruction": "紧凑一点",
        "requirements": requirements,
    }
    payload = prepare_context([{"id": "c1", "title": "雨", "text": "😀门开了。"}], request, [])
    assert payload["requirements"] == requirements
    assert payload["editorial_policy"] == "chapter-prose-v1"
    assert collaboration_prompt(payload).version == "novel-chapter-rewrite-v2"
    assert collaboration_prompt({"scope": "chapter_rewrite"}).version == "novel-chapter-rewrite-v1"


def test_requirements_are_not_silently_ignored_outside_chapter_rewrite():
    request = {
        "mode": "rewrite",
        "scope": "chapter",
        "chapter_id": "c1",
        "anchor": None,
        "instruction": "改写",
        "requirements": {"preserve": "门打开", "allow_changes": ""},
    }
    with pytest.raises(ValueError, match="requirements_scope_invalid"):
        prepare_context([{"id": "c1", "title": "雨", "text": "门开了。"}], request, [])


@pytest.mark.parametrize("invalid", ["source", "candidate", "major", "exhausted", "plan"])
def test_invalid_review_evidence_and_revision_decisions_are_rejected(invalid):
    payload = {"target": "门开了。", "candidate": {"text": "门关了。"}, "revision_exhausted": False}
    finding = {
        "category": "preservation",
        "severity": "warning",
        "message": "动作变化。",
        "source_quote": "门开了。",
        "candidate_quote": "门关了。",
        "suggestion": "保留原动作",
    }
    review = {
        "summary": "需要调整",
        "action": "revise",
        "revision_plan": "恢复开门",
        "findings": [finding],
    }
    if invalid == "source":
        finding["source_quote"] = "不存在"
    if invalid == "candidate":
        finding["candidate_quote"] = "不存在"
    if invalid == "major":
        finding["severity"] = "major"
        review["action"] = "accept"
    if invalid == "exhausted":
        payload["revision_exhausted"] = True
    if invalid == "plan":
        review["revision_plan"] = ""
    with pytest.raises(ValueError):
        validate_chapter_review(review, payload)


def test_exhausted_review_can_leave_a_literary_decision_to_author():
    review = {
        "summary": "仍有含义变化需作者判断",
        "action": "needs_author",
        "revision_plan": "",
        "findings": [
            {
                "category": "meaning",
                "severity": "major",
                "message": "结局变化",
                "source_quote": "",
                "candidate_quote": "",
                "suggestion": "请作者确认",
            }
        ],
    }
    assert (
        validate_chapter_review(
            review, {"target": "原稿", "candidate": {"text": "新稿"}, "revision_exhausted": True}
        )
        == review
    )


def test_change_evidence_exposes_near_copy_without_literary_gate():
    from casefile.agent_runtime.novel_chapter_review import (
        chapter_change_evidence,
        chapter_review_prompt,
    )

    source = "雨" * 100 + "😀门开了。"
    candidate = source[:-1] + "！"
    evidence = chapter_change_evidence(source, candidate)
    assert evidence["source_chars"] == len(source)
    assert evidence["candidate_chars"] == len(candidate)
    assert evidence["unchanged_chars"] == len(source) - 1
    assert evidence["changes"] == [{"before": "。", "after": "！"}]
    assert (
        chapter_review_prompt({"editorial_policy": "chapter-editorial-v2"}).version
        == "novel-chapter-review-v2"
    )
    assert (
        chapter_review_prompt({"editorial_policy": "chapter-editorial-v1"}).version
        == "novel-chapter-review-v1"
    )
