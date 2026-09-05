"""Read-only prose manifest projections and atomic cancellation convergence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from casefile_contracts import CompileManifest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from casefile.data_postgres.models import (
    AgentModelCall,
    AgentStepRun,
    CompileArtifact,
    CompileRun,
    TaskAttempt,
    TaskRun,
)
from casefile.domain.narrative_compiler import canonical_json_sha256
from casefile.domain.narrative_compiler.prose_checklist import PROSE_CHECKLIST_POLICY_HASH


def scene_usage(
    session: Session, task_id: int, scene_id: str, recovered: list[str]
) -> dict[str, Any]:
    calls = list(
        session.scalars(
            select(AgentModelCall)
            .join(AgentStepRun, AgentStepRun.id == AgentModelCall.agent_step_run_id)
            .where(
                AgentModelCall.task_run_id == task_id,
                AgentStepRun.diagnostic_jsonb["scene_id"].astext == scene_id,
            )
        )
    )
    return {
        "call_count": len({c.request_fingerprint for c in calls}),
        "physical_request_count": len(calls),
        "unknown_usage_count": sum(not c.usage_jsonb.get("usage_known", False) for c in calls),
        "usage": {
            k: sum(int(c.usage_jsonb.get(k, 0)) for c in calls)
            for k in ("input_tokens", "output_tokens", "total_tokens")
        },
        "latency_ms": sum(c.latency_ms or 0 for c in calls),
        "recovered_call_hashes": sorted(set(recovered)),
    }


def project_prose_scene(
    session: Session,
    run_id: int,
    task_id: int,
    scene: dict[str, Any],
    state: str,
    reason: str | None,
    recovered: list[str],
) -> dict[str, Any]:
    artifacts = list(
        session.scalars(
            select(CompileArtifact)
            .where(CompileArtifact.compile_run_id == run_id)
            .order_by(CompileArtifact.id)
        )
    )
    items = [a for a in artifacts if a.content_jsonb.get("scene_id") == scene["scene_id"]]

    def hashes(schema: str) -> list[str]:
        return list(
            dict.fromkeys(a.content_hash for a in items if a.schema_id == f"compiler.{schema}.v1")
        )

    renders = [a for a in items if a.artifact_kind == "scene_render"]
    return {
        "scene_id": scene["scene_id"],
        "scene_ordinal": scene["discourse_order"],
        "final_state": state,
        "checklist_hash": next(iter(hashes("prose-judge-checklist")), None),
        "render_hashes": hashes("scene-render"),
        "judge_report_hashes": [
            a.content_hash
            for a in items
            if a.schema_id == "compiler.prose-judge-report.v1"
            and a.content_jsonb["role"] != "arbiter"
        ],
        "arbiter_report_hashes": [
            a.content_hash
            for a in items
            if a.schema_id == "compiler.prose-judge-report.v1"
            and a.content_jsonb["role"] == "arbiter"
        ],
        "consensus_report_hashes": hashes("prose-consensus-report"),
        "quality_report_hashes": hashes("prose-quality-report"),
        "accepted_render_hash": next(
            (a.content_hash for a in renders if a.content_jsonb["stage"] == "accepted"), None
        ),
        "rewrite_count": sum(a.content_jsonb["stage"].startswith("rewrite_") for a in renders),
        "failure_reason": reason,
        **scene_usage(session, task_id, scene["scene_id"], recovered),
    }


def build_prose_manifest(
    runtime: dict[str, Any],
    source: dict[str, Any],
    scenes: list[dict[str, Any]],
    ordered: list[dict[str, Any]],
    status: str,
    reason: str | None,
    candidate_hash: str | None = None,
) -> dict[str, Any]:
    prompts = runtime["prompts"]
    components = runtime["components"]
    data = {
        "schema_id": "compiler.compile-manifest.v1",
        "source": source,
        "components": {
            "checklist_policy_hash": PROSE_CHECKLIST_POLICY_HASH,
            "council_policy_hash": runtime["semantic_policy_hash"],
            "writer_component_hash": components["writer"],
            "rewrite_component_hash": components["rewrite"],
            "quality_critic_component_hash": components["quality"],
            "polisher_component_hash": components["polisher"],
            **{
                f"{role}_component_hash": canonical_json_sha256(
                    {
                        "judge": components["judge"],
                        "prompt": prompts[f"prose_{role}"],
                        "model": runtime["generation_model"],
                    }
                )
                for role in (
                    "fidelity_judge",
                    "adversarial_judge",
                    "coherence_judge",
                    "arbiter",
                )
            },
        },
        "runtime": runtime,
        "scenes": scenes,
        "shadow_status": status,
        "incomplete_reason": reason,
        "novel_candidate_hash": candidate_hash,
        "not_run_scene_ids": [
            s["scene_id"] for s in ordered if s["scene_id"] not in {r["scene_id"] for r in scenes}
        ],
    }
    CompileManifest.model_validate(data)
    return data


def finalize_prose_cancellation(
    session: Session, task: TaskRun, attempt: TaskAttempt | None, now: datetime
) -> None:
    """Called only inside the existing locked cancellation transaction, including lease expiry."""
    if not task.input_jsonb.get("prose_renderer_shadow") or attempt is None:
        return
    run = session.scalar(select(CompileRun).where(CompileRun.task_run_id == task.id))
    if run is None:
        return
    artifacts = list(
        session.scalars(select(CompileArtifact).where(CompileArtifact.compile_run_id == run.id))
    )
    if any(a.artifact_key == "compiler.compile_manifest" for a in artifacts):
        return
    plan = next((a for a in artifacts if a.schema_id == "compiler.scene-plan.v2"), None)
    if plan is None:
        return
    reason = "compiler_prose_cancelled"
    steps = list(
        session.scalars(
            select(AgentStepRun).where(
                AgentStepRun.task_run_id == task.id, AgentStepRun.component_id.like("prose_%")
            )
        )
    )
    for step in steps:
        if step.status != "running":
            continue
        for call in session.scalars(
            select(AgentModelCall).where(
                AgentModelCall.agent_step_run_id == step.id, AgentModelCall.status == "running"
            )
        ):
            call.status, call.error_code, call.parse_status = "failed", reason, "cancelled"
            call.finished_at = now
        step.status, step.finished_at = "failed", now
        step.diagnostic_jsonb = {**step.diagnostic_jsonb, "error_code": reason}
    ordered = sorted(plan.content_jsonb["scenes"], key=lambda s: s["discourse_order"])
    source = {
        "scene_plan_hash": plan.content_hash,
        "profile_hash": task.input_jsonb["profile"]["content_hash"],
        "narrative_ir_hash": next(
            (a.content_hash for a in artifacts if a.artifact_kind == "narrative_ir"), None
        ),
    }
    scenes: list[dict[str, Any]] = []
    for scene in ordered:
        sid = scene["scene_id"]
        accepted = next(
            (a for a in artifacts if a.artifact_key == f"compiler.scene_render.{sid}.accepted"),
            None,
        )
        if accepted is None and not any(s.diagnostic_jsonb.get("scene_id") == sid for s in steps):
            continue
        state = "inconclusive_infrastructure"
        if accepted is not None:
            polished = any(
                a.content_hash == accepted.content_jsonb["previous_render_hash"]
                and a.content_jsonb.get("stage") == "polished"
                for a in artifacts
            )
            state = "finalized_polished" if polished else "finalized_original"
        scenes.append(
            project_prose_scene(
                session, run.id, task.id, scene, state, reason if accepted is None else None, []
            )
        )
    data = build_prose_manifest(
        task.input_jsonb["prose_runtime"],
        source,
        scenes,
        ordered,
        "inconclusive_infrastructure",
        reason,
    )
    digest = canonical_json_sha256(data)
    execution_no = (
        session.scalar(
            select(func.coalesce(func.max(AgentStepRun.execution_no), 0)).where(
                AgentStepRun.task_attempt_id == attempt.id,
                AgentStepRun.component_id == "prose_manifest",
            )
        )
        or 0
    ) + 1
    step = AgentStepRun(
        project_id=task.project_id,
        task_run_id=task.id,
        task_attempt_id=attempt.id,
        component_id="prose_manifest",
        execution_no=execution_no,
        status="succeeded",
        input_hash=task.input_hash,
        upstream_hashes_jsonb={"scene_plan": plan.content_hash},
        output_hash=digest,
        ir_schema_id=data["schema_id"],
        component_version=task.input_jsonb["prose_runtime"]["version"],
        diagnostic_jsonb={"reason": reason},
        usage_jsonb={},
        started_at=now,
        finished_at=now,
    )
    session.add(step)
    session.flush()
    session.add(
        CompileArtifact(
            project_id=run.project_id,
            casefile_id=run.casefile_id,
            compile_run_id=run.id,
            task_run_id=task.id,
            agent_step_run_id=step.id,
            artifact_kind="compile_manifest",
            artifact_key="compiler.compile_manifest",
            schema_id=data["schema_id"],
            content_hash=digest,
            content_jsonb=data,
        )
    )
