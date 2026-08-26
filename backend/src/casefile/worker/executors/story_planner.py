"""Durable N4.3 Story Planner component execution and recovery."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import select

from casefile.agent_runtime.constraint_first_story_planner import (
    CONSTRAINT_FIRST_PIPELINE_VERSION,
    SEMANTIC_FILL_PROMPT_VERSION,
    SKELETON_PROMPT_VERSION,
    ConstraintFirstStage,
    execute_constraint_first_story_planner,
)
from casefile.agent_runtime.credentials import decrypt_api_key
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.scene_compiler import SCENE_COMPILER_PIPELINE_VERSION
from casefile.agent_runtime.story_planner import (
    STORY_PLANNER_AGENT_VERSION,
    StoryPlannerRequest,
    StoryPlannerRound,
    execute_story_planner,
)
from casefile.application.compiler.constants import (
    NARRATIVE_IR_COMPONENT_ID,
    NOVEL_PLAN_ARTIFACT_KEY,
    NOVEL_PLAN_SCHEMA_ID,
    STORY_PLANNER_COMPONENT_ID,
)
from casefile.application.task_events import append_task_event
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentStepRun,
    CompileArtifact,
    CompileRun,
    TaskAttempt,
    TaskRun,
    UserProviderSetting,
)
from casefile.domain.narrative_compiler import (
    REFERENCE_SOLVER_VERSION,
    NovelPlanRepairResult,
    ReferencePlanningSolver,
    build_planner_input_bundle,
    build_planner_input_bundle_v3,
    build_planner_model_view_v4,
    canonical_json_sha256,
    canonicalize_novel_plan,
    compile_planning_problem,
    planning_component_fingerprint,
    repair_novel_plan_candidate,
    story_planner_component_fingerprint,
    validate_novel_plan_candidate,
)
from casefile.worker.executors.compiler import CompilerExecutionError
from casefile.worker.support import TaskCancellationRequested


def execute_story_planner_component(
    worker: Any,
    *,
    task_run_id: int,
    attempt_id: int,
    run: CompileRun,
    manifest_json: dict[str, Any],
    narrative_ir_json: dict[str, Any],
    narrative_ir_hash: str,
) -> tuple[int, str, bool]:
    if _task_agent_version(worker, task_run_id) in {
        CONSTRAINT_FIRST_PIPELINE_VERSION,
        SCENE_COMPILER_PIPELINE_VERSION,
    }:
        return _execute_constraint_first_component(
            worker,
            task_run_id=task_run_id,
            attempt_id=attempt_id,
            run=run,
            manifest_json=manifest_json,
            narrative_ir_json=narrative_ir_json,
            narrative_ir_hash=narrative_ir_hash,
        )
    planner_input = build_planner_input_bundle(
        narrative_ir=narrative_ir_json,
        exposure=manifest_json.get("exposure"),
        profile=manifest_json["profile"]["frozen_payload"],
        compile_mode=manifest_json["mode"],
    )
    task, api_key, prompt_hash, component_hash = _load_context(worker, task_run_id, planner_input)
    reusable = _find_reusable(worker, run.project_id, component_hash)
    if reusable is not None:
        return _reuse_artifact(
            worker,
            task_run_id,
            attempt_id,
            run,
            reusable,
            component_hash,
            narrative_ir_hash,
        )

    recovered = _recover_candidate(worker, task_run_id, component_hash)
    if recovered is None:
        step_id = _start_step(worker, task_run_id, attempt_id, component_hash, narrative_ir_hash)
        request = StoryPlannerRequest(
            task_run_id=task_run_id,
            prompt_version=task.prompt_version,
            planner_input=planner_input,
            input_hash=component_hash,
            model_id=str(task.model_id),
            api_key=api_key,
            max_turns=1,
            network_retries=int(task.budget_jsonb.get("network_retries", 0)),
        )
        execution = execute_story_planner(
            worker.provider_factory(task),
            request,
            before_call=lambda call_no, _request: _start_call(
                worker, task, attempt_id, step_id, call_no, prompt_hash, component_hash
            ),
            after_round=lambda round_result: _finish_call(worker, step_id, round_result),
        )
        candidate = execution.candidate
    else:
        recovered_step_id, candidate = recovered
        step_id = _start_step(
            worker,
            task_run_id,
            attempt_id,
            component_hash,
            narrative_ir_hash,
            resumed_from_step_id=recovered_step_id,
        )

    repair = repair_novel_plan_candidate(candidate, planner_input=planner_input)
    if not repair.before.valid:
        _record_repair_evaluation(
            worker,
            task_run_id=task_run_id,
            attempt_id=attempt_id,
            step_id=step_id,
            repair=repair,
        )
    candidate = repair.candidate
    # Model output never bypasses the authoritative validator. Unrepairable candidates fail closed.
    validate_novel_plan_candidate(candidate, planner_input=planner_input)
    novel_plan = canonicalize_novel_plan(
        candidate,
        planner_input=planner_input,
        planner_version=STORY_PLANNER_AGENT_VERSION,
        component_fingerprint=component_hash,
    )
    content_hash = canonical_json_sha256(novel_plan)
    artifact_id = _commit(
        worker,
        task_run_id,
        attempt_id,
        run,
        step_id,
        component_hash,
        novel_plan,
        content_hash,
    )
    return artifact_id, content_hash, False


def _task_agent_version(worker: Any, task_run_id: int) -> str | None:
    with worker.session_factory() as session:
        return cast(
            str | None,
            session.scalar(select(TaskRun.agent_version).where(TaskRun.id == task_run_id)),
        )


def _execute_constraint_first_component(
    worker: Any,
    *,
    task_run_id: int,
    attempt_id: int,
    run: CompileRun,
    manifest_json: dict[str, Any],
    narrative_ir_json: dict[str, Any],
    narrative_ir_hash: str,
) -> tuple[int, str, bool]:
    planner_input = build_planner_input_bundle_v3(
        narrative_ir=narrative_ir_json,
        exposure=manifest_json.get("exposure"),
        profile=manifest_json["profile"]["frozen_payload"],
        compile_mode=manifest_json["mode"],
    )
    model_view = build_planner_model_view_v4(planner_input)
    problem = compile_planning_problem(planner_input)
    task, api_key = _load_provider_binding(worker, task_run_id)
    skeleton_prompt = load_prompt("story_planner_skeleton", SKELETON_PROMPT_VERSION)
    fill_prompt = load_prompt("story_planner_semantic_fill", SEMANTIC_FILL_PROMPT_VERSION)
    fingerprint = planning_component_fingerprint(
        planner_input=planner_input,
        problem=problem,
        solver_version=REFERENCE_SOLVER_VERSION,
        skeleton_prompt_version=SKELETON_PROMPT_VERSION,
        skeleton_prompt_sha256=skeleton_prompt.system_prompt_sha256,
        fill_prompt_version=SEMANTIC_FILL_PROMPT_VERSION,
        fill_prompt_sha256=fill_prompt.system_prompt_sha256,
    )
    fingerprint.update(
        {
            "provider": task.provider,
            "model_id": task.model_id,
            "provider_config_version": task.provider_config_version,
            "planner_model_view_schema_id": model_view["schema_id"],
            "planner_model_view_hash": canonical_json_sha256(model_view),
            "structural_patch_schema_id": "compiler.story-plan-structural-patch.v1",
        }
    )
    component_hash = canonical_json_sha256(fingerprint)
    reusable = _find_reusable(worker, run.project_id, component_hash)
    if reusable is not None:
        return _reuse_artifact(
            worker,
            task_run_id,
            attempt_id,
            run,
            reusable,
            component_hash,
            narrative_ir_hash,
            component_version=CONSTRAINT_FIRST_PIPELINE_VERSION,
        )

    step_id = _start_step(
        worker,
        task_run_id,
        attempt_id,
        component_hash,
        narrative_ir_hash,
        component_version=CONSTRAINT_FIRST_PIPELINE_VERSION,
    )
    prompt_by_stage = {
        "skeleton_proposal": (
            SKELETON_PROMPT_VERSION,
            skeleton_prompt.system_prompt_sha256,
        ),
        "semantic_fill": (
            SEMANTIC_FILL_PROMPT_VERSION,
            fill_prompt.system_prompt_sha256,
        ),
    }
    execution = execute_constraint_first_story_planner(
        worker.provider_factory(task),
        ReferencePlanningSolver(),
        task_run_id=task_run_id,
        planner_input=planner_input,
        model_view=model_view,
        component_hash=component_hash,
        model_id=str(task.model_id),
        api_key=api_key,
        network_retries=int(task.budget_jsonb.get("network_retries", 0)),
        recover_stage=lambda stage, input_hash: _recover_constraint_stage(
            worker,
            task_run_id,
            stage,
            input_hash,
        ),
        before_stage=lambda stage, input_hash, prompt_version, schema_id: (
            _start_constraint_call(
                worker,
                task,
                attempt_id,
                step_id,
                stage,
                input_hash,
                prompt_version,
                prompt_by_stage[stage][1],
                schema_id,
            )
        ),
        after_stage=lambda stage: (
            None if stage.recovered else _finish_constraint_call(worker, step_id, stage)
        ),
    )
    novel_plan = canonicalize_novel_plan(
        execution.candidate,
        planner_input=planner_input,
        planner_version=CONSTRAINT_FIRST_PIPELINE_VERSION,
        component_fingerprint=component_hash,
    )
    content_hash = canonical_json_sha256(novel_plan)
    artifact_id = _commit(
        worker,
        task_run_id,
        attempt_id,
        run,
        step_id,
        component_hash,
        novel_plan,
        content_hash,
    )
    return artifact_id, content_hash, False


def _load_provider_binding(worker: Any, task_run_id: int) -> tuple[TaskRun, str]:
    with worker.session_factory() as session, session.begin():
        task = session.get(TaskRun, task_run_id)
        if (
            task is None
            or task.provider is None
            or task.model_id is None
            or task.provider_setting_id is None
        ):
            raise CompilerExecutionError("compiler_story_planner_provider_missing")
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
            raise CompilerExecutionError("compiler_story_planner_provider_binding_mismatch")
        api_key = decrypt_api_key(
            setting.secret_ciphertext,
            setting.secret_nonce,
            user_id=setting.user_id,
            provider=setting.provider,
            key_version=setting.key_version,
        )
        session.expunge(task)
        return task, api_key


def _load_context(
    worker: Any, task_run_id: int, planner_input: dict[str, Any]
) -> tuple[TaskRun, str, str, str]:
    with worker.session_factory() as session, session.begin():
        task = session.get(TaskRun, task_run_id)
        if (
            task is None
            or task.provider is None
            or task.model_id is None
            or task.provider_setting_id is None
        ):
            raise CompilerExecutionError("compiler_story_planner_provider_missing")
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
            raise CompilerExecutionError("compiler_story_planner_provider_binding_mismatch")
        prompt_hash = load_prompt("story_planner", task.prompt_version).system_prompt_sha256
        fingerprint = story_planner_component_fingerprint(
            planner_input=planner_input,
            prompt_version=task.prompt_version,
            prompt_sha256=prompt_hash,
            provider=task.provider,
            model_id=task.model_id,
            provider_config_version=int(task.provider_config_version or 0),
        )
        api_key = decrypt_api_key(
            setting.secret_ciphertext,
            setting.secret_nonce,
            user_id=setting.user_id,
            provider=setting.provider,
            key_version=setting.key_version,
        )
        component_hash = canonical_json_sha256(fingerprint)
        session.expunge(task)
        return task, api_key, prompt_hash, component_hash


def _find_reusable(worker: Any, project_id: int, component_hash: str) -> CompileArtifact | None:
    with worker.session_factory() as session:
        artifact: CompileArtifact | None = session.scalar(
            select(CompileArtifact)
            .join(AgentStepRun, AgentStepRun.id == CompileArtifact.agent_step_run_id)
            .where(
                CompileArtifact.artifact_key == NOVEL_PLAN_ARTIFACT_KEY,
                CompileArtifact.project_id == project_id,
                AgentStepRun.input_hash == component_hash,
                AgentStepRun.status.in_(("succeeded", "reused")),
            )
            .order_by(CompileArtifact.id.desc())
        )
        if artifact is not None:
            session.expunge(artifact)
        return artifact


def _start_step(
    worker: Any,
    task_run_id: int,
    attempt_id: int,
    component_hash: str,
    narrative_ir_hash: str,
    resumed_from_step_id: int | None = None,
    component_version: str = STORY_PLANNER_AGENT_VERSION,
) -> int:
    with worker.session_factory() as session, session.begin():
        task, attempt = _lock_active(session, worker, task_run_id, attempt_id)
        previous = session.scalar(
            select(AgentStepRun)
            .where(
                AgentStepRun.task_run_id == task.id,
                AgentStepRun.component_id == STORY_PLANNER_COMPONENT_ID,
                AgentStepRun.input_hash == component_hash,
            )
            .order_by(AgentStepRun.id.desc())
        )
        if previous is not None and previous.status == "running":
            previous.status = "failed"
            previous.finished_at = datetime.now(UTC)
            previous.diagnostic_jsonb = {"possible_duplicate_call": True}
        execution_no = 1 if previous is None else previous.execution_no + 1
        step = AgentStepRun(
            project_id=task.project_id,
            task_run_id=task.id,
            task_attempt_id=attempt.id,
            component_id=STORY_PLANNER_COMPONENT_ID,
            parent_component_id=NARRATIVE_IR_COMPONENT_ID,
            execution_no=execution_no,
            status="running",
            input_hash=component_hash,
            upstream_hashes_jsonb={"narrative_ir": narrative_ir_hash},
            ir_schema_id=NOVEL_PLAN_SCHEMA_ID,
            component_version=component_version,
            diagnostic_jsonb={
                "possible_duplicate_call": previous is not None and resumed_from_step_id is None,
                "replayed_persisted_output": resumed_from_step_id is not None,
            },
            usage_jsonb={},
            resumed_from_step_run_id=resumed_from_step_id,
        )
        session.add(step)
        session.flush()
        append_task_event(
            session,
            task,
            "compiler.story_planner.started",
            STORY_PLANNER_COMPONENT_ID,
            {"input_hash": component_hash},
        )
        return step.id


def _start_call(
    worker: Any,
    task: TaskRun,
    attempt_id: int,
    step_id: int,
    call_no: int,
    prompt_hash: str,
    component_hash: str,
) -> None:
    with worker.session_factory() as session, session.begin():
        current, _attempt = _lock_active(session, worker, task.id, attempt_id)
        session.add(
            AgentModelCall(
                project_id=current.project_id,
                task_run_id=current.id,
                task_attempt_id=attempt_id,
                agent_step_run_id=step_id,
                call_no=call_no,
                status="running",
                provider=str(task.provider),
                model_id=str(task.model_id),
                output_protocol="strict_schema",
                prompt_version=task.prompt_version,
                prompt_component_id=STORY_PLANNER_COMPONENT_ID,
                prompt_sha256=prompt_hash,
                target_schema_id="compiler.novel-plan-candidate.v1",
                input_hash=component_hash,
            )
        )


def _record_repair_evaluation(
    worker: Any,
    *,
    task_run_id: int,
    attempt_id: int,
    step_id: int,
    repair: NovelPlanRepairResult,
) -> None:
    diagnostic = {
        "repair_version": repair.repair_version,
        "applied": repair.applied,
        "changes": list(repair.changes),
        "before_violations": [
            {"code": item.code, "details": item.details} for item in repair.before.violations
        ],
        "after_violations": [
            {"code": item.code, "details": item.details} for item in repair.after.violations
        ],
    }
    with worker.session_factory() as session, session.begin():
        task, _attempt = _lock_active(session, worker, task_run_id, attempt_id)
        step = session.scalar(
            select(AgentStepRun)
            .where(AgentStepRun.id == step_id)
            .with_for_update(of=AgentStepRun)
        )
        if step is None or step.task_run_id != task.id:
            raise CompilerExecutionError("compiler_story_planner_step_mismatch")
        step.diagnostic_jsonb = {**(step.diagnostic_jsonb or {}), "candidate_repair": diagnostic}
        append_task_event(
            session,
            task,
            "compiler.story_planner.repair_evaluated",
            STORY_PLANNER_COMPONENT_ID,
            diagnostic,
        )


def _finish_call(worker: Any, step_id: int, result: StoryPlannerRound) -> None:
    raw = result.raw_output or json.dumps(
        result.candidate, ensure_ascii=False, separators=(",", ":")
    )
    encoded = raw.encode("utf-8")
    bounded = encoded[:262_144].decode("utf-8", errors="ignore")
    with worker.session_factory() as session, session.begin():
        call = session.scalar(
            select(AgentModelCall)
            .where(
                AgentModelCall.agent_step_run_id == step_id,
                AgentModelCall.call_no == result.call_no,
            )
            .with_for_update(of=AgentModelCall)
        )
        if call is None:
            raise CompilerExecutionError("compiler_story_planner_call_missing")
        call.status = "succeeded"
        call.output_hash = canonical_json_sha256(result.candidate or {})
        call.output_size_bytes = len(encoded)
        call.raw_output_text = bounded
        call.raw_output_truncated = len(encoded) > len(bounded.encode("utf-8"))
        call.issues_jsonb = list(result.structural_errors)
        call.usage_jsonb = {**result.usage, "latency_ms": result.latency_ms}
        call.finished_at = datetime.now(UTC)


def _start_constraint_call(
    worker: Any,
    task: TaskRun,
    attempt_id: int,
    step_id: int,
    stage: str,
    input_hash: str,
    prompt_version: str,
    prompt_hash: str,
    schema_id: str,
) -> None:
    call_no = 1 if stage == "skeleton_proposal" else 2
    with worker.session_factory() as session, session.begin():
        current, _attempt = _lock_active(session, worker, task.id, attempt_id)
        session.add(
            AgentModelCall(
                project_id=current.project_id,
                task_run_id=current.id,
                task_attempt_id=attempt_id,
                agent_step_run_id=step_id,
                call_no=call_no,
                status="running",
                provider=str(task.provider),
                model_id=str(task.model_id),
                output_protocol=(
                    "json_object" if task.provider == "deepseek" else "strict_schema"
                ),
                prompt_version=prompt_version,
                prompt_component_id=stage,
                prompt_sha256=prompt_hash,
                target_schema_id=schema_id,
                input_hash=input_hash,
            )
        )


def _finish_constraint_call(
    worker: Any,
    step_id: int,
    stage: ConstraintFirstStage,
) -> None:
    raw = json.dumps(stage.output, ensure_ascii=False, separators=(",", ":"))
    encoded = raw.encode("utf-8")
    with worker.session_factory() as session, session.begin():
        call = session.scalar(
            select(AgentModelCall)
            .where(
                AgentModelCall.agent_step_run_id == step_id,
                AgentModelCall.prompt_component_id == stage.stage,
                AgentModelCall.input_hash == stage.input_hash,
            )
            .with_for_update(of=AgentModelCall)
        )
        if call is None:
            raise CompilerExecutionError("compiler_story_planner_call_missing")
        call.status = "succeeded"
        call.output_hash = canonical_json_sha256(stage.output)
        call.output_size_bytes = len(encoded)
        call.raw_output_text = raw
        call.raw_output_truncated = False
        call.issues_jsonb = []
        call.usage_jsonb = {**stage.usage, "latency_ms": stage.latency_ms}
        call.finished_at = datetime.now(UTC)


def _recover_constraint_stage(
    worker: Any,
    task_run_id: int,
    stage: str,
    input_hash: str,
) -> dict[str, Any] | None:
    with worker.session_factory() as session:
        call = session.scalar(
            select(AgentModelCall)
            .where(
                AgentModelCall.task_run_id == task_run_id,
                AgentModelCall.prompt_component_id == stage,
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
        if not isinstance(value, dict):
            return None
        if call.output_hash != canonical_json_sha256(value):
            return None
        return value


def _recover_candidate(
    worker: Any, task_run_id: int, component_hash: str
) -> tuple[int, dict[str, Any]] | None:
    with worker.session_factory() as session, session.begin():
        call = session.scalar(
            select(AgentModelCall)
            .join(AgentStepRun, AgentStepRun.id == AgentModelCall.agent_step_run_id)
            .where(
                AgentModelCall.task_run_id == task_run_id,
                AgentModelCall.status == "succeeded",
                AgentModelCall.raw_output_text.is_not(None),
                AgentStepRun.input_hash == component_hash,
            )
            .order_by(AgentModelCall.id.desc())
        )
        if call is None or call.raw_output_text is None or call.issues_jsonb:
            return None
        value = json.loads(call.raw_output_text)
        if not isinstance(value, dict):
            return None
        if session.get(AgentStepRun, call.agent_step_run_id) is None:
            return None
        return call.agent_step_run_id, value


def _reuse_artifact(
    worker: Any,
    task_run_id: int,
    attempt_id: int,
    run: CompileRun,
    source: CompileArtifact,
    component_hash: str,
    narrative_ir_hash: str,
    component_version: str = STORY_PLANNER_AGENT_VERSION,
) -> tuple[int, str, bool]:
    with worker.session_factory() as session, session.begin():
        task, attempt = _lock_active(session, worker, task_run_id, attempt_id)
        now = datetime.now(UTC)
        step = AgentStepRun(
            project_id=task.project_id,
            task_run_id=task.id,
            task_attempt_id=attempt.id,
            component_id=STORY_PLANNER_COMPONENT_ID,
            parent_component_id=NARRATIVE_IR_COMPONENT_ID,
            execution_no=1,
            status="reused",
            input_hash=component_hash,
            upstream_hashes_jsonb={"narrative_ir": narrative_ir_hash},
            output_hash=source.content_hash,
            ir_schema_id=NOVEL_PLAN_SCHEMA_ID,
            component_version=component_version,
            diagnostic_jsonb={"reused_artifact_id": source.id},
            usage_jsonb={},
            resumed_from_step_run_id=source.agent_step_run_id,
            started_at=now,
            finished_at=now,
        )
        session.add(step)
        session.flush()
        if source.compile_run_id == run.id:
            append_task_event(
                session,
                task,
                "compiler.story_planner.reused",
                STORY_PLANNER_COMPONENT_ID,
                {"artifact_id": source.id, "source_artifact_id": source.id},
            )
            return source.id, source.content_hash, True
        artifact = CompileArtifact(
            project_id=run.project_id,
            casefile_id=run.casefile_id,
            compile_run_id=run.id,
            task_run_id=task.id,
            agent_step_run_id=step.id,
            artifact_kind="novel_plan",
            artifact_key=NOVEL_PLAN_ARTIFACT_KEY,
            schema_id=NOVEL_PLAN_SCHEMA_ID,
            content_hash=source.content_hash,
            content_jsonb=source.content_jsonb,
        )
        session.add(artifact)
        session.flush()
        append_task_event(
            session,
            task,
            "compiler.story_planner.reused",
            STORY_PLANNER_COMPONENT_ID,
            {"artifact_id": artifact.id, "source_artifact_id": source.id},
        )
        return artifact.id, artifact.content_hash, True


def _commit(
    worker: Any,
    task_run_id: int,
    attempt_id: int,
    run: CompileRun,
    step_id: int,
    component_hash: str,
    content: dict[str, Any],
    content_hash: str,
) -> int:
    with worker.session_factory() as session, session.begin():
        task, _attempt = _lock_active(session, worker, task_run_id, attempt_id)
        step = session.scalar(
            select(AgentStepRun)
            .where(AgentStepRun.id == step_id)
            .with_for_update(of=AgentStepRun)
        )
        if step is None or step.input_hash != component_hash:
            raise CompilerExecutionError("compiler_story_planner_step_mismatch")
        step.status = "succeeded"
        step.output_hash = content_hash
        step.output_jsonb = None
        step.finished_at = datetime.now(UTC)
        artifact = CompileArtifact(
            project_id=run.project_id,
            casefile_id=run.casefile_id,
            compile_run_id=run.id,
            task_run_id=task.id,
            agent_step_run_id=step.id,
            artifact_kind="novel_plan",
            artifact_key=NOVEL_PLAN_ARTIFACT_KEY,
            schema_id=NOVEL_PLAN_SCHEMA_ID,
            content_hash=content_hash,
            content_jsonb=content,
        )
        session.add(artifact)
        session.flush()
        append_task_event(
            session,
            task,
            "compiler.story_planner.completed",
            STORY_PLANNER_COMPONENT_ID,
            {"artifact_id": artifact.id, "output_hash": content_hash},
        )
        return artifact.id


def _lock_active(session: Any, worker: Any, task_run_id: int, attempt_id: int) -> tuple[Any, Any]:
    task = session.scalar(select(TaskRun).where(TaskRun.id == task_run_id).with_for_update())
    attempt = session.scalar(
        select(TaskAttempt).where(TaskAttempt.id == attempt_id).with_for_update()
    )
    if task is None or attempt is None:
        raise CompilerExecutionError("compiler_run_task_mismatch")
    if task.status == "cancelling":
        raise TaskCancellationRequested
    if (
        task.status != "running"
        or task.leased_by != worker.config.worker_id
        or attempt.status != "running"
        or attempt.task_run_id != task.id
    ):
        raise CompilerExecutionError("compiler_worker_lease_lost")
    return task, attempt


def fail_story_planner_component(
    worker: Any, task_run_id: int, attempt_id: int, error_code: str
) -> None:
    with worker.session_factory() as session, session.begin():
        task = session.get(TaskRun, task_run_id)
        if task is None:
            return
        now = datetime.now(UTC)
        step = session.scalar(
            select(AgentStepRun)
            .where(
                AgentStepRun.task_run_id == task_run_id,
                AgentStepRun.task_attempt_id == attempt_id,
                AgentStepRun.component_id == STORY_PLANNER_COMPONENT_ID,
                AgentStepRun.status == "running",
            )
            .order_by(AgentStepRun.id.desc())
            .with_for_update(of=AgentStepRun)
        )
        if step is not None:
            calls = session.scalars(
                select(AgentModelCall)
                .where(
                    AgentModelCall.agent_step_run_id == step.id,
                    AgentModelCall.status == "running",
                )
                .with_for_update(of=AgentModelCall)
            )
            for call in calls:
                call.status = "failed"
                call.error_code = error_code
                call.finished_at = now
            step.status = "failed"
            step.finished_at = now
            step.diagnostic_jsonb = {
                **step.diagnostic_jsonb,
                "failure_layer": "story_planner",
                "issues": [{"code": error_code, "path": "", "message": error_code}],
                "recoverable": False,
            }
        append_task_event(
            session,
            task,
            "compiler.story_planner.failed",
            STORY_PLANNER_COMPONENT_ID,
            {"error_code": error_code},
        )


__all__ = ["execute_story_planner_component", "fail_story_planner_component"]
