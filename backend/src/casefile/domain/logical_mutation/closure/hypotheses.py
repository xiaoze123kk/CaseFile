"""Hypothesis evidence-assessment closure for policy v2."""

from __future__ import annotations

from casefile.domain.logical_mutation.closure.common import issue
from casefile.domain.logical_mutation.closure.context import ClosureContext
from casefile.domain.logical_mutation.models import ClosureIssue

_MATERIAL_STRENGTHS = {"moderate", "strong"}


def _ref_id(value: object) -> str | None:
    if not isinstance(value, dict) or not value.get("object_id"):
        return None
    return str(value["object_id"])


def evaluate_hypothesis_rules(context: ClosureContext) -> list[ClosureIssue]:
    result: list[ClosureIssue] = []
    index = context.candidate_index
    for hypothesis in context.candidate.get("hypotheses", []):
        hypothesis_id = str(hypothesis["id"])
        assessments = index.assessments_by_hypothesis.get(hypothesis_id, ())
        material_support = any(
            item.get("effect") == "supports"
            and item.get("strength") in _MATERIAL_STRENGTHS
            for item in assessments
        )
        material_contradiction = any(
            item.get("effect") == "contradicts"
            and item.get("strength") in _MATERIAL_STRENGTHS
            for item in assessments
        )
        if (
            hypothesis.get("status") in {"supported", "accepted"}
            and material_contradiction
            and not material_support
        ):
            result.append(
                issue(
                    context,
                    "hypothesis_assessment_status_conflict",
                    "repair_required",
                    "假设状态与证据评估冲突",
                    "假设声明为 supported/accepted，但矩阵只有实质反证而没有实质支持。",
                    (hypothesis_id,),
                    ("review_assessments", "change_hypothesis_status"),
                )
            )

        resolution_id = _ref_id(hypothesis.get("target_resolution_ref"))
        group = set(index.hypotheses_by_resolution.get(resolution_id or "", ()))
        declared = {
            ref_id
            for ref in hypothesis.get("competing_hypothesis_refs", [])
            if (ref_id := _ref_id(ref)) is not None
        }
        expected_competitors = group - {hypothesis_id}
        if len(group) > 1 and declared != expected_competitors:
            result.append(
                issue(
                    context,
                    "competing_hypothesis_group_incomplete",
                    "warning",
                    "竞争假设集合尚未闭合",
                    "该核心问题下的竞争假设引用尚未覆盖完整同组集合。",
                    (hypothesis_id, *tuple(sorted(expected_competitors ^ declared))),
                )
            )

        scope = set(index.matrix_scope_by_hypothesis.get(hypothesis_id, ()))
        assessed = {
            ref_id
            for item in assessments
            if (ref_id := _ref_id(item.get("information_ref"))) is not None
        }
        missing = tuple(sorted(scope - assessed))
        unscoped = tuple(sorted(assessed - scope)) if scope else ()
        if len(group) > 1 and missing:
            result.append(
                issue(
                    context,
                    "missing_evidence_assessment",
                    "warning",
                    "竞争矩阵存在未评估信息",
                    "该假设尚未评估同一核心问题推理路径使用的全部信息。",
                    (hypothesis_id, *missing),
                )
            )
        if unscoped:
            result.append(
                issue(
                    context,
                    "unscoped_evidence_assessment",
                    "warning",
                    "证据评估不在当前矩阵范围",
                    "该评估引用的信息没有进入同组假设的推理路径范围。",
                    (hypothesis_id, *unscoped),
                )
            )
    return result
