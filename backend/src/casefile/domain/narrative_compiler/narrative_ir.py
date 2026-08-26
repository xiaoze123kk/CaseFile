"""Deterministic CaseFile-to-NarrativeIR projection and semantic validation."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from casefile_contracts import CaseFile, NarrativeIR
from pydantic import ValidationError

from casefile.domain.narrative_compiler.foundation import (
    CompilerContractError,
    canonical_json_sha256,
)
from casefile.domain.narrative_compiler.source_refs import (
    build_source_ref,
    validate_source_ref_against_value,
)

NARRATIVE_IR_SCHEMA_ID = "compiler.narrative-ir.v1"
NARRATIVE_IR_PROJECTION_VERSION = "compiler.narrative-ir-projection.v1"
SOURCE_SCHEMA_ID = "casefile.v2"

COLLECTION_TYPES: tuple[tuple[str, str], ...] = (
    ("resolution_specs", "resolution_spec"),
    ("entities", "entity"),
    ("relationships", "relationship"),
    ("locations", "location"),
    ("events", "event"),
    ("information_units", "information_unit"),
    ("claims", "claim"),
    ("hypotheses", "hypothesis"),
    ("reasoning_paths", "reasoning_path"),
    ("constraints", "constraint"),
    ("structure_locks", "structure_lock"),
)


@dataclass(frozen=True, slots=True)
class ReferenceFieldSpec:
    """One explicit CaseFile reference field and its stable IR relation."""

    object_type: str
    path: tuple[str, ...]
    relation: str
    fragment_path: str
    context_key: str | None = None
    context_anchor: str | None = None


def _spec(
    object_type: str,
    path: str,
    relation: str,
    fragment_path: str | None = None,
    *,
    context_key: str | None = None,
    context_anchor: str | None = None,
) -> ReferenceFieldSpec:
    return ReferenceFieldSpec(
        object_type,
        tuple(path.split(".")),
        relation,
        fragment_path or f"/{path.split('.')[0]}",
        context_key,
        context_anchor,
    )


REFERENCE_FIELD_SPECS: tuple[ReferenceFieldSpec, ...] = (
    *(_spec(kind, "source_refs", "object.source") for _, kind in COLLECTION_TYPES),
    _spec("resolution_spec", "accepted_answers", "resolution.accepted_answer"),
    _spec("resolution_spec", "required_claim_refs", "resolution.required_claim"),
    _spec(
        "resolution_spec",
        "conclusion.values.*.value",
        "resolution.conclusion_value",
        "/conclusion",
        context_key="slot_id",
    ),
    _spec(
        "resolution_spec",
        "conclusion.selected_hypothesis_refs",
        "resolution.selected_hypothesis",
        "/conclusion",
    ),
    _spec(
        "resolution_spec",
        "conclusion.supporting_reasoning_path_refs",
        "resolution.supporting_reasoning_path",
        "/conclusion",
    ),
    _spec(
        "entity",
        "knowledge_states.*.as_of_event_ref",
        "entity.knowledge_anchor",
        "/knowledge_states",
        context_anchor="as_of_event_ref",
    ),
    _spec(
        "entity",
        "knowledge_states.*.knows_refs",
        "entity.knows",
        "/knowledge_states",
        context_anchor="as_of_event_ref",
    ),
    _spec(
        "entity",
        "knowledge_states.*.believes_refs",
        "entity.believes",
        "/knowledge_states",
        context_anchor="as_of_event_ref",
    ),
    _spec(
        "entity",
        "knowledge_states.*.false_belief_refs",
        "entity.false_belief",
        "/knowledge_states",
        context_anchor="as_of_event_ref",
    ),
    _spec("relationship", "from_ref", "relationship.from"),
    _spec("relationship", "to_ref", "relationship.to"),
    _spec("location", "parent_ref", "location.parent"),
    _spec("location", "adjacency_refs", "location.adjacent"),
    _spec(
        "location", "travel_times.*.to_ref", "location.travel_to", "/travel_times"
    ),
    _spec("event", "time.anchor_event_ref", "event.relative_anchor", "/time"),
    _spec("event", "participant_refs", "event.participant"),
    _spec("event", "location_ref", "event.location"),
    _spec("event", "cause_refs", "event.cause"),
    _spec("event", "effect_refs", "event.effect"),
    _spec("event", "observed_by_refs", "event.observer"),
    _spec("information_unit", "source_event_ref", "information.source_event"),
    _spec(
        "information_unit", "supports_claim_refs", "information.supports_claim"
    ),
    _spec(
        "information_unit", "refutes_claim_refs", "information.refutes_claim"
    ),
    _spec(
        "information_unit",
        "availability.perspective_refs",
        "information.perspective",
        "/availability",
    ),
    _spec(
        "information_unit",
        "availability.alternative_path_refs",
        "information.alternative_path",
        "/availability",
    ),
    _spec("claim", "support_refs", "claim.support"),
    _spec("claim", "refute_refs", "claim.refute"),
    _spec("claim", "dependency_claim_refs", "claim.dependency"),
    _spec(
        "hypothesis", "target_resolution_ref", "hypothesis.target_resolution"
    ),
    _spec("hypothesis", "required_claim_refs", "hypothesis.required_claim"),
    _spec("hypothesis", "falsifier_refs", "hypothesis.falsifier"),
    _spec(
        "hypothesis", "competing_hypothesis_refs", "hypothesis.competitor"
    ),
    _spec(
        "hypothesis",
        "evidence_assessments.*.information_ref",
        "hypothesis.evidence",
        "/evidence_assessments",
    ),
    _spec("reasoning_path", "target_ref", "reasoning.target"),
    _spec(
        "reasoning_path",
        "steps.*.input_refs",
        "reasoning.input",
        "/steps",
        context_key="step_id",
    ),
    _spec(
        "reasoning_path",
        "steps.*.output_ref",
        "reasoning.output",
        "/steps",
        context_key="step_id",
    ),
    _spec(
        "reasoning_path", "alternative_path_refs", "reasoning.alternative_path"
    ),
    _spec("constraint", "scope_refs", "constraint.scope"),
    _spec("constraint", "conflict_refs", "constraint.conflict"),
    _spec("structure_lock", "object_ref", "structure_lock.object"),
)


def narrative_ir_component_fingerprint(document: dict[str, Any]) -> dict[str, str]:
    """Return the complete semantic invalidation boundary for this component."""

    return {
        "projection_version": NARRATIVE_IR_PROJECTION_VERSION,
        "source_schema_id": SOURCE_SCHEMA_ID,
        "target_schema_id": NARRATIVE_IR_SCHEMA_ID,
        "source_content_hash": canonical_json_sha256(document),
    }


def project_narrative_ir(document: dict[str, Any]) -> NarrativeIR:
    """Project one validated CaseFile document into deterministic NarrativeIR."""

    try:
        CaseFile.model_validate(document)
        ir = NarrativeIR.model_validate(_project_raw(document))
    except ValidationError as error:
        raise CompilerContractError("compiler_narrative_ir_contract_invalid") from error
    return validate_narrative_ir(ir, source_document=document)


def project_narrative_ir_json(document: dict[str, Any]) -> dict[str, Any]:
    """Return the schema-valid JSON representation without losing required nulls."""

    project_narrative_ir(document)
    return _project_raw(document)


def validate_narrative_ir(
    ir: NarrativeIR | dict[str, Any], *, source_document: dict[str, Any]
) -> NarrativeIR:
    """Independently re-prove content, provenance, catalog, and edge semantics."""

    try:
        resolved = ir if isinstance(ir, NarrativeIR) else NarrativeIR.model_validate(ir)
        CaseFile.model_validate(source_document)
    except ValidationError as error:
        raise CompilerContractError("compiler_narrative_ir_contract_invalid") from error
    expected = NarrativeIR.model_validate(_project_raw(source_document))
    if resolved != expected:
        raise CompilerContractError("compiler_narrative_ir_semantic_mismatch")
    _validate_no_runtime_identity(
        resolved.model_dump(mode="json", exclude_none=True)
    )
    return resolved


def _project_raw(document: dict[str, Any]) -> dict[str, Any]:
    case_ref = {"object_type": "casefile", "object_id": document["casefile_id"]}
    objects: dict[str, list[dict[str, Any]]] = {}
    edges: list[dict[str, Any]] = []
    catalog: set[tuple[str, str]] = {("casefile", document["casefile_id"])}
    for collection, object_type in COLLECTION_TYPES:
        envelopes = []
        for value in document[collection]:
            object_ref = {"object_type": object_type, "object_id": value["id"]}
            logical_key = (object_type, value["id"])
            if logical_key in catalog:
                raise CompilerContractError("compiler_narrative_ir_duplicate_object")
            catalog.add(logical_key)
            whole_ref = build_source_ref(object_ref, "", value)
            envelopes.append(
                {
                    "object_ref": object_ref,
                    "source_ref": whole_ref.model_dump(mode="json"),
                    "value": value,
                }
            )
            object_edges, covered = _project_object_edges(object_type, object_ref, value)
            discovered = set(_walk_object_refs(value))
            if discovered != covered:
                raise CompilerContractError("compiler_narrative_ir_reference_unmapped")
            edges.extend(object_edges)
        objects[collection] = envelopes
    for edge in edges:
        target = edge["to_ref"]
        target_key = (target["object_type"], target["object_id"])
        if target["object_type"] != "source_fragment" and target_key not in catalog:
            raise CompilerContractError("compiler_narrative_ir_dangling_reference")
    root_paths = ["", "/title", "/status", "/version", "/brief_ref"]
    if "spatial_scenes" in document:
        root_paths.append("/spatial_scenes")
    root_paths.extend(("/content_notices", "/extensions"))
    root_refs = [
        build_source_ref(
            case_ref,
            path,
            document if path == "" else document[path[1:]],
        ).model_dump(mode="json")
        for path in root_paths
    ]
    result: dict[str, Any] = {
        "schema_id": NARRATIVE_IR_SCHEMA_ID,
        "projection_version": NARRATIVE_IR_PROJECTION_VERSION,
        "source": {
            "casefile_ref": case_ref,
            "source_schema_id": SOURCE_SCHEMA_ID,
            "content_hash": canonical_json_sha256(document),
            "root_source_refs": root_refs,
        },
        "case": {
            "title": document["title"],
            "status": document["status"],
            "version": document["version"],
            "brief_ref": document["brief_ref"],
        },
        "objects": objects,
        "content_notices": document["content_notices"],
        "extensions": document["extensions"],
        "indexes": {"reference_edges": edges},
    }
    if "spatial_scenes" in document:
        result["spatial_scenes"] = document["spatial_scenes"]
    return result


def _project_object_edges(
    object_type: str, object_ref: dict[str, str], value: dict[str, Any]
) -> tuple[list[dict[str, Any]], set[tuple[str | int, ...]]]:
    edges = []
    covered: set[tuple[str | int, ...]] = set()
    for spec in REFERENCE_FIELD_SPECS:
        if spec.object_type != object_type:
            continue
        for terminal, path, context_item, context_ordinal in _resolve_spec(value, spec.path):
            candidates = terminal if isinstance(terminal, list) else [terminal]
            for ordinal, candidate in enumerate(candidates, start=1):
                if not _is_object_ref(candidate):
                    continue
                actual_path = path + ((ordinal - 1,) if isinstance(terminal, list) else ())
                covered.add(actual_path)
                fragment = _resolve_stable_fragment(value, spec.fragment_path)
                source_ref = build_source_ref(object_ref, spec.fragment_path, fragment)
                validate_source_ref_against_value(source_ref, value)
                edge: dict[str, Any] = {
                    "relation": spec.relation,
                    "from_ref": object_ref,
                    "to_ref": candidate,
                    "ordinal": ordinal,
                    "source_ref": source_ref.model_dump(mode="json"),
                }
                if context_ordinal is not None:
                    context: dict[str, Any] = {
                        "container_path": spec.fragment_path,
                        "container_ordinal": context_ordinal,
                    }
                    if spec.context_key and isinstance(context_item, dict):
                        key = context_item.get(spec.context_key)
                        if isinstance(key, str):
                            context["container_key"] = key
                    if spec.context_anchor and isinstance(context_item, dict):
                        anchor = context_item.get(spec.context_anchor)
                        if _is_object_ref(anchor):
                            context["anchor_ref"] = anchor
                    edge["context"] = context
                edges.append(edge)
    return edges, covered


def _resolve_spec(
    value: Any, path: tuple[str, ...]
) -> Iterator[tuple[Any, tuple[str | int, ...], Any | None, int | None]]:
    def walk(
        current: Any,
        remaining: tuple[str, ...],
        actual: tuple[str | int, ...],
        context_item: Any | None,
        context_ordinal: int | None,
    ) -> Iterator[tuple[Any, tuple[str | int, ...], Any | None, int | None]]:
        if not remaining:
            yield current, actual, context_item, context_ordinal
            return
        token, *tail = remaining
        if token == "*":
            if not isinstance(current, list):
                return
            for index, item in enumerate(current):
                yield from walk(
                    item,
                    tuple(tail),
                    actual + (index,),
                    item,
                    index + 1,
                )
        elif isinstance(current, dict) and token in current:
            yield from walk(
                current[token],
                tuple(tail),
                actual + (token,),
                context_item,
                context_ordinal,
            )

    yield from walk(value, path, (), None, None)


def _walk_object_refs(
    value: Any, path: tuple[str | int, ...] = ()
) -> Iterator[tuple[str | int, ...]]:
    if _is_object_ref(value):
        yield path
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_object_refs(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_object_refs(item, path + (index,))


def _resolve_stable_fragment(value: dict[str, Any], pointer: str) -> Any:
    current: Any = value
    for segment in pointer[1:].split("/"):
        if not isinstance(current, dict) or segment not in current:
            raise CompilerContractError("compiler_source_ref_path_invalid")
        current = current[segment]
    return current


def _is_object_ref(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"object_type", "object_id"}
        and isinstance(value["object_type"], str)
        and isinstance(value["object_id"], str)
    )


def _validate_no_runtime_identity(value: dict[str, Any]) -> None:
    forbidden = {
        "compile_run_id",
        "task_run_id",
        "snapshot_id",
        "draft_id",
        "canon_version_id",
        "profile_version",
        "exposure_revision",
        "provider",
        "model_id",
    }
    generated = {
        key: item
        for key, item in value.items()
        if key not in {"objects", "extensions", "content_notices", "spatial_scenes"}
    }

    def scan(item: Any) -> None:
        if isinstance(item, dict):
            if forbidden.intersection(item):
                raise CompilerContractError("compiler_narrative_ir_runtime_identity_forbidden")
            for nested in item.values():
                scan(nested)
        elif isinstance(item, list):
            for nested in item:
                scan(nested)

    scan(generated)


__all__ = [
    "COLLECTION_TYPES",
    "NARRATIVE_IR_PROJECTION_VERSION",
    "NARRATIVE_IR_SCHEMA_ID",
    "REFERENCE_FIELD_SPECS",
    "SOURCE_SCHEMA_ID",
    "narrative_ir_component_fingerprint",
    "project_narrative_ir",
    "project_narrative_ir_json",
    "validate_narrative_ir",
]
