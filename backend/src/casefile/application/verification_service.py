"""Application adapter for VerificationEngine observations and read models."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from typing import Any, Literal, cast

import rfc8785
from sqlalchemy import select
from sqlalchemy.orm import Session

from casefile.application.errors import ApplicationError, not_found
from casefile.application.verification_engine import (
    VerificationEngine,
    VerificationResult,
)
from casefile.data_postgres.models import (
    AgentPatchOperation,
    VerificationFindingPatchOperation,
    VerificationFindingRef,
    VerificationFindingReview,
    VerificationRun,
)
from casefile.data_postgres.models import VerificationFinding as FindingRow
from casefile.data_postgres.repositories import OwnedDraft, ProjectRepository


class VerificationService:
    """Keep SQL persistence outside the pure verification core."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

    @staticmethod
    def enabled_for_persistence() -> bool:
        return os.getenv("CASEFILE_CHAT_VERIFICATION_PERSISTENCE", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @staticmethod
    def enabled_for_read_model() -> bool:
        return os.getenv("CASEFILE_CHAT_VERIFICATION_READ_MODEL", "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def record_chat_result(
        self,
        *,
        owned: OwnedDraft,
        task_run_id: int,
        document: dict[str, Any],
        audit_findings: list[dict[str, Any]],
        profile: str,
        trigger: str = "chat",
        patch_set_id: int | None = None,
        draft_revision: int | None = None,
    ) -> tuple[VerificationRun, VerificationResult] | None:
        if not self.enabled_for_persistence():
            return None
        frozen_revision = owned.draft.revision if draft_revision is None else draft_revision
        engine = VerificationEngine(
            profile=cast(Literal["fast", "balanced", "strict"], profile),
            draft_revision=frozen_revision,
        )
        result = engine.verify(document, llm_findings=audit_findings)
        run = self._persist_result(
            owned=owned,
            result=result,
            document=document,
            trigger=trigger,
            profile=profile,
            source_task_run_id=task_run_id,
            patch_set_id=patch_set_id,
            draft_revision=frozen_revision,
        )
        return run, result

    def record_result(
        self,
        *,
        owned: OwnedDraft,
        document: dict[str, Any],
        result: VerificationResult,
        profile: str,
        trigger: str,
        source_task_run_id: int | None = None,
        patch_set_id: int | None = None,
        draft_revision: int | None = None,
    ) -> VerificationRun:
        if not self.enabled_for_persistence():
            raise ApplicationError(
                "verification_persistence_disabled",
                "规范化验证结果当前未启用。",
                status_code=503,
            )
        return self._persist_result(
            owned=owned,
            result=result,
            document=document,
            trigger=trigger,
            profile=profile,
            source_task_run_id=source_task_run_id,
            patch_set_id=patch_set_id,
            draft_revision=draft_revision,
        )

    def link_patch_operations(
        self,
        *,
        project_id: int,
        verification_run_id: int,
        operations: list[AgentPatchOperation],
        finding_refs_by_operation_id: dict[int, str | None],
    ) -> None:
        """Bind accepted suggestion operations to persisted legacy finding refs."""

        findings = list(
            self.session.scalars(
                select(FindingRow).where(
                    FindingRow.project_id == project_id,
                    FindingRow.verification_run_id == verification_run_id,
                )
            )
        )
        finding_by_legacy_id = {
            str(row.payload_jsonb.get("legacy_finding_id")): row
            for row in findings
            if row.payload_jsonb.get("legacy_finding_id") is not None
        }
        for operation in operations:
            legacy_key = finding_refs_by_operation_id.get(operation.id)
            if legacy_key is None:
                continue
            finding = finding_by_legacy_id.get(legacy_key)
            if finding is None:
                continue
            self.session.add(
                VerificationFindingPatchOperation(
                    project_id=project_id,
                    finding_id=finding.id,
                    patch_operation_id=operation.id,
                    relation_kind="fixes",
                )
            )
        self.session.flush()

    def list_findings(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        draft_id: int | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id)
            if owned is None:
                raise not_found("Project")
            query = select(FindingRow).where(FindingRow.project_id == project_id)
            query = query.where(FindingRow.draft_id == (draft_id or owned.draft.id))
            if status is not None:
                query = query.where(FindingRow.status == status)
            rows = list(
                self.session.scalars(
                    query.order_by(FindingRow.last_seen_at.desc(), FindingRow.id.desc())
                )
            )
            return [self._finding_view(row) for row in rows]

    def current_read_model(self, owned: OwnedDraft) -> dict[str, Any]:
        """Read the latest persisted facts for an already-owned Draft transaction."""

        if not self.enabled_for_read_model():
            return {"enabled": False, "runs": [], "findings": []}
        rows = list(
            self.session.scalars(
                select(FindingRow)
                .where(
                    FindingRow.project_id == owned.project.id,
                    FindingRow.draft_id == owned.draft.id,
                )
                .order_by(FindingRow.last_seen_at.desc(), FindingRow.id.desc())
            )
        )
        run = self.session.scalar(
            select(VerificationRun)
            .where(
                VerificationRun.project_id == owned.project.id,
                VerificationRun.draft_id == owned.draft.id,
            )
            .order_by(VerificationRun.started_at.desc(), VerificationRun.id.desc())
        )
        latest_findings = []
        if run is not None:
            latest_findings = list(
                self.session.scalars(
                    select(FindingRow)
                    .where(
                        FindingRow.project_id == owned.project.id,
                        FindingRow.verification_run_id == run.id,
                    )
                    .order_by(FindingRow.id)
                )
            )
        return {
            "enabled": True,
            "latest_run": None if run is None else self._run_view(run, latest_findings),
            "findings": [self._finding_view(row) for row in rows],
        }

    def get_run(
        self,
        actor_user_id: int,
        project_id: int,
        verification_run_id: int,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id)
            if owned is None:
                raise not_found("Project")
            run = self.session.scalar(
                select(VerificationRun).where(
                    VerificationRun.project_id == project_id,
                    VerificationRun.id == verification_run_id,
                )
            )
            if run is None:
                raise not_found("VerificationRun")
            findings = list(
                self.session.scalars(
                    select(FindingRow)
                    .where(
                        FindingRow.project_id == project_id,
                        FindingRow.verification_run_id == run.id,
                    )
                    .order_by(FindingRow.id)
                )
            )
            return self._run_view(run, findings)

    def review_finding(
        self,
        actor_user_id: int,
        project_id: int,
        finding_id: int,
        *,
        decision: str,
        note: str | None,
    ) -> dict[str, Any]:
        if decision not in {"confirm", "resolve", "reopen", "dismiss"}:
            raise ApplicationError(
                "verification_review_decision_invalid",
                "验证问题审阅决定无效。",
                status_code=422,
            )
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id, lock=True)
            if owned is None:
                raise not_found("Project")
            row = self.session.scalar(
                select(FindingRow)
                .where(FindingRow.project_id == project_id, FindingRow.id == finding_id)
                .with_for_update()
            )
            if row is None:
                raise not_found("VerificationFinding")
            self.session.add(
                VerificationFindingReview(
                    project_id=project_id,
                    finding_id=row.id,
                    actor_user_id=actor_user_id,
                    decision=decision,
                    note=note.strip() if note and note.strip() else None,
                )
            )
            if decision == "resolve":
                row.status = "resolved"
            elif decision == "reopen":
                row.status = "reopened"
            elif decision == "dismiss":
                row.status = "dismissed"
            if decision == "resolve":
                row.resolved_at = datetime.now(UTC)
            elif decision == "reopen":
                row.resolved_at = None
            self.session.flush()
            return self._finding_view(row)

    def _persist_result(
        self,
        *,
        owned: OwnedDraft,
        result: VerificationResult,
        document: dict[str, Any],
        trigger: str,
        profile: str,
        source_task_run_id: int | None,
        patch_set_id: int | None,
        draft_revision: int | None = None,
    ) -> VerificationRun:
        if trigger not in {"chat", "manual", "pre_apply", "post_apply"}:
            raise ValueError(f"Unsupported verification trigger: {trigger}")
        now = datetime.now(UTC)
        deterministic_count = sum(item.kind == "deterministic" for item in result.findings)
        run = VerificationRun(
            project_id=owned.project.id,
            casefile_id=owned.casefile.id,
            draft_id=owned.draft.id,
            source_task_run_id=source_task_run_id,
            patch_set_id=patch_set_id,
            trigger=trigger,
            profile=profile,
            engine_version=result.engine_version,
            draft_revision=owned.draft.revision if draft_revision is None else draft_revision,
            input_hash=_content_hash(document),
            status="succeeded",
            started_at=now,
            completed_at=now,
            finding_count=len(result.findings),
            deterministic_finding_count=deterministic_count,
            llm_finding_count=len(result.findings) - deterministic_count,
        )
        self.session.add(run)
        self.session.flush()
        self._reconcile_previous_findings(
            owned=owned,
            current_findings=result.findings,
            profile=profile,
            now=now,
        )
        for finding in result.findings:
            row = FindingRow(
                project_id=owned.project.id,
                casefile_id=owned.casefile.id,
                draft_id=owned.draft.id,
                verification_run_id=run.id,
                finding_key=finding.finding_key,
                kind=finding.kind,
                severity=finding.severity,
                status=finding.status,
                title=finding.title,
                message=finding.message,
                suggested_fix=finding.suggested_fix,
                rule_code=finding.rule_code,
                confidence=finding.confidence,
                draft_revision=finding.draft_revision,
                payload_jsonb=dict(finding.payload),
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(row)
            self.session.flush()
            for ref in finding.refs:
                self.session.add(
                    VerificationFindingRef(
                        project_id=owned.project.id,
                        finding_id=row.id,
                        ref_kind=ref.ref_kind,
                        ref_key=ref.ref_key,
                        role=ref.role,
                    )
                )
        self.session.flush()
        return run

    def _reconcile_previous_findings(
        self,
        *,
        owned: OwnedDraft,
        current_findings: tuple[Any, ...],
        profile: str,
        now: datetime,
    ) -> None:
        """Reconcile lifecycle state without deleting prior observations.

        A finding key is scoped to one run.  Across runs, the semantic identity
        is the kind, rule code, and canonical set of refs.  Fast runs contain
        no LLM observations, so they must not resolve an older probabilistic
        finding merely because that slot was not evaluated.
        """

        previous = list(
            self.session.scalars(
                select(FindingRow).where(
                    FindingRow.project_id == owned.project.id,
                    FindingRow.draft_id == owned.draft.id,
                )
            )
        )
        if not previous:
            return
        refs = list(
            self.session.scalars(
                select(VerificationFindingRef).where(
                    VerificationFindingRef.project_id == owned.project.id,
                    VerificationFindingRef.finding_id.in_(row.id for row in previous),
                )
            )
        )
        refs_by_finding: dict[int, list[tuple[str, str, str]]] = {}
        for ref in refs:
            refs_by_finding.setdefault(ref.finding_id, []).append(
                (ref.ref_kind, ref.ref_key, ref.role)
            )
        current_identities = {
            _finding_identity(
                finding.kind,
                finding.rule_code,
                [(ref.ref_kind, ref.ref_key, ref.role) for ref in finding.refs],
            )
            for finding in current_findings
        }
        for row in previous:
            if row.kind == "llm" and profile == "fast":
                continue
            identity = _finding_identity(
                row.kind,
                row.rule_code,
                refs_by_finding.get(row.id, []),
            )
            if identity in current_identities:
                if row.status in {"resolved", "dismissed"}:
                    row.status = "reopened"
                row.last_seen_at = now
                row.resolved_at = None
            elif row.status in {"open", "reopened"}:
                row.status = "resolved"
                row.resolved_at = now

    def _finding_view(self, row: FindingRow) -> dict[str, Any]:
        refs = list(
            self.session.scalars(
                select(VerificationFindingRef)
                .where(
                    VerificationFindingRef.project_id == row.project_id,
                    VerificationFindingRef.finding_id == row.id,
                )
                .order_by(VerificationFindingRef.id)
            )
        )
        return {
            "finding_id": row.id,
            "verification_run_id": row.verification_run_id,
            "finding_key": row.finding_key,
            "kind": row.kind,
            "severity": row.severity,
            "status": row.status,
            "title": row.title,
            "message": row.message,
            "suggested_fix": row.suggested_fix,
            "rule_code": row.rule_code,
            "confidence": row.confidence,
            "draft_revision": row.draft_revision,
            "refs": [
                {"ref_kind": ref.ref_kind, "ref_key": ref.ref_key, "role": ref.role}
                for ref in refs
            ],
            "payload": row.payload_jsonb,
            "first_seen_at": _time(row.first_seen_at),
            "last_seen_at": _time(row.last_seen_at),
            "resolved_at": _time(row.resolved_at),
        }

    def _run_view(
        self,
        run: VerificationRun,
        findings: list[FindingRow],
    ) -> dict[str, Any]:
        return {
            "verification_run_id": run.id,
            "project_id": run.project_id,
            "casefile_id": run.casefile_id,
            "draft_id": run.draft_id,
            "source_task_run_id": run.source_task_run_id,
            "patch_set_id": run.patch_set_id,
            "trigger": run.trigger,
            "profile": run.profile,
            "engine_version": run.engine_version,
            "draft_revision": run.draft_revision,
            "input_hash": run.input_hash,
            "status": run.status,
            "started_at": _time(run.started_at),
            "completed_at": _time(run.completed_at),
            "finding_count": run.finding_count,
            "deterministic_finding_count": run.deterministic_finding_count,
            "llm_finding_count": run.llm_finding_count,
            "findings": [self._finding_view(row) for row in findings],
        }


def _content_hash(document: dict[str, Any]) -> str:
    return hashlib.sha256(rfc8785.dumps(document)).hexdigest()


def _finding_identity(
    kind: str,
    rule_code: str,
    refs: list[tuple[str, str, str]],
) -> str:
    return hashlib.sha256(
        rfc8785.dumps(
            {
                "kind": kind,
                "rule_code": rule_code,
                "refs": sorted(refs),
            }
        )
    ).hexdigest()


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


__all__ = ["VerificationService"]
