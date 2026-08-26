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
    ExposurePlanObligation,
    ExposurePlanObligationRef,
    ExposurePlanRevision,
)


@dataclass(frozen=True, slots=True)
class FrozenExposureObligation:
    obligation: ExposurePlanObligation
    refs: tuple[CaseFileObject, ...]


@dataclass(frozen=True, slots=True)
class FrozenExposureEntry:
    entry: ExposurePlanEntry
    refs: tuple[CaseFileObject, ...]
    obligations: tuple[FrozenExposureObligation, ...]


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
        obligations = list(
            self.session.scalars(
                select(ExposurePlanObligation)
                .where(ExposurePlanObligation.entry_id.in_(by_entry))
                .order_by(ExposurePlanObligation.entry_id, ExposurePlanObligation.id)
            )
        )
        by_obligation: dict[int, list[CaseFileObject]] = {
            obligation.id: [] for obligation in obligations
        }
        if by_obligation:
            rows = self.session.execute(
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
                .where(ExposurePlanObligationRef.obligation_id.in_(by_obligation))
                .order_by(
                    ExposurePlanObligationRef.obligation_id,
                    ExposurePlanObligationRef.ordinal,
                )
            )
            for reference, registry in rows:
                by_obligation[reference.obligation_id].append(registry)
        by_entry_obligations: dict[int, list[FrozenExposureObligation]] = {
            entry.id: [] for entry in entries
        }
        for obligation in obligations:
            by_entry_obligations[obligation.entry_id].append(
                FrozenExposureObligation(
                    obligation,
                    tuple(by_obligation[obligation.id]),
                )
            )
        return [
            FrozenExposureEntry(
                entry,
                tuple(by_entry[entry.id]),
                tuple(by_entry_obligations[entry.id]),
            )
            for entry in entries
        ]

    def project_exposure_revision_payload(self, revision_id: int) -> dict[str, object]:
        """Project one exact immutable Exposure revision in canonical sequence order."""

        revision = self.session.get(ExposurePlanRevision, revision_id)
        if revision is None:
            raise RuntimeError("ExposurePlanRevision is missing")
        include_obligations = revision.payload_schema_id == "casefile.exposure-plan.v2"
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
                    **(
                        {
                            "planning_obligations": [
                                {
                                    "kind": obligation.obligation.obligation_kind,
                                    "obligation_key": obligation.obligation.obligation_key,
                                    "level": obligation.obligation.level,
                                    **(
                                        {
                                            "min_distinct": (
                                                obligation.obligation.min_distinct
                                            )
                                        }
                                        if obligation.obligation.obligation_kind
                                        == "participant_coverage"
                                        else {}
                                    ),
                                    (
                                        "eligible_refs"
                                        if obligation.obligation.obligation_kind
                                        == "participant_coverage"
                                        else "required_refs"
                                    ): [
                                        {
                                            "object_type": ref.object_type,
                                            "object_id": ref.object_id,
                                        }
                                        for ref in obligation.refs
                                    ],
                                }
                                for obligation in item.obligations
                            ]
                        }
                        if include_obligations
                        else {}
                    ),
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

    def get_artifact(
        self, project_id: int, run_id: int, artifact_id: int
    ) -> CompileArtifact | None:
        return self.session.scalar(
            select(CompileArtifact).where(
                CompileArtifact.project_id == project_id,
                CompileArtifact.compile_run_id == run_id,
                CompileArtifact.id == artifact_id,
            )
        )


__all__ = ["CompilerRepository", "FrozenExposureEntry", "FrozenExposureObligation"]
