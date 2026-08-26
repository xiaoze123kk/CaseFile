"""Persistence boundary for versioned linear Exposure Plans."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.data_postgres.models import (
    CaseFileObject,
    ExposurePlan,
    ExposurePlanEntry,
    ExposurePlanEntryRef,
    ExposurePlanObligation,
    ExposurePlanObligationRef,
    ExposurePlanRevision,
)
from casefile.data_postgres.repositories import OwnedDraft


@dataclass(frozen=True, slots=True)
class ExposureEntryWrite:
    """One validated entry and its registry identities for a new revision."""

    entry_key: str
    title: str
    note: str | None
    object_registry_ids: tuple[int, ...]
    obligations: tuple[ExposureObligationWrite, ...]


@dataclass(frozen=True, slots=True)
class ExposureObligationWrite:
    """One validated typed obligation with normalized registry identities."""

    obligation_key: str
    obligation_kind: str
    level: str
    min_distinct: int | None
    object_registry_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ExposureObligationRead:
    """One stored obligation plus its ordered referenced objects."""

    obligation: ExposurePlanObligation
    objects: tuple[CaseFileObject, ...]


@dataclass(frozen=True, slots=True)
class ExposureEntryRead:
    """One stored entry plus stable referenced CaseFile objects."""

    entry: ExposurePlanEntry
    objects: tuple[CaseFileObject, ...]
    obligations: tuple[ExposureObligationRead, ...]


class ExposurePlanRepository:
    """Read and append complete Exposure Plan revisions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_draft(
        self,
        owned: OwnedDraft,
        *,
        lock: bool = False,
    ) -> ExposurePlan | None:
        statement = select(ExposurePlan).where(
            ExposurePlan.project_id == owned.project.id,
            ExposurePlan.casefile_id == owned.casefile.id,
            ExposurePlan.draft_id == owned.draft.id,
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def create_blank(self, owned: OwnedDraft, actor_user_id: int) -> ExposurePlan:
        plan = ExposurePlan(
            project_id=owned.project.id,
            casefile_id=owned.casefile.id,
            draft_id=owned.draft.id,
            revision=0,
            current_revision_id=None,
            created_by_user_id=actor_user_id,
        )
        self.session.add(plan)
        self.session.flush()
        return plan

    def registries_by_object_id(
        self,
        owned: OwnedDraft,
        object_ids: set[str],
    ) -> dict[str, CaseFileObject]:
        if not object_ids:
            return {}
        rows = self.session.scalars(
            select(CaseFileObject).where(
                CaseFileObject.project_id == owned.project.id,
                CaseFileObject.casefile_id == owned.casefile.id,
                CaseFileObject.draft_id == owned.draft.id,
                CaseFileObject.object_id.in_(object_ids),
                CaseFileObject.deleted_at.is_(None),
            )
        )
        return {row.object_id: row for row in rows}

    def append_revision(
        self,
        owned: OwnedDraft,
        plan: ExposurePlan,
        actor_user_id: int,
        entries: list[ExposureEntryWrite],
    ) -> ExposurePlanRevision:
        revision_no = plan.revision + 1
        revision = ExposurePlanRevision(
            project_id=owned.project.id,
            casefile_id=owned.casefile.id,
            draft_id=owned.draft.id,
            plan_id=plan.id,
            revision_no=revision_no,
            payload_schema_id="casefile.exposure-plan.v2",
            created_by_user_id=actor_user_id,
        )
        self.session.add(revision)
        self.session.flush()

        for sequence_no, item in enumerate(entries, start=1):
            entry = ExposurePlanEntry(
                project_id=owned.project.id,
                casefile_id=owned.casefile.id,
                draft_id=owned.draft.id,
                plan_revision_id=revision.id,
                entry_key=item.entry_key,
                sequence_no=sequence_no,
                title=item.title,
                note=item.note,
            )
            self.session.add(entry)
            self.session.flush()
            self.session.add_all(
                ExposurePlanEntryRef(
                    project_id=owned.project.id,
                    casefile_id=owned.casefile.id,
                    draft_id=owned.draft.id,
                    entry_id=entry.id,
                    object_registry_id=registry_id,
                    ordinal=ordinal,
                )
                for ordinal, registry_id in enumerate(
                    item.object_registry_ids,
                    start=1,
                )
            )
            for obligation_write in item.obligations:
                obligation = ExposurePlanObligation(
                    project_id=owned.project.id,
                    casefile_id=owned.casefile.id,
                    draft_id=owned.draft.id,
                    plan_revision_id=revision.id,
                    entry_id=entry.id,
                    obligation_key=obligation_write.obligation_key,
                    obligation_kind=obligation_write.obligation_kind,
                    level=obligation_write.level,
                    min_distinct=obligation_write.min_distinct,
                )
                self.session.add(obligation)
                self.session.flush()
                self.session.add_all(
                    ExposurePlanObligationRef(
                        project_id=owned.project.id,
                        casefile_id=owned.casefile.id,
                        draft_id=owned.draft.id,
                        plan_revision_id=revision.id,
                        obligation_id=obligation.id,
                        object_registry_id=registry_id,
                        ordinal=ordinal,
                    )
                    for ordinal, registry_id in enumerate(
                        obligation_write.object_registry_ids,
                        start=1,
                    )
                )

        plan.revision = revision_no
        plan.current_revision_id = revision.id
        self.session.flush()
        return revision

    def read_current_entries(self, plan: ExposurePlan) -> list[ExposureEntryRead]:
        if plan.current_revision_id is None:
            return []
        entries = list(
            self.session.scalars(
                select(ExposurePlanEntry)
                .where(ExposurePlanEntry.plan_revision_id == plan.current_revision_id)
                .order_by(ExposurePlanEntry.sequence_no)
            )
        )
        if not entries:
            return []
        by_entry_id: dict[int, list[CaseFileObject]] = {entry.id: [] for entry in entries}
        reference_rows = self.session.execute(
            select(ExposurePlanEntryRef, CaseFileObject)
            .join(
                CaseFileObject,
                (CaseFileObject.project_id == ExposurePlanEntryRef.project_id)
                & (CaseFileObject.casefile_id == ExposurePlanEntryRef.casefile_id)
                & (CaseFileObject.draft_id == ExposurePlanEntryRef.draft_id)
                & (CaseFileObject.id == ExposurePlanEntryRef.object_registry_id),
            )
            .where(ExposurePlanEntryRef.entry_id.in_(by_entry_id))
            .order_by(ExposurePlanEntryRef.entry_id, ExposurePlanEntryRef.ordinal)
        )
        for reference, registry in reference_rows:
            by_entry_id[reference.entry_id].append(registry)
        obligations = list(
            self.session.scalars(
                select(ExposurePlanObligation)
                .where(ExposurePlanObligation.entry_id.in_(by_entry_id))
                .order_by(ExposurePlanObligation.entry_id, ExposurePlanObligation.id)
            )
        )
        by_obligation_id: dict[int, list[CaseFileObject]] = {
            obligation.id: [] for obligation in obligations
        }
        if by_obligation_id:
            obligation_rows = self.session.execute(
                select(ExposurePlanObligationRef, CaseFileObject)
                .join(
                    CaseFileObject,
                    (CaseFileObject.project_id == ExposurePlanObligationRef.project_id)
                    & (CaseFileObject.casefile_id == ExposurePlanObligationRef.casefile_id)
                    & (CaseFileObject.draft_id == ExposurePlanObligationRef.draft_id)
                    & (
                        CaseFileObject.id
                        == ExposurePlanObligationRef.object_registry_id
                    ),
                )
                .where(ExposurePlanObligationRef.obligation_id.in_(by_obligation_id))
                .order_by(
                    ExposurePlanObligationRef.obligation_id,
                    ExposurePlanObligationRef.ordinal,
                )
            )
            for reference, registry in obligation_rows:
                by_obligation_id[reference.obligation_id].append(registry)
        by_entry_obligations: dict[int, list[ExposureObligationRead]] = {
            entry.id: [] for entry in entries
        }
        for obligation in obligations:
            by_entry_obligations[obligation.entry_id].append(
                ExposureObligationRead(
                    obligation,
                    tuple(by_obligation_id[obligation.id]),
                )
            )
        return [
            ExposureEntryRead(
                entry,
                tuple(by_entry_id[entry.id]),
                tuple(by_entry_obligations[entry.id]),
            )
            for entry in entries
        ]


__all__ = [
    "ExposureEntryRead",
    "ExposureEntryWrite",
    "ExposureObligationRead",
    "ExposureObligationWrite",
    "ExposurePlanRepository",
]
