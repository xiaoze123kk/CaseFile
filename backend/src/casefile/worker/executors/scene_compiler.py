"""Durable shadow Worker lifecycle for N4.4 Scene Semantic Fill and ScenePlanIR v2."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from casefile.agent_runtime.credentials import decrypt_api_key
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.scene_compiler import (
    SCENE_COMPILER_PIPELINE_VERSION,
    SCENE_SEMANTIC_FILL_PROMPT_VERSION,
    SceneFillStage,
    execute_scene_semantic_fill,
)
from casefile.application.compiler.constants import (
    SCENE_FILL_COMPONENT_ID,
    SCENE_PLAN_ARTIFACT_KEY,
    SCENE_PLAN_V2_COMPONENT_ID,
    SCENE_PLAN_V2_COMPONENT_VERSION,
    SCENE_PLAN_V2_SCHEMA_ID,
    STORY_PLANNER_COMPONENT_ID,
)
from casefile.application.task_events import append_task_event
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentStepRun,
    CompileRun,
    TaskAttempt,
    TaskRun,
    UserProviderSetting,
)
from casefile.domain.narrative_compiler import (
    SCENE_COMPILER_BATCH_SIZE,
    SCENE_STATE_ENGINE_VERSION,
    build_scene_compiler_input_v2,
    build_scene_compiler_model_view,
    canonical_json_sha256,
    compile_scene_plan_v2,
)
from casefile.worker.executors.compiler import CompilerExecutionError
from casefile.worker.failures import TaskCancellationRequested


def execute_scene_compiler_component(
    worker: Any,
    *,
    task_run_id: int,
    attempt_id: int,
    run: CompileRun,
    manifest_json: dict[str, Any],
    novel_plan_json: dict[str, Any],
    novel_plan_hash: str,
    narrative_ir_json: dict[str, Any],
    narrative_ir_hash: str,
) -> tuple[int, str, bool]:
    task, api_key = _load_provider_binding(worker, task_run_id)
    bundle = build_scene_compiler_input_v2(
        novel_plan=novel_plan_json,
        narrative_ir=narrative_ir_json,
        exposure=manifest_json.get("exposure"),
        profile=manifest_json["profile"],
    )
    model_view = build_scene_compiler_model_view(bundle)
    required_turns = 2 + len(model_view["batches"])
    if int(task.budget_jsonb.get("max_turns", 0)) < required_turns:
        raise CompilerExecutionError("compiler_scene_fill_budget_insufficient")
    prompt = load_prompt("scene_compiler_semantic_fill", SCENE_SEMANTIC_FILL_PROMPT_VERSION)
    fingerprint = {
        "pipeline_version": SCENE_COMPILER_PIPELINE_VERSION,
        "scene_compiler_input_hash": bundle["source"]["input_hash"],
        "model_view_hash": canonical_json_sha256(model_view),
        "prompt_version": SCENE_SEMANTIC_FILL_PROMPT_VERSION,
        "prompt_sha256": prompt.system_prompt_sha256,
        "provider": task.provider,
        "model_id": task.model_id,
        "provider_config_version": task.provider_config_version,
        "batch_size": SCENE_COMPILER_BATCH_SIZE,
        "state_engine_version": SCENE_STATE_ENGINE_VERSION,
    }
    component_hash = canonical_json_sha256(fingerprint)
    step_id = _start_step(
        worker,
        task_run_id=task_run_id,
        attempt_id=attempt_id,
        component_hash=component_hash,
        novel_plan_hash=novel_plan_hash,
        narrative_ir_hash=narrative_ir_hash,
        batch_count=len(model_view["batches"]),
    )
    execution = execute_scene_semantic_fill(
        worker.provider_factory(task),
        task_run_id=task_run_id,
        model_view=model_view,
        component_hash=component_hash,
        model_id=str(task.model_id),
        api_key=api_key,
        network_retries=int(task.budget_jsonb.get("network_retries", 0)),
        recover_stage=lambda batch_id, input_hash: _recover_stage(
            worker, task_run_id, batch_id, input_hash
        ),
        before_stage=lambda batch_id, ordinal, input_hash, prompt_version, schema_id: _start_call(
            worker,
            task,
            attempt_id,
            step_id,
            batch_id,
            ordinal,
            input_hash,
            prompt_version,
            prompt.system_prompt_sha256,
            schema_id,
        ),
        after_stage=lambda stage: None if stage.recovered else _finish_call(worker, step_id, stage),
    )
    proposals = list(execution.proposals)
    scene_plan = compile_scene_plan_v2(scene_compiler_input=bundle, semantic_fills=proposals)
    scene_plan_hash = canonical_json_sha256(scene_plan)
    _finish_step(worker, step_id, execution.stages, proposals)
    artifact_id, reused = worker._materialize_json_artifact_component(
        task_run_id=task_run_id,
        attempt_id=attempt_id,
        run=run,
        component_id=SCENE_PLAN_V2_COMPONENT_ID,
        component_version=SCENE_PLAN_V2_COMPONENT_VERSION,
        component_input_hash=scene_plan["source"]["component_fingerprint"],
        upstream_hashes={
            "novel_plan": novel_plan_hash,
            "narrative_ir": narrative_ir_hash,
            "scene_compiler_input": bundle["source"]["input_hash"],
            "semantic_fill": scene_plan["source"]["semantic_fill_hash"],
        },
        artifact_kind="scene_plan",
        artifact_key=SCENE_PLAN_ARTIFACT_KEY,
        schema_id=SCENE_PLAN_V2_SCHEMA_ID,
        content_hash=scene_plan_hash,
        content_json=scene_plan,
        event_prefix="compiler.scene_plan_v2",
        parent_component_id=SCENE_FILL_COMPONENT_ID,
    )
    return artifact_id, scene_plan_hash, reused


def fail_scene_compiler_component(
    worker: Any, task_run_id: int, attempt_id: int, error_code: str
) -> None:
    with worker.session_factory() as session, session.begin():
        current_task = session.get(TaskRun, task_run_id)
        if current_task is not None and current_task.input_jsonb.get("prose_renderer_shadow"):
            _lock_active(session, worker, task_run_id, attempt_id)
        steps = list(
            session.scalars(
                select(AgentStepRun)
                .where(
                    AgentStepRun.task_run_id == task_run_id,
                    AgentStepRun.task_attempt_id == attempt_id,
                    AgentStepRun.component_id == SCENE_FILL_COMPONENT_ID,
                    AgentStepRun.status == "running",
                )
                .with_for_update(of=AgentStepRun)
            )
        )
        now = datetime.now(UTC)
        for step in steps:
            calls = list(
                session.scalars(
                    select(AgentModelCall)
                    .where(
                        AgentModelCall.agent_step_run_id == step.id,
                        AgentModelCall.status == "running",
                    )
                    .with_for_update(of=AgentModelCall)
                )
            )
            for call in calls:
                call.status = "failed"
                call.error_code = error_code
                call.finished_at = now
            step.status = "failed"
            step.finished_at = now
            step.diagnostic_jsonb = {
                "failure_layer": "scene_semantic_fill",
                "issues": [{"code": error_code}],
            }


def _load_provider_binding(worker: Any, task_run_id: int) -> tuple[TaskRun, str]:
    with worker.session_factory() as session, session.begin():
        task = session.get(TaskRun, task_run_id)
        if (
            task is None
            or task.agent_version != SCENE_COMPILER_PIPELINE_VERSION
            or task.provider is None
            or task.model_id is None
            or task.provider_setting_id is None
        ):
            raise CompilerExecutionError("compiler_scene_fill_provider_missing")
        setting = session.get(UserProviderSetting, task.provider_setting_id)
        if (
            setting is None
            or setting.user_id != task.actor_user_id
            or setting.provider != task.provider
            or setting.config_version != task.provider_config_version
            or setting.secret_ciphertext is None
            or setting.secret_nonce is None
            or setting.key_version is None
        ):
            raise CompilerExecutionError("compiler_scene_fill_provider_binding_mismatch")
        api_key = decrypt_api_key(
            setting.secret_ciphertext,
            setting.secret_nonce,
            user_id=setting.user_id,
            provider=setting.provider,
            key_version=setting.key_version,
        )
        session.expunge(task)
        return task, api_key


def _start_step(
    worker: Any,
    *,
    task_run_id: int,
    attempt_id: int,
    component_hash: str,
    novel_plan_hash: str,
    narrative_ir_hash: str,
    batch_count: int,
) -> int:
    with worker.session_factory() as session, session.begin():
        task, attempt = _lock_active(session, worker, task_run_id, attempt_id)
        previous = session.scalar(
            select(AgentStepRun)
            .where(
                AgentStepRun.task_run_id == task.id,
                AgentStepRun.component_id == SCENE_FILL_COMPONENT_ID,
                AgentStepRun.input_hash == component_hash,
            )
            .order_by(AgentStepRun.id.desc())
        )
        if previous is not None and previous.status == "running":
            previous.status = "failed"
            previous.finished_at = datetime.now(UTC)
            previous.diagnostic_jsonb = {"possible_duplicate_call": True}
        step = AgentStepRun(
            project_id=task.project_id,
            task_run_id=task.id,
            task_attempt_id=attempt.id,
            component_id=SCENE_FILL_COMPONENT_ID,
            parent_component_id=STORY_PLANNER_COMPONENT_ID,
            execution_no=1 if previous is None else previous.execution_no + 1,
            status="running",
            input_hash=component_hash,
            upstream_hashes_jsonb={
                "novel_plan": novel_plan_hash,
                "narrative_ir": narrative_ir_hash,
            },
            ir_schema_id="compiler.scene-semantic-fill.v1",
            component_version=SCENE_COMPILER_PIPELINE_VERSION,
            output_jsonb=None,
            diagnostic_jsonb={"batch_count": batch_count},
            usage_jsonb={},
            resumed_from_step_run_id=None,
        )
        session.add(step)
        session.flush()
        append_task_event(
            session,
            task,
            "compiler.scene_compiler.started",
            SCENE_FILL_COMPONENT_ID,
            {"input_hash": component_hash, "batch_count": batch_count},
        )
        return step.id


def _start_call(
    worker: Any,
    task: TaskRun,
    attempt_id: int,
    step_id: int,
    batch_id: str,
    ordinal: int,
    input_hash: str,
    prompt_version: str,
    prompt_hash: str,
    schema_id: str,
) -> None:
    with worker.session_factory() as session, session.begin():
        current, _attempt = _lock_active(session, worker, task.id, attempt_id)
        session.add(
            AgentModelCall(
                project_id=current.project_id,
                task_run_id=current.id,
                task_attempt_id=attempt_id,
                agent_step_run_id=step_id,
                call_no=ordinal,
                status="running",
                provider=str(task.provider),
                model_id=str(task.model_id),
                output_protocol=("json_object" if task.provider == "deepseek" else "strict_schema"),
                prompt_version=prompt_version,
                prompt_component_id=f"scene_semantic_fill.batch.{ordinal:03d}",
                prompt_sha256=prompt_hash,
                target_schema_id=schema_id,
                input_hash=input_hash,
                issues_jsonb=[{"batch_id": batch_id, "phase": "running"}],
            )
        )


def _finish_call(worker: Any, step_id: int, stage: SceneFillStage) -> None:
    raw = stage.raw_output or json.dumps(stage.output, ensure_ascii=False, separators=(",", ":"))
    encoded = raw.encode("utf-8")
    bounded = encoded[:262_144].decode("utf-8", errors="ignore")
    with worker.session_factory() as session, session.begin():
        from casefile.worker.executors.prose_store import fence_prose_step

        fence_prose_step(session, worker.config.worker_id, step_id)
        call = session.scalar(
            select(AgentModelCall)
            .where(
                AgentModelCall.agent_step_run_id == step_id,
                AgentModelCall.call_no == stage.batch_ordinal,
            )
            .with_for_update(of=AgentModelCall)
        )
        if call is None:
            raise CompilerExecutionError("compiler_scene_fill_call_missing")
        call.status = "succeeded"
        call.output_hash = canonical_json_sha256(stage.output)
        call.output_size_bytes = len(encoded)
        call.raw_output_text = bounded
        call.raw_output_truncated = len(encoded) > len(bounded.encode("utf-8"))
        call.issues_jsonb = []
        call.usage_jsonb = {**stage.usage, "latency_ms": stage.latency_ms}
        call.finished_at = datetime.now(UTC)


def _recover_stage(
    worker: Any, task_run_id: int, batch_id: str, input_hash: str
) -> dict[str, Any] | None:
    with worker.session_factory() as session:
        call = session.scalar(
            select(AgentModelCall)
            .where(
                AgentModelCall.task_run_id == task_run_id,
                AgentModelCall.input_hash == input_hash,
                AgentModelCall.status == "succeeded",
                AgentModelCall.raw_output_text.is_not(None),
                AgentModelCall.raw_output_truncated.is_(False),
            )
            .order_by(AgentModelCall.id.desc())
        )
        if call is None or call.raw_output_text is None or call.issues_jsonb:
            return None
        value = json.loads(call.raw_output_text)
        if not isinstance(value, dict) or value.get("batch_id") != batch_id:
            return None
        return value


def _finish_step(
    worker: Any,
    step_id: int,
    stages: tuple[SceneFillStage, ...],
    proposals: list[dict[str, Any]],
) -> None:
    usage: dict[str, float] = {}
    for stage in stages:
        for key, value in stage.usage.items():
            if isinstance(value, (int, float)):
                usage[key] = usage.get(key, 0.0) + float(value)
    with worker.session_factory() as session, session.begin():
        from casefile.worker.executors.prose_store import fence_prose_step

        fence_prose_step(session, worker.config.worker_id, step_id)
        step = session.scalar(
            select(AgentStepRun).where(AgentStepRun.id == step_id).with_for_update(of=AgentStepRun)
        )
        if step is None or step.status != "running":
            raise CompilerExecutionError("compiler_scene_fill_step_missing")
        step.status = "succeeded"
        step.output_hash = canonical_json_sha256(proposals)
        step.output_jsonb = None
        step.diagnostic_jsonb = {
            **step.diagnostic_jsonb,
            "batches": [
                {
                    "batch_id": stage.batch_id,
                    "input_hash": stage.input_hash,
                    "output_hash": canonical_json_sha256(stage.output),
                    "recovered": stage.recovered,
                }
                for stage in stages
            ],
        }
        step.usage_jsonb = usage
        step.finished_at = datetime.now(UTC)


def _lock_active(
    session: Any, worker: Any, task_run_id: int, attempt_id: int
) -> tuple[TaskRun, TaskAttempt]:
    task = session.scalar(select(TaskRun).where(TaskRun.id == task_run_id).with_for_update())
    attempt = session.scalar(
        select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
    )
    if task is not None and task.input_jsonb.get("prose_renderer_shadow"):
        from casefile.worker.executors.prose_store import assert_prose_owner

        assert_prose_owner(task, attempt, worker.config.worker_id)
    if (
        task is not None
        and task.status == "cancelling"
        and task.leased_by == worker.config.worker_id
    ):
        raise TaskCancellationRequested
    if (
        task is None
        or attempt is None
        or task.status != "running"
        or task.leased_by != worker.config.worker_id
        or attempt.task_run_id != task.id
        or attempt.status != "running"
    ):
        raise CompilerExecutionError("compiler_worker_lease_lost")
    return task, attempt


__all__ = ["execute_scene_compiler_component", "fail_scene_compiler_component"]
