"""Providerless deterministic Compiler component executor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from casefile_contracts import CompileInputManifest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile.agent_runtime.scene_compiler import SCENE_COMPILER_PIPELINE_VERSION
from casefile.application.compiler.constants import (
    INPUT_FREEZE_COMPONENT_ID,
    INPUT_FREEZE_COMPONENT_VERSION,
    INPUT_MANIFEST_ARTIFACT_KEY,
    INPUT_MANIFEST_SCHEMA_ID,
    NARRATIVE_IR_ARTIFACT_KEY,
    NARRATIVE_IR_COMPONENT_ID,
    NARRATIVE_IR_COMPONENT_VERSION,
    NARRATIVE_IR_SCHEMA_ID,
    SCENE_PLAN_ARTIFACT_KEY,
    SCENE_PLAN_COMPONENT_ID,
    SCENE_PLAN_COMPONENT_VERSION,
    SCENE_PLAN_SCHEMA_ID,
    SCENE_PLAN_V2_COMPONENT_ID,
    SCENE_PLAN_V2_COMPONENT_VERSION,
    SCENE_PLAN_V2_SCHEMA_ID,
    STORY_PLANNER_COMPONENT_ID,
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
    compile_scene_plan_json,
    narrative_ir_component_fingerprint,
    project_narrative_ir_json,
    scene_plan_component_fingerprint,
    validate_compile_input_manifest,
)
from casefile.worker.executors.completion import CompletionExecutor
from casefile.worker.failures import TaskCancellationRequested
from casefile.worker.provider_resolution import ProviderFactory


class CompilerExecutionError(RuntimeError):
    def __init__(self, error_code: str) -> None:
        self.error_code = error_code
        super().__init__(error_code)


def _normalize_compiler_error(error: Exception, *, fallback_code: str) -> CompilerExecutionError:
    """Preserve stable Compiler codes at the domain-to-Worker boundary."""

    if isinstance(error, CompilerExecutionError):
        return error
    if isinstance(error, CompilerContractError):
        return CompilerExecutionError(error.reason_code)
    return CompilerExecutionError(fallback_code)


@dataclass(frozen=True, slots=True)
class _CompilerRuntimeConfig:
    worker_id: str


class CompilerExecutor:
    """Execute Compiler tasks through the compositional Worker runtime."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        worker_id: str,
        provider_factory: ProviderFactory,
        completion: CompletionExecutor,
    ) -> None:
        self.session_factory = session_factory
        self.config = _CompilerRuntimeConfig(worker_id=worker_id)
        self.provider_factory = provider_factory
        self._completion = completion

    def execute(self, task_run_id: int, attempt_id: int) -> None:
        self._execute_novel_compile(task_run_id, attempt_id)

    def _locked_completion_rows(
        self,
        session: Session,
        task_run_id: int,
        attempt_id: int,
        *,
        expected_task_type: str,
    ) -> tuple[TaskRun, TaskAttempt]:
        return self._completion._locked_completion_rows(
            session,
            task_run_id,
            attempt_id,
            expected_task_type=expected_task_type,
        )

    def _finish_auxiliary_success(
        self,
        session: Session,
        task: TaskRun,
        attempt: TaskAttempt,
        *,
        candidate: dict[str, Any],
        usage: dict[str, Any],
        message: str,
    ) -> None:
        self._completion._finish_auxiliary_success(
            session,
            task,
            attempt,
            candidate=candidate,
            usage=usage,
            message=message,
        )

    def _execute_novel_compile(self, task_run_id: int, attempt_id: int) -> None:
        try:
            manifest_json, run, snapshot_json = self._validate_compile_inputs(task_run_id)
            manifest_artifact_id, manifest_reused = self._materialize_input_manifest(
                task_run_id, attempt_id, run, manifest_json
            )
        except TaskCancellationRequested:
            raise
        except Exception as error:
            normalized = _normalize_compiler_error(
                error,
                fallback_code="compiler_input_freeze_failed",
            )
            self._record_compile_failure_step(
                task_run_id,
                attempt_id,
                normalized,
                component_id=INPUT_FREEZE_COMPONENT_ID,
                component_version=INPUT_FREEZE_COMPONENT_VERSION,
                schema_id=INPUT_MANIFEST_SCHEMA_ID,
                input_hash=None,
                upstream_hashes={},
                failure_layer="frozen_input",
                event_prefix="compiler.input_freeze",
            )
            if normalized is error:
                raise
            raise normalized from error

        component_input_hash: str | None = None
        upstream_hashes: dict[str, str] = {}
        try:
            fingerprint = narrative_ir_component_fingerprint(snapshot_json)
            component_input_hash = canonical_json_sha256(fingerprint)
            upstream_hashes = {"source_snapshot": fingerprint["source_content_hash"]}
            narrative_ir_json = project_narrative_ir_json(snapshot_json)
            narrative_ir_hash = canonical_json_sha256(narrative_ir_json)
            narrative_artifact_id, narrative_reused = self._materialize_json_artifact_component(
                task_run_id=task_run_id,
                attempt_id=attempt_id,
                run=run,
                component_id=NARRATIVE_IR_COMPONENT_ID,
                component_version=NARRATIVE_IR_COMPONENT_VERSION,
                component_input_hash=component_input_hash,
                upstream_hashes=upstream_hashes,
                artifact_kind="narrative_ir",
                artifact_key=NARRATIVE_IR_ARTIFACT_KEY,
                schema_id=NARRATIVE_IR_SCHEMA_ID,
                content_hash=narrative_ir_hash,
                content_json=narrative_ir_json,
                event_prefix="compiler.narrative_ir",
                parent_component_id=INPUT_FREEZE_COMPONENT_ID,
            )
        except TaskCancellationRequested:
            raise
        except Exception as error:
            normalized = _normalize_compiler_error(
                error,
                fallback_code="compiler_narrative_ir_projection_failed",
            )
            self._record_compile_failure_step(
                task_run_id,
                attempt_id,
                normalized,
                component_id=NARRATIVE_IR_COMPONENT_ID,
                component_version=NARRATIVE_IR_COMPONENT_VERSION,
                schema_id=NARRATIVE_IR_SCHEMA_ID,
                input_hash=component_input_hash,
                upstream_hashes=upstream_hashes,
                failure_layer="narrative_ir",
                event_prefix="compiler.narrative_ir",
            )
            if normalized is error:
                raise
            raise normalized from error

        novel_plan_artifact_id: int | None = None
        novel_plan_hash: str | None = None
        planner_reused = False
        scene_plan_artifact_id: int | None = None
        scene_plan_hash: str | None = None
        scene_plan_reused = False
        with self.session_factory() as session:
            task_identity = session.execute(
                select(TaskRun.provider, TaskRun.agent_version).where(
                    TaskRun.id == task_run_id
                )
            ).one()
            planner_enabled = task_identity.provider is not None
            shadow_scene_compiler = (
                task_identity.agent_version == SCENE_COMPILER_PIPELINE_VERSION
            )
        if planner_enabled:
            from casefile.worker.executors.story_planner import (
                execute_story_planner_component,
                fail_story_planner_component,
            )

            try:
                novel_plan_artifact_id, novel_plan_hash, planner_reused = (
                    execute_story_planner_component(
                        self,
                        task_run_id=task_run_id,
                        attempt_id=attempt_id,
                        run=run,
                        manifest_json=manifest_json,
                        narrative_ir_json=narrative_ir_json,
                        narrative_ir_hash=narrative_ir_hash,
                    )
                )
            except TaskCancellationRequested:
                raise
            except Exception as error:
                normalized = _normalize_compiler_error(
                    error, fallback_code="compiler_story_planner_failed"
                )
                fail_story_planner_component(self, task_run_id, attempt_id, normalized.error_code)
                if normalized is error:
                    raise
                raise normalized from error

            scene_component_input_hash: str | None = None
            scene_upstream_hashes: dict[str, str] = {}
            try:
                with self.session_factory() as session:
                    novel_plan_json = session.scalar(
                        select(CompileArtifact.content_jsonb).where(
                            CompileArtifact.id == novel_plan_artifact_id
                        )
                    )
                if novel_plan_json is None or novel_plan_hash is None:
                    raise CompilerExecutionError("compiler_scene_plan_novel_plan_missing")
                scene_upstream_hashes = {
                    "novel_plan": novel_plan_hash,
                    "narrative_ir": narrative_ir_hash,
                }
                if shadow_scene_compiler:
                    from casefile.worker.executors.scene_compiler import (
                        execute_scene_compiler_component,
                    )

                    scene_plan_artifact_id, scene_plan_hash, scene_plan_reused = (
                        execute_scene_compiler_component(
                            self,
                            task_run_id=task_run_id,
                            attempt_id=attempt_id,
                            run=run,
                            manifest_json=manifest_json,
                            novel_plan_json=novel_plan_json,
                            novel_plan_hash=novel_plan_hash,
                            narrative_ir_json=narrative_ir_json,
                            narrative_ir_hash=narrative_ir_hash,
                        )
                    )
                else:
                    scene_fingerprint = scene_plan_component_fingerprint(novel_plan_json)
                    scene_component_input_hash = canonical_json_sha256(scene_fingerprint)
                    scene_plan_json = compile_scene_plan_json(novel_plan_json)
                    scene_plan_hash = canonical_json_sha256(scene_plan_json)
                    scene_plan_artifact_id, scene_plan_reused = (
                        self._materialize_json_artifact_component(
                            task_run_id=task_run_id,
                            attempt_id=attempt_id,
                            run=run,
                            component_id=SCENE_PLAN_COMPONENT_ID,
                            component_version=SCENE_PLAN_COMPONENT_VERSION,
                            component_input_hash=scene_component_input_hash,
                            upstream_hashes=scene_upstream_hashes,
                            artifact_kind="scene_plan",
                            artifact_key=SCENE_PLAN_ARTIFACT_KEY,
                            schema_id=SCENE_PLAN_SCHEMA_ID,
                            content_hash=scene_plan_hash,
                            content_json=scene_plan_json,
                            event_prefix="compiler.scene_plan",
                            parent_component_id=STORY_PLANNER_COMPONENT_ID,
                        )
                    )
            except TaskCancellationRequested:
                raise
            except Exception as error:
                normalized = _normalize_compiler_error(
                    error, fallback_code="compiler_scene_plan_compilation_failed"
                )
                if shadow_scene_compiler:
                    from casefile.worker.executors.scene_compiler import (
                        fail_scene_compiler_component,
                    )

                    fail_scene_compiler_component(
                        self, task_run_id, attempt_id, normalized.error_code
                    )
                self._record_compile_failure_step(
                    task_run_id,
                    attempt_id,
                    normalized,
                    component_id=(
                        SCENE_PLAN_V2_COMPONENT_ID
                        if shadow_scene_compiler
                        else SCENE_PLAN_COMPONENT_ID
                    ),
                    component_version=(
                        SCENE_PLAN_V2_COMPONENT_VERSION
                        if shadow_scene_compiler
                        else SCENE_PLAN_COMPONENT_VERSION
                    ),
                    schema_id=(
                        SCENE_PLAN_V2_SCHEMA_ID
                        if shadow_scene_compiler
                        else SCENE_PLAN_SCHEMA_ID
                    ),
                    input_hash=scene_component_input_hash,
                    upstream_hashes=scene_upstream_hashes,
                    failure_layer="scene_plan",
                    event_prefix="compiler.scene_plan",
                )
                if normalized is error:
                    raise
                raise normalized from error

        with self.session_factory() as session, session.begin():
            task, attempt = self._locked_completion_rows(
                session,
                task_run_id,
                attempt_id,
                expected_task_type="novel_compile",
            )
            component_reuse = {
                "input_manifest": manifest_reused,
                "narrative_ir": narrative_reused,
            }
            result: dict[str, Any] = {
                "compile_run_id": run.id,
                "input_manifest_artifact_id": manifest_artifact_id,
                "narrative_ir_artifact_id": narrative_artifact_id,
                "narrative_ir_hash": narrative_ir_hash,
                "input_hash": run.input_hash,
                "reused": manifest_reused,
                "component_reuse": component_reuse,
            }
            if novel_plan_artifact_id is not None:
                result["novel_plan_artifact_id"] = novel_plan_artifact_id
                result["novel_plan_hash"] = novel_plan_hash
                component_reuse["novel_plan"] = planner_reused
            if scene_plan_artifact_id is not None:
                result["scene_plan_artifact_id"] = scene_plan_artifact_id
                result["scene_plan_hash"] = scene_plan_hash
                component_reuse["scene_plan"] = scene_plan_reused
            self._finish_auxiliary_success(
                session,
                task,
                attempt,
                candidate=result,
                usage={},
                message="编译输入、故事规划与场景执行计划已形成不可变构建产物。",
            )

    def _validate_compile_inputs(
        self, task_run_id: int
    ) -> tuple[dict[str, Any], CompileRun, dict[str, Any]]:
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
            return manifest_json, run, dict(snapshot.snapshot_jsonb)

    def _materialize_input_manifest(
        self,
        task_run_id: int,
        attempt_id: int,
        run: CompileRun,
        manifest_json: dict[str, Any],
    ) -> tuple[int, bool]:
        return self._materialize_json_artifact_component(
            task_run_id=task_run_id,
            attempt_id=attempt_id,
            run=run,
            component_id=INPUT_FREEZE_COMPONENT_ID,
            component_version=INPUT_FREEZE_COMPONENT_VERSION,
            component_input_hash=run.input_hash,
            upstream_hashes={},
            artifact_kind="input_manifest",
            artifact_key=INPUT_MANIFEST_ARTIFACT_KEY,
            schema_id=INPUT_MANIFEST_SCHEMA_ID,
            content_hash=run.input_hash,
            content_json=manifest_json,
            event_prefix="compiler.input_freeze",
            parent_component_id=None,
        )

    def _materialize_json_artifact_component(
        self,
        *,
        task_run_id: int,
        attempt_id: int,
        run: CompileRun,
        component_id: str,
        component_version: str,
        component_input_hash: str,
        upstream_hashes: dict[str, str],
        artifact_kind: str,
        artifact_key: str,
        schema_id: str,
        content_hash: str,
        content_json: dict[str, Any],
        event_prefix: str,
        parent_component_id: str | None,
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
                f"{event_prefix}.started",
                component_id,
                {"compile_run_id": run.id, "input_hash": component_input_hash},
            )
            existing = session.scalar(
                select(CompileArtifact).where(
                    CompileArtifact.compile_run_id == run.id,
                    CompileArtifact.artifact_key == artifact_key,
                )
            )
            now = datetime.now(UTC)
            if existing is not None:
                if (
                    existing.task_run_id != task.id
                    or existing.artifact_kind != artifact_kind
                    or existing.content_hash != content_hash
                    or existing.schema_id != schema_id
                    or existing.content_jsonb != content_json
                ):
                    raise CompilerExecutionError("compiler_artifact_hash_conflict")
                step = AgentStepRun(
                    project_id=task.project_id,
                    task_run_id=task.id,
                    task_attempt_id=attempt.id,
                    component_id=component_id,
                    parent_component_id=parent_component_id,
                    execution_no=1,
                    status="reused",
                    input_hash=component_input_hash,
                    upstream_hashes_jsonb=upstream_hashes,
                    output_hash=content_hash,
                    ir_schema_id=schema_id,
                    component_version=component_version,
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
                    f"{event_prefix}.reused",
                    component_id,
                    {"compile_run_id": run.id, "artifact_id": existing.id},
                )
                return existing.id, True

            step = AgentStepRun(
                project_id=task.project_id,
                task_run_id=task.id,
                task_attempt_id=attempt.id,
                component_id=component_id,
                parent_component_id=parent_component_id,
                execution_no=1,
                status="succeeded",
                input_hash=component_input_hash,
                upstream_hashes_jsonb=upstream_hashes,
                output_hash=content_hash,
                ir_schema_id=schema_id,
                component_version=component_version,
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
                artifact_kind=artifact_kind,
                artifact_key=artifact_key,
                schema_id=schema_id,
                content_hash=content_hash,
                content_jsonb=content_json,
            )
            session.add(artifact)
            session.flush()
            append_task_event(
                session,
                task,
                f"{event_prefix}.completed",
                component_id,
                {"compile_run_id": run.id, "artifact_id": artifact.id},
            )
            return artifact.id, False

    def _record_compile_failure_step(
        self,
        task_run_id: int,
        attempt_id: int,
        error: CompilerExecutionError,
        *,
        component_id: str,
        component_version: str,
        schema_id: str,
        input_hash: str | None,
        upstream_hashes: dict[str, str],
        failure_layer: str,
        event_prefix: str,
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
                    AgentStepRun.component_id == component_id,
                )
            )
            if existing is None:
                now = datetime.now(UTC)
                code = error.error_code
                session.add(
                    AgentStepRun(
                        project_id=task.project_id,
                        task_run_id=task.id,
                        task_attempt_id=attempt.id,
                        component_id=component_id,
                        parent_component_id=(
                            INPUT_FREEZE_COMPONENT_ID
                            if component_id == NARRATIVE_IR_COMPONENT_ID
                            else None
                        ),
                        execution_no=1,
                        status="failed",
                        input_hash=input_hash or task.input_hash,
                        upstream_hashes_jsonb=upstream_hashes,
                        output_hash=None,
                        ir_schema_id=schema_id,
                        component_version=component_version,
                        output_jsonb=None,
                        diagnostic_jsonb={
                            "failure_layer": failure_layer,
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
                f"{event_prefix}.failed",
                component_id,
                {"error_code": error.error_code},
            )


__all__ = ["CompilerExecutionError", "CompilerExecutor"]
