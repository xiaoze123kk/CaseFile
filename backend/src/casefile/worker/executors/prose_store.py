"""Attempt-fenced Shadow artifacts and physical Provider call journal."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Event, Thread
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from casefile.agent_runtime.prose_runtime import PROSE_RUNTIME_VERSION
from casefile.application.task_lease import is_current_task_attempt
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentStepRun,
    CompileArtifact,
    CompileRun,
    TaskAttempt,
    TaskRun,
)
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile.worker.failures import TaskCancellationRequested


class ProseLeaseLost(RuntimeError):
    """The former owner must leave all durable state to the current owner."""


class ProseResultUnknown(RuntimeError):
    """An external request has no durable response and must not be resent."""


def assert_prose_owner(
    task: TaskRun | None, attempt: TaskAttempt | None, worker_id: str, *, allow_cancel: bool = False
) -> None:
    if (
        task is None
        or attempt is None
        or task.leased_by != worker_id
        or not is_current_task_attempt(task, attempt)
    ):
        raise ProseLeaseLost("compiler_prose_lease_lost")
    if task.status == "cancelling" and not allow_cancel:
        raise TaskCancellationRequested


def fence_prose_step(session: Session, worker_id: str, step_id: int) -> None:
    """Fence upstream Compiler callbacks by the step's original Attempt."""
    step = session.get(AgentStepRun, step_id)
    if step is None:
        return
    task = session.get(TaskRun, step.task_run_id)
    if task is None:
        return
    task = session.scalar(select(TaskRun).where(TaskRun.id == step.task_run_id).with_for_update())
    attempt = session.scalar(
        select(TaskAttempt).where(TaskAttempt.id == step.task_attempt_id).with_for_update()
    )
    assert_prose_owner(task, attempt, worker_id)


class ProseStore:
    def __init__(
        self,
        factory: sessionmaker[Session],
        run: CompileRun,
        attempt_id: int,
        worker_id: str,
        runtime: dict[str, Any],
        lease_seconds: int = 600,
    ) -> None:
        self.factory = factory
        self.run = run
        self.attempt_id = attempt_id
        self.worker_id = worker_id
        self.runtime = runtime
        self.lease_seconds = lease_seconds
        self.scene_id = ""
        self.phase = "semantic_0"
        self.current_step_id: int | None = None
        self.current_request: Any = None
        self.recovered_hashes: list[str] = []

    def lock(self, session: Session, *, allow_cancel: bool = False) -> tuple[TaskRun, TaskAttempt]:
        task = session.scalar(
            select(TaskRun).where(TaskRun.id == self.run.task_run_id).with_for_update()
        )
        attempt = session.scalar(
            select(TaskAttempt).where(TaskAttempt.id == self.attempt_id).with_for_update()
        )
        assert_prose_owner(task, attempt, self.worker_id, allow_cancel=allow_cancel)
        assert task is not None and attempt is not None
        return task, attempt

    def boundary(self) -> None:
        with self.factory() as session, session.begin():
            self.lock(session)

    @contextmanager
    def heartbeat(self) -> Iterator[None]:
        stop = Event()
        failures: list[Exception] = []

        def renew() -> None:
            while not stop.wait(min(5.0, self.lease_seconds / 3)):
                try:
                    with self.factory() as session, session.begin():
                        task, _ = self.lock(session, allow_cancel=True)
                        task.lease_expires_at = datetime.now(UTC) + timedelta(
                            seconds=self.lease_seconds
                        )
                except Exception as error:
                    failures.append(error)
                    return

        self.boundary()
        thread = Thread(target=renew, name="prose-lease", daemon=True)
        thread.start()
        try:
            yield
            if failures:
                raise failures[0]
        finally:
            stop.set()
            thread.join()

    def _step(
        self,
        session: Session,
        component: str,
        fingerprint: str,
        *,
        status: str = "running",
        source: int | None = None,
        request: Any = None,
    ) -> AgentStepRun:
        ordinal = (
            session.scalar(
                select(func.coalesce(func.max(AgentStepRun.execution_no), 0)).where(
                    AgentStepRun.task_attempt_id == self.attempt_id,
                    AgentStepRun.component_id == component,
                )
            )
            or 0
        )
        step = AgentStepRun(
            project_id=self.run.project_id,
            task_run_id=self.run.task_run_id,
            task_attempt_id=self.attempt_id,
            component_id=component,
            execution_no=ordinal + 1,
            status=status,
            input_hash=fingerprint,
            upstream_hashes_jsonb={
                "compile_input": self.run.input_hash,
                "runtime": canonical_json_sha256(self.runtime),
                **{
                    a.artifact_key: a.content_hash
                    for a in session.scalars(
                        select(CompileArtifact).where(CompileArtifact.compile_run_id == self.run.id)
                    )
                },
            },
            ir_schema_id=(
                "compiler.compile-manifest.v1"
                if request is None
                else "compiler.scene-render.v1"
                if component in {"prose_writer", "prose_rewrite", "prose_polisher"}
                else "compiler.prose-quality-report.v1"
                if component == "prose_quality_critic"
                else "compiler.prose-judge-report.v1"
            ),
            component_version=PROSE_RUNTIME_VERSION,
            diagnostic_jsonb={"scene_id": self.scene_id, "phase": self.phase},
            usage_jsonb={},
            resumed_from_step_run_id=source,
            finished_at=datetime.now(UTC) if status == "reused" else None,
        )
        session.add(step)
        session.flush()
        return step

    def artifact(
        self,
        kind: str,
        key: str,
        content: dict[str, Any],
        component: str,
        *,
        source_step: int | None = None,
        allow_cancel: bool = False,
    ) -> CompileArtifact:
        digest = canonical_json_sha256(content)
        fingerprint = canonical_json_sha256(
            {
                "input": self.run.input_hash,
                "runtime": self.runtime,
                "key": key,
                "content_hash": digest,
            }
        )
        with self.factory() as session, session.begin():
            self.lock(session, allow_cancel=allow_cancel)
            existing = session.scalar(
                select(CompileArtifact).where(
                    CompileArtifact.compile_run_id == self.run.id,
                    CompileArtifact.artifact_key == key,
                )
            )
            if existing is not None:
                if (
                    existing.content_hash != digest
                    or canonical_json_sha256(existing.content_jsonb) != digest
                ):
                    raise ProseResultUnknown("compiler_prose_artifact_identity_conflict")
                return existing
            step = session.get(AgentStepRun, source_step) if source_step is not None else None
            if step is None:
                step = self._step(session, component, fingerprint)
                step.ir_schema_id = content["schema_id"]
                step.status = "succeeded"
                step.output_hash = digest
                step.finished_at = datetime.now(UTC)
            artifact = CompileArtifact(
                project_id=self.run.project_id,
                casefile_id=self.run.casefile_id,
                compile_run_id=self.run.id,
                task_run_id=self.run.task_run_id,
                agent_step_run_id=step.id,
                artifact_kind=kind,
                artifact_key=key,
                schema_id=content["schema_id"],
                content_hash=digest,
                content_jsonb=content,
            )
            session.add(artifact)
            session.flush()
            return artifact

    def begin_request(
        self, component: str, request: Any, result_type: Any, transport_type: Any
    ) -> Any:
        prompt_id = "prose_rewriter" if component == "prose_rewrite" else component
        if component == "prose_quality_critic" and request.request_kind == "pairwise":
            prompt_id = "prose_quality_pairwise"
        prompt = self.runtime["prompts"][prompt_id]
        expected_model = self.runtime[
            "quality_model" if component == "prose_quality_critic" else "generation_model"
        ]
        if (
            request.prompt_version != prompt["version"]
            or request.prompt_hash != prompt["hash"]
            or request.model_id != expected_model
        ):
            raise ProseResultUnknown("compiler_prose_component_binding_mismatch")
        fingerprint = canonical_json_sha256(
            {
                "compile_input": self.run.input_hash,
                "scene": self.scene_id,
                "phase": self.phase,
                "runtime": self.runtime,
                "request": request.request_fingerprint,
            }
        )
        self.current_request = request
        with self.factory() as session, session.begin():
            self.lock(session)
            prior = list(
                session.scalars(
                    select(AgentModelCall)
                    .where(
                        AgentModelCall.task_run_id == self.run.task_run_id,
                        AgentModelCall.request_fingerprint == fingerprint,
                    )
                    .order_by(AgentModelCall.id)
                )
            )
            completed = next((c for c in reversed(prior) if c.response_jsonb is not None), None)
            if completed is not None:
                data = dict(completed.response_jsonb or {})
                try:
                    parsed = json.loads(completed.raw_output_text or "")
                except json.JSONDecodeError:
                    parsed = None
                candidate = parsed if isinstance(parsed, dict) else None
                if (
                    sha256((completed.raw_output_text or "").encode()).hexdigest()
                    != completed.output_hash
                    or data.get("raw_response") != completed.raw_output_text
                    or data.get("output_hash") != completed.output_hash
                    or data.get("candidate") != candidate
                ):
                    raise ProseResultUnknown("compiler_prose_response_hash_mismatch")
                data["transport_attempts"] = tuple(
                    transport_type(**a) for a in data["transport_attempts"]
                )
                data["recovered"] = True
                step = self._step(
                    session,
                    component,
                    fingerprint,
                    source=completed.agent_step_run_id,
                    request=request,
                )
                step.usage_jsonb = dict(completed.usage_jsonb)
                self.current_step_id = step.id
                self.recovered_hashes.append(request.request_fingerprint)
                return result_type(**data)
            if prior:
                raise ProseResultUnknown("compiler_prose_external_result_unknown")
            step = self._step(session, component, fingerprint, request=request)
            self.current_step_id = step.id
        return None

    def before_transport(self, request: Any, ordinal: int) -> None:
        with self.factory() as session, session.begin():
            self.lock(session)
            step = session.get(AgentStepRun, self.current_step_id)
            if step is None or step.status != "running":
                raise ProseLeaseLost("compiler_prose_step_not_running")
            session.add(
                AgentModelCall(
                    project_id=self.run.project_id,
                    task_run_id=self.run.task_run_id,
                    task_attempt_id=self.attempt_id,
                    agent_step_run_id=step.id,
                    call_no=ordinal,
                    status="running",
                    provider="deepseek",
                    model_id=request.model_id,
                    output_protocol="json_object",
                    prompt_version=request.prompt_version,
                    prompt_component_id=step.component_id,
                    prompt_sha256=request.prompt_hash,
                    target_schema_id=str(request.input_payload["output_schema_id"]),
                    input_hash=request.input_hash,
                    request_fingerprint=step.input_hash,
                    usage_jsonb={},
                    issues_jsonb=[],
                    parse_status="pending",
                )
            )

    def failed_transport(self, attempt: Any) -> None:
        with self.factory() as session, session.begin():
            self.lock(session, allow_cancel=True)
            call = session.scalar(
                select(AgentModelCall).where(
                    AgentModelCall.agent_step_run_id == self.current_step_id,
                    AgentModelCall.call_no == attempt.attempt_index,
                )
            )
            if call is not None and call.status == "running":
                call.status = "failed"
                call.error_code = "prose_transport_failed"
                call.issues_jsonb = [{"code": attempt.error_code}]
                call.latency_ms = attempt.latency_ms
                call.usage_jsonb = {
                    "usage_known": attempt.usage is not None,
                    **(attempt.usage or {}),
                }
                call.parse_status = "transport_failed"
                call.finished_at = datetime.now(UTC)

    def save_response(self, result: Any) -> None:
        with self.factory() as session, session.begin():
            self.lock(session, allow_cancel=True)
            call = session.scalar(
                select(AgentModelCall)
                .where(
                    AgentModelCall.agent_step_run_id == self.current_step_id,
                    AgentModelCall.status == "running",
                )
                .order_by(AgentModelCall.call_no.desc())
            )
            if call is None:
                raise ProseResultUnknown("compiler_prose_call_missing")
            call.raw_output_text = result.raw_response
            call.output_hash = sha256(result.raw_response.encode()).hexdigest()
            call.output_size_bytes = len(result.raw_response.encode())
            call.response_jsonb = asdict(result)
            observed_usage = result.transport_attempts[-1].usage
            call.usage_jsonb = {
                **(observed_usage or {}),
                "usage_known": observed_usage is not None,
            }
            call.latency_ms = result.transport_attempts[-1].latency_ms
            call.parse_status = "response_saved"

    def finish_steps(
        self,
        *,
        error_code: str | None = None,
        allow_cancel: bool = False,
        outputs: dict[str, str] | None = None,
    ) -> None:
        with self.factory() as session, session.begin():
            self.lock(session, allow_cancel=allow_cancel)
            steps = list(
                session.scalars(
                    select(AgentStepRun).where(
                        AgentStepRun.task_run_id == self.run.task_run_id,
                        AgentStepRun.component_id.like("prose_%"),
                        AgentStepRun.status == "running",
                    )
                )
            )
            for step in steps:
                calls = list(
                    session.scalars(
                        select(AgentModelCall).where(
                            AgentModelCall.agent_step_run_id
                            == (step.resumed_from_step_run_id or step.id)
                        )
                    )
                )
                output_hash = next(
                    (
                        (outputs or {}).get((c.response_jsonb or {}).get("request_fingerprint", ""))
                        for c in reversed(calls)
                        if c.response_jsonb
                    ),
                    None,
                )
                step_error = None if output_hash else error_code
                for call in calls:
                    if call.status == "running":
                        call.status = "failed" if step_error else "succeeded"
                        call.parse_status = "invalid" if step_error else "validated"
                        call.error_code = "prose_component_failed" if step_error else None
                        call.issues_jsonb = [{"code": step_error}] if step_error else []
                        call.finished_at = datetime.now(UTC)
                step.status = (
                    "failed"
                    if step_error
                    else "reused"
                    if step.resumed_from_step_run_id
                    else "succeeded"
                )
                step.output_hash = output_hash
                step.diagnostic_jsonb = {**step.diagnostic_jsonb, "error_code": step_error}
                step.usage_jsonb = {
                    key: sum(int(c.usage_jsonb.get(key, 0)) for c in calls)
                    for key in ("input_tokens", "output_tokens", "total_tokens")
                }
                step.finished_at = datetime.now(UTC)
