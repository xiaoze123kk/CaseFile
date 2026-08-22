"""Precomputed immutable facts used by closure rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

_COLLECTION_TYPES = {
    "resolution_specs": "resolution_spec",
    "entities": "entity",
    "relationships": "relationship",
    "locations": "location",
    "events": "event",
    "information_units": "information_unit",
    "claims": "claim",
    "hypotheses": "hypothesis",
    "reasoning_paths": "reasoning_path",
    "constraints": "constraint",
    "structure_locks": "structure_lock",
}


@dataclass(frozen=True, slots=True)
class ReasoningPathHealth:
    path_id: str
    target_type: str | None
    information_grounded: bool
    invalid_output_ids: tuple[str, ...]
    required_for_resolution: bool

    @property
    def healthy_for_resolution(self) -> bool:
        return bool(
            self.required_for_resolution
            and self.information_grounded
            and self.target_type in {"claim", "hypothesis", "resolution_spec"}
            and not self.invalid_output_ids
        )


@dataclass(frozen=True, slots=True)
class ClosureIndex:
    objects_by_id: Mapping[str, Mapping[str, Any]]
    object_types: Mapping[str, str]
    supporters_by_claim: Mapping[str, tuple[str, ...]]
    refuters_by_claim: Mapping[str, tuple[str, ...]]
    dependent_claims_by_claim: Mapping[str, tuple[str, ...]]
    hypotheses_by_resolution: Mapping[str, tuple[str, ...]]
    paths_by_target: Mapping[str, tuple[str, ...]]
    information_inputs_by_path: Mapping[str, tuple[str, ...]]
    assessments_by_hypothesis: Mapping[str, tuple[Mapping[str, Any], ...]]
    matrix_scope_by_hypothesis: Mapping[str, tuple[str, ...]]
    path_health_by_id: Mapping[str, ReasoningPathHealth]


def _freeze_sets(values: dict[str, set[str]]) -> Mapping[str, tuple[str, ...]]:
    return MappingProxyType(
        {key: tuple(sorted(items)) for key, items in sorted(values.items())}
    )


def _ref_id(value: Any) -> str | None:
    if not isinstance(value, Mapping) or not value.get("object_id"):
        return None
    return str(value["object_id"])


def build_closure_index(document: Mapping[str, Any]) -> ClosureIndex:
    objects: dict[str, Mapping[str, Any]] = {}
    object_types: dict[str, str] = {}
    for collection, object_type in _COLLECTION_TYPES.items():
        for item in document.get(collection, []):
            object_id = str(item["id"])
            objects[object_id] = item
            object_types[object_id] = object_type

    supporters: dict[str, set[str]] = {}
    refuters: dict[str, set[str]] = {}
    dependent_claims: dict[str, set[str]] = {}
    for claim in document.get("claims", []):
        claim_id = str(claim["id"])
        supporters[claim_id] = {
            str(ref["object_id"]) for ref in claim.get("support_refs", [])
        }
        refuters[claim_id] = {
            str(ref["object_id"]) for ref in claim.get("refute_refs", [])
        }
        for ref in claim.get("dependency_claim_refs", []):
            dependent_claims.setdefault(str(ref["object_id"]), set()).add(claim_id)

    hypotheses_by_resolution: dict[str, set[str]] = {}
    assessments: dict[str, tuple[Mapping[str, Any], ...]] = {}
    required_claims_by_hypothesis: dict[str, set[str]] = {}
    for hypothesis in document.get("hypotheses", []):
        hypothesis_id = str(hypothesis["id"])
        resolution_id = _ref_id(hypothesis.get("target_resolution_ref"))
        if resolution_id is not None:
            hypotheses_by_resolution.setdefault(resolution_id, set()).add(hypothesis_id)
        assessments[hypothesis_id] = tuple(hypothesis.get("evidence_assessments", []))
        required_claims_by_hypothesis[hypothesis_id] = {
            required_id
            for ref in hypothesis.get("required_claim_refs", [])
            if (required_id := _ref_id(ref)) is not None
        }

    paths_by_target: dict[str, set[str]] = {}
    information_inputs: dict[str, set[str]] = {}
    health: dict[str, ReasoningPathHealth] = {}
    for path in document.get("reasoning_paths", []):
        path_id = str(path["id"])
        target_id = _ref_id(path.get("target_ref"))
        if target_id is not None:
            paths_by_target.setdefault(target_id, set()).add(path_id)
        inputs: set[str] = set()
        invalid_outputs: set[str] = set()
        for step in path.get("steps", []):
            for ref in step.get("input_refs", []):
                input_id = _ref_id(ref)
                if input_id is None:
                    continue
                if object_types.get(input_id) == "information_unit":
                    inputs.add(input_id)
            output_id = _ref_id(step.get("output_ref"))
            if output_id is not None and object_types.get(output_id) not in {
                "claim",
                "hypothesis",
            }:
                invalid_outputs.add(output_id)
        information_inputs[path_id] = inputs
        health[path_id] = ReasoningPathHealth(
            path_id=path_id,
            target_type=object_types.get(target_id) if target_id is not None else None,
            information_grounded=bool(inputs),
            invalid_output_ids=tuple(sorted(invalid_outputs)),
            required_for_resolution=bool(path.get("required_for_resolution")),
        )

    matrix_scope: dict[str, set[str]] = {}
    for hypothesis_ids in hypotheses_by_resolution.values():
        group_scope: set[str] = set()
        claim_ids = {
            claim_id
            for hypothesis_id in hypothesis_ids
            for claim_id in required_claims_by_hypothesis.get(hypothesis_id, set())
        }
        for target_id in {*hypothesis_ids, *claim_ids}:
            for path_id in paths_by_target.get(target_id, set()):
                group_scope.update(information_inputs.get(path_id, set()))
        for hypothesis_id in hypothesis_ids:
            matrix_scope[hypothesis_id] = set(group_scope)

    return ClosureIndex(
        objects_by_id=MappingProxyType(objects),
        object_types=MappingProxyType(object_types),
        supporters_by_claim=_freeze_sets(supporters),
        refuters_by_claim=_freeze_sets(refuters),
        dependent_claims_by_claim=_freeze_sets(dependent_claims),
        hypotheses_by_resolution=_freeze_sets(hypotheses_by_resolution),
        paths_by_target=_freeze_sets(paths_by_target),
        information_inputs_by_path=_freeze_sets(information_inputs),
        assessments_by_hypothesis=MappingProxyType(dict(sorted(assessments.items()))),
        matrix_scope_by_hypothesis=_freeze_sets(matrix_scope),
        path_health_by_id=MappingProxyType(dict(sorted(health.items()))),
    )
