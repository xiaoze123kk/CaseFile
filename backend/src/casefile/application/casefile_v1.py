"""Lossless v1 CaseFile persistence and normalized-state projection."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterator
from copy import deepcopy
from decimal import Decimal
from typing import Any

import rfc8785
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.application.errors import ApplicationError
from casefile.contracts import validate_casefile
from casefile.contracts.object_types import COLLECTION_TYPES as COLLECTION_TYPES
from casefile.data_postgres.models import (
    AuditEvent,
    Brief,
    BriefVersion,
    CaseFileConstraint,
    CaseFileContractRef,
    CaseFileObject,
    Claim,
    Draft,
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

KNOWLEDGE_STATE_COUNT_ATTRIBUTE = "_casefile_v1_knowledge_state_count"
EVIDENCE_ASSESSMENTS_PRESENT_ATTRIBUTE = "_casefile_v1_evidence_assessments_present"


def casefile_content_hash(document: dict[str, Any]) -> str:
    """Return the lowercase SHA-256 of RFC 8785 canonical bytes."""

    return hashlib.sha256(rfc8785.dumps(document)).hexdigest()


def generation_candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return the stable, user-facing summary persisted on a generation TaskRun."""

    validate_casefile(candidate)
    return {
        "title": candidate["title"],
        "content_hash": casefile_content_hash(candidate),
        "object_counts": {
            collection: len(candidate[collection]) for collection, _object_type in COLLECTION_TYPES
        },
        "reasoning_questions": [
            item["reasoning_question"] for item in candidate["resolution_specs"]
        ],
        "constraint_statements": [item["statement"] for item in candidate["constraints"]],
    }


def validate_generation_candidate_context(
    owned: OwnedDraft,
    candidate: dict[str, Any],
    *,
    brief: Brief,
    brief_version: BriefVersion,
) -> None:
    """Validate one candidate against its CaseFile, Brief, and Draft version context."""

    validate_casefile(candidate)
    _validate_generation_context(owned, candidate, brief, brief_version)


def prepare_generation_candidate(
    owned: OwnedDraft,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Return a candidate in the target Draft schema without mutating stored history."""

    if candidate.get("schema_version") == owned.draft.schema_version:
        return candidate
    if candidate.get("schema_version") == "1.0" and owned.draft.schema_version == "2.0":
        upgraded = _upgrade_casefile_v1_to_v2(candidate)
        validate_casefile(upgraded)
        return upgraded
    return candidate


def _upgrade_casefile_v1_to_v2(candidate: dict[str, Any]) -> dict[str, Any]:
    upgraded = deepcopy(candidate)
    upgraded["schema_version"] = "2.0"
    for event in upgraded["events"]:
        legacy = event["time"]
        precision = legacy["precision"]
        if precision == "unknown":
            event["time"] = {"kind": "unknown"}
            continue
        wall_precision = precision if precision in {"day", "hour", "minute", "second"} else "second"
        start = _legacy_wall_clock(legacy["start"], wall_precision)
        end = legacy["end"]
        if end is not None:
            event["time"] = {
                "kind": "range",
                "start": start,
                "end": _legacy_wall_clock(end, wall_precision),
                "precision": wall_precision,
            }
        elif precision == "approximate":
            event["time"] = {
                "kind": "approximate",
                "value": start,
                "precision": wall_precision,
            }
        else:
            event["time"] = {
                "kind": "exact",
                "value": start,
                "precision": wall_precision,
            }
    return upgraded


def _legacy_wall_clock(value: str, precision: str) -> str:
    local = re.sub(r"(?:Z|[+-][0-9]{2}:[0-9]{2})$", "", value)
    lengths = {"day": 10, "hour": 13, "minute": 16}
    return local[: lengths[precision]] if precision in lengths else local


def adopt_generation_candidate(
    session: Session,
    source: OwnedDraft,
    current: OwnedDraft,
    *,
    candidate: dict[str, Any],
    brief: Brief,
    brief_version: BriefVersion,
    task_run_id: int,
    actor_user_id: int,
    expected_current_draft_id: int,
) -> DraftSnapshot:
    """Materialize a candidate as an independent Draft and select it atomically."""

    candidate = prepare_generation_candidate(source, candidate)
    validate_generation_candidate_context(
        source,
        candidate,
        brief=brief,
        brief_version=brief_version,
    )
    if current.casefile.current_draft_id != expected_current_draft_id:
        raise ApplicationError(
            "current_draft_changed",
            "当前工作稿已在其他位置切换，请刷新后重试。",
            status_code=409,
            details={"current_draft_id": current.casefile.current_draft_id},
        )
    if current.project.status == "archived" or current.casefile.status == "archived":
        raise ApplicationError(
            "project_archived",
            "已归档的项目不能修改。",
            status_code=409,
        )
    if source.draft.status != "active":
        raise ApplicationError(
            "draft_locked",
            "候选来源工作稿已锁定，不能采用该候选。",
            status_code=409,
        )

    pristine_current = (
        source.draft.id == current.draft.id
        and source.draft.id == expected_current_draft_id
        and current.draft.brief_version_id is None
        and current.draft.revision == 1
        and session.scalar(
            select(func.count(CaseFileObject.id)).where(CaseFileObject.draft_id == current.draft.id)
        )
        == 0
        and session.scalar(
            select(func.count(DraftOperation.id)).where(DraftOperation.draft_id == current.draft.id)
        )
        == 0
    )
    if pristine_current:
        target = current
    else:
        target_draft = Draft(
            project_id=current.project.id,
            casefile_id=current.casefile.id,
            base_canon_version_id=source.draft.base_canon_version_id,
            revision=1,
            title=candidate["title"],
            document_status=candidate["status"],
            version_id=candidate["version"]["version_id"],
            version_no=candidate["version"]["version_no"],
            parent_version_id=candidate["version"]["parent_version_id"],
            brief_version_id=brief_version.id,
            schema_version=candidate["schema_version"],
            status="active",
            content_notices_jsonb=candidate["content_notices"],
            extensions_jsonb=candidate["extensions"],
        )
        session.add(target_draft)
        session.flush()
        target = OwnedDraft(current.project, current.casefile, target_draft)

    target.draft.title = candidate["title"]
    target.draft.document_status = candidate["status"]
    target.draft.schema_version = candidate["schema_version"]
    target.draft.version_id = candidate["version"]["version_id"]
    target.draft.version_no = candidate["version"]["version_no"]
    target.draft.parent_version_id = candidate["version"]["parent_version_id"]
    target.draft.brief_version_id = brief_version.id
    target.draft.content_notices_jsonb = candidate["content_notices"]
    target.draft.extensions_jsonb = candidate["extensions"]

    registry_by_object_id = _create_registries(session, target, candidate)
    _create_content_rows(session, target, candidate, registry_by_object_id)
    _create_contract_refs(session, target, candidate, registry_by_object_id)

    base_revision = target.draft.revision

    content_hash = casefile_content_hash(candidate)
    sequence_no = int(
        session.scalar(
            select(func.coalesce(func.max(DraftOperation.sequence_no), 0) + 1).where(
                DraftOperation.draft_id == target.draft.id
            )
        )
        or 1
    )
    session.add(
        DraftOperation(
            project_id=target.project.id,
            casefile_id=target.casefile.id,
            draft_id=target.draft.id,
            casefile_object_id=None,
            sequence_no=sequence_no,
            operation_group_no=sequence_no,
            operation_type="agent_adopt_brief_candidate",
            field_path="",
            old_value_jsonb=None,
            new_value_jsonb={
                "brief_version_id": brief_version.id,
                "content_hash": content_hash,
                "task_run_id": task_run_id,
            },
            base_revision=base_revision,
            result_revision=base_revision + 1,
            actor_kind="user",
            actor_user_id=actor_user_id,
            actor_ref=None,
        )
    )
    previous_draft_id = current.casefile.current_draft_id
    current.casefile.current_draft_id = target.draft.id
    if target.draft.id != previous_draft_id:
        session.add(
            AuditEvent(
                project_id=current.project.id,
                casefile_id=current.casefile.id,
                actor_kind="user",
                actor_user_id=actor_user_id,
                actor_ref=None,
                action="draft.activated",
                target_type="draft",
                target_id=target.draft.id,
                trace_id=None,
                details_jsonb={
                    "previous_draft_id": previous_draft_id,
                    "task_run_id": task_run_id,
                },
            )
        )
    session.flush()
    session.refresh(target.draft)

    projected = build_casefile_document(session, target)
    projected_hash = casefile_content_hash(projected)
    if projected != candidate or projected_hash != content_hash:
        raise ApplicationError(
            "casefile_roundtrip_mismatch",
            "标准化后的 CaseFile 投影与已校验候选不一致。",
            status_code=500,
            details={"candidate_hash": content_hash, "projected_hash": projected_hash},
        )

    snapshot = DraftSnapshot(
        project_id=target.project.id,
        casefile_id=target.casefile.id,
        draft_id=target.draft.id,
        snapshot_revision=target.draft.revision,
        schema_version=target.draft.schema_version,
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
            "当前草稿没有指向已确认的创作简报版本。",
            status_code=409,
        )

    document: dict[str, Any] = {
        "schema_version": owned.draft.schema_version,
        "casefile_id": owned.casefile.object_id,
        "title": owned.draft.title,
        "status": owned.draft.document_status,
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
    expected_version = {
        "version_id": owned.draft.version_id,
        "version_no": owned.draft.version_no,
        "parent_version_id": owned.draft.parent_version_id,
    }
    failures: dict[str, Any] = {}
    if candidate["casefile_id"] != owned.casefile.object_id:
        failures["casefile_id"] = owned.casefile.object_id
    if candidate["schema_version"] != owned.draft.schema_version:
        failures["schema_version"] = owned.draft.schema_version
    if candidate["brief_ref"] != expected_brief_ref:
        failures["brief_ref"] = expected_brief_ref
    if candidate["version"] != expected_version:
        failures["version"] = expected_version
    if candidate["status"] != "draft":
        failures["status"] = "draft"
    if failures:
        raise ApplicationError(
            "generation_context_mismatch",
            "生成的 CaseFile 与任务冻结时的上下文不一致。",
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
        ordinal_offset = int(
            session.scalar(
                select(func.coalesce(func.max(CaseFileObject.contract_ordinal), 0)).where(
                    CaseFileObject.draft_id == owned.draft.id,
                    CaseFileObject.object_type == object_type,
                )
            )
            or 0
        )
        for ordinal, item in enumerate(candidate[collection], start=ordinal_offset + 1):
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


def create_casefile_objects(
    session: Session,
    owned: OwnedDraft,
    objects_by_collection: dict[str, list[dict[str, Any]]],
) -> dict[str, CaseFileObject]:
    """Materialize validated new objects without replacing the current Draft."""

    unknown = set(objects_by_collection) - {name for name, _ in COLLECTION_TYPES}
    if unknown:
        raise ApplicationError(
            "casefile_collection_invalid",
            "创建对象包含不受支持的集合。",
            status_code=422,
            details={"collections": sorted(unknown)},
        )
    partial = {
        collection: deepcopy(objects_by_collection.get(collection, []))
        for collection, _ in COLLECTION_TYPES
    }
    registries = _create_registries(session, owned, partial)
    _create_content_rows(session, owned, partial, registries)
    _create_contract_refs(session, owned, partial, registries)
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
    # Event.narrative_order is a normalized-table uniqueness key, while the
    # public CaseFile contract deliberately derives event order from the
    # collection ordinal.  Archived candidate rows remain available for audit,
    # so start each new projection after their largest internal ordinal instead
    # of reusing 1 on every candidate adoption.
    event_order_offset = int(
        session.scalar(
            select(func.coalesce(func.max(Event.narrative_order), 0)).where(
                Event.draft_id == owned.draft.id
            )
        )
        or 0
    )
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
            conclusion_outcome=(item.get("conclusion") or {}).get("outcome"),
            conclusion_review_status=(item.get("conclusion") or {}).get("review_status"),
            conclusion_summary=(item.get("conclusion") or {}).get("summary"),
            conclusion_rationale=(item.get("conclusion") or {}).get("rationale"),
            conclusion_unresolved_gaps_jsonb=(item.get("conclusion") or {}).get(
                "unresolved_gaps", []
            ),
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
                    value_jsonb=_resolution_slot_scalar_value(item, slot["slot_id"]),
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
                # ObjectRef values live in CaseFileContractRef. Retain the
                # complete nested-list shape here so an all-empty state does
                # not disappear during a normalized round trip.
                attributes_jsonb={KNOWLEDGE_STATE_COUNT_ATTRIBUTE: len(item["knowledge_states"])},
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
                geo_jsonb=item.get("spatial_position", {}),
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
                narrative_order=event_order_offset + ordinal,
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
                # An all-empty optional list has no ContractRef row. Preserve
                # its presence separately so legacy snapshots that omitted
                # the field and newer candidates that explicitly supplied []
                # both round trip exactly without a schema migration.
                exclusion_rule_jsonb={
                    EVIDENCE_ASSESSMENTS_PRESENT_ATTRIBUTE: "evidence_assessments" in item
                },
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
        metadata: dict[str, Any] = {}
        if parent is not None:
            for key in ("minutes", "effect", "strength", "rationale", "slot_id"):
                if key in parent:
                    metadata[key] = parent[key]
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


def iter_contract_object_refs(
    value: Any,
) -> Iterator[tuple[str, int, dict[str, str], dict[str, Any]]]:
    """Yield normalized ObjectRef edges from one complete v1 object."""

    yield from _walk_object_refs(value)


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
        projected = {
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
        conclusion = _project_resolution_conclusion(row, slots, refs[registry.id])
        if conclusion is not None:
            projected["conclusion"] = conclusion
        result.append(projected)
    return result


def _resolution_slot_scalar_value(item: dict[str, Any], slot_id: str) -> Any | None:
    conclusion = item.get("conclusion")
    if conclusion is None:
        return None
    for value in conclusion["values"]:
        if value["slot_id"] == slot_id and not _is_object_ref(value["value"]):
            return value["value"]
    return None


def _project_resolution_conclusion(
    row: ResolutionSpec,
    slots: list[ResolutionSlot],
    refs: list[CaseFileContractRef],
) -> dict[str, Any] | None:
    if row.conclusion_outcome is None:
        return None
    object_values = {
        str(ref.metadata_jsonb.get("slot_id")): _ref_value(ref)
        for ref in refs
        if ref.field_path.startswith("/conclusion/values/")
        and ref.field_path.endswith("/value")
        and ref.metadata_jsonb.get("slot_id")
    }
    values = []
    for slot in slots:
        value = object_values.get(slot.slot_key, slot.value_jsonb)
        if value is not None:
            values.append({"slot_id": slot.slot_key, "value": value})
    return {
        "outcome": row.conclusion_outcome,
        "review_status": row.conclusion_review_status,
        "summary": row.conclusion_summary,
        "values": values,
        "selected_hypothesis_refs": _ref_values(refs, "/conclusion/selected_hypothesis_refs"),
        "supporting_reasoning_path_refs": _ref_values(
            refs, "/conclusion/supporting_reasoning_path_refs"
        ),
        "rationale": row.conclusion_rationale,
        "unresolved_gaps": row.conclusion_unresolved_gaps_jsonb,
    }


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
        state_indices = _knowledge_state_indices(row, refs[registry.id])
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
        location = {
            **_common(registry, refs),
            "id": registry.object_id,
            "name": row.name,
            "parent_ref": _optional_ref(refs[registry.id], "/parent_ref"),
            "adjacency_refs": _ref_values(refs[registry.id], "/adjacency_refs"),
            "access_rules": row.access_rules_jsonb,
            "travel_times": travel_times,
            "visibility_rules": row.visibility_rules_jsonb,
        }
        if row.geo_jsonb:
            location["spatial_position"] = row.geo_jsonb
        result.append(location)
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
            assessment_indices = _path_indices(
                refs[registry.id], r"^/evidence_assessments/(\d+)/information_ref$"
            )
            evidence_assessments = []
            for index in assessment_indices:
                ref_row = _single_ref_row(
                    refs[registry.id], f"/evidence_assessments/{index}/information_ref"
                )
                evidence_assessments.append(
                    {
                        "information_ref": _ref_value(ref_row),
                        "effect": ref_row.metadata_jsonb["effect"],
                        "strength": ref_row.metadata_jsonb["strength"],
                        "rationale": ref_row.metadata_jsonb["rationale"],
                    }
                )
            hypothesis = {
                **_common(registry, refs),
                "id": registry.object_id,
                "title": row.title,
                "proposition": row.summary,
                "target_resolution_ref": _single_ref(refs[registry.id], "/target_resolution_ref"),
                "required_claim_refs": _ref_values(refs[registry.id], "/required_claim_refs"),
                "falsifier_refs": _ref_values(refs[registry.id], "/falsifier_refs"),
                "competing_hypothesis_refs": _ref_values(
                    refs[registry.id], "/competing_hypothesis_refs"
                ),
                "status": row.status,
                "score": _number(row.score),
            }
            if (
                row.exclusion_rule_jsonb.get(EVIDENCE_ASSESSMENTS_PRESENT_ATTRIBUTE) is True
                or evidence_assessments
            ):
                hypothesis["evidence_assessments"] = evidence_assessments
            result.append(hypothesis)
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
            f"字段 {field_path} 应恰好对应一个标准化引用。",
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


def _knowledge_state_indices(
    entity: Entity,
    refs: list[CaseFileContractRef],
) -> list[int]:
    state_count = entity.attributes_jsonb.get(KNOWLEDGE_STATE_COUNT_ATTRIBUTE)
    if isinstance(state_count, int) and not isinstance(state_count, bool) and state_count >= 0:
        return list(range(state_count))
    return _path_indices(
        refs,
        (
            r"^/knowledge_states/(\d+)/(?:as_of_event_ref|knows_refs|"
            r"believes_refs|false_belief_refs)"
        ),
    )


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
