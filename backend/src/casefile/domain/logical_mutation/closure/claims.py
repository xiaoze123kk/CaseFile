"""Deterministic Claim closure for policy v2."""

from __future__ import annotations

from casefile.domain.logical_mutation.closure.common import issue
from casefile.domain.logical_mutation.closure.context import ClosureContext
from casefile.domain.logical_mutation.models import ClosureIssue

_DEPENDENCY_INCOMPATIBLE = {"refuted", "disputed", "unresolved", "unsupported"}


def evaluate_claim_rules(context: ClosureContext) -> list[ClosureIssue]:
    result: list[ClosureIssue] = []
    claims = context.candidate_index.objects_by_id
    for claim in context.candidate.get("claims", []):
        claim_id = str(claim["id"])
        supporters = set(context.candidate_index.supporters_by_claim.get(claim_id, ()))
        refuters = set(context.candidate_index.refuters_by_claim.get(claim_id, ()))
        status = claim.get("status")
        if status == "supported" and not supporters:
            result.append(
                issue(
                    context,
                    "claim_supported_without_support",
                    "repair_required",
                    "Claim 缺少声明的支撑",
                    "标记为 supported 的 Claim 至少需要一项支撑信息。",
                    (claim_id,),
                    ("attach_support", "downgrade_claim_status"),
                )
            )
        if status == "refuted" and not refuters:
            result.append(
                issue(
                    context,
                    "claim_refuted_without_refutation",
                    "repair_required",
                    "Claim 缺少声明的反证",
                    "标记为 refuted 的 Claim 至少需要一项反驳信息。",
                    (claim_id,),
                    ("attach_refutation", "change_claim_status"),
                )
            )
        if status == "disputed" and (not supporters or not refuters):
            result.append(
                issue(
                    context,
                    "claim_disputed_without_two_sided_evidence",
                    "warning",
                    "争议 Claim 的双侧证据不完整",
                    "标记为 disputed 的 Claim 尚未同时声明支撑和反驳信息。",
                    (claim_id,),
                )
            )
        overlap = tuple(sorted(supporters & refuters))
        if overlap:
            result.append(
                issue(
                    context,
                    "claim_support_refute_overlap",
                    "warning",
                    "同一信息同时支撑并反驳 Claim",
                    "请确认该双向作用是创作意图，而不是引用录入错误。",
                    (claim_id, *overlap),
                )
            )
        incompatible = tuple(
            sorted(
                str(ref["object_id"])
                for ref in claim.get("dependency_claim_refs", [])
                if claims.get(str(ref["object_id"]), {}).get("status")
                in _DEPENDENCY_INCOMPATIBLE
            )
        )
        if status in {"partially_supported", "supported"} and incompatible:
            result.append(
                issue(
                    context,
                    "claim_dependency_incompatible",
                    "repair_required",
                    "Claim 依赖与当前状态不一致",
                    "已成立的 Claim 不能依赖当前未成立的必要 Claim。",
                    (claim_id, *incompatible),
                    ("repair_dependency_claim", "change_claim_status"),
                )
            )
    return result
