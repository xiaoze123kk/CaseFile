"""Lossless v1 CaseFile persistence and normalized-state projection."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import rfc8785
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.application.errors import ApplicationError
from casefile.contracts import validate_casefile
from casefile.data_postgres.models import (
    Brief,
    BriefVersion,
    CaseFileConstraint,
    CaseFileContractRef,
    CaseFileObject,
    Claim,
    DraftOperation,
    DraftSnapshot,
    Entity,
    Event,
    Hypothesis,
    InformationUnit,
    Location,
    ReasoningNode,
    ReasoningPath,
    Relationship,
    ResolutionSlot,
    ResolutionSpec,
    StructureLock,
)
from casefile.data_postgres.repositories import OwnedDraft

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


def casefile_content_hash(document: dict[str, Any]) -> str:
    """Return the lowercase SHA-256 of RFC 8785 canonical bytes."""

    return hashlib.sha256(rfc8785.dumps(document)).hexdigest()


def write_generated_casefile(
    session: Session,
    owned: OwnedDraft,
    *,
    candidate: dict[str, Any],
    brief: Brief,
    brief_version: BriefVersion,
    task_run_id: int,
    actor_user_id: int,
) -> DraftSnapshot:
    """Atomically replace a strictly empty Draft with one validated v1 candidate."""

    validate_casefile(candidate)
    _validate_generation_context(owned, candidate, brief, brief_version)
    active_count = session.scalar(
        select(func.count(CaseFileObject.id)).where(
            CaseFileObject.draft_id == owned.draft.id,
            CaseFileObject.deleted_at.is_(None),
        )
    )
    if active_count:
        raise ApplicationError(
            "draft_not_empty",
            "Full CaseFile generation requires a strictly empty Draft",
            status_code=409,
        )

    registry_by_object_id = _create_registries(session, owned, candidate)
    _create_content_rows(session, owned, candidate, registry_by_object_id)
    _create_contract_refs(session, owned, candidate, registry_by_object_id)

    base_revision = owned.draft.revision
    owned.project.title = candidate["title"]
    owned.casefile.title = candidate["title"]
    owned.casefile.status = candidate["status"]
    owned.casefile.schema_version = candidate["schema_version"]
    owned.draft.schema_version = candidate["schema_version"]
    owned.draft.version_id = candidate["version"]["version_id"]
    owned.draft.version_no = candidate["version"]["version_no"]
    owned.draft.parent_version_id = candidate["version"]["parent_version_id"]
    owned.draft.brief_version_id = brief_version.id
    owned.draft.content_notices_jsonb = candidate["content_notices"]
    owned.draft.extensions_jsonb = candidate["extensions"]

    content_hash = casefile_content_hash(candidate)
    sequence_no = int(
        session.scalar(
            select(func.coalesce(func.max(DraftOperation.sequence_no), 0) + 1).where(
                DraftOperation.draft_id == owned.draft.id
            )
        )
        or 1
    )
    session.add(
        DraftOperation(
            project_id=owned.project.id,
            casefile_id=owned.casefile.id,
            draft_id=owned.draft.id,
            casefile_object_id=None,
            sequence_no=sequence_no,
            operation_group_no=sequence_no,
            operation_type="agent_generate_from_brief",
            field_path="",
            old_value_jsonb=None,
            new_value_jsonb={
                "brief_version_id": brief_version.id,
                "content_hash": content_hash,
                "task_run_id": task_run_id,
            },
            base_revision=base_revision,
            result_revision=base_revision + 1,
            actor_kind="agent",
            actor_user_id=None,
            actor_ref=f"task_run:{task_run_id}",
        )
    )
    session.flush()
    session.refresh(owned.draft)

    projected = build_casefile_document(session, owned)
    projected_hash = casefile_content_hash(projected)
    if projected != candidate or projected_hash != content_hash:
        raise ApplicationError(
            "casefile_roundtrip_mismatch",
            "The normalized CaseFile projection differs from the validated candidate",
            status_code=500,
            details={"candidate_hash": content_hash, "projected_hash": projected_hash},
        )

    snapshot = DraftSnapshot(
        project_id=owned.project.id,
        casefile_id=owned.casefile.id,
        draft_id=owned.draft.id,
        snapshot_revision=owned.draft.revision,
        schema_version=owned.draft.schema_version,
        snapshot_jsonb=projected,
        content_hash=content_hash,
        created_by_user_id=actor_user_id,
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def build_casefile_document(session: Session, owned: OwnedDraft) -> dict[str, Any]:
    """Project normalized v1 tables into the exact public CaseFile contract."""

    registries = list(
        session.scalars(
            select(CaseFileObject)
            .where(
                CaseFileObject.draft_id == owned.draft.id,
                CaseFileObject.deleted_at.is_(None),
            )
            .order_by(CaseFileObject.object_type, CaseFileObject.contract_ordinal)
        )
    )
    registry_by_type: dict[str, list[CaseFileObject]] = defaultdict(list)
    for registry in registries:
        registry_by_type[registry.object_type].append(registry)

    refs_by_source: dict[int, list[CaseFileContractRef]] = defaultdict(list)
    for row in session.scalars(
        select(CaseFileContractRef)
        .where(CaseFileContractRef.draft_id == owned.draft.id)
        .order_by(
            CaseFileContractRef.from_object_id,
            CaseFileContractRef.field_path,
            CaseFileContractRef.ordinal,
        )
    ):
        refs_by_source[row.from_object_id].append(row)

    brief_version = None
    brief = None
    if owned.draft.brief_version_id is not None:
        brief_version = session.get(BriefVersion, owned.draft.brief_version_id)
        if brief_version is not None:
            brief = session.get(Brief, brief_version.brief_id)
    if brief is None or brief_version is None:
        raise ApplicationError(
            "brief_version_missing",
            "The Draft does not point to a confirmed Brief version",
            status_code=409,
        )

    document: dict[str, Any] = {
        "schema_version": owned.casefile.schema_version,
        "casefile_id": owned.casefile.object_id,
        "title": owned.casefile.title,
        "status": owned.casefile.status,
        "version": {
            "version_id": owned.draft.version_id,
            "version_no": owned.draft.version_no,
            "parent_version_id": owned.draft.parent_version_id,
        },
        "brief_ref": {"brief_id": brief.public_id, "version": brief_version.version_no},
        "resolution_specs": _project_resolutions(
            session, registry_by_type["resolution_spec"], refs_by_source
        ),
        "entities": _project_entities(session, registry_by_type["entity"], refs_by_source),
        "relationships": _project_relationships(
            session, registry_by_type["relationship"], refs_by_source
        ),
        "locations": _project_locations(session, registry_by_type["location"], refs_by_source),
        "events": _project_events(session, registry_by_type["event"], refs_by_source),
        "information_units": _project_information_units(
            session, registry_by_type["information_unit"], refs_by_source
        ),
        "claims": _project_claims(session, registry_by_type["claim"], refs_by_source),
        "hypotheses": _project_hypotheses(session, registry_by_type["hypothesis"], refs_by_source),
        "reasoning_paths": _project_reasoning_paths(
            session, registry_by_type["reasoning_path"], refs_by_source
        ),
        "constraints": _project_constraints(
            session, registry_by_type["constraint"], refs_by_source
        ),
        "structure_locks": _project_structure_locks(
            session, registry_by_type["structure_lock"], refs_by_source
        ),
        "content_notices": owned.draft.content_notices_jsonb,
        "extensions": owned.draft.extensions_jsonb,
    }
    validate_casefile(document)
    return document


def _validate_generation_context(
    owned: OwnedDraft,
    candidate: dict[str, Any],
    brief: Brief,
    brief_version: BriefVersion,
) -> None:
    expected_brief_ref = {"brief_id": brief.public_id, "version": brief_version.version_no}
    failures: dict[str, Any] = {}
    if candidate["casefile_id"] != owned.casefile.object_id:
        failures["casefile_id"] = owned.casefile.object_id
    if candidate["brief_ref"] != expected_brief_ref:
        failures["brief_ref"] = expected_brief_ref
    if candidate["status"] != "draft":
        failures["status"] = "draft"
    if failures:
        raise ApplicationError(
            "generation_context_mismatch",
            "The generated CaseFile does not match its frozen task context",
            status_code=422,
            details={"expected": failures},
        )


def _create_registries(
    session: Session,
    owned: OwnedDraft,
    candidate: dict[str, Any],
) -> dict[str, CaseFileObject]:
    registries: dict[str, CaseFileObject] = {}
    for collection, object_type in COLLECTION_TYPES:
        for ordinal, item in enumerate(candidate[collection], start=1):
            created_by = item["created_by"]
            registry = CaseFileObject(
                project_id=owned.project.id,
                casefile_id=owned.casefile.id,
                draft_id=owned.draft.id,
                object_id=item["id"],
                object_type=object_type,
                contract_ordinal=ordinal,
                revision=item["revision"],
                description=item.get("description"),
                tags_jsonb=item["tags"],
                created_by_type=created_by["actor_type"],
                created_by_id=created_by["actor_id"],
                contract_updated_at=item["updated_at"],
                source_jsonb={"kind": created_by["actor_type"]},
                confidence=_decimal(item["confidence"]),
                confirmation_status=item["confirmation_status"],
            )
            session.add(registry)
            registries[item["id"]] = registry
    session.flush()
    return registries


def _create_content_rows(
    session: Session,
    owned: OwnedDraft,
    candidate: dict[str, Any],
    registries: dict[str, CaseFileObject],
) -> None:
    lineage = {
        "project_id": owned.project.id,
        "casefile_id": owned.casefile.id,
        "draft_id": owned.draft.id,
    }
    for item in candidate["resolution_specs"]:
        registry = registries[item["id"]]
        text_answers = {
            str(index): value
            for index, value in enumerate(item["accepted_answers"], start=1)
            if isinstance(value, str)
        }
        resolution = ResolutionSpec(
            **lineage,
            object_registry_id=registry.id,
            title=item["title"],
            question_type=item["question_type"],
            target_question=item["reasoning_question"],
            conclusion_mode=item["conclusion_mode"],
            accepted_answer_texts_jsonb=text_answers,
            fairness_requirements_jsonb=[],
            conclusion_pattern_jsonb={},
            status="draft",
        )
        session.add(resolution)
        session.flush()
        for ordinal, slot in enumerate(item["required_slots"], start=1):
            session.add(
                ResolutionSlot(
                    **lineage,
                    resolution_spec_id=resolution.id,
                    slot_key=slot["slot_id"],
                    value_type=slot["value_type"],
                    label=slot["slot_id"],
                    is_required=slot["required"],
                    ordinal=ordinal,
                    value_jsonb=None,
                )
            )

    for item in candidate["entities"]:
        session.add(
            Entity(
                **lineage,
                object_registry_id=registries[item["id"]].id,
                entity_kind=item["entity_type"],
                name=item["name"],
                description=item.get("description"),
                aliases_jsonb=item["aliases"],
                traits_jsonb=item["traits"],
                goals_jsonb=item["goals"],
                secrets_jsonb=item["secrets"],
                capabilities_jsonb=item["capabilities"],
                attributes_jsonb={},
            )
        )
    for item in candidate["relationships"]:
        session.add(
            Relationship(
                **lineage,
                object_registry_id=registries[item["id"]].id,
                title=item["title"],
                relationship_type=item["relationship_type"],
                direction=item["direction"],
                truth_status=item["truth_status"],
                visibility=item["visibility"],
            )
        )
    for item in candidate["locations"]:
        session.add(
            Location(
                **lineage,
                entity_id=None,
                object_registry_id=registries[item["id"]].id,
                name=item["name"],
                geo_jsonb={},
                movement_rules_jsonb={},
                access_rules_jsonb=item["access_rules"],
                visibility_rules_jsonb=item["visibility_rules"],
            )
        )
    for ordinal, item in enumerate(candidate["events"], start=1):
        session.add(
            Event(
                **lineage,
                object_registry_id=registries[item["id"]].id,
                title=item["title"],
                summary=item.get("description"),
                start_time_jsonb=None,
                end_time_jsonb=None,
                time_jsonb=item["time"],
                narrative_order=ordinal,
                narrative_phase_id=None,
                location_id=None,
                visibility="restricted",
                truth_status=item["truth_status"],
            )
        )
    for item in candidate["information_units"]:
        session.add(
            InformationUnit(
                **lineage,
                object_registry_id=registries[item["id"]].id,
                information_kind=item["information_type"],
                title=item["title"],
                body_text=item["content"],
                reliability=item["reliability"],
                truth_status=item["truth_status"],
                classification=item["classification"],
                acquisition_conditions_jsonb=item["availability"]["acquisition_conditions"],
                source_credibility=None,
                visible_from_phase_id=None,
                is_misleading=item["classification"] == "misleading",
                status="active",
            )
        )
    for item in candidate["claims"]:
        session.add(
            Claim(
                **lineage,
                object_registry_id=registries[item["id"]].id,
                title=item["title"],
                statement=item["statement"],
                claim_type=item["claim_type"],
                materiality=item["materiality"],
                status=item["status"],
            )
        )
    for item in candidate["hypotheses"]:
        session.add(
            Hypothesis(
                **lineage,
                object_registry_id=registries[item["id"]].id,
                title=item["title"],
                summary=item["proposition"],
                status=item["status"],
                score=_decimal(item["score"]),
                exclusion_rule_jsonb={},
            )
        )
    for item in candidate["reasoning_paths"]:
        path = ReasoningPath(
            **lineage,
            object_registry_id=registries[item["id"]].id,
            name=item["title"],
            reasoning_type=item["path_type"],
            status="active",
            confidence=_decimal(item["confidence"]),
            human_confirmed=item["confirmation_status"] == "user_confirmed",
            summary=item.get("description"),
            required_for_resolution=item["required_for_resolution"],
        )
        session.add(path)
        session.flush()
        for ordinal, step in enumerate(item["steps"], start=1):
            session.add(
                ReasoningNode(
                    **lineage,
                    reasoning_path_id=path.id,
                    node_key=step["step_id"],
                    ordinal=ordinal,
                    source_object_id=None,
                    node_type=step["operation"],
                    statement=step["operation"],
                    attributes_jsonb={},
                )
            )
    for item in candidate["constraints"]:
        session.add(
            CaseFileConstraint(
                **lineage,
                object_registry_id=registries[item["id"]].id,
                target_object_id=None,
                title=item["title"],
                statement=item["statement"],
                rule_expression=item["rule_expression"],
                constraint_kind="contract",
                constraint_level=item["level"],
                rule_jsonb={},
                status="active",
                conflict_status="none",
            )
        )
    for item in candidate["structure_locks"]:
        session.add(
            StructureLock(
                **lineage,
                object_registry_id=registries[item["id"]].id,
                title=item["title"],
                lock_type=item["lock_type"],
                field_paths_jsonb=item["field_paths"],
                reason=item["reason"],
            )
        )
    session.flush()


def _create_contract_refs(
    session: Session,
    owned: OwnedDraft,
    candidate: dict[str, Any],
    registries: dict[str, CaseFileObject],
) -> None:
    for collection, _ in COLLECTION_TYPES:
        for item in candidate[collection]:
            source = registries[item["id"]]
            for path, ordinal, ref, metadata in _walk_object_refs(item):
                session.add(
                    CaseFileContractRef(
                        project_id=owned.project.id,
                        casefile_id=owned.casefile.id,
                        draft_id=owned.draft.id,
                        from_object_id=source.id,
                        field_path=path,
                        object_type=ref["object_type"],
                        object_id=ref["object_id"],
                        ordinal=ordinal,
                        metadata_jsonb=metadata,
                    )
                )
    session.flush()


def _walk_object_refs(
    value: Any,
    path: str = "",
    *,
    parent: dict[str, Any] | None = None,
) -> Iterator[tuple[str, int, dict[str, str], dict[str, Any]]]:
    if _is_object_ref(value):
        metadata = {}
        if parent is not None and "minutes" in parent:
            metadata["minutes"] = parent["minutes"]
        yield path, 1, value, metadata
        return
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}/{_escape_pointer(key)}"
            yield from _walk_object_refs(child, child_path, parent=value)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            if _is_object_ref(child):
                yield path, index + 1, child, {}
            else:
                yield from _walk_object_refs(child, f"{path}/{index}", parent=parent)


def _project_resolutions(
    session: Session,
    registries: list[CaseFileObject],
    refs: dict[int, list[CaseFileContractRef]],
) -> list[dict[str, Any]]:
    result = []
    for registry in registries:
        row = session.scalar(
            select(ResolutionSpec).where(ResolutionSpec.object_registry_id == registry.id)
        )
        if row is None:
            continue
        slots = list(
            session.scalars(
                select(ResolutionSlot)
                .where(ResolutionSlot.resolution_spec_id == row.id)
                .order_by(ResolutionSlot.ordinal)
            )
        )
        accepted: dict[int, Any] = {
            int(key): value for key, value in row.accepted_answer_texts_jsonb.items()
        }
        for ref in _refs_at(refs[registry.id], "/accepted_answers"):
            accepted[ref.ordinal] = _ref_value(ref)
        result.append(
            {
                **_common(registry, refs),
                "id": registry.object_id,
                "title": row.title,
                "question_type": row.question_type,
                "reasoning_question": row.target_question,
                "conclusion_mode": row.conclusion_mode,
                "required_slots": [
                    {
                        "slot_id": slot.slot_key,
                        "value_type": slot.value_type,
                        "required": slot.is_required,
                    }
                    for slot in slots
                ],
                "accepted_answers": [accepted[index] for index in sorted(accepted)],
                "required_claim_refs": _ref_values(refs[registry.id], "/required_claim_refs"),
            }
        )
    return result


def _project_entities(
    session: Session,
    registries: list[CaseFileObject],
    refs: dict[int, list[CaseFileContractRef]],
) -> list[dict[str, Any]]:
    result = []
    for registry in registries:
        row = session.scalar(select(Entity).where(Entity.object_registry_id == registry.id))
        if row is None:
            continue
        state_indices = _path_indices(
            refs[registry.id],
            (
                r"^/knowledge_states/(\d+)/(?:as_of_event_ref|knows_refs|"
                r"believes_refs|false_belief_refs)"
            ),
        )
        knowledge_states = []
        for index in state_indices:
            prefix = f"/knowledge_states/{index}"
            knowledge_states.append(
                {
                    "as_of_event_ref": _optional_ref(
                        refs[registry.id], f"{prefix}/as_of_event_ref"
                    ),
                    "knows_refs": _ref_values(refs[registry.id], f"{prefix}/knows_refs"),
                    "believes_refs": _ref_values(refs[registry.id], f"{prefix}/believes_refs"),
                    "false_belief_refs": _ref_values(
                        refs[registry.id], f"{prefix}/false_belief_refs"
                    ),
                }
            )
        result.append(
            {
                **_common(registry, refs),
                "id": registry.object_id,
                "entity_type": row.entity_kind,
                "name": row.name,
                "aliases": row.aliases_jsonb,
                "traits": row.traits_jsonb,
                "goals": row.goals_jsonb,
                "secrets": row.secrets_jsonb,
                "capabilities": row.capabilities_jsonb,
                "knowledge_states": knowledge_states,
            }
        )
    return result


def _project_relationships(
    session: Session,
    registries: list[CaseFileObject],
    refs: dict[int, list[CaseFileContractRef]],
) -> list[dict[str, Any]]:
    result = []
    for registry in registries:
        row = session.scalar(
            select(Relationship).where(Relationship.object_registry_id == registry.id)
        )
        if row is not None:
            result.append(
                {
                    **_common(registry, refs),
                    "id": registry.object_id,
                    "title": row.title,
                    "from_ref": _single_ref(refs[registry.id], "/from_ref"),
                    "to_ref": _single_ref(refs[registry.id], "/to_ref"),
                    "relationship_type": row.relationship_type,
                    "direction": row.direction,
                    "truth_status": row.truth_status,
                    "visibility": row.visibility,
                }
            )
    return result


def _project_locations(
    session: Session,
    registries: list[CaseFileObject],
    refs: dict[int, list[CaseFileContractRef]],
) -> list[dict[str, Any]]:
    result = []
    for registry in registries:
        row = session.scalar(select(Location).where(Location.object_registry_id == registry.id))
        if row is None:
            continue
        travel_indices = _path_indices(refs[registry.id], r"^/travel_times/(\d+)/to_ref$")
        travel_times = []
        for index in travel_indices:
            ref_row = _single_ref_row(refs[registry.id], f"/travel_times/{index}/to_ref")
            travel_times.append(
                {"to_ref": _ref_value(ref_row), "minutes": ref_row.metadata_jsonb["minutes"]}
            )
        result.append(
            {
                **_common(registry, refs),
                "id": registry.object_id,
                "name": row.name,
                "parent_ref": _optional_ref(refs[registry.id], "/parent_ref"),
                "adjacency_refs": _ref_values(refs[registry.id], "/adjacency_refs"),
                "access_rules": row.access_rules_jsonb,
                "travel_times": travel_times,
                "visibility_rules": row.visibility_rules_jsonb,
            }
        )
    return result


def _project_events(
    session: Session,
    registries: list[CaseFileObject],
    refs: dict[int, list[CaseFileContractRef]],
) -> list[dict[str, Any]]:
    result = []
    for registry in registries:
        row = session.scalar(select(Event).where(Event.object_registry_id == registry.id))
        if row is not None:
            result.append(
                {
                    **_common(registry, refs),
                    "id": registry.object_id,
                    "title": row.title,
                    "truth_status": row.truth_status,
                    "time": row.time_jsonb,
                    "participant_refs": _ref_values(refs[registry.id], "/participant_refs"),
                    "location_ref": _optional_ref(refs[registry.id], "/location_ref"),
                    "cause_refs": _ref_values(refs[registry.id], "/cause_refs"),
                    "effect_refs": _ref_values(refs[registry.id], "/effect_refs"),
                    "observed_by_refs": _ref_values(refs[registry.id], "/observed_by_refs"),
                }
            )
    return result


def _project_information_units(
    session: Session,
    registries: list[CaseFileObject],
    refs: dict[int, list[CaseFileContractRef]],
) -> list[dict[str, Any]]:
    result = []
    for registry in registries:
        row = session.scalar(
            select(InformationUnit).where(InformationUnit.object_registry_id == registry.id)
        )
        if row is not None:
            result.append(
                {
                    **_common(registry, refs),
                    "id": registry.object_id,
                    "information_type": row.information_kind,
                    "title": row.title,
                    "content": row.body_text,
                    "source_event_ref": _optional_ref(refs[registry.id], "/source_event_ref"),
                    "reliability": row.reliability,
                    "truth_status": row.truth_status,
                    "supports_claim_refs": _ref_values(refs[registry.id], "/supports_claim_refs"),
                    "refutes_claim_refs": _ref_values(refs[registry.id], "/refutes_claim_refs"),
                    "availability": {
                        "perspective_refs": _ref_values(
                            refs[registry.id], "/availability/perspective_refs"
                        ),
                        "acquisition_conditions": row.acquisition_conditions_jsonb,
                        "alternative_path_refs": _ref_values(
                            refs[registry.id], "/availability/alternative_path_refs"
                        ),
                    },
                    "classification": row.classification,
                }
            )
    return result


def _project_claims(
    session: Session,
    registries: list[CaseFileObject],
    refs: dict[int, list[CaseFileContractRef]],
) -> list[dict[str, Any]]:
    result = []
    for registry in registries:
        row = session.scalar(select(Claim).where(Claim.object_registry_id == registry.id))
        if row is not None:
            result.append(
                {
                    **_common(registry, refs),
                    "id": registry.object_id,
                    "title": row.title,
                    "statement": row.statement,
                    "claim_type": row.claim_type,
                    "support_refs": _ref_values(refs[registry.id], "/support_refs"),
                    "refute_refs": _ref_values(refs[registry.id], "/refute_refs"),
                    "dependency_claim_refs": _ref_values(
                        refs[registry.id], "/dependency_claim_refs"
                    ),
                    "status": row.status,
                    "materiality": row.materiality,
                }
            )
    return result


def _project_hypotheses(
    session: Session,
    registries: list[CaseFileObject],
    refs: dict[int, list[CaseFileContractRef]],
) -> list[dict[str, Any]]:
    result = []
    for registry in registries:
        row = session.scalar(select(Hypothesis).where(Hypothesis.object_registry_id == registry.id))
        if row is not None:
            result.append(
                {
                    **_common(registry, refs),
                    "id": registry.object_id,
                    "title": row.title,
                    "proposition": row.summary,
                    "target_resolution_ref": _single_ref(
                        refs[registry.id], "/target_resolution_ref"
                    ),
                    "required_claim_refs": _ref_values(refs[registry.id], "/required_claim_refs"),
                    "falsifier_refs": _ref_values(refs[registry.id], "/falsifier_refs"),
                    "competing_hypothesis_refs": _ref_values(
                        refs[registry.id], "/competing_hypothesis_refs"
                    ),
                    "status": row.status,
                    "score": _number(row.score),
                }
            )
    return result


def _project_reasoning_paths(
    session: Session,
    registries: list[CaseFileObject],
    refs: dict[int, list[CaseFileContractRef]],
) -> list[dict[str, Any]]:
    result = []
    for registry in registries:
        row = session.scalar(
            select(ReasoningPath).where(ReasoningPath.object_registry_id == registry.id)
        )
        if row is None:
            continue
        steps = list(
            session.scalars(
                select(ReasoningNode)
                .where(ReasoningNode.reasoning_path_id == row.id)
                .order_by(ReasoningNode.ordinal)
            )
        )
        result.append(
            {
                **_common(registry, refs),
                "id": registry.object_id,
                "title": row.name,
                "path_type": row.reasoning_type,
                "target_ref": _single_ref(refs[registry.id], "/target_ref"),
                "steps": [
                    {
                        "step_id": step.node_key,
                        "input_refs": _ref_values(refs[registry.id], f"/steps/{index}/input_refs"),
                        "operation": step.node_type,
                        "output_ref": _single_ref(refs[registry.id], f"/steps/{index}/output_ref"),
                    }
                    for index, step in enumerate(steps)
                ],
                "required_for_resolution": row.required_for_resolution,
                "alternative_path_refs": _ref_values(refs[registry.id], "/alternative_path_refs"),
            }
        )
    return result


def _project_constraints(
    session: Session,
    registries: list[CaseFileObject],
    refs: dict[int, list[CaseFileContractRef]],
) -> list[dict[str, Any]]:
    result = []
    for registry in registries:
        row = session.scalar(
            select(CaseFileConstraint).where(CaseFileConstraint.object_registry_id == registry.id)
        )
        if row is not None:
            result.append(
                {
                    **_common(registry, refs),
                    "id": registry.object_id,
                    "title": row.title,
                    "level": row.constraint_level,
                    "scope_refs": _ref_values(refs[registry.id], "/scope_refs"),
                    "statement": row.statement,
                    "rule_expression": row.rule_expression,
                    "conflict_refs": _ref_values(refs[registry.id], "/conflict_refs"),
                }
            )
    return result


def _project_structure_locks(
    session: Session,
    registries: list[CaseFileObject],
    refs: dict[int, list[CaseFileContractRef]],
) -> list[dict[str, Any]]:
    result = []
    for registry in registries:
        row = session.scalar(
            select(StructureLock).where(StructureLock.object_registry_id == registry.id)
        )
        if row is not None:
            result.append(
                {
                    **_common(registry, refs),
                    "id": registry.object_id,
                    "title": row.title,
                    "lock_type": row.lock_type,
                    "object_ref": _single_ref(refs[registry.id], "/object_ref"),
                    "field_paths": row.field_paths_jsonb,
                    "reason": row.reason,
                }
            )
    return result


def _common(
    registry: CaseFileObject,
    refs: dict[int, list[CaseFileContractRef]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "tags": registry.tags_jsonb,
        "source_refs": _ref_values(refs[registry.id], "/source_refs"),
        "confidence": _number(registry.confidence),
        "confirmation_status": registry.confirmation_status,
        "created_by": {
            "actor_type": registry.created_by_type,
            "actor_id": registry.created_by_id,
        },
        "updated_at": registry.contract_updated_at,
        "revision": registry.revision,
    }
    if registry.description is not None:
        result["description"] = registry.description
    return result


def _refs_at(rows: list[CaseFileContractRef], field_path: str) -> list[CaseFileContractRef]:
    return sorted(
        (row for row in rows if row.field_path == field_path),
        key=lambda row: row.ordinal,
    )


def _ref_values(rows: list[CaseFileContractRef], field_path: str) -> list[dict[str, str]]:
    return [_ref_value(row) for row in _refs_at(rows, field_path)]


def _single_ref(rows: list[CaseFileContractRef], field_path: str) -> dict[str, str]:
    return _ref_value(_single_ref_row(rows, field_path))


def _optional_ref(rows: list[CaseFileContractRef], field_path: str) -> dict[str, str] | None:
    matches = _refs_at(rows, field_path)
    return None if not matches else _ref_value(matches[0])


def _single_ref_row(rows: list[CaseFileContractRef], field_path: str) -> CaseFileContractRef:
    matches = _refs_at(rows, field_path)
    if len(matches) != 1:
        raise ApplicationError(
            "casefile_mapping_incomplete",
            f"Expected exactly one normalized ref at {field_path}",
            status_code=500,
        )
    return matches[0]


def _ref_value(row: CaseFileContractRef) -> dict[str, str]:
    return {"object_type": row.object_type, "object_id": row.object_id}


def _path_indices(rows: list[CaseFileContractRef], pattern: str) -> list[int]:
    matcher = re.compile(pattern)
    indices = {
        int(match.group(1)) for row in rows if (match := matcher.match(row.field_path)) is not None
    }
    return sorted(indices)


def _is_object_ref(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"object_type", "object_id"}
        and isinstance(value["object_type"], str)
        and isinstance(value["object_id"], str)
    )


def _escape_pointer(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _decimal(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _number(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)
