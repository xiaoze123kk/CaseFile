"""Persistence queries for Narrative Compiler profiles, runs, and artifacts."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.data_postgres.models import (
    CaseFileObject,
    CompileArtifact,
    CompilerProfile,
    CompilerProfileVersion,
    CompileRun,
    ExposurePlanEntry,
    ExposurePlanEntryRef,
    ExposurePlanRevision,
)


@dataclass(frozen=True, slots=True)
class FrozenExposureEntry:
    entry: ExposurePlanEntry
    refs: tuple[CaseFileObject, ...]


class CompilerRepository:
    """Compiler-specific persistence without semantic validation."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_profile(
        self, project_id: int, profile_id: int, *, lock: bool = False
    ) -> CompilerProfile | None:
        statement = select(CompilerProfile).where(
            CompilerProfile.project_id == project_id,
            CompilerProfile.id == profile_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get_profile_version(
        self, project_id: int, version_id: int
    ) -> CompilerProfileVersion | None:
        return self.session.scalar(
            select(CompilerProfileVersion).where(
                CompilerProfileVersion.project_id == project_id,
                CompilerProfileVersion.id == version_id,
            )
        )

    def list_profiles(self, project_id: int) -> list[CompilerProfile]:
        return list(
            self.session.scalars(
                select(CompilerProfile)
                .where(CompilerProfile.project_id == project_id)
                .order_by(CompilerProfile.profile_key, CompilerProfile.id)
            )
        )

    def list_profile_versions(self, profile_id: int) -> list[CompilerProfileVersion]:
        return list(
            self.session.scalars(
                select(CompilerProfileVersion)
                .where(CompilerProfileVersion.compiler_profile_id == profile_id)
                .order_by(CompilerProfileVersion.version_no)
            )
        )

    def get_exposure_revision(
        self,
        *,
        project_id: int,
        casefile_id: int,
        draft_id: int,
        revision_id: int,
    ) -> ExposurePlanRevision | None:
        return self.session.scalar(
            select(ExposurePlanRevision).where(
                ExposurePlanRevision.project_id == project_id,
                ExposurePlanRevision.casefile_id == casefile_id,
                ExposurePlanRevision.draft_id == draft_id,
                ExposurePlanRevision.id == revision_id,
            )
        )

    def read_exposure_revision_entries(
        self, revision_id: int
    ) -> list[FrozenExposureEntry]:
        entries = list(
            self.session.scalars(
                select(ExposurePlanEntry)
                .where(ExposurePlanEntry.plan_revision_id == revision_id)
                .order_by(ExposurePlanEntry.sequence_no, ExposurePlanEntry.id)
            )
        )
        by_entry: dict[int, list[CaseFileObject]] = {entry.id: [] for entry in entries}
        if by_entry:
            rows = self.session.execute(
                select(ExposurePlanEntryRef, CaseFileObject)
                .join(
                    CaseFileObject,
                    (CaseFileObject.project_id == ExposurePlanEntryRef.project_id)
                    & (CaseFileObject.casefile_id == ExposurePlanEntryRef.casefile_id)
                    & (CaseFileObject.draft_id == ExposurePlanEntryRef.draft_id)
                    & (CaseFileObject.id == ExposurePlanEntryRef.object_registry_id),
                )
                .where(ExposurePlanEntryRef.entry_id.in_(by_entry))
                .order_by(ExposurePlanEntryRef.entry_id, ExposurePlanEntryRef.ordinal)
            )
            for reference, registry in rows:
                by_entry[reference.entry_id].append(registry)
        return [FrozenExposureEntry(entry, tuple(by_entry[entry.id])) for entry in entries]

    def project_exposure_revision_payload(self, revision_id: int) -> dict[str, object]:
        """Project one exact immutable Exposure revision in canonical sequence order."""

        return {
            "entries": [
                {
                    "entry_key": item.entry.entry_key,
                    "sequence_no": item.entry.sequence_no,
                    "title": item.entry.title,
                    "note": item.entry.note,
                    "refs": [
                        {"object_type": ref.object_type, "object_id": ref.object_id}
                        for ref in item.refs
                    ],
                }
                for item in self.read_exposure_revision_entries(revision_id)
            ]
        }

    def get_run(self, project_id: int, run_id: int) -> CompileRun | None:
        return self.session.scalar(
            select(CompileRun).where(
                CompileRun.project_id == project_id,
                CompileRun.id == run_id,
            )
        )

    def list_runs(self, project_id: int) -> list[CompileRun]:
        return list(
            self.session.scalars(
                select(CompileRun)
                .where(CompileRun.project_id == project_id)
                .order_by(CompileRun.created_at.desc(), CompileRun.id.desc())
            )
        )

    def list_artifacts(self, run_id: int) -> list[CompileArtifact]:
        return list(
            self.session.scalars(
                select(CompileArtifact)
                .where(CompileArtifact.compile_run_id == run_id)
                .order_by(CompileArtifact.id)
            )
        )


__all__ = ["CompilerRepository", "FrozenExposureEntry"]
