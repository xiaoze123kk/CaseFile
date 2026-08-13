"""Deterministic local-key linker and current CaseFile compiler."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from casefile_contracts import CaseFile
from pydantic import ValidationError

from casefile.agent_runtime.brief_to_draft_v8.ir import (
    BLUEPRINT_COLLECTIONS,
    DOMAIN_COLLECTIONS,
    CaseBlueprintV1,
    EvidenceLogicIR,
    ResolutionGovernanceIRV1,
    SemanticObjectIR,
    StoryWorldIRV1,
    TimeIR,
)
from casefile.agent_runtime.brief_to_draft_v11.contracts import (
    ApproximateTemporalPositionIRV2,
    ExactTemporalPositionIRV2,
    RangeTemporalPositionIRV2,
    RelativeTemporalPositionIRV2,
    StoryWorldIRV2,
    TemporalPositionIRV2,
    UnknownTemporalPositionIRV2,
)
from casefile.agent_runtime.brief_to_draft_v15.contracts import (
    ResolutionGovernanceIRV2,
    ResolutionSpecIRV2,
)
from casefile.contracts import ContractValidationError, validate_casefile
from casefile.contracts.validation import COLLECTION_OBJECT_TYPES

_PREFIXES = {
    "resolution_specs": "res",
    "entities": "ent",
    "relationships": "rel",
    "locations": "loc",
    "events": "evt",
    "information_units": "info",
    "claims": "claim",
    "hypotheses": "hyp",
    "reasoning_paths": "path",
    "constraints": "con",
    "structure_locks": "lock",
}


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    local_key: str
    collection: str
    object_id: str
    object_type: str


@dataclass(frozen=True, slots=True)
class SourceLocation:
    component_id: str
    ir_path: str


@dataclass(frozen=True, slots=True)
class LinkedDraftV1:
    blueprint: CaseBlueprintV1
    story: StoryWorldIRV1 | StoryWorldIRV2
    evidence: EvidenceLogicIR
    governance: ResolutionGovernanceIRV1 | ResolutionGovernanceIRV2
    id_directory: dict[str, DirectoryEntry]
    source_map: dict[str, SourceLocation]


class LinkerValidationError(ContractValidationError):
    """Stable local-key and planned-object diagnostics from Reference Linker."""


def link_draft(
    blueprint: CaseBlueprintV1,
    story: StoryWorldIRV1 | StoryWorldIRV2,
    evidence: EvidenceLogicIR,
    governance: ResolutionGovernanceIRV1 | ResolutionGovernanceIRV2,
    *,
    task_run_id: int,
) -> LinkedDraftV1:
    """Allocate stable IDs and reject coverage or reference errors without guessing."""

    directory: dict[str, DirectoryEntry] = {}
    for collection in BLUEPRINT_COLLECTIONS:
        prefix = _PREFIXES[collection]
        for ordinal, item in enumerate(getattr(blueprint, collection), start=1):
            directory[item.local_key] = DirectoryEntry(
                local_key=item.local_key,
                collection=collection,
                object_id=f"{prefix}_t{task_run_id}_{ordinal:03d}",
                object_type=COLLECTION_OBJECT_TYPES[collection],
            )

    issues: list[dict[str, Any]] = []
    source_map: dict[str, SourceLocation] = {}
    domain_values = {
        "story_world": story,
        "evidence_logic": evidence,
        "resolution_governance": governance,
    }
    ir_by_key: dict[str, tuple[str, str, SemanticObjectIR]] = {}
    for component_id, collections in DOMAIN_COLLECTIONS.items():
        domain = domain_values[component_id]
        for collection in collections:
            objects: list[SemanticObjectIR] = getattr(domain, collection)
            planned = [item.local_key for item in getattr(blueprint, collection)]
            actual = [item.local_key for item in objects]
            _coverage_issues(issues, component_id, collection, planned, actual)
            for index, item in enumerate(objects):
                if item.local_key in ir_by_key:
                    issues.append(
                        _issue(
                            "duplicate_ir_object",
                            f"/{collection}/{index}/local_key",
                            f"local_key {item.local_key!r} 出现在多个领域输出中。",
                            component_id,
                            f"/{collection}/{index}/local_key",
                        )
                    )
                    continue
                ir_by_key[item.local_key] = (component_id, collection, item)
                source_map[f"/{collection}/{index}"] = SourceLocation(
                    component_id=component_id,
                    ir_path=f"/{collection}/{index}",
                )

    for key, (component_id, collection, item) in ir_by_key.items():
        _validate_object_references(
            issues,
            directory,
            component_id,
            collection,
            item,
            base_path=f"/{collection}/{key}",
        )

    if issues:
        raise LinkerValidationError(issues)
    return LinkedDraftV1(
        blueprint=blueprint,
        story=story,
        evidence=evidence,
        governance=governance,
        id_directory=directory,
        source_map=source_map,
    )


def compile_casefile(
    linked: LinkedDraftV1,
    *,
    casefile_id: str,
    brief_id: str,
    brief_version: int,
    version_id: str,
    version_no: int,
    parent_version_id: str | None,
    schema_version: str = "2.0",
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    """Compile linked v1 semantic IR into a current contract-valid CaseFile."""

    timestamp = (updated_at or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
    directory = linked.id_directory

    def ref(key: str) -> dict[str, str]:
        entry = directory[key]
        return {"object_type": entry.object_type, "object_id": entry.object_id}

    def refs(keys: Iterable[str]) -> list[dict[str, str]]:
        return [ref(key) for key in keys]

    def temporal_position(value: TimeIR | TemporalPositionIRV2) -> dict[str, Any]:
        if isinstance(value, (ExactTemporalPositionIRV2, ApproximateTemporalPositionIRV2)):
            return value.model_dump(mode="json")
        if isinstance(value, RangeTemporalPositionIRV2):
            return value.model_dump(mode="json")
        if isinstance(value, RelativeTemporalPositionIRV2):
            return {
                "kind": "relative",
                "anchor_event_ref": ref(value.anchor_event_key),
                "relation": value.relation,
                "offset_minutes": value.offset_minutes,
            }
        if isinstance(value, UnknownTemporalPositionIRV2):
            return {"kind": "unknown"}
        precision = str(value.precision)
        if precision == "unknown":
            return {"kind": "unknown"}
        wall_precision = precision if precision in {"day", "hour", "minute", "second"} else "second"

        def wall_clock(moment: datetime) -> str:
            local = moment.replace(tzinfo=None)
            if wall_precision == "day":
                return local.date().isoformat()
            if wall_precision == "hour":
                return local.strftime("%Y-%m-%dT%H")
            if wall_precision == "minute":
                return local.strftime("%Y-%m-%dT%H:%M")
            return local.isoformat(timespec="seconds")

        start = wall_clock(value.start)
        if value.end is not None:
            return {
                "kind": "range",
                "start": start,
                "end": wall_clock(value.end),
                "precision": wall_precision,
            }
        if precision == "approximate":
            return {
                "kind": "approximate",
                "value": start,
                "precision": wall_precision,
            }
        return {"kind": "exact", "value": start, "precision": wall_precision}

    def metadata(item: SemanticObjectIR) -> dict[str, Any]:
        value: dict[str, Any] = {
            "tags": list(item.tags),
            "source_refs": [],
            "confidence": None,
            "confirmation_status": "ai_inferred",
            "created_by": {
                "actor_type": "agent",
                "actor_id": "agent_brief_to_draft",
            },
            "updated_at": timestamp,
            "revision": 1,
        }
        if item.description is not None:
            value["description"] = item.description
        return value

    def compiled_conclusion(item: SemanticObjectIR) -> dict[str, Any]:
        if not isinstance(item, ResolutionSpecIRV2):
            return {}
        conclusion = item.conclusion
        return {
            "conclusion": {
                "outcome": conclusion.outcome,
                "review_status": "proposed",
                "summary": conclusion.summary,
                "values": [
                    {
                        "slot_id": f"slot_{value.slot_key}",
                        "value": (
                            refs([value.value_key])[0]
                            if value.value_key is not None
                            else value.value
                        ),
                    }
                    for value in conclusion.values
                ],
                "selected_hypothesis_refs": refs(conclusion.selected_hypothesis_keys),
                "supporting_reasoning_path_refs": refs(
                    conclusion.supporting_reasoning_path_keys
                ),
                "rationale": conclusion.rationale,
                "unresolved_gaps": conclusion.unresolved_gaps,
            }
        }

    story = linked.story
    evidence = linked.evidence
    governance = linked.governance
    document: dict[str, Any] = {
        "schema_version": schema_version,
        "casefile_id": casefile_id,
        "title": linked.blueprint.title,
        "status": "draft",
        "version": {
            "version_id": version_id,
            "version_no": version_no,
            "parent_version_id": parent_version_id,
        },
        "brief_ref": {"brief_id": brief_id, "version": brief_version},
        "resolution_specs": [
            {
                **metadata(item),
                "id": directory[item.local_key].object_id,
                "title": item.title,
                "question_type": item.question_type,
                "reasoning_question": item.reasoning_question,
                "conclusion_mode": item.conclusion_mode,
                "required_slots": [
                    {
                        "slot_id": f"slot_{slot.slot_key}",
                        "value_type": slot.value_type,
                        "required": slot.required,
                    }
                    for slot in item.required_slots
                ],
                "accepted_answers": [
                    *item.accepted_answer_texts,
                    *refs(item.accepted_answer_keys),
                ],
                "required_claim_refs": refs(item.required_claim_keys),
                **compiled_conclusion(item),
            }
            for item in governance.resolution_specs
        ],
        "entities": [
            {
                **metadata(item),
                "id": directory[item.local_key].object_id,
                "entity_type": item.entity_type,
                "name": item.name,
                "aliases": item.aliases,
                "traits": item.traits,
                "goals": item.goals,
                "secrets": item.secrets,
                "capabilities": item.capabilities,
                "knowledge_states": [
                    {
                        "as_of_event_ref": (
                            None if state.as_of_event_key is None else ref(state.as_of_event_key)
                        ),
                        "knows_refs": refs(state.knows_keys),
                        "believes_refs": refs(state.believes_keys),
                        "false_belief_refs": refs(state.false_belief_keys),
                    }
                    for state in item.knowledge_states
                ],
            }
            for item in story.entities
        ],
        "relationships": [
            {
                **metadata(item),
                "id": directory[item.local_key].object_id,
                "title": item.title,
                "from_ref": ref(item.from_key),
                "to_ref": ref(item.to_key),
                "relationship_type": item.relationship_type,
                "direction": item.direction,
                "truth_status": item.truth_status,
                "visibility": item.visibility,
            }
            for item in story.relationships
        ],
        "locations": [
            {
                **metadata(item),
                "id": directory[item.local_key].object_id,
                "name": item.name,
                "spatial_position": (
                    None
                    if item.spatial_position is None
                    else item.spatial_position.model_dump(mode="json")
                ),
                "parent_ref": None if item.parent_key is None else ref(item.parent_key),
                "adjacency_refs": refs(item.adjacency_keys),
                "access_rules": item.access_rules,
                "travel_times": [
                    {"to_ref": ref(value.to_key), "minutes": value.minutes}
                    for value in item.travel_times
                ],
                "visibility_rules": item.visibility_rules,
            }
            for item in story.locations
        ],
        "events": [
            {
                **metadata(item),
                "id": directory[item.local_key].object_id,
                "title": item.title,
                "truth_status": item.truth_status,
                "time": temporal_position(item.time),
                "participant_refs": refs(item.participant_keys),
                "location_ref": None if item.location_key is None else ref(item.location_key),
                "cause_refs": refs(item.cause_keys),
                "effect_refs": refs(item.effect_keys),
                "observed_by_refs": refs(item.observed_by_keys),
            }
            for item in story.events
        ],
        "information_units": [
            {
                **metadata(item),
                "id": directory[item.local_key].object_id,
                "information_type": item.information_type,
                "title": item.title,
                "content": item.content,
                "source_event_ref": (
                    None if item.source_event_key is None else ref(item.source_event_key)
                ),
                "reliability": item.reliability,
                "truth_status": item.truth_status,
                "supports_claim_refs": refs(item.supports_claim_keys),
                "refutes_claim_refs": refs(item.refutes_claim_keys),
                "availability": {
                    "perspective_refs": refs(item.availability.perspective_keys),
                    "acquisition_conditions": item.availability.acquisition_conditions,
                    "alternative_path_refs": refs(item.availability.alternative_path_keys),
                },
                "classification": item.classification,
            }
            for item in evidence.information_units
        ],
        "claims": [
            {
                **metadata(item),
                "id": directory[item.local_key].object_id,
                "title": item.title,
                "statement": item.statement,
                "claim_type": item.claim_type,
                "support_refs": refs(item.support_keys),
                "refute_refs": refs(item.refute_keys),
                "dependency_claim_refs": refs(item.dependency_claim_keys),
                "status": item.status,
                "materiality": item.materiality,
            }
            for item in evidence.claims
        ],
        "hypotheses": [
            {
                **metadata(item),
                "id": directory[item.local_key].object_id,
                "title": item.title,
                "proposition": item.proposition,
                "target_resolution_ref": ref(item.target_resolution_key),
                "required_claim_refs": refs(item.required_claim_keys),
                "falsifier_refs": refs(item.falsifier_keys),
                "competing_hypothesis_refs": refs(item.competing_hypothesis_keys),
                "evidence_assessments": [
                    {
                        "information_ref": ref(assessment.information_key),
                        "effect": assessment.effect,
                        "strength": assessment.strength,
                        "rationale": assessment.rationale,
                    }
                    for assessment in getattr(item, "evidence_assessments", [])
                ],
                "status": item.status,
                "score": item.score,
            }
            for item in evidence.hypotheses
        ],
        "reasoning_paths": [
            {
                **metadata(item),
                "id": directory[item.local_key].object_id,
                "title": item.title,
                "path_type": item.path_type,
                "target_ref": ref(item.target_key),
                "steps": [
                    {
                        "step_id": f"step_{step.step_key}",
                        "input_refs": refs(step.input_keys),
                        "operation": step.operation,
                        "output_ref": ref(step.output_key),
                    }
                    for step in item.steps
                ],
                "required_for_resolution": item.required_for_resolution,
                "alternative_path_refs": refs(item.alternative_path_keys),
            }
            for item in evidence.reasoning_paths
        ],
        "constraints": [
            {
                **metadata(item),
                "id": directory[item.local_key].object_id,
                "title": item.title,
                "level": item.level,
                "scope_refs": refs(item.scope_keys),
                "statement": item.statement,
                "rule_expression": item.rule_expression,
                "conflict_refs": refs(item.conflict_keys),
            }
            for item in governance.constraints
        ],
        "structure_locks": [
            {
                **metadata(item),
                "id": directory[item.local_key].object_id,
                "title": item.title,
                "lock_type": item.lock_type,
                "object_ref": ref(item.object_key),
                "field_paths": item.field_paths,
                "reason": item.reason,
            }
            for item in governance.structure_locks
        ],
        "content_notices": [
            {
                "notice_id": f"notice_t{_task_id(directory)}_{index:03d}",
                "category": item.category,
                "severity": item.severity,
                "description": item.description,
            }
            for index, item in enumerate(governance.content_notices, start=1)
        ],
        "extensions": {},
    }
    # The current v1 contract marks several nullable fields as required. Preserve
    # those explicit nulls; fields that are genuinely optional remain controlled by
    # the generated contract model rather than by model output.
    try:
        # Pydantic materializes every optional field with ``None``.  That is
        # not equivalent to omitting an optional field in the JSON Schema: for
        # example, ``spatial_position: null`` violates the contract.  Remove
        # optional nulls, then put back only fields the v1 schema explicitly
        # requires to be nullable.
        candidate = CaseFile.model_validate(document).model_dump(mode="json", exclude_none=True)
        _restore_required_nullable_fields(candidate)
    except ValidationError as error:
        raise ContractValidationError(
            _pydantic_casefile_issues(error, linked.source_map)
        ) from error
    try:
        validate_casefile(candidate)
    except ContractValidationError as error:
        raise ContractValidationError(
            _source_mapped_issues(error.errors, linked.source_map)
        ) from error
    return candidate


def _restore_required_nullable_fields(candidate: dict[str, Any]) -> None:
    """Restore only current CaseFile fields that are both required and nullable."""

    version = candidate.get("version")
    if isinstance(version, dict):
        version.setdefault("parent_version_id", None)

    # ``confidence`` is CoreMetadata's only required nullable field and is
    # shared by every planned semantic object collection.
    for collection in _PREFIXES:
        objects = candidate.get(collection, [])
        if not isinstance(objects, list):
            continue
        for item in objects:
            if isinstance(item, dict):
                item.setdefault("confidence", None)

    for entity in candidate.get("entities", []):
        if not isinstance(entity, dict):
            continue
        for state in entity.get("knowledge_states", []):
            if isinstance(state, dict):
                state.setdefault("as_of_event_ref", None)

    for location in candidate.get("locations", []):
        if isinstance(location, dict):
            location.setdefault("parent_ref", None)

    for event in candidate.get("events", []):
        if not isinstance(event, dict):
            continue
        event.setdefault("location_ref", None)
        time = event.get("time")
        if isinstance(time, dict) and "kind" not in time:
            time.setdefault("end", None)
        elif isinstance(time, dict) and time.get("kind") == "relative":
            time.setdefault("offset_minutes", None)

    for information in candidate.get("information_units", []):
        if isinstance(information, dict):
            information.setdefault("source_event_ref", None)

    for hypothesis in candidate.get("hypotheses", []):
        if isinstance(hypothesis, dict):
            hypothesis.setdefault("score", None)

    for constraint in candidate.get("constraints", []):
        if isinstance(constraint, dict):
            constraint.setdefault("rule_expression", None)


def _task_id(directory: dict[str, DirectoryEntry]) -> str:
    first = next(iter(directory.values()))
    return first.object_id.split("_t", 1)[1].split("_", 1)[0]


def _pydantic_casefile_issues(
    error: ValidationError,
    source_map: dict[str, SourceLocation],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for item in error.errors(include_url=False, include_context=False, include_input=False):
        path = _json_pointer(item.get("loc", ()))
        source = _source_for_path(path, source_map)
        issue_type = str(item.get("type") or "casefile_schema_invalid")
        issues.append(
            {
                "code": issue_type,
                "path": path,
                "message": _pydantic_message(issue_type),
                "component_id": source.component_id,
                "failure_layer": "casefile_schema",
                "schema_id": "casefile-v1",
                "ir_path": source.ir_path,
            }
        )
    return issues or [
        {
            "code": "casefile_schema_invalid",
            "path": "",
            "message": "生成对象未通过 CaseFile v1 结构校验。",
            "component_id": "casefile_compiler",
            "failure_layer": "casefile_schema",
            "schema_id": "casefile-v1",
            "ir_path": "",
        }
    ]


def _source_mapped_issues(
    issues: list[dict[str, Any]],
    source_map: dict[str, SourceLocation],
) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for issue in issues:
        path = str(issue.get("path") or "")
        source = _source_for_path(path, source_map)
        mapped.append(
            {
                **issue,
                "component_id": issue.get("component_id") or source.component_id,
                "failure_layer": issue.get("failure_layer") or "casefile_schema",
                "schema_id": issue.get("schema_id") or "casefile-v1",
                "ir_path": issue.get("ir_path") or source.ir_path,
            }
        )
    return mapped


def _source_for_path(path: str, source_map: dict[str, SourceLocation]) -> SourceLocation:
    matching = [
        (base_path, source)
        for base_path, source in source_map.items()
        if path == base_path or path.startswith(f"{base_path}/")
    ]
    if matching:
        return max(matching, key=lambda entry: len(entry[0]))[1]
    return SourceLocation(component_id="casefile_compiler", ir_path=path)


def _json_pointer(parts: object) -> str:
    if not isinstance(parts, tuple):
        return ""
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "" if not escaped else "/" + "/".join(escaped)


def _pydantic_message(issue_type: str) -> str:
    if issue_type.startswith("datetime_"):
        return "时间必须是带时区的 ISO 8601 日期时间。"
    return "生成对象未通过 CaseFile v1 结构校验。"


def _coverage_issues(
    issues: list[dict[str, Any]],
    component_id: str,
    collection: str,
    planned: list[str],
    actual: list[str],
) -> None:
    missing = [key for key in planned if key not in actual]
    extra = [key for key in actual if key not in planned]
    duplicates = sorted({key for key in actual if actual.count(key) > 1})
    for code, values, message in (
        ("planned_object_missing", missing, "缺少蓝图计划对象"),
        ("unplanned_object", extra, "出现蓝图外对象"),
        ("duplicate_ir_object", duplicates, "领域输出重复对象"),
    ):
        for key in values:
            issues.append(
                _issue(
                    code,
                    f"/{collection}",
                    f"{message}: {key}",
                    component_id,
                    f"/{collection}",
                )
            )
    if not missing and not extra and actual != planned:
        issues.append(
            _issue(
                "planned_order_mismatch",
                f"/{collection}",
                "领域对象顺序必须与蓝图一致。",
                component_id,
                f"/{collection}",
            )
        )


def _validate_object_references(
    issues: list[dict[str, Any]],
    directory: dict[str, DirectoryEntry],
    component_id: str,
    collection: str,
    item: SemanticObjectIR,
    *,
    base_path: str,
) -> None:
    rules = _reference_rules(collection, item)
    for field_name, keys, allowed_collections in rules:
        for index, key in enumerate(keys):
            path = f"{base_path}/{field_name}/{index}"
            target = directory.get(key)
            if target is None:
                issues.append(
                    _issue(
                        "unknown_local_key",
                        path,
                        f"引用 local_key {key!r} 不存在。",
                        component_id,
                        path,
                    )
                )
            elif allowed_collections and target.collection not in allowed_collections:
                issues.append(
                    _issue(
                        "local_key_type_mismatch",
                        path,
                        f"{key!r} 属于 {target.collection}，不允许用于该引用字段。",
                        component_id,
                        path,
                    )
                )


def _reference_rules(collection: str, item: Any) -> list[tuple[str, list[str], set[str]]]:
    any_object = set(BLUEPRINT_COLLECTIONS)
    if collection == "entities":
        states = item.knowledge_states
        return [
            (
                "knowledge_states/as_of_event_key",
                [s.as_of_event_key for s in states if s.as_of_event_key],
                {"events"},
            ),
            (
                "knowledge_states/knows_keys",
                [k for s in states for k in s.knows_keys],
                {"information_units"},
            ),
            (
                "knowledge_states/believes_keys",
                [k for s in states for k in s.believes_keys],
                {"claims"},
            ),
            (
                "knowledge_states/false_belief_keys",
                [k for s in states for k in s.false_belief_keys],
                {"claims"},
            ),
        ]
    if collection == "relationships":
        return [
            ("from_key", [item.from_key], {"entities"}),
            ("to_key", [item.to_key], {"entities"}),
        ]
    if collection == "locations":
        parent = item.parent_key
        return [
            ("parent_key", [] if parent is None else [parent], {"locations"}),
            ("adjacency_keys", item.adjacency_keys, {"locations"}),
            ("travel_times", [value.to_key for value in item.travel_times], {"locations"}),
        ]
    if collection == "events":
        location = item.location_key
        rules = [
            ("participant_keys", item.participant_keys, {"entities"}),
            ("location_key", [] if location is None else [location], {"locations"}),
            ("cause_keys", item.cause_keys, {"events"}),
            ("effect_keys", item.effect_keys, {"events"}),
            ("observed_by_keys", item.observed_by_keys, {"entities"}),
        ]
        if isinstance(item.time, RelativeTemporalPositionIRV2):
            rules.append(("time/anchor_event_key", [item.time.anchor_event_key], {"events"}))
        return rules
    if collection == "information_units":
        source = item.source_event_key
        availability = item.availability
        return [
            ("source_event_key", [] if source is None else [source], {"events"}),
            ("supports_claim_keys", item.supports_claim_keys, {"claims"}),
            ("refutes_claim_keys", item.refutes_claim_keys, {"claims"}),
            ("availability/perspective_keys", availability.perspective_keys, {"entities"}),
            (
                "availability/alternative_path_keys",
                availability.alternative_path_keys,
                {"reasoning_paths"},
            ),
        ]
    if collection == "claims":
        return [
            ("support_keys", item.support_keys, {"information_units"}),
            ("refute_keys", item.refute_keys, {"information_units"}),
            ("dependency_claim_keys", item.dependency_claim_keys, {"claims"}),
        ]
    if collection == "hypotheses":
        return [
            ("target_resolution_key", [item.target_resolution_key], {"resolution_specs"}),
            ("required_claim_keys", item.required_claim_keys, {"claims"}),
            ("falsifier_keys", item.falsifier_keys, {"information_units", "claims"}),
            ("competing_hypothesis_keys", item.competing_hypothesis_keys, {"hypotheses"}),
            (
                "evidence_assessments/information_key",
                [
                    assessment.information_key
                    for assessment in getattr(item, "evidence_assessments", [])
                ],
                {"information_units"},
            ),
        ]
    if collection == "reasoning_paths":
        steps = item.steps
        return [
            ("target_key", [item.target_key], {"resolution_specs", "claims", "hypotheses"}),
            ("steps/input_keys", [key for step in steps for key in step.input_keys], any_object),
            ("steps/output_key", [step.output_key for step in steps], {"claims", "hypotheses"}),
            ("alternative_path_keys", item.alternative_path_keys, {"reasoning_paths"}),
        ]
    if collection == "resolution_specs":
        conclusion = getattr(item, "conclusion", None)
        value_keys = (
            [value.value_key for value in conclusion.values if value.value_key]
            if conclusion is not None
            else []
        )
        return [
            (
                "accepted_answer_keys",
                item.accepted_answer_keys,
                {"entities", "claims", "hypotheses"},
            ),
            ("required_claim_keys", item.required_claim_keys, {"claims"}),
            ("conclusion/values/value_key", value_keys, any_object),
            (
                "conclusion/selected_hypothesis_keys",
                [] if conclusion is None else conclusion.selected_hypothesis_keys,
                {"hypotheses"},
            ),
            (
                "conclusion/supporting_reasoning_path_keys",
                [] if conclusion is None else conclusion.supporting_reasoning_path_keys,
                {"reasoning_paths"},
            ),
        ]
    if collection == "constraints":
        return [
            ("scope_keys", item.scope_keys, any_object),
            ("conflict_keys", item.conflict_keys, {"constraints"}),
        ]
    if collection == "structure_locks":
        return [("object_key", [item.object_key], any_object)]
    return []


def _issue(
    code: str,
    path: str,
    message: str,
    component_id: str,
    ir_path: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "path": path,
        "message": message,
        "component_id": component_id,
        "failure_layer": "reference_linker",
        "schema_id": "linked-draft-v1",
        "ir_path": ir_path,
    }
