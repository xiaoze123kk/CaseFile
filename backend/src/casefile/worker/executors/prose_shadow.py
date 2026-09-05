"""Serial N4.5 Shadow orchestration over frozen N4.4 artifacts."""

from __future__ import annotations

from typing import Any

from casefile_contracts import NovelCandidate
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from casefile.agent_runtime.credentials import decrypt_api_key
from casefile.agent_runtime.prose_polish_supervisor import execute_prose_polish_supervisor
from casefile.agent_runtime.prose_rewrite_supervisor import execute_bounded_prose_rewrite
from casefile.agent_runtime.prose_runtime import prose_runtime_binding
from casefile.agent_runtime.prose_writer import execute_prose_writer
from casefile.data_postgres.models import CompileArtifact, TaskRun, UserProviderSetting
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    build_prose_judge_checklist,
    canonical_json_sha256,
)
from casefile.worker.executors.prose_providers import DurableProseProvider, ProseProviders
from casefile.worker.executors.prose_store import ProseLeaseLost, ProseResultUnknown, ProseStore
from casefile.worker.failures import TaskCancellationRequested


class ProseShadowExecutor:
    def __init__(self, store: ProseStore, providers: ProseProviders) -> None:
        self.store = store
        self.provider = DurableProseProvider(providers, store)
        self.scenes: list[dict[str, Any]] = []
        self.ordered: list[dict[str, Any]] = []
        self.source: dict[str, Any] = {
            "scene_plan_hash": None,
            "narrative_ir_hash": None,
            "profile_hash": None,
        }

    def execute(self, manifest: dict[str, Any]) -> CompileArtifact:
        store = self.store
        store.boundary()
        with store.factory() as session:
            existing = session.scalar(
                select(CompileArtifact).where(
                    CompileArtifact.compile_run_id == store.run.id,
                    CompileArtifact.artifact_key == "compiler.compile_manifest",
                )
            )
            if existing is not None:
                return existing
        try:
            if (
                not manifest.get("prose_renderer_shadow")
                or store.run.compile_mode != "preview"
                or manifest.get("prose_runtime")
                != prose_runtime_binding(
                    manifest["profile"]["frozen_payload"]["structure"]["target_scenes"]
                )
            ):
                raise CompilerContractError("compiler_prose_runtime_binding_mismatch")
            profile = manifest["profile"]["frozen_payload"]
            self.source["profile_hash"] = canonical_json_sha256(profile)
            with store.factory() as session:
                task = session.get(TaskRun, store.run.task_run_id)
                setting = (
                    session.get(UserProviderSetting, task.provider_setting_id) if task else None
                )
                if (
                    task is None
                    or task.provider != "deepseek"
                    or setting is None
                    or setting.provider != task.provider
                    or setting.user_id != task.actor_user_id
                    or setting.config_version != task.provider_config_version
                    or setting.secret_ciphertext is None
                    or setting.secret_nonce is None
                    or setting.key_version is None
                    or setting.credential_status == "deleted"
                ):
                    raise CompilerContractError("compiler_prose_provider_binding_mismatch")
                api_key = decrypt_api_key(
                    setting.secret_ciphertext,
                    setting.secret_nonce,
                    user_id=setting.user_id,
                    provider=setting.provider,
                    key_version=setting.key_version,
                )
                artifacts = {
                    a.artifact_key: a
                    for a in session.scalars(
                        select(CompileArtifact).where(
                            CompileArtifact.compile_run_id == store.run.id
                        )
                    )
                }
            plan_artifact = artifacts.get("compiler.scene_plan")
            ir_artifact = artifacts.get("compiler.narrative_ir")
            if plan_artifact is None or ir_artifact is None:
                raise CompilerContractError("compiler_prose_upstream_missing")
            for artifact in (plan_artifact, ir_artifact):
                if canonical_json_sha256(artifact.content_jsonb) != artifact.content_hash:
                    raise CompilerContractError("compiler_prose_upstream_hash_mismatch")
            plan, narrative = plan_artifact.content_jsonb, ir_artifact.content_jsonb
            self.source.update(
                scene_plan_hash=plan_artifact.content_hash,
                narrative_ir_hash=ir_artifact.content_hash,
            )
            if plan.get("schema_id") != "compiler.scene-plan.v2":
                raise CompilerContractError("compiler_prose_scene_plan_v2_required")
            self.ordered = sorted(plan["scenes"], key=lambda s: s["discourse_order"])
            # Validate the full upstream closure before the first prose request.
            build_prose_judge_checklist(
                scene_plan=plan,
                narrative_ir=narrative,
                profile=profile,
                scene_id=self.ordered[0]["scene_id"],
            )
        except (TaskCancellationRequested, ProseLeaseLost, SQLAlchemyError):
            raise
        except Exception as error:
            precondition_reason = (
                str(error)
                if isinstance(error, CompilerContractError)
                else "compiler_prose_precondition_invalid"
            )
            return self._manifest("blocked_precondition", precondition_reason)

        previous: dict[str, Any] | None = None
        accepted: list[dict[str, Any]] = []
        for scene in self.ordered:
            store.scene_id = scene["scene_id"]
            store.phase = "semantic_0"
            store.recovered_hashes = []
            try:
                state, previous, reason = self._scene(plan, narrative, profile, previous, api_key)
            except (ProseLeaseLost, TaskCancellationRequested):
                raise
            except Exception as error:
                # Persistence errors must not be converted to a successful TaskRun.
                if isinstance(error, SQLAlchemyError):
                    raise
                state, previous = "inconclusive_infrastructure", None
                reason = (
                    str(error)
                    if isinstance(error, ProseResultUnknown)
                    else "compiler_prose_execution_failed"
                )
                store.finish_steps(error_code=reason)
            self.scenes.append(self._scene_manifest(scene, state, reason))
            if previous is None:
                return self._manifest(state, reason or "compiler_prose_semantic_rejected")
            accepted.append(previous)
        candidate = {
            "schema_id": "compiler.novel-candidate.v1",
            "scene_plan_hash": self.source["scene_plan_hash"],
            "profile_hash": self.source["profile_hash"],
            "accepted_scenes": [
                {
                    "scene_id": r["scene_id"],
                    "scene_ordinal": r["scene_ordinal"],
                    "render_hash": canonical_json_sha256(r),
                }
                for r in accepted
            ],
            "merged_text": "\n\n".join(b["text"] for r in accepted for b in r["blocks"]),
            "scene_count": len(accepted),
            "character_count": sum(r["character_count"] for r in accepted),
        }
        NovelCandidate.model_validate(candidate)
        artifact = store.artifact(
            "novel_candidate", "compiler.novel_candidate", candidate, "prose_manifest"
        )
        return self._manifest("succeeded", None, candidate_hash=artifact.content_hash)

    def _scene(
        self,
        plan: dict[str, Any],
        narrative: dict[str, Any],
        profile: dict[str, Any],
        previous: dict[str, Any] | None,
        api_key: str,
    ) -> tuple[str, dict[str, Any] | None, str | None]:
        store = self.store
        checklist = build_prose_judge_checklist(
            scene_plan=plan,
            narrative_ir=narrative,
            profile=profile,
            scene_id=store.scene_id,
            previous_scene_render=previous,
        )
        store.artifact(
            "scene_context",
            f"compiler.scene_context.{store.scene_id}",
            checklist,
            "prose_checklist",
        )
        writer = execute_prose_writer(
            self.provider,
            scene_plan=plan,
            narrative_ir=narrative,
            profile=profile,
            checklist=checklist,
            previous_scene_render=previous,
            model_id="deepseek-v4-pro",
            api_key=api_key,
            remaining_scene_call_budget=23,
        )
        self.observe("writer", writer)
        if writer.status != "completed" or writer.render is None:
            return "inconclusive_infrastructure", None, writer.error_code
        rewrite = execute_bounded_prose_rewrite(
            self.provider,
            self.provider,
            scene_plan=plan,
            narrative_ir=narrative,
            profile=profile,
            checklist=checklist,
            previous_scene_render=previous,
            initial_render=writer.render,
            model_id="deepseek-v4-pro",
            api_key=api_key,
            remaining_scene_call_budget=22,
            observe=self.observe,
        )
        if rewrite.status != "semantic_accepted" or rewrite.final_render is None:
            return (
                (
                    "semantic_rejected"
                    if rewrite.status == "semantic_rejected"
                    else "inconclusive_infrastructure"
                ),
                None,
                rewrite.error_code,
            )
        consensus = rewrite.rounds[-1].council.consensus
        assert consensus is not None
        polish = execute_prose_polish_supervisor(
            self.provider,
            self.provider,
            self.provider,
            checklist=checklist,
            profile=profile,
            original_render=rewrite.final_render,
            semantic_consensus=consensus,
            quality_model_id="deepseek-v4-flash",
            generation_model_id="deepseek-v4-pro",
            api_key=api_key,
            observe=self.observe,
        )
        if polish.accepted_render is None:
            return "inconclusive_infrastructure", None, polish.error_code
        store.artifact(
            "scene_render",
            f"compiler.scene_render.{store.scene_id}.accepted",
            polish.accepted_render,
            "prose_manifest",
        )
        return polish.status, polish.accepted_render, None

    def observe(self, name: str, execution: Any) -> None:
        store = self.store
        store.boundary()
        error = execution.error_code if execution.status != "completed" else None
        # An invalid Arbiter response is a protocol error, not a legal uncertain verdict.
        if name in {"semantic", "preservation"} and execution.error_code:
            error = execution.error_code
        outputs: dict[str, str] = {}
        rendered = getattr(execution, "render", None)
        single_report = getattr(execution, "report", None)
        single_call = getattr(execution, "call", None)
        if single_call is not None and (rendered or single_report):
            outputs[single_call.request_fingerprint] = canonical_json_sha256(
                rendered or single_report
            )
        if name in {"semantic", "preservation"}:
            for report in (*execution.judge_reports, execution.arbiter_report):
                if report is not None:
                    call = next(c for c in execution.calls if c.role == report["role"])
                    outputs[call.request_fingerprint] = canonical_json_sha256(report)
        if name == "pairwise":
            outputs.update(
                {
                    call.request_fingerprint: canonical_json_sha256(report)
                    for report, call in zip(execution.reports, execution.calls, strict=False)
                }
            )
        store.finish_steps(error_code=error, outputs=outputs)
        render = getattr(execution, "render", None)
        if render is not None:
            call = execution.call
            store.artifact(
                "scene_render",
                f"compiler.scene_render.{store.scene_id}.{render['stage']}",
                render,
                {"writer": "prose_writer", "rewrite": "prose_rewrite", "polish": "prose_polisher"}[
                    name
                ],
                source_step=self.provider.steps.get(call.request_fingerprint) if call else None,
            )
            store.phase = f"semantic_{render['round']}" if name != "polish" else "preservation"
        if name in {"semantic", "preservation"}:
            prefix = "preservation" if name == "preservation" else store.phase
            for report in (*execution.judge_reports, execution.arbiter_report):
                if report is not None:
                    call = next((c for c in execution.calls if c.role == report["role"]), None)
                    store.artifact(
                        "validation_report",
                        f"compiler.validation_report.{store.scene_id}.{prefix}.{report['role']}",
                        report,
                        f"prose_{report['role']}_judge"
                        if report["role"] != "arbiter"
                        else "prose_arbiter",
                        source_step=self.provider.steps.get(call.request_fingerprint)
                        if call
                        else None,
                    )
            if execution.consensus is not None:
                store.artifact(
                    "validation_report",
                    f"compiler.validation_report.{store.scene_id}.{prefix}.consensus",
                    execution.consensus,
                    "prose_manifest",
                )
            if name == "semantic":
                store.phase = (
                    "rewrite"
                    if execution.consensus and execution.consensus["scene_verdict"] != "pass"
                    else "findings"
                )
            else:
                store.phase = "pairwise"
        if name == "findings" and execution.report is not None:
            store.artifact(
                "validation_report",
                f"compiler.validation_report.{store.scene_id}.quality.findings",
                execution.report,
                "prose_quality_critic",
                source_step=self.provider.steps.get(execution.call.request_fingerprint),
            )
            store.phase = "polished"
        if name == "pairwise":
            for report, call in zip(execution.reports, execution.calls, strict=False):
                position = (
                    "original_first"
                    if report["position_mapping"]["a"] == "original"
                    else "polished_first"
                )
                store.artifact(
                    "validation_report",
                    f"compiler.validation_report.{store.scene_id}.quality.pairwise.{position}",
                    report,
                    "prose_quality_critic",
                    source_step=self.provider.steps.get(call.request_fingerprint),
                )
        if error and name in {"semantic", "preservation"}:
            raise ProseResultUnknown(error)

    def _scene_manifest(
        self, scene: dict[str, Any], state: str, reason: str | None
    ) -> dict[str, Any]:
        from casefile.application.compiler.prose_projection import project_prose_scene

        with self.store.factory() as session:
            return project_prose_scene(
                session,
                self.store.run.id,
                self.store.run.task_run_id,
                scene,
                state,
                reason,
                self.store.recovered_hashes,
            )

    def _manifest(
        self,
        status: str,
        reason: str | None,
        *,
        candidate_hash: str | None = None,
        allow_cancel: bool = False,
    ) -> CompileArtifact:
        from casefile.application.compiler.prose_projection import build_prose_manifest

        data = build_prose_manifest(
            self.store.runtime,
            self.source,
            self.scenes,
            self.ordered,
            status,
            reason,
            candidate_hash,
        )
        return self.store.artifact(
            "compile_manifest",
            "compiler.compile_manifest",
            data,
            "prose_manifest",
            allow_cancel=allow_cancel,
        )

    def cancel(self) -> None:
        self.store.finish_steps(error_code="compiler_prose_cancelled", allow_cancel=True)
        if self.store.scene_id and self.store.scene_id not in {s["scene_id"] for s in self.scenes}:
            scene = next(s for s in self.ordered if s["scene_id"] == self.store.scene_id)
            self.scenes.append(
                self._scene_manifest(
                    scene, "inconclusive_infrastructure", "compiler_prose_cancelled"
                )
            )
        self._manifest("inconclusive_infrastructure", "compiler_prose_cancelled", allow_cancel=True)
