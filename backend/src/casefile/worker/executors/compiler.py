"""Providerless N4.1 Compiler input-freeze executor."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from casefile_contracts import CompileInputManifest
from pydantic import ValidationError
from sqlalchemy import select

from casefile.application.compiler.constants import (
    INPUT_FREEZE_COMPONENT_ID,
    INPUT_FREEZE_COMPONENT_VERSION,
    INPUT_MANIFEST_ARTIFACT_KEY,
    INPUT_MANIFEST_SCHEMA_ID,
)
from casefile.application.task_events import append_task_event
from casefile.data_postgres.compiler_repository import CompilerRepository
from casefile.data_postgres.models import (
    AgentStepRun,
    CanonVersion,
    CompileArtifact,
    CompileRun,
    DraftSnapshot,
    TaskAttempt,
    TaskRun,
)
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    canonical_json_sha256,
    validate_compile_input_manifest,
)
from casefile.worker.support import TaskCancellationRequested


class CompilerExecutionError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


class CompilerTaskExecutorMixin:
    """Execute deterministic Compiler foundation tasks without Provider context."""

    session_factory: Any
    config: Any

    def _execute_novel_compile(self, task_run_id: int, attempt_id: int) -> None:
        try:
            manifest_json, run = self._validate_compile_inputs(task_run_id)
            artifact_id, reused = self._materialize_input_manifest(
                task_run_id, attempt_id, run, manifest_json
            )
            with self.session_factory() as session, session.begin():
                task, attempt = self._locked_completion_rows(  # type: ignore[attr-defined]
                    session,
                    task_run_id,
                    attempt_id,
                    expected_task_type="novel_compile",
                )
                result = {
                    "compile_run_id": run.id,
                    "input_manifest_artifact_id": artifact_id,
                    "input_hash": run.input_hash,
                    "reused": reused,
                }
                self._finish_auxiliary_success(  # type: ignore[attr-defined]
                    session,
                    task,
                    attempt,
                    candidate=result,
                    usage={},
                    message="编译输入已冻结并形成不可变构建产物。",
                )
        except TaskCancellationRequested:
            raise
        except Exception as error:
            self._record_compile_failure_step(task_run_id, attempt_id, error)
            raise

    def _validate_compile_inputs(
        self, task_run_id: int
    ) -> tuple[dict[str, Any], CompileRun]:
        with self.session_factory() as session, session.begin():
            task = session.scalar(select(TaskRun).where(TaskRun.id == task_run_id))
            if (
                task is None
                or task.status != "running"
                or task.leased_by != self.config.worker_id
                or task.task_type != "novel_compile"
            ):
                raise CompilerExecutionError("compiler_run_task_mismatch")
            run = session.scalar(select(CompileRun).where(CompileRun.task_run_id == task.id))
            if run is None or run.input_hash != task.input_hash:
                raise CompilerExecutionError("compiler_run_task_mismatch")
            try:
                manifest = CompileInputManifest.model_validate(task.input_jsonb)
                validate_compile_input_manifest(manifest)
            except (ValidationError, CompilerContractError) as error:
                raise CompilerExecutionError("compiler_manifest_invalid") from error
            manifest_json = manifest.model_dump(mode="json")
            if canonical_json_sha256(manifest_json) != task.input_hash:
                raise CompilerExecutionError("compiler_input_hash_mismatch")

            snapshot = session.get(DraftSnapshot, run.source_snapshot_id)
            binding = manifest.source_snapshot
            if (
                snapshot is None
                or snapshot.project_id != run.project_id
                or snapshot.casefile_id != run.casefile_id
                or snapshot.draft_id != run.draft_id
                or snapshot.id != binding.snapshot_id
                or snapshot.snapshot_revision != binding.snapshot_revision
                or snapshot.schema_version != binding.schema_version
                or snapshot.content_hash != binding.content_hash
                or canonical_json_sha256(snapshot.snapshot_jsonb) != snapshot.content_hash
            ):
                raise CompilerExecutionError("compiler_snapshot_binding_mismatch")

            if run.source_canon_version_id is None:
                if manifest.source_canon is not None:
                    raise CompilerExecutionError("compiler_canon_binding_mismatch")
            else:
                canon = session.get(CanonVersion, run.source_canon_version_id)
                cb = manifest.source_canon
                if (
                    canon is None
                    or cb is None
                    or canon.id != cb.canon_version_id
                    or canon.source_snapshot_id != snapshot.id
                    or canon.version_no != cb.version_no
                    or canon.content_hash != cb.content_hash
                    or canon.content_jsonb != snapshot.snapshot_jsonb
                    or canonical_json_sha256(canon.content_jsonb) != canon.content_hash
                ):
                    raise CompilerExecutionError("compiler_canon_binding_mismatch")

            repository = CompilerRepository(session)
            profile = repository.get_profile_version(
                run.project_id, run.compiler_profile_version_id
            )
            pb = manifest.profile
            profile_owner = (
                None
                if profile is None
                else repository.get_profile(profile.project_id, profile.compiler_profile_id)
            )
            if (
                profile is None
                or profile_owner is None
                or profile_owner.profile_key != pb.profile_key
                or profile.version_no != pb.profile_version
                or profile.schema_id != pb.profile_schema_id
                or profile.payload_jsonb != pb.frozen_payload
                or profile.content_hash != pb.content_hash
                or canonical_json_sha256(profile.payload_jsonb) != profile.content_hash
            ):
                raise CompilerExecutionError("compiler_profile_binding_mismatch")

            if run.exposure_plan_revision_id is None:
                if manifest.exposure is not None:
                    raise CompilerExecutionError("compiler_exposure_binding_mismatch")
            else:
                exposure = repository.get_exposure_revision(
                    project_id=run.project_id,
                    casefile_id=run.casefile_id,
                    draft_id=run.draft_id,
                    revision_id=run.exposure_plan_revision_id,
                )
                eb = manifest.exposure
                exposure_payload = (
                    None
                    if exposure is None
                    else repository.project_exposure_revision_payload(exposure.id)
                )
                if (
                    exposure is None
                    or eb is None
                    or exposure.id != eb.plan_revision_id
                    or exposure.revision_no != eb.revision_no
                    or exposure_payload != eb.frozen_payload
                    or canonical_json_sha256(eb.frozen_payload) != eb.content_hash
                ):
                    raise CompilerExecutionError("compiler_exposure_binding_mismatch")
            session.expunge(run)
            return manifest_json, run

    def _materialize_input_manifest(
        self,
        task_run_id: int,
        attempt_id: int,
        run: CompileRun,
        manifest_json: dict[str, Any],
    ) -> tuple[int, bool]:
        with self.session_factory() as session, session.begin():
            task = session.scalar(
                select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
            )
            attempt = session.scalar(
                select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
            )
            if task is None or attempt is None:
                raise CompilerExecutionError("compiler_run_task_mismatch")
            if task.status == "cancelling" and task.leased_by == self.config.worker_id:
                raise TaskCancellationRequested
            if (
                task.status != "running"
                or task.leased_by != self.config.worker_id
                or attempt.status != "running"
                or attempt.task_run_id != task.id
            ):
                raise CompilerExecutionError("compiler_worker_lease_lost")
            append_task_event(
                session,
                task,
                "compiler.input_freeze.started",
                "compiler_input_freeze",
                {"compile_run_id": run.id, "input_hash": run.input_hash},
            )
            existing = session.scalar(
                select(CompileArtifact).where(
                    CompileArtifact.compile_run_id == run.id,
                    CompileArtifact.artifact_key == INPUT_MANIFEST_ARTIFACT_KEY,
                )
            )
            now = datetime.now(UTC)
            if existing is not None:
                if (
                    existing.task_run_id != task.id
                    or existing.content_hash != task.input_hash
                    or existing.schema_id != INPUT_MANIFEST_SCHEMA_ID
                    or existing.content_jsonb != manifest_json
                ):
                    raise CompilerExecutionError("compiler_artifact_hash_conflict")
                step = AgentStepRun(
                    project_id=task.project_id,
                    task_run_id=task.id,
                    task_attempt_id=attempt.id,
                    component_id=INPUT_FREEZE_COMPONENT_ID,
                    parent_component_id=None,
                    execution_no=1,
                    status="reused",
                    input_hash=task.input_hash,
                    upstream_hashes_jsonb={},
                    output_hash=task.input_hash,
                    ir_schema_id=INPUT_MANIFEST_SCHEMA_ID,
                    component_version=INPUT_FREEZE_COMPONENT_VERSION,
                    output_jsonb=None,
                    diagnostic_jsonb={},
                    usage_jsonb={},
                    resumed_from_step_run_id=existing.agent_step_run_id,
                    started_at=now,
                    finished_at=now,
                )
                session.add(step)
                session.flush()
                append_task_event(
                    session,
                    task,
                    "compiler.input_freeze.reused",
                    "compiler_input_freeze",
                    {"compile_run_id": run.id, "artifact_id": existing.id},
                )
                return existing.id, True

            step = AgentStepRun(
                project_id=task.project_id,
                task_run_id=task.id,
                task_attempt_id=attempt.id,
                component_id=INPUT_FREEZE_COMPONENT_ID,
                parent_component_id=None,
                execution_no=1,
                status="succeeded",
                input_hash=task.input_hash,
                upstream_hashes_jsonb={},
                output_hash=task.input_hash,
                ir_schema_id=INPUT_MANIFEST_SCHEMA_ID,
                component_version=INPUT_FREEZE_COMPONENT_VERSION,
                output_jsonb=None,
                diagnostic_jsonb={},
                usage_jsonb={},
                resumed_from_step_run_id=None,
                started_at=now,
                finished_at=now,
            )
            session.add(step)
            session.flush()
            artifact = CompileArtifact(
                project_id=run.project_id,
                casefile_id=run.casefile_id,
                compile_run_id=run.id,
                task_run_id=task.id,
                agent_step_run_id=step.id,
                artifact_kind="input_manifest",
                artifact_key=INPUT_MANIFEST_ARTIFACT_KEY,
                schema_id=INPUT_MANIFEST_SCHEMA_ID,
                content_hash=task.input_hash,
                content_jsonb=manifest_json,
            )
            session.add(artifact)
            session.flush()
            append_task_event(
                session,
                task,
                "compiler.input_freeze.completed",
                "compiler_input_freeze",
                {"compile_run_id": run.id, "artifact_id": artifact.id},
            )
            return artifact.id, False

    def _record_compile_failure_step(
        self, task_run_id: int, attempt_id: int, error: Exception
    ) -> None:
        with self.session_factory() as session, session.begin():
            task = session.scalar(
                select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
            )
            attempt = session.scalar(
                select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
            )
            if (
                task is None
                or attempt is None
                or task.status != "running"
                or task.leased_by != self.config.worker_id
                or attempt.status != "running"
            ):
                return
            existing = session.scalar(
                select(AgentStepRun.id).where(
                    AgentStepRun.task_attempt_id == attempt.id,
                    AgentStepRun.component_id == INPUT_FREEZE_COMPONENT_ID,
                )
            )
            if existing is None:
                now = datetime.now(UTC)
                code = getattr(error, "error_code", "compiler_input_freeze_failed")
                session.add(
                    AgentStepRun(
                        project_id=task.project_id,
                        task_run_id=task.id,
                        task_attempt_id=attempt.id,
                        component_id=INPUT_FREEZE_COMPONENT_ID,
                        parent_component_id=None,
                        execution_no=1,
                        status="failed",
                        input_hash=task.input_hash,
                        upstream_hashes_jsonb={},
                        output_hash=None,
                        ir_schema_id=INPUT_MANIFEST_SCHEMA_ID,
                        component_version=INPUT_FREEZE_COMPONENT_VERSION,
                        output_jsonb=None,
                        diagnostic_jsonb={
                            "failure_layer": "frozen_input",
                            "recoverable": False,
                            "issues": [{"code": code, "path": "", "message": code}],
                        },
                        usage_jsonb={},
                        resumed_from_step_run_id=None,
                        started_at=now,
                        finished_at=now,
                    )
                )
            append_task_event(
                session,
                task,
                "compiler.input_freeze.failed",
                "compiler_input_freeze",
                {"error_code": getattr(error, "error_code", "compiler_input_freeze_failed")},
            )


__all__ = ["CompilerExecutionError", "CompilerTaskExecutorMixin"]
