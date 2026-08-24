"""ReasoningPath and Resolution closure for policy v2."""

from __future__ import annotations

from casefile.domain.logical_mutation.closure.common import issue
from casefile.domain.logical_mutation.closure.context import ClosureContext
from casefile.domain.logical_mutation.models import ClosureIssue

_CLAIM_INCOMPATIBLE = {"unsupported", "refuted", "disputed", "unresolved"}


def _ref_id(value: object) -> str | None:
    if not isinstance(value, dict) or not value.get("object_id"):
        return None
    return str(value["object_id"])


def evaluate_reasoning_rules(context: ClosureContext) -> list[ClosureIssue]:
    result: list[ClosureIssue] = []
    index = context.candidate_index
    for path in context.candidate.get("reasoning_paths", []):
        path_id = str(path["id"])
        health = index.path_health_by_id[path_id]
        target_id = _ref_id(path.get("target_ref"))
        if health.target_type not in {"claim", "hypothesis", "resolution_spec"}:
            result.append(
                issue(
                    context,
                    "reasoning_path_target_type_invalid",
                    "hard_invariant",
                    "推理路径目标类型非法",
                    "推理路径只能指向 Claim、Hypothesis 或 ResolutionSpec。",
                    (path_id, *((target_id,) if target_id is not None else ())),
                    object_roles=(
                        "path",
                        *(("related",) if target_id is not None else ()),
                    ),
                )
            )
        if health.invalid_output_ids:
            result.append(
                issue(
                    context,
                    "reasoning_step_output_type_invalid",
                    "hard_invariant",
                    "推理步骤输出类型非法",
                    "推理步骤只能输出 Claim 或 Hypothesis。",
                    (path_id, *health.invalid_output_ids),
                    object_roles=(
                        "path",
                        *("related",) * len(health.invalid_output_ids),
                    ),
                )
            )
        if health.required_for_resolution and not health.information_grounded:
            result.append(
                issue(
                    context,
                    "reasoning_required_path_without_information_input",
                    "repair_required",
                    "必要推理路径缺少信息输入",
                    "标记为解答所需的推理路径必须至少使用一项 InformationUnit。",
                    (path_id,),
                    ("attach_information_input", "make_path_optional"),
                    object_roles=("path",),
                )
            )
        if health.required_for_resolution and health.incompatible_claim_input_ids:
            result.append(
                issue(
                    context,
                    "reasoning_required_path_incompatible_claim_input",
                    "repair_required",
                    "必要推理路径使用了未成立的 Claim",
                    "标记为解答所需的推理路径不能依赖当前未成立的 Claim 输入。",
                    (path_id, *health.incompatible_claim_input_ids),
                    ("repair_input_claim", "make_path_optional"),
                    object_roles=(
                        "path",
                        *("prerequisite",) * len(health.incompatible_claim_input_ids),
                    ),
                )
            )
    return result


def evaluate_resolution_rules(context: ClosureContext) -> list[ClosureIssue]:
    result: list[ClosureIssue] = []
    index = context.candidate_index
    for resolution in context.candidate.get("resolution_specs", []):
        conclusion = resolution.get("conclusion") or {}
        if conclusion.get("outcome") != "answer":
            continue
        resolution_id = str(resolution["id"])
        incompatible = tuple(
            sorted(
                ref_id
                for ref in resolution.get("required_claim_refs", [])
                if (ref_id := _ref_id(ref)) is not None
                and index.objects_by_id.get(ref_id, {}).get("status")
                in _CLAIM_INCOMPATIBLE
            )
        )
        if incompatible:
            result.append(
                issue(
                    context,
                    "resolution_required_claim_incompatible",
                    "repair_required",
                    "结论的必要 Claim 未成立",
                    "答案结论不能依赖当前未成立的必要 Claim。",
                    (resolution_id, *incompatible),
                    ("repair_required_claim", "make_resolution_undetermined"),
                    object_roles=(
                        "resolution",
                        *("prerequisite",) * len(incompatible),
                    ),
                )
            )
        unhealthy = tuple(
            sorted(
                ref_id
                for ref in conclusion.get("supporting_reasoning_path_refs", [])
                if (ref_id := _ref_id(ref)) is not None
                and (
                    not index.path_health_by_id.get(ref_id)
                    or not index.path_health_by_id[ref_id].healthy_for_resolution
                )
            )
        )
        if unhealthy:
            result.append(
                issue(
                    context,
                    "resolution_basis_path_unhealthy",
                    "repair_required",
                    "结论依据路径不健康",
                    (
                        "答案结论使用了目标类型、信息接地、Claim 输入状态"
                        "或 required 状态不满足要求的路径。"
                    ),
                    (resolution_id, *unhealthy),
                    ("repair_reasoning_path", "make_resolution_undetermined"),
                    object_roles=(
                        "resolution",
                        *("path",) * len(unhealthy),
                    ),
                )
            )
    return result
