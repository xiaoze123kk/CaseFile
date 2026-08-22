"""Deterministic logical-closure rules for candidate CaseFile documents."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from casefile.domain.logical_mutation.graph import LogicalGraph
from casefile.domain.logical_mutation.models import (
    ClosureIssue,
    CreateObject,
    DeleteObject,
    MutationSet,
    UpdateField,
)
from casefile.domain.logical_mutation.policy import cycle_policies

_ROOT_TYPES = {"resolution_spec", "structure_lock"}
_SKELETON_TYPES = {"resolution_spec", "hypothesis", "reasoning_path", "claim", "structure_lock"}
_CLAIM_INCOMPATIBLE = {"unsupported", "refuted", "disputed", "unresolved"}


def evaluate_closure_rules(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    baseline_graph: LogicalGraph,
    candidate_graph: LogicalGraph,
    mutation_set: MutationSet,
) -> tuple[ClosureIssue, ...]:
    issues: list[ClosureIssue] = []
    issues.extend(_reciprocity_issues(candidate, mutation_set))
    issues.extend(_cycle_issues(candidate_graph))
    issues.extend(_critical_support_issues(candidate, mutation_set))
    issues.extend(_hypothesis_requirement_issues(candidate, mutation_set))
    issues.extend(_resolution_basis_issues(candidate, mutation_set))
    issues.extend(_new_object_integration_issues(candidate_graph, mutation_set))
    issues.extend(_structure_lock_issues(candidate, mutation_set))
    issues.extend(_root_delete_issues(baseline_graph, mutation_set))
    del baseline
    return tuple(sorted(issues, key=lambda issue: (issue.rule_code, issue.object_ids)))


def _reciprocity_issues(
    document: Mapping[str, Any], mutation_set: MutationSet
) -> list[ClosureIssue]:
    support_by_info = _relation_pairs(document.get("information_units", []), "supports_claim_refs")
    support_by_claim = {
        (target, source)
        for source, target in _relation_pairs(document.get("claims", []), "support_refs")
    }
    refute_by_info = _relation_pairs(document.get("information_units", []), "refutes_claim_refs")
    refute_by_claim = {
        (target, source)
        for source, target in _relation_pairs(document.get("claims", []), "refute_refs")
    }
    result = []
    for code, mismatch in (
        ("evidence_support_reciprocity_violation", support_by_info ^ support_by_claim),
        ("evidence_refute_reciprocity_violation", refute_by_info ^ refute_by_claim),
    ):
        for info_id, claim_id in sorted(mismatch):
            result.append(
                _issue(
                    code,
                    "hard_invariant",
                    "证据关系投影不一致",
                    "信息与 Claim 的双向关系必须一致。",
                    (info_id, claim_id),
                    mutation_set,
                )
            )
    return result


def _cycle_issues(graph: LogicalGraph) -> list[ClosureIssue]:
    result = []
    for policy in cycle_policies():
        for cycle in graph.cycles(policy.relation):
            result.append(
                ClosureIssue(
                    policy.cycle_rule_code or "logical_relation_cycle",
                    "hard_invariant",
                    policy.cycle_title or "逻辑关系形成循环",
                    "该依赖无法得到确定的闭包顺序。",
                    cycle.object_ids,
                    dependency_path=cycle.object_ids,
                )
            )
    return result


def _critical_support_issues(
    document: Mapping[str, Any], mutation_set: MutationSet
) -> list[ClosureIssue]:
    result = []
    for claim in document.get("claims", []):
        if (
            claim.get("materiality") == "critical"
            and claim.get("status") == "supported"
            and not claim.get("support_refs")
        ):
            result.append(
                _issue(
                    "critical_claim_support_lost",
                    "repair_required",
                    "关键 Claim 失去支撑",
                    "标记为 supported 的关键 Claim 至少需要一项有效支撑。",
                    (str(claim["id"]),),
                    mutation_set,
                    ("attach_alternative_evidence", "downgrade_claim_status"),
                )
            )
    return result


def _hypothesis_requirement_issues(
    document: Mapping[str, Any], mutation_set: MutationSet
) -> list[ClosureIssue]:
    claims = {item["id"]: item for item in document.get("claims", [])}
    result = []
    for hypothesis in document.get("hypotheses", []):
        if hypothesis.get("status") not in {"supported", "accepted"}:
            continue
        invalid = [
            str(ref["object_id"])
            for ref in hypothesis.get("required_claim_refs", [])
            if claims.get(ref["object_id"], {}).get("status") in _CLAIM_INCOMPATIBLE
        ]
        if invalid:
            result.append(
                _issue(
                    "hypothesis_required_claim_incompatible",
                    "repair_required",
                    "假设的必要 Claim 未成立",
                    "supported/accepted 假设不能依赖未成立的必要 Claim。",
                    (str(hypothesis["id"]), *sorted(invalid)),
                    mutation_set,
                    ("repair_required_claim", "change_hypothesis_status"),
                )
            )
    return result


def _resolution_basis_issues(
    document: Mapping[str, Any], mutation_set: MutationSet
) -> list[ClosureIssue]:
    hypotheses = {item["id"]: item for item in document.get("hypotheses", [])}
    result = []
    for resolution in document.get("resolution_specs", []):
        conclusion = resolution.get("conclusion")
        if not conclusion or conclusion.get("outcome") != "answer":
            continue
        invalid = [
            str(ref["object_id"])
            for ref in conclusion.get("selected_hypothesis_refs", [])
            if hypotheses.get(ref["object_id"], {}).get("status") not in {"supported", "accepted"}
        ]
        if invalid:
            result.append(
                _issue(
                    "resolution_basis_weakened",
                    "repair_required",
                    "结论依据已经减弱",
                    "答案结论选择了当前未达到 supported/accepted 状态的假设。",
                    (str(resolution["id"]), *sorted(invalid)),
                    mutation_set,
                    ("repair_hypothesis", "make_resolution_undetermined"),
                )
            )
    return result


def _new_object_integration_issues(
    graph: LogicalGraph, mutation_set: MutationSet
) -> list[ClosureIssue]:
    result = []
    for operation in mutation_set.operations:
        if not isinstance(operation, CreateObject) or graph.degree(operation.object_id) > 0:
            continue
        object_type = graph.object_type(operation.object_id) or "unknown"
        level = (
            "repair_required"
            if mutation_set.actor != "author" or object_type in _SKELETON_TYPES
            else "warning"
        )
        result.append(
            _issue(
                "new_object_not_integrated",
                level,
                "新对象尚未接入卷宗",
                "新对象没有进入任何有效语义关系。",
                (operation.object_id,),
                mutation_set,
                ("connect_object",),
            )
        )
    return result


def _root_delete_issues(graph: LogicalGraph, mutation_set: MutationSet) -> list[ClosureIssue]:
    if mutation_set.mode == "restructure":
        return []
    result = []
    for operation in mutation_set.operations:
        if not isinstance(operation, DeleteObject):
            continue
        object_type = graph.object_type(operation.object_id)
        required_path = object_type == "reasoning_path" and any(
            edge.prerequisite_id == operation.object_id and edge.relation == "basis_of_resolution"
            for edge in graph.edges
        )
        selected_hypothesis = object_type == "hypothesis" and any(
            edge.prerequisite_id == operation.object_id
            and edge.relation == "selected_by_resolution"
            for edge in graph.edges
        )
        if object_type in _ROOT_TYPES or required_path or selected_hypothesis:
            result.append(
                _issue(
                    "root_structure_delete_requires_explicit_restructure",
                    "hard_invariant",
                    "删除推理骨架需要重构模式",
                    "普通修改不能删除当前结论或结构锁依赖的核心对象。",
                    (operation.object_id,),
                    mutation_set,
                )
            )
    return result


def _structure_lock_issues(
    document: Mapping[str, Any], mutation_set: MutationSet
) -> list[ClosureIssue]:
    locks_by_target: dict[str, list[Mapping[str, Any]]] = {}
    for lock in document.get("structure_locks", []):
        reference = lock.get("object_ref")
        if isinstance(reference, Mapping):
            locks_by_target.setdefault(str(reference.get("object_id", "")), []).append(lock)
    result: list[ClosureIssue] = []
    for operation in mutation_set.operations:
        locks = locks_by_target.get(operation.object_id, [])
        for lock in locks:
            locked_paths = tuple(str(path) for path in lock.get("field_paths", []))
            conflicts = isinstance(operation, DeleteObject) or (
                isinstance(operation, UpdateField)
                and any(
                    operation.field_path == path
                    or operation.field_path.startswith(f"{path}/")
                    for path in locked_paths
                )
            )
            if conflicts:
                result.append(
                    _issue(
                        "structure_lock_conflict",
                        "hard_invariant",
                        "修改与结构锁冲突",
                        "结构锁保护的对象或字段不能通过 MutationSet 绕过。",
                        (operation.object_id, str(lock["id"])),
                        mutation_set,
                    )
                )
    return result


def _relation_pairs(values: Sequence[Mapping[str, Any]], field: str) -> set[tuple[str, str]]:
    return {
        (str(item["id"]), str(ref["object_id"])) for item in values for ref in item.get(field, [])
    }


def _issue(
    code: str,
    level: str,
    title: str,
    message: str,
    object_ids: tuple[str, ...],
    mutation_set: MutationSet,
    repair_kinds: tuple[str, ...] = (),
) -> ClosureIssue:
    targets = set(object_ids)
    caused = tuple(
        operation.operation_id
        for operation in mutation_set.operations
        if operation.object_id in targets
    )
    return ClosureIssue(code, level, title, message, object_ids, caused, repair_kinds=repair_kinds)  # type: ignore[arg-type]
