"""Deterministic normalized-state → CaseFile document projection and hashing."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from decimal import Decimal
from typing import Any

import rfc8785
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.contracts import validate_casefile
from casefile.data_postgres.models import (
    CaseFileConstraint,
    CaseFileObject,
    CaseFileRef,
    Claim,
    Entity,
    Event,
    EvidenceItem,
    Hypothesis,
    InformationUnit,
    KnowledgeState,
    KnowledgeStateEntry,
    Location,
    NarrativePhase,
    Person,
    ReasoningEdge,
    ReasoningNode,
    ReasoningPath,
    ResolutionSlot,
    ResolutionSpec,
    Testimony,
)
from casefile.data_postgres.repositories import OwnedDraft


def build_casefile_document(session: Session, owned: OwnedDraft) -> dict[str, Any]:
    """Project every active normalized object into the public 0.1.0 contract."""

    registries = list(
        session.scalars(
            select(CaseFileObject).where(
                CaseFileObject.draft_id == owned.draft.id,
                CaseFileObject.deleted_at.is_(None),
            )
        )
    )
    registry_by_id = {row.id: row for row in registries}
    stable_by_registry_id = {row.id: row.object_id for row in registries}

    refs: dict[tuple[int, str], list[str]] = defaultdict(list)
    for row in session.scalars(
        select(CaseFileRef)
        .where(CaseFileRef.draft_id == owned.draft.id)
        .order_by(CaseFileRef.from_object_id, CaseFileRef.ref_kind, CaseFileRef.ordinal)
    ):
        target_id = stable_by_registry_id.get(row.to_object_id)
        if row.from_object_id in registry_by_id and target_id is not None:
            refs[(row.from_object_id, row.ref_kind)].append(target_id)

    phases = [
        row
        for row in session.scalars(
            select(NarrativePhase)
            .where(NarrativePhase.draft_id == owned.draft.id)
            .order_by(NarrativePhase.phase_order)
        )
        if row.object_registry_id in registry_by_id
    ]
    phase_object_id = {
        row.id: stable_by_registry_id[row.object_registry_id] for row in phases
    }

    entities = [
        row
        for row in session.scalars(
            select(Entity).where(Entity.draft_id == owned.draft.id).order_by(Entity.id)
        )
        if row.object_registry_id in registry_by_id
    ]
    entity_object_id = {
        row.id: stable_by_registry_id[row.object_registry_id] for row in entities
    }
    people = {
        row.entity_id: row
        for row in session.scalars(select(Person).where(Person.draft_id == owned.draft.id))
    }
    locations = {
        row.entity_id: row
        for row in session.scalars(select(Location).where(Location.draft_id == owned.draft.id))
    }
    location_object_id = {
        extension.id: entity_object_id[entity_id]
        for entity_id, extension in locations.items()
        if entity_id in entity_object_id
    }
    person_object_id = {
        extension.id: entity_object_id[entity_id]
        for entity_id, extension in people.items()
        if entity_id in entity_object_id
    }

    events = [
        row
        for row in session.scalars(
            select(Event)
            .where(Event.draft_id == owned.draft.id)
            .order_by(Event.narrative_order)
        )
        if row.object_registry_id in registry_by_id
    ]
    event_object_id = {
        row.id: stable_by_registry_id[row.object_registry_id] for row in events
    }

    information_units = [
        row
        for row in session.scalars(
            select(InformationUnit)
            .where(InformationUnit.draft_id == owned.draft.id)
            .order_by(InformationUnit.id)
        )
        if row.object_registry_id in registry_by_id
    ]
    information_object_id = {
        row.id: stable_by_registry_id[row.object_registry_id] for row in information_units
    }
    evidence = {
        row.information_unit_id: row
        for row in session.scalars(
            select(EvidenceItem).where(EvidenceItem.draft_id == owned.draft.id)
        )
    }
    testimonies = {
        row.information_unit_id: row
        for row in session.scalars(select(Testimony).where(Testimony.draft_id == owned.draft.id))
    }

    claims = [
        row
        for row in session.scalars(
            select(Claim).where(Claim.draft_id == owned.draft.id).order_by(Claim.id)
        )
        if row.object_registry_id in registry_by_id
    ]
    hypotheses = [
        row
        for row in session.scalars(
            select(Hypothesis).where(Hypothesis.draft_id == owned.draft.id).order_by(Hypothesis.id)
        )
        if row.object_registry_id in registry_by_id
    ]
    reasoning_paths = [
        row
        for row in session.scalars(
            select(ReasoningPath)
            .where(ReasoningPath.draft_id == owned.draft.id)
            .order_by(ReasoningPath.id)
        )
        if row.object_registry_id in registry_by_id
    ]
    nodes_by_path: dict[int, list[ReasoningNode]] = defaultdict(list)
    node_key_by_id: dict[int, str] = {}
    for node in session.scalars(
        select(ReasoningNode)
        .where(ReasoningNode.draft_id == owned.draft.id)
        .order_by(ReasoningNode.reasoning_path_id, ReasoningNode.ordinal)
    ):
        nodes_by_path[node.reasoning_path_id].append(node)
        node_key_by_id[node.id] = node.node_key
    edges_by_path: dict[int, list[ReasoningEdge]] = defaultdict(list)
    for edge in session.scalars(
        select(ReasoningEdge)
        .where(ReasoningEdge.draft_id == owned.draft.id)
        .order_by(ReasoningEdge.reasoning_path_id, ReasoningEdge.id)
    ):
        edges_by_path[edge.reasoning_path_id].append(edge)

    resolution = session.scalar(
        select(ResolutionSpec).where(ResolutionSpec.draft_id == owned.draft.id)
    )
    if resolution is not None and resolution.object_registry_id not in registry_by_id:
        resolution = None
    slots = [] if resolution is None else list(
        session.scalars(
            select(ResolutionSlot)
            .where(ResolutionSlot.resolution_spec_id == resolution.id)
            .order_by(ResolutionSlot.ordinal)
        )
    )

    constraints = [
        row
        for row in session.scalars(
            select(CaseFileConstraint)
            .where(CaseFileConstraint.draft_id == owned.draft.id)
            .order_by(CaseFileConstraint.id)
        )
        if row.object_registry_id in registry_by_id
    ]
    knowledge_states = [
        row
        for row in session.scalars(
            select(KnowledgeState)
            .where(KnowledgeState.draft_id == owned.draft.id)
            .order_by(KnowledgeState.id)
        )
        if row.object_registry_id in registry_by_id
    ]
    entries_by_state: dict[int, list[KnowledgeStateEntry]] = defaultdict(list)
    for entry in session.scalars(
        select(KnowledgeStateEntry)
        .where(KnowledgeStateEntry.draft_id == owned.draft.id)
        .order_by(KnowledgeStateEntry.knowledge_state_id, KnowledgeStateEntry.ordinal)
    ):
        entries_by_state[entry.knowledge_state_id].append(entry)

    document: dict[str, Any] = {
        "casefile": {
            "schema_version": owned.casefile.schema_version,
            "title": owned.casefile.title,
        },
        "narrative_phases": [
            {
                "object_id": stable_by_registry_id[row.object_registry_id],
                "name": row.name,
                "phase_order": row.phase_order,
                "description": row.description,
                "release_rule": row.release_rule_jsonb,
                "status": row.status,
                **_common(registry_by_id[row.object_registry_id]),
            }
            for row in phases
        ],
        "entities": [
            _entity_document(
                row,
                registry_by_id[row.object_registry_id],
                people.get(row.id),
                locations.get(row.id),
                refs,
            )
            for row in sorted(
                entities, key=lambda item: stable_by_registry_id[item.object_registry_id]
            )
        ],
        "events": [
            {
                "object_id": stable_by_registry_id[row.object_registry_id],
                "title": row.title,
                "summary": row.summary,
                "start_time": row.start_time_jsonb,
                "end_time": row.end_time_jsonb,
                "narrative_order": row.narrative_order,
                "narrative_phase_object_id": _optional_lookup(
                    phase_object_id, row.narrative_phase_id
                ),
                "location_object_id": _optional_lookup(location_object_id, row.location_id),
                "actor_object_ids": refs[(row.object_registry_id, "event_actor")],
                "visibility": row.visibility,
                "truth_status": row.truth_status,
                **_common(registry_by_id[row.object_registry_id]),
            }
            for row in events
        ],
        "information_units": [
            _information_document(
                row,
                registry_by_id[row.object_registry_id],
                phase_object_id,
                event_object_id,
                person_object_id,
                evidence.get(row.id),
                testimonies.get(row.id),
                refs,
            )
            for row in sorted(
                information_units,
                key=lambda item: stable_by_registry_id[item.object_registry_id],
            )
        ],
        "claims": [
            {
                "object_id": stable_by_registry_id[row.object_registry_id],
                "statement": row.statement,
                "status": row.status,
                **_common(registry_by_id[row.object_registry_id]),
            }
            for row in sorted(
                claims, key=lambda item: stable_by_registry_id[item.object_registry_id]
            )
        ],
        "hypotheses": [
            {
                "object_id": stable_by_registry_id[row.object_registry_id],
                "title": row.title,
                "summary": row.summary,
                "status": row.status,
                "score": _number(row.score),
                "exclusion_rule": row.exclusion_rule_jsonb,
                "claim_object_ids": refs[(row.object_registry_id, "hypothesis_claim")],
                "required_information_object_ids": refs[
                    (row.object_registry_id, "hypothesis_required_information")
                ],
                **_common(registry_by_id[row.object_registry_id]),
            }
            for row in sorted(
                hypotheses, key=lambda item: stable_by_registry_id[item.object_registry_id]
            )
        ],
        "reasoning_paths": [
            _reasoning_document(
                row,
                registry_by_id[row.object_registry_id],
                nodes_by_path[row.id],
                edges_by_path[row.id],
                stable_by_registry_id,
                node_key_by_id,
            )
            for row in sorted(
                reasoning_paths,
                key=lambda item: stable_by_registry_id[item.object_registry_id],
            )
        ],
        "resolution_spec": None
        if resolution is None
        else {
            "object_id": stable_by_registry_id[resolution.object_registry_id],
            "question_type": resolution.question_type,
            "target_question": resolution.target_question,
            "conclusion_pattern": resolution.conclusion_pattern_jsonb,
            "status": resolution.status,
            "slots": [
                {
                    "slot_key": slot.slot_key,
                    "label": slot.label,
                    "is_required": slot.is_required,
                    "ordinal": slot.ordinal,
                    "value": slot.value_jsonb,
                }
                for slot in slots
            ],
            **_common(registry_by_id[resolution.object_registry_id]),
        },
        "constraints": [
            {
                "object_id": stable_by_registry_id[row.object_registry_id],
                "target_object_id": _optional_lookup(
                    stable_by_registry_id, row.target_object_id
                ),
                "constraint_kind": row.constraint_kind,
                "constraint_level": row.constraint_level,
                "rule": row.rule_jsonb,
                "status": row.status,
                "conflict_status": row.conflict_status,
                **_common(registry_by_id[row.object_registry_id]),
            }
            for row in sorted(
                constraints, key=lambda item: stable_by_registry_id[item.object_registry_id]
            )
        ],
        "knowledge_states": [
            {
                "object_id": stable_by_registry_id[row.object_registry_id],
                "entity_object_id": entity_object_id.get(row.entity_id),
                "narrative_phase_object_id": phase_object_id.get(row.narrative_phase_id),
                "status": row.status,
                "notes": row.notes,
                "entries": [
                    {
                        "information_unit_object_id": information_object_id.get(
                            entry.information_unit_id
                        ),
                        "cognition_status": entry.cognition_status,
                        "disclosure_status": entry.disclosure_status,
                        "acquired_from_object_id": _optional_lookup(
                            stable_by_registry_id,
                            entry.acquired_from_object_id
                        ),
                        "certainty": _number(entry.certainty),
                        "ordinal": entry.ordinal,
                    }
                    for entry in entries_by_state[row.id]
                ],
                **_common(registry_by_id[row.object_registry_id]),
            }
            for row in sorted(
                knowledge_states,
                key=lambda item: stable_by_registry_id[item.object_registry_id],
            )
        ],
    }
    validate_casefile(document)
    return document


def casefile_content_hash(document: dict[str, Any]) -> str:
    """Return the lowercase SHA-256 of RFC 8785 canonical bytes."""

    return hashlib.sha256(rfc8785.dumps(document)).hexdigest()


def _entity_document(
    row: Entity,
    registry: CaseFileObject,
    person: Person | None,
    location: Location | None,
    refs: dict[tuple[int, str], list[str]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "object_id": registry.object_id,
        "entity_kind": row.entity_kind,
        "name": row.name,
        "description": row.description,
        "traits": row.traits_jsonb,
        "attributes": row.attributes_jsonb,
        **_common(registry),
    }
    if person is not None:
        result["person"] = {"role": person.role, "background": person.background}
    if location is not None:
        result["location"] = {
            "geo": location.geo_jsonb,
            "movement_rules": location.movement_rules_jsonb,
            "adjacent_location_object_ids": refs[(registry.id, "location_adjacent_to")],
        }
    return result


def _information_document(
    row: InformationUnit,
    registry: CaseFileObject,
    phase_object_id: dict[int, str],
    event_object_id: dict[int, str],
    person_object_id: dict[int, str],
    evidence: EvidenceItem | None,
    testimony: Testimony | None,
    refs: dict[tuple[int, str], list[str]],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "object_id": registry.object_id,
        "information_kind": row.information_kind,
        "title": row.title,
        "body_text": row.body_text,
        "source_credibility": _number(row.source_credibility),
        "visible_from_phase_object_id": _optional_lookup(
            phase_object_id, row.visible_from_phase_id
        ),
        "is_misleading": row.is_misleading,
        "status": row.status,
        "supports_claim_object_ids": refs[(registry.id, "supports")],
        "refutes_claim_object_ids": refs[(registry.id, "refutes")],
        **_common(registry),
    }
    if evidence is not None:
        result["evidence"] = {
            "evidence_kind": evidence.evidence_kind,
            "source_event_object_id": _optional_lookup(
                event_object_id, evidence.source_event_id
            ),
        }
    if testimony is not None:
        result["testimony"] = {
            "speaker_person_object_id": person_object_id.get(testimony.speaker_person_id),
            "quote_text": testimony.quote_text,
            "audio_asset_ref": testimony.audio_asset_ref,
        }
    return result


def _reasoning_document(
    row: ReasoningPath,
    registry: CaseFileObject,
    nodes: list[ReasoningNode],
    edges: list[ReasoningEdge],
    stable_by_registry_id: dict[int, str],
    node_key_by_id: dict[int, str],
) -> dict[str, Any]:
    return {
        "object_id": registry.object_id,
        "name": row.name,
        "reasoning_type": row.reasoning_type,
        "status": row.status,
        "confidence": _number(row.confidence),
        "human_confirmed": row.human_confirmed,
        "summary": row.summary,
        "nodes": [
            {
                "node_key": node.node_key,
                "ordinal": node.ordinal,
                "source_object_id": _optional_lookup(
                    stable_by_registry_id, node.source_object_id
                ),
                "node_type": node.node_type,
                "statement": node.statement,
                "attributes": node.attributes_jsonb,
            }
            for node in nodes
        ],
        "edges": [
            {
                "from_node_key": node_key_by_id.get(edge.from_node_id),
                "to_node_key": node_key_by_id.get(edge.to_node_id),
                "argument_kind": edge.argument_kind,
                "confidence": _number(edge.confidence),
                "human_confirmed": edge.human_confirmed,
                "attributes": edge.attributes_jsonb,
            }
            for edge in edges
        ],
        "source": registry.source_jsonb,
        "confirmation_status": registry.confirmation_status,
    }


def _common(registry: CaseFileObject) -> dict[str, Any]:
    return {
        "source": registry.source_jsonb,
        "confidence": _number(registry.confidence),
        "confirmation_status": registry.confirmation_status,
    }


def _number(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def _optional_lookup(values: dict[int, str], key: int | None) -> str | None:
    return None if key is None else values.get(key)
