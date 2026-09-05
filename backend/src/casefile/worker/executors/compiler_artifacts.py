"""Attempt-fenced Compiler JSON artifact persistence and reuse."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile.application.task_events import append_task_event
from casefile.application.task_lease import is_current_task_attempt
from casefile.data_postgres.models import (
    AgentStepRun,
    CompileArtifact,
    CompileRun,
    TaskAttempt,
    TaskRun,
)
from casefile.worker.executors.prose_store import assert_prose_owner
from casefile.worker.failures import CompilerExecutionError, TaskCancellationRequested


def materialize_json_artifact_component(
    session_factory: sessionmaker[Session],
    worker_id: str,
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
    with session_factory() as session, session.begin():
        task = session.scalar(
            select(TaskRun).where(TaskRun.id == task_run_id).with_for_update()
        )
        attempt = session.scalar(
            select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
        )
        if task is not None and task.input_jsonb.get("prose_renderer_shadow"):
            assert_prose_owner(task, attempt, worker_id)
        if task is None or attempt is None:
            raise CompilerExecutionError("compiler_run_task_mismatch")
        if task.status == "cancelling" and task.leased_by == worker_id:
            raise TaskCancellationRequested
        if (
            task.status != "running"
            or task.leased_by != worker_id
            or not is_current_task_attempt(task, attempt)
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
