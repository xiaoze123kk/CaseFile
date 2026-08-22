"""Type-specific integration rules for newly created v2 objects."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from casefile.domain.logical_mutation.closure.common import issue
from casefile.domain.logical_mutation.closure.context import ClosureContext
from casefile.domain.logical_mutation.models import ClosureIssue, CreateObject

_V1_SKELETON_TYPES = {
    "resolution_spec",
    "hypothesis",
    "reasoning_path",
    "claim",
    "structure_lock",
}


def _reference_ids(values: list[Mapping[str, Any]]) -> set[str]:
    return {str(value["object_id"]) for value in values if value.get("object_id")}


def _ref_id(value: object) -> str | None:
    if not isinstance(value, Mapping) or not value.get("object_id"):
        return None
    return str(value["object_id"])


def _semantic_integration(context: ClosureContext) -> dict[str, set[str]]:
    document = context.candidate
    result: dict[str, set[str]] = {
        "information_unit": set(),
        "claim": set(),
        "hypothesis": set(),
        "reasoning_path": set(),
    }
    for info in document.get("information_units", []):
        if info.get("supports_claim_refs") or info.get("refutes_claim_refs"):
            result["information_unit"].add(str(info["id"]))
    for claim in document.get("claims", []):
        claim_id = str(claim["id"])
        if (
            claim.get("support_refs")
            or claim.get("refute_refs")
            or claim.get("dependency_claim_refs")
            or context.candidate_index.dependent_claims_by_claim.get(claim_id)
        ):
            result["claim"].add(claim_id)
    for hypothesis in document.get("hypotheses", []):
        hypothesis_id = str(hypothesis["id"])
        if hypothesis.get("required_claim_refs") or hypothesis.get("evidence_assessments"):
            result["hypothesis"].add(hypothesis_id)
    for path in document.get("reasoning_paths", []):
        path_id = str(path["id"])
        semantic_inputs = any(
            context.candidate_index.object_types.get(ref_id)
            in {"information_unit", "claim", "hypothesis"}
            for step in path.get("steps", [])
            for ref in step.get("input_refs", [])
            if (ref_id := _ref_id(ref)) is not None
        )
        if semantic_inputs:
            result["reasoning_path"].add(path_id)
        for step in path.get("steps", []):
            for ref in step.get("input_refs", []):
                object_id = _ref_id(ref)
                if object_id is None:
                    continue
                object_type = context.candidate_index.object_types.get(object_id)
                if object_type in result:
                    result[object_type].add(object_id)
            output_id = _ref_id(step.get("output_ref"))
            if output_id is None:
                continue
            output_type = context.candidate_index.object_types.get(output_id)
            if output_type in result:
                result[output_type].add(output_id)
        target_id = _ref_id(path.get("target_ref"))
        if target_id is None:
            continue
        target_type = context.candidate_index.object_types.get(target_id)
        if target_type in result:
            result[target_type].add(target_id)
    for hypothesis in document.get("hypotheses", []):
        for ref in hypothesis.get("required_claim_refs", []):
            if (object_id := _ref_id(ref)) is not None:
                result["claim"].add(object_id)
        for assessment in hypothesis.get("evidence_assessments", []):
            if (object_id := _ref_id(assessment.get("information_ref"))) is not None:
                result["information_unit"].add(object_id)
    for resolution in document.get("resolution_specs", []):
        for ref in resolution.get("required_claim_refs", []):
            if (object_id := _ref_id(ref)) is not None:
                result["claim"].add(object_id)
        conclusion = resolution.get("conclusion") or {}
        result["hypothesis"].update(
            _reference_ids(conclusion.get("selected_hypothesis_refs", []))
        )
        result["reasoning_path"].update(
            _reference_ids(conclusion.get("supporting_reasoning_path_refs", []))
        )
    return result


def evaluate_integration_rules(context: ClosureContext) -> list[ClosureIssue]:
    result: list[ClosureIssue] = []
    integrated = _semantic_integration(context)
    for operation in context.mutation_set.operations:
        if not isinstance(operation, CreateObject):
            continue
        object_id = operation.object_id
        object_type = context.candidate_index.object_types.get(object_id, "unknown")
        value = context.candidate_index.objects_by_id.get(object_id, {})
        connected = object_id in integrated.get(object_type, set())
        if object_type not in integrated:
            connected = context.candidate_graph.degree(object_id) > 0
        if connected:
            if (
                object_type == "reasoning_path"
                and value.get("required_for_resolution")
                and not any(
                    edge.prerequisite_id == object_id
                    and edge.relation == "basis_of_resolution"
                    for edge in context.candidate_graph.edges
                )
            ):
                connected = False
            else:
                continue

        asserted_complete = bool(
            (
                object_type == "information_unit"
                and value.get("classification") in {"key", "supporting"}
            )
            or (object_type == "claim" and value.get("status") == "supported")
            or (
                object_type == "hypothesis"
                and value.get("status") in {"supported", "accepted"}
            )
            or (object_type == "reasoning_path" and value.get("required_for_resolution"))
        )
        if object_type in integrated:
            level = "repair_required" if asserted_complete else "warning"
        else:
            level = (
                "repair_required"
                if context.mutation_set.actor != "author"
                or object_type in _V1_SKELETON_TYPES
                else "warning"
            )
        code = f"new_{object_type}_not_reasoning_integrated"
        result.append(
            issue(
                context,
                code,
                level,
                "新对象尚未接入推理结构",
                "该对象声明的完成状态与当前 Evidence/Claim/Reasoning 关系不一致。",
                (object_id,),
                ("connect_object",),
            )
        )
    return result
