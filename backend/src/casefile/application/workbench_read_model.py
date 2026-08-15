"""Read-only current-Draft validation, source provenance, and audit facts."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.application.casefile_v1 import COLLECTION_TYPES
from casefile.application.errors import not_found
from casefile.application.snapshot import build_casefile_document
from casefile.contracts import ContractValidationError, public_validation_issues
from casefile.data_postgres.models import (
    AuditEvent,
    BriefVersion,
    CaseFileObject,
    DraftOperation,
    SourceRecord,
)
from casefile.data_postgres.repositories import OwnedDraft, ProjectRepository

_AUDIT_LIMIT = 100
_COLLECTION_OBJECT_TYPES = dict(COLLECTION_TYPES)
_OBJECT_PATH = re.compile(r"^/([^/]+)/(\d+)(/.*)?$")


class WorkbenchReadModel:
    """Assemble owner-filtered workbench facts without changing project state."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

    def get_context(self, actor_user_id: int, project_id: int) -> dict[str, Any]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id)
            if owned is None:
                raise not_found("Project")

            document, validation = self._validation(owned)
            return {
                "project_id": owned.project.id,
                "draft_id": owned.draft.id,
                "draft_revision": owned.draft.revision,
                "validation": validation,
                "sources": self._sources(owned),
                "contract_source_refs": _contract_source_refs(document),
                "audit_entries": self._audit_entries(owned),
            }

    def _validation(self, owned: OwnedDraft) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if owned.draft.brief_version_id is None:
            return None, {
                "status": "unavailable",
                "validator": "casefile.contracts.validate_casefile",
                "schema_version": owned.draft.schema_version,
                "issue_count": 0,
                "issues": [],
                "reason": "draft_has_no_confirmed_brief",
            }
        try:
            document = build_casefile_document(self.session, owned)
        except ContractValidationError as error:
            object_index = self._validation_object_index(owned)
            issues = [
                _validation_issue(item, object_index)
                for item in public_validation_issues(error.errors)
            ]
            return None, {
                "status": "failed",
                "validator": "casefile.contracts.validate_casefile",
                "schema_version": owned.draft.schema_version,
                "issue_count": len(issues),
                "issues": issues,
                "reason": None,
            }
        return document, {
            "status": "passed",
            "validator": "casefile.contracts.validate_casefile",
            "schema_version": str(document["schema_version"]),
            "issue_count": 0,
            "issues": [],
            "reason": None,
        }

    def _validation_object_index(
        self,
        owned: OwnedDraft,
    ) -> dict[tuple[str, int], dict[str, str]]:
        rows = list(
            self.session.scalars(
                select(CaseFileObject)
                .where(
                    CaseFileObject.draft_id == owned.draft.id,
                    CaseFileObject.deleted_at.is_(None),
                )
                .order_by(CaseFileObject.object_type, CaseFileObject.contract_ordinal)
            )
        )
        positions: dict[str, int] = {}
        result: dict[tuple[str, int], dict[str, str]] = {}
        for row in rows:
            index = positions.get(row.object_type, 0)
            positions[row.object_type] = index + 1
            result[(row.object_type, index)] = {
                "object_type": row.object_type,
                "object_id": row.object_id,
            }
        return result

    def _sources(self, owned: OwnedDraft) -> list[dict[str, Any]]:
        version_id = owned.draft.brief_version_id
        if version_id is None:
            return []
        version = self.session.scalar(
            select(BriefVersion).where(
                BriefVersion.id == version_id,
                BriefVersion.project_id == owned.project.id,
            )
        )
        if version is None:
            return []

        source_ids = _source_record_ids(version.content_jsonb)
        if not source_ids:
            return []
        rows = list(
            self.session.scalars(
                select(SourceRecord).where(
                    SourceRecord.project_id == owned.project.id,
                    SourceRecord.id.in_(source_ids),
                )
            )
        )
        by_id = {row.id: row for row in rows}
        return [_source_view(by_id[source_id]) for source_id in source_ids if source_id in by_id]

    def _audit_entries(self, owned: OwnedDraft) -> list[dict[str, Any]]:
        audit_events = list(
            self.session.scalars(
                select(AuditEvent)
                .where(AuditEvent.project_id == owned.project.id)
                .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
                .limit(_AUDIT_LIMIT)
            )
        )
        operations = list(
            self.session.scalars(
                select(DraftOperation)
                .where(DraftOperation.draft_id == owned.draft.id)
                .order_by(DraftOperation.created_at.desc(), DraftOperation.sequence_no.desc())
                .limit(_AUDIT_LIMIT)
            )
        )
        registry_ids = {
            row.casefile_object_id for row in operations if row.casefile_object_id is not None
        }
        object_ids: dict[int, str] = {}
        if registry_ids:
            registries = self.session.execute(
                select(CaseFileObject.id, CaseFileObject.object_id).where(
                    CaseFileObject.draft_id == owned.draft.id,
                    CaseFileObject.id.in_(registry_ids),
                )
            )
            object_ids = {row.id: row.object_id for row in registries}

        entries = [(row.occurred_at, _audit_event_view(row)) for row in audit_events]
        entries.extend(
            (row.created_at, _draft_operation_view(row, object_ids)) for row in operations
        )
        entries.sort(
            key=lambda item: (
                item[0],
                item[1]["source_table"],
                item[1]["record_id"],
            ),
            reverse=True,
        )
        return [entry for _occurred_at, entry in entries[:_AUDIT_LIMIT]]


def _validation_issue(
    issue: dict[str, str],
    object_index: dict[tuple[str, int], dict[str, str]],
) -> dict[str, Any]:
    target = _validation_target(issue["path"], object_index)
    object_ref = target["object_ref"]
    if object_ref is None:
        location_identity = issue["path"]
    else:
        location_identity = "\x00".join(
            (
                object_ref["object_type"],
                object_ref["object_id"],
                target["field_path"],
            )
        )
    identity = "\x00".join((issue["code"], location_identity, issue["message"]))
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return {
        "issue_id": f"validator:{suffix}",
        "code": issue["code"],
        "path": issue["path"],
        "message": issue["message"],
        "severity": "error",
        "target": target,
    }


def _validation_target(
    path: str,
    object_index: dict[tuple[str, int], dict[str, str]],
) -> dict[str, Any]:
    match = _OBJECT_PATH.fullmatch(path)
    if match is None:
        return {"object_ref": None, "field_path": path}
    collection, raw_index, field_path = match.groups()
    object_type = _COLLECTION_OBJECT_TYPES.get(collection)
    if object_type is None:
        return {"object_ref": None, "field_path": path}
    object_ref = object_index.get((object_type, int(raw_index)))
    if object_ref is None:
        return {"object_ref": None, "field_path": path}
    return {"object_ref": object_ref, "field_path": field_path or ""}


def _source_record_ids(content: dict[str, Any]) -> list[int]:
    raw_ids = content.get("source_record_ids", [])
    if not isinstance(raw_ids, list):
        return []
    result: list[int] = []
    for value in raw_ids:
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value > 0
            and value not in result
        ):
            result.append(value)
    return result


def _source_view(source: SourceRecord) -> dict[str, Any]:
    return {
        "trace_id": f"source_records:{source.id}",
        "source_table": "source_records",
        "source_record_id": source.id,
        "source_kind": source.source_kind,
        "content_text": source.content_text,
        "content_hash": source.content_hash,
        "parent_source_record_id": source.parent_source_record_id,
        "generated_by_task_run_id": source.generated_by_task_run_id,
        "created_by_user_id": source.created_by_user_id,
        "created_at": source.created_at.isoformat(),
    }


def _audit_event_view(event: AuditEvent) -> dict[str, Any]:
    return {
        "entry_id": f"audit_events:{event.id}",
        "source_table": "audit_events",
        "record_id": event.id,
        "occurred_at": event.occurred_at.isoformat(),
        "actor": _actor_view(event.actor_kind, event.actor_user_id, event.actor_ref),
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "trace_id": event.trace_id,
        "details": event.details_jsonb,
    }


def _draft_operation_view(operation: DraftOperation, object_ids: dict[int, str]) -> dict[str, Any]:
    object_id = (
        object_ids.get(operation.casefile_object_id)
        if operation.casefile_object_id is not None
        else None
    )
    return {
        "entry_id": f"draft_operations:{operation.id}",
        "source_table": "draft_operations",
        "record_id": operation.id,
        "occurred_at": operation.created_at.isoformat(),
        "actor": _actor_view(operation.actor_kind, operation.actor_user_id, operation.actor_ref),
        "action": operation.operation_type,
        "target_type": "casefile_object" if object_id else "draft",
        "target_id": object_id or operation.draft_id,
        "trace_id": None,
        "details": {
            "sequence_no": operation.sequence_no,
            "operation_group_no": operation.operation_group_no,
            "field_path": operation.field_path,
            "object_id": object_id,
            "base_revision": operation.base_revision,
            "result_revision": operation.result_revision,
        },
    }


def _actor_view(
    actor_kind: str, actor_user_id: int | None, actor_ref: str | None
) -> dict[str, Any]:
    return {"kind": actor_kind, "user_id": actor_user_id, "ref": actor_ref}


def _contract_source_refs(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    if document is None:
        return []
    paths_by_id: dict[str, list[str]] = {}
    for path, value in _walk_values(document):
        if (
            isinstance(value, dict)
            and value.get("object_type") == "source_fragment"
            and isinstance(value.get("object_id"), str)
        ):
            source_id = value["object_id"]
            paths_by_id.setdefault(source_id, []).append(path)
    return [
        {"source_fragment_id": source_id, "paths": paths}
        for source_id, paths in sorted(paths_by_id.items())
    ]


def _walk_values(value: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from _walk_values(child, f"{path}/{escaped}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_values(child, f"{path}/{index}")
