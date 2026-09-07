"""Transactions for versioned Compiler Profiles and durable CompileRuns."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

from casefile.agent_runtime.constraint_first_story_planner import (
    CONSTRAINT_FIRST_PIPELINE_VERSION,
    CONSTRAINT_FIRST_PROMPT_BUNDLE_VERSION,
)
from casefile.agent_runtime.prose_runtime import PROSE_RUNTIME_VERSION, prose_runtime_binding
from casefile.agent_runtime.scene_compiler import (
    SCENE_COMPILER_PIPELINE_VERSION,
    SCENE_COMPILER_PROMPT_BUNDLE_VERSION,
)
from casefile.agent_runtime.story_planner import STORY_PLANNER_TOOLSET_VERSION
from casefile.application.casefile_v1 import build_casefile_document, casefile_content_hash
from casefile.application.compiler.constants import (
    INPUT_MANIFEST_SCHEMA_ID,
    NARRATIVE_COMPILER_VERSION,
    NO_PROMPT_VERSION,
    NO_TOOLSET_VERSION,
)
from casefile.application.errors import ApplicationError, not_found, revision_conflict
from casefile.application.task_events import append_task_event
from casefile.application.workflow_views import task_view
from casefile.data_postgres.compiler_repository import CompilerRepository
from casefile.data_postgres.models import (
    AgentModelCall,
    AuditEvent,
    CanonVersion,
    CompilerProfile,
    CompilerProfileVersion,
    CompileRun,
    DraftSnapshot,
    TaskRun,
    UserProviderSetting,
)
from casefile.data_postgres.repositories import ProjectRepository, SnapshotRepository
from casefile.domain.narrative_compiler import (
    SCENE_COMPILER_BATCH_SIZE,
    CompilerContractError,
    canonical_json_sha256,
    validate_compile_input_manifest,
    validate_novel_profile_v2,
)
from casefile_contracts import (
    CanonBinding,
    CompileInputManifest,
    CompileMode,
    CompilerProfileBinding,
    ExposureBinding,
    NovelProfile,
    NovelProfileV2,
    SnapshotBinding,
)


class CompilerService:
    """Own Compiler application transactions and public read models."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.snapshots = SnapshotRepository(session)
        self.compiler = CompilerRepository(session)

    def resume_run(
        self,
        actor_user_id: int,
        project_id: int,
        run_id: int,
        *,
        expected_draft_id: int,
        expected_draft_revision: int,
    ) -> dict[str, Any]:
        """Resume the same frozen build once; successful exact inputs remain reusable."""
        from casefile.agent_runtime.prompt_repository import load_prompt
        from casefile.agent_runtime.scene_compiler import SCENE_SEMANTIC_FILL_PROMPT_VERSION

        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id, lock=True)
            if owned is None:
                raise not_found("Project")
            run = self.session.get(CompileRun, run_id)
            if run is None or run.project_id != project_id:
                raise not_found("CompileRun")
            task = self.session.scalar(
                select(TaskRun).where(TaskRun.id == run.task_run_id).with_for_update()
            )
            draft = owned.draft
            if (
                draft is None
                or draft.id != expected_draft_id
                or draft.project_id != project_id
                or run.draft_id != draft.id
                or draft.revision != expected_draft_revision
                or task is None
                or task.input_draft_revision != expected_draft_revision
            ):
                raise ApplicationError(
                    "compiler_resume_stale", "工作稿已变化，请重新规划。", status_code=409
                )
            artifacts = self.compiler.list_artifacts(run.id)
            prose_manifest = max(
                (a for a in artifacts if a.artifact_key.startswith("compiler.compile_manifest")),
                key=lambda a: a.id,
                default=None,
            )
            prose_resume = bool(
                run.prose_renderer_shadow
                and prose_manifest is not None
                and prose_manifest.content_jsonb["shadow_status"] != "succeeded"
            )
            if (
                task.status != "failed" and not (prose_resume and task.status == "succeeded")
            ) or task.attempt_count >= 2:
                raise ApplicationError(
                    "compiler_resume_unavailable", "仅失败任务可继续一次。", status_code=409
                )
            setting = self.session.get(UserProviderSetting, task.provider_setting_id)
            if (
                setting is None
                or setting.credential_status == "deleted"
                or setting.config_version != task.provider_config_version
            ):
                raise ApplicationError(
                    "compiler_resume_provider_changed",
                    "模型配置已变化，请重新规划。",
                    status_code=409,
                )
            prompt = load_prompt("scene_compiler_semantic_fill", SCENE_SEMANTIC_FILL_PROMPT_VERSION)
            calls = list(
                self.session.scalars(
                    select(AgentModelCall).where(
                        AgentModelCall.task_run_id == task.id,
                        AgentModelCall.prompt_component_id.like("scene_semantic_fill.%"),
                    )
                )
            )
            if prose_resume:
                frozen = next(
                    (a.content_jsonb for a in artifacts if a.schema_id == INPUT_MANIFEST_SCHEMA_ID),
                    {},
                )
                runtime = frozen.get("prose_runtime", {})
                if runtime != prose_runtime_binding(runtime.get("scene_count")):
                    raise ApplicationError(
                        "compiler_resume_version_changed",
                        "生成协议已更新，请重新规划。",
                        status_code=409,
                    )
                assert prose_manifest is not None
                if (
                    prose_manifest.content_jsonb.get("incomplete_reason")
                    == "compiler_prose_local_plan_conflict"
                ):
                    raise ApplicationError(
                        "compiler_resume_plan_conflict",
                        "场景规划存在冲突，请先调整方案。",
                        status_code=409,
                    )
            elif (
                not calls
                or any(call.prompt_sha256 != prompt.system_prompt_sha256 for call in calls)
                or int(task.budget_jsonb.get("max_repairs", 0)) != 1
            ):
                raise ApplicationError(
                    "compiler_resume_version_changed",
                    "生成协议已更新，请重新规划。",
                    status_code=409,
                )
            append_task_event(
                self.session,
                task,
                "task.resumed",
                "queued",
                {
                    "previous_error_code": task.error_code,
                    "previous_attempt_count": task.attempt_count,
                },
            )
            task.status = "queued"
            task.stage = "queued"
            task.completed_at = None
            task.error_code = None
            task.error_details_jsonb = {}
            task.leased_by = None
            task.lease_expires_at = None
            task.cancel_requested_at = None
            return self._run_view(run, task)

    def create_profile(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        profile_key: str,
        name: str,
        schema_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id)
            if owned is None:
                raise not_found("Project")
            profile = CompilerProfile(
                project_id=project_id,
                profile_key=profile_key,
                name=name.strip(),
                current_version_id=None,
                created_by_user_id=actor_user_id,
            )
            self.session.add(profile)
            self.session.flush()
            version = CompilerProfileVersion(
                project_id=project_id,
                compiler_profile_id=profile.id,
                version_no=1,
                schema_id=schema_id,
                payload_jsonb=payload,
                content_hash=canonical_json_sha256(payload),
                created_by_user_id=actor_user_id,
            )
            self.session.add(version)
            self.session.flush()
            profile.current_version_id = version.id
            self.session.flush()
            return self._profile_view(profile, [version])

    def append_profile_version(
        self,
        actor_user_id: int,
        project_id: int,
        profile_id: int,
        *,
        expected_current_version_id: int,
        schema_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id)
            if owned is None:
                raise not_found("Project")
            profile = self.compiler.get_profile(project_id, profile_id, lock=True)
            if profile is None:
                raise not_found("CompilerProfile")
            if profile.current_version_id != expected_current_version_id:
                raise ApplicationError(
                    "compiler_profile_version_conflict",
                    "编译配置已被更新，请刷新后重试。",
                    status_code=409,
                    details={"current_version_id": profile.current_version_id},
                )
            versions = self.compiler.list_profile_versions(profile.id)
            version = CompilerProfileVersion(
                project_id=project_id,
                compiler_profile_id=profile.id,
                version_no=versions[-1].version_no + 1,
                schema_id=schema_id,
                payload_jsonb=payload,
                content_hash=canonical_json_sha256(payload),
                created_by_user_id=actor_user_id,
            )
            self.session.add(version)
            self.session.flush()
            profile.current_version_id = version.id
            self.session.flush()
            versions.append(version)
            return self._profile_view(profile, versions)

    def list_profiles(self, actor_user_id: int, project_id: int) -> list[dict[str, Any]]:
        with self.session.begin():
            if self.projects.get_owned(actor_user_id, project_id) is None:
                raise not_found("Project")
            return [
                self._profile_view(profile, self.compiler.list_profile_versions(profile.id))
                for profile in self.compiler.list_profiles(project_id)
            ]

    def get_profile(self, actor_user_id: int, project_id: int, profile_id: int) -> dict[str, Any]:
        with self.session.begin():
            if self.projects.get_owned(actor_user_id, project_id) is None:
                raise not_found("Project")
            profile = self.compiler.get_profile(project_id, profile_id)
            if profile is None:
                raise not_found("CompilerProfile")
            return self._profile_view(profile, self.compiler.list_profile_versions(profile.id))

    def create_run(
        self,
        actor_user_id: int,
        project_id: int,
        *,
        mode: Literal["preview", "canonical"],
        expected_draft_id: int,
        expected_draft_revision: int,
        canon_version_id: int | None,
        exposure_plan_revision_id: int | None,
        compiler_profile_version_id: int,
        planner_provider: str | None = None,
        scene_compiler_shadow: bool = False,
        prose_renderer_shadow: bool = False,
        approved_plan_run_id: int | None = None,
    ) -> dict[str, Any]:
        with self.session.begin():
            owned = self.projects.get_owned(actor_user_id, project_id, lock=True)
            if owned is None:
                raise not_found("Project")
            if owned.draft.id != expected_draft_id:
                raise ApplicationError(
                    "current_draft_changed",
                    "当前工作稿已切换，请刷新后重试。",
                    status_code=409,
                    details={"current_draft_id": owned.draft.id},
                )
            if owned.draft.revision != expected_draft_revision:
                raise revision_conflict(
                    expected=owned.draft.revision, received=expected_draft_revision
                )

            document = build_casefile_document(self.session, owned)
            document_hash = casefile_content_hash(document)
            snapshot = self.snapshots.find_revision(owned.draft.id, owned.draft.revision)
            if snapshot is None:
                snapshot = self.snapshots.create(
                    owned,
                    document=document,
                    content_hash=document_hash,
                    actor_user_id=actor_user_id,
                )
            elif (
                snapshot.snapshot_revision != owned.draft.revision
                or snapshot.schema_version != owned.draft.schema_version
                or snapshot.snapshot_jsonb != document
                or snapshot.content_hash != document_hash
            ):
                raise ApplicationError(
                    "compiler_snapshot_projection_mismatch",
                    "已有快照与当前工作稿投影不一致，编译已停止。",
                    status_code=409,
                )

            canon = self._resolve_canon(
                project_id=project_id,
                casefile_id=owned.casefile.id,
                snapshot=snapshot,
                mode=mode,
                canon_version_id=canon_version_id,
            )
            profile = self.compiler.get_profile_version(project_id, compiler_profile_version_id)
            if profile is None:
                raise not_found("CompilerProfileVersion")
            if prose_renderer_shadow:
                if mode != "preview" or planner_provider != "deepseek":
                    raise ApplicationError(
                        "compiler_prose_shadow_mode_provider_invalid",
                        "正文影子运行仅支持 preview 和 DeepSeek。",
                        status_code=422,
                    )
                if profile.schema_id != "compiler.novel-profile.v2":
                    raise ApplicationError(
                        "compiler_prose_shadow_profile_v2_required",
                        "正文影子运行需要 Profile v2。",
                        status_code=422,
                    )
                try:
                    validate_novel_profile_v2(profile.payload_jsonb)
                except CompilerContractError as error:
                    raise ApplicationError(
                        error.reason_code, "正文 Profile v2 配置无效。", status_code=422
                    ) from error
                scene_compiler_shadow = True
            setting: UserProviderSetting | None = None
            novel_profile: NovelProfile | NovelProfileV2 | None = None
            if planner_provider is not None:
                setting = self.session.scalar(
                    select(UserProviderSetting)
                    .where(
                        UserProviderSetting.user_id == actor_user_id,
                        UserProviderSetting.provider == planner_provider,
                    )
                    .with_for_update()
                )
                if setting is None or setting.credential_status == "deleted":
                    raise ApplicationError(
                        "provider_setting_required",
                        f"开始 Story Planner 前请先配置 {planner_provider} API 密钥。",
                        status_code=409,
                        details={"provider": planner_provider},
                    )
                try:
                    novel_profile = (
                        NovelProfileV2
                        if profile.schema_id == "compiler.novel-profile.v2"
                        else NovelProfile
                    ).model_validate(profile.payload_jsonb)
                except ValidationError as error:
                    raise ApplicationError(
                        "compiler_novel_profile_invalid",
                        "Story Planner 配置不满足 Novel Profile 契约。",
                        status_code=422,
                    ) from error
            exposure = self._exposure_binding(
                project_id=project_id,
                casefile_id=owned.casefile.id,
                draft_id=owned.draft.id,
                revision_id=exposure_plan_revision_id,
            )
            if setting is not None and mode == "canonical" and exposure is None:
                raise ApplicationError(
                    "compiler_story_planner_exposure_required",
                    "正式 Story Plan 必须绑定非空 Exposure revision。",
                    status_code=422,
                )
            if setting is not None and novel_profile is not None:
                exposure_policy = novel_profile.exposure_policy.value
                if exposure is None and exposure_policy != "planner_default":
                    raise ApplicationError(
                        "compiler_story_planner_exposure_policy_invalid",
                        "未绑定 Exposure 时必须显式使用 planner_default。",
                        status_code=422,
                    )
                if exposure is not None and exposure_policy != "bound_plan":
                    raise ApplicationError(
                        "compiler_story_planner_bound_exposure_policy_invalid",
                        "绑定 Exposure 时 Profile 必须使用 bound_plan。",
                        status_code=422,
                    )
            if scene_compiler_shadow and setting is None:
                raise ApplicationError(
                    "compiler_scene_shadow_provider_required",
                    "Scene Compiler 影子运行必须显式绑定 Planner Provider。",
                    status_code=422,
                )
            scene_batch_count = (
                0
                if novel_profile is None
                else _scene_batch_count(novel_profile.model_dump(mode="json"))
            )
            manifest = CompileInputManifest(
                target="novel",
                mode=CompileMode(mode),
                source_snapshot=SnapshotBinding(
                    snapshot_id=snapshot.id,
                    draft_id=snapshot.draft_id,
                    snapshot_revision=snapshot.snapshot_revision,
                    schema_version=snapshot.schema_version,
                    content_hash=snapshot.content_hash,
                ),
                source_canon=(
                    None
                    if canon is None
                    else CanonBinding(
                        canon_version_id=canon.id,
                        source_snapshot_id=canon.source_snapshot_id,
                        version_no=canon.version_no,
                        content_hash=canon.content_hash,
                    )
                ),
                exposure=exposure,
                profile=CompilerProfileBinding(
                    profile_key=self._profile_key(profile),
                    profile_schema_id=profile.schema_id,
                    profile_version=profile.version_no,
                    frozen_payload=profile.payload_jsonb,
                    content_hash=profile.content_hash,
                ),
                compiler_version=NARRATIVE_COMPILER_VERSION,
            )
            try:
                validate_compile_input_manifest(manifest)
            except CompilerContractError as error:
                raise ApplicationError(
                    error.reason_code,
                    "冻结编译输入不满足约束。",
                    status_code=422,
                ) from error
            manifest_json = manifest.model_dump(mode="json", exclude_unset=True)
            if approved_plan_run_id is not None:
                if not prose_renderer_shadow:
                    raise ApplicationError(
                        "compiler_approval_mode_invalid",
                        "确认方案后才能开始正文编译。",
                        status_code=422,
                    )
                manifest_json["approved_novel_plan"] = self._approved_plan_binding(
                    project_id,
                    approved_plan_run_id,
                    manifest_json,
                )
            if prose_renderer_shadow:
                manifest_json["prose_renderer_shadow"] = True
                manifest_json["prose_runtime"] = prose_runtime_binding(
                    profile.payload_jsonb["structure"]["target_scenes"]
                )
            input_hash = canonical_json_sha256(manifest_json)
            task = TaskRun(
                project_id=project_id,
                casefile_id=owned.casefile.id,
                draft_id=owned.draft.id,
                brief_version_id=None,
                input_source_record_id=None,
                input_brief_revision=None,
                brief_intake_id=None,
                input_brief_intake_revision=None,
                base_brief_intake_candidate_id=None,
                agent_thread_id=None,
                input_message_id=None,
                output_message_id=None,
                input_hash=input_hash,
                input_jsonb=manifest_json,
                actor_user_id=actor_user_id,
                provider_setting_id=None if setting is None else setting.id,
                task_type="novel_compile",
                status="queued",
                stage="queued",
                input_draft_revision=owned.draft.revision,
                provider=None if setting is None else setting.provider,
                model_id=(
                    "deepseek-v4-pro"
                    if prose_renderer_shadow
                    or (scene_compiler_shadow and planner_provider == "deepseek")
                    else None
                    if setting is None
                    else setting.model_id
                ),
                provider_config_version=None if setting is None else setting.config_version,
                schema_version=INPUT_MANIFEST_SCHEMA_ID,
                agent_version=(
                    NARRATIVE_COMPILER_VERSION
                    if setting is None
                    else (
                        SCENE_COMPILER_PIPELINE_VERSION
                        if scene_compiler_shadow
                        else CONSTRAINT_FIRST_PIPELINE_VERSION
                    )
                ),
                prompt_version=(
                    NO_PROMPT_VERSION
                    if setting is None
                    else (
                        SCENE_COMPILER_PROMPT_BUNDLE_VERSION
                        if scene_compiler_shadow
                        else CONSTRAINT_FIRST_PROMPT_BUNDLE_VERSION
                    )
                ),
                toolset_version=(
                    NO_TOOLSET_VERSION if setting is None else STORY_PLANNER_TOOLSET_VERSION
                ),
                budget_jsonb=(
                    {}
                    if setting is None
                    else {
                        **setting.default_budget_jsonb,
                        "max_turns": (2 + 2 * scene_batch_count if scene_compiler_shadow else 2),
                        "max_repairs": 1 if scene_compiler_shadow else 0,
                    }
                ),
                usage_jsonb={},
                attempt_count=0,
                error_details_jsonb={},
            )
            self.session.add(task)
            self.session.flush()
            run = CompileRun(
                project_id=project_id,
                casefile_id=owned.casefile.id,
                draft_id=owned.draft.id,
                task_run_id=task.id,
                target_kind="novel",
                prose_renderer_shadow=prose_renderer_shadow,
                compile_mode=mode,
                source_snapshot_id=snapshot.id,
                source_canon_version_id=None if canon is None else canon.id,
                exposure_plan_revision_id=exposure_plan_revision_id,
                compiler_profile_version_id=profile.id,
                compiler_version=NARRATIVE_COMPILER_VERSION,
                input_hash=input_hash,
                created_by_user_id=actor_user_id,
            )
            self.session.add(run)
            self.session.flush()
            append_task_event(
                self.session,
                task,
                "task.queued",
                "queued",
                {"task_type": "novel_compile", "compile_run_id": run.id},
            )
            self.session.add(
                AuditEvent(
                    project_id=project_id,
                    casefile_id=owned.casefile.id,
                    actor_kind="user",
                    actor_user_id=actor_user_id,
                    actor_ref=None,
                    action="compiler.run_created",
                    target_type="compile_run",
                    target_id=run.id,
                    trace_id=None,
                    details_jsonb={"task_run_id": task.id, "input_hash": input_hash},
                )
            )
            self.session.flush()
            return self._run_view(run, task)

    def _approved_plan_binding(
        self, project_id: int, source_run_id: int, manifest: dict[str, Any]
    ) -> dict[str, str]:
        source = self.compiler.get_run(project_id, source_run_id)
        task = self.session.get(TaskRun, source.task_run_id) if source else None
        if source is None or task is None or task.status != "succeeded":
            raise ApplicationError(
                "compiler_plan_not_ready", "方案尚未完成，暂不能生成正文。", status_code=409
            )
        if any(
            task.input_jsonb.get(key) != manifest.get(key)
            for key in (
                "source_snapshot",
                "profile",
                "exposure",
                "mode",
                "source_canon",
                "compiler_version",
            )
        ):
            raise ApplicationError(
                "compiler_plan_stale",
                "工作稿或编译设置已变化，请重新推荐方案后确认。",
                status_code=409,
            )
        artifact = next(
            (
                item
                for item in self.compiler.list_artifacts(source.id)
                if item.schema_id == "compiler.novel-plan.v1"
            ),
            None,
        )
        if (
            artifact is None
            or canonical_json_sha256(artifact.content_jsonb) != artifact.content_hash
        ):
            raise ApplicationError(
                "compiler_plan_not_ready", "方案内容不完整，请重新推荐。", status_code=409
            )
        return {
            "artifact_kind": "novel_plan",
            "artifact_key": artifact.artifact_key,
            "schema_id": artifact.schema_id,
            "content_hash": artifact.content_hash,
        }

    def list_runs(self, actor_user_id: int, project_id: int) -> list[dict[str, Any]]:
        with self.session.begin():
            if self.projects.get_owned(actor_user_id, project_id) is None:
                raise not_found("Project")
            return [
                self._run_view(run, self.session.get(TaskRun, run.task_run_id))
                for run in self.compiler.list_runs(project_id)
            ]

    def get_run(self, actor_user_id: int, project_id: int, run_id: int) -> dict[str, Any]:
        with self.session.begin():
            if self.projects.get_owned(actor_user_id, project_id) is None:
                raise not_found("Project")
            run = self.compiler.get_run(project_id, run_id)
            if run is None:
                raise not_found("CompileRun")
            task = self.session.get(TaskRun, run.task_run_id)
            if task is None:
                raise RuntimeError("CompileRun TaskRun is missing")
            return self._run_view(run, task)

    def get_artifact(
        self,
        actor_user_id: int,
        project_id: int,
        run_id: int,
        artifact_id: int,
    ) -> dict[str, Any]:
        """Return one immutable artifact after complete ownership scoping."""

        with self.session.begin():
            if self.projects.get_owned(actor_user_id, project_id) is None:
                raise not_found("Project")
            run = self.compiler.get_run(project_id, run_id)
            if run is None:
                raise not_found("CompileRun")
            artifact = self.compiler.get_artifact(project_id, run.id, artifact_id)
            if artifact is None:
                raise not_found("CompileArtifact")
            return {
                "artifact_id": artifact.id,
                "compile_run_id": artifact.compile_run_id,
                "artifact_kind": artifact.artifact_kind,
                "artifact_key": artifact.artifact_key,
                "schema_id": artifact.schema_id,
                "content_hash": artifact.content_hash,
                "agent_step_run_id": artifact.agent_step_run_id,
                "content": artifact.content_jsonb,
                "created_at": artifact.created_at.isoformat(),
            }

    def _resolve_canon(
        self,
        *,
        project_id: int,
        casefile_id: int,
        snapshot: DraftSnapshot,
        mode: str,
        canon_version_id: int | None,
    ) -> CanonVersion | None:
        if mode == "preview":
            if canon_version_id is not None:
                raise ApplicationError(
                    "compiler_manifest_preview_canon_forbidden",
                    "预览编译不能绑定正式版本。",
                    status_code=422,
                )
            return None
        if canon_version_id is None:
            raise ApplicationError(
                "compiler_manifest_canon_required",
                "正式编译必须选择 Canon 版本。",
                status_code=422,
            )
        canon = self.session.scalar(
            select(CanonVersion).where(
                CanonVersion.project_id == project_id,
                CanonVersion.casefile_id == casefile_id,
                CanonVersion.id == canon_version_id,
            )
        )
        if canon is None:
            raise not_found("CanonVersion")
        if canon.source_snapshot_id != snapshot.id or canon.content_hash != snapshot.content_hash:
            raise ApplicationError(
                "compiler_canon_binding_mismatch",
                "Canon 与本次冻结快照不一致。",
                status_code=422,
            )
        return canon

    def _exposure_binding(
        self,
        *,
        project_id: int,
        casefile_id: int,
        draft_id: int,
        revision_id: int | None,
    ) -> ExposureBinding | None:
        if revision_id is None:
            return None
        revision = self.compiler.get_exposure_revision(
            project_id=project_id,
            casefile_id=casefile_id,
            draft_id=draft_id,
            revision_id=revision_id,
        )
        if revision is None:
            raise not_found("ExposurePlanRevision")
        payload = self.compiler.project_exposure_revision_payload(revision.id)
        return ExposureBinding(
            draft_id=draft_id,
            plan_revision_id=revision.id,
            revision_no=revision.revision_no,
            frozen_payload=payload,
            content_hash=canonical_json_sha256(payload),
        )

    def _profile_key(self, version: CompilerProfileVersion) -> str:
        profile = self.compiler.get_profile(version.project_id, version.compiler_profile_id)
        if profile is None:
            raise RuntimeError("CompilerProfileVersion owner is missing")
        return profile.profile_key

    def _profile_view(
        self, profile: CompilerProfile, versions: list[CompilerProfileVersion]
    ) -> dict[str, Any]:
        return {
            "profile_id": profile.id,
            "project_id": profile.project_id,
            "profile_key": profile.profile_key,
            "name": profile.name,
            "current_version_id": profile.current_version_id,
            "versions": [
                {
                    "version_id": version.id,
                    "version_no": version.version_no,
                    "schema_id": version.schema_id,
                    "payload": version.payload_jsonb,
                    "content_hash": version.content_hash,
                    "created_at": version.created_at.isoformat(),
                }
                for version in versions
            ],
            "created_at": profile.created_at.isoformat(),
            "updated_at": profile.updated_at.isoformat(),
        }

    def _shadow_view(self, run: CompileRun, task: TaskRun) -> dict[str, Any]:
        if not run.prose_renderer_shadow:
            return {"prose_shadow": {"status": "disabled"}}
        artifacts = self.compiler.list_artifacts(run.id)
        main_ready = any(a.schema_id == "compiler.scene-plan.v2" for a in artifacts)
        manifest = max(
            (a for a in artifacts if a.artifact_key.startswith("compiler.compile_manifest")),
            key=lambda a: a.id,
            default=None,
        )
        status = (
            "running"
            if task.status in {"queued", "running", "cancelling"} and main_ready
            else manifest.content_jsonb["shadow_status"]
            if manifest
            else "inconclusive_infrastructure"
            if task.status in {"failed", "cancelled"}
            else "running"
            if main_ready
            else "pending"
        )
        return {
            "compilation": {"status": "succeeded" if main_ready else task.status},
            "prose_shadow": {
                "status": status,
                "manifest_artifact_id": manifest.id if manifest else None,
                "plan_issues": [
                    issue["required_plan_change"]
                    for artifact in artifacts
                    if artifact.schema_id == "compiler.prose-continuity-review.v1"
                    and artifact.content_jsonb.get("verdict") == "blocked"
                    for issue in artifact.content_jsonb["issues"]
                ],
                "resume_available": bool(
                    task.status in {"succeeded", "failed"}
                    and task.attempt_count < 2
                    and manifest is not None
                    and manifest.content_jsonb["shadow_status"] == "inconclusive_infrastructure"
                    and manifest.content_jsonb.get("runtime", {}).get("version")
                    == PROSE_RUNTIME_VERSION
                ),
                "completed_scene_count": sum(
                    a.artifact_key.endswith(".accepted") and a.artifact_kind == "scene_render"
                    for a in artifacts
                ),
                "is_adopted": False,
            },
        }

    def _run_view(self, run: CompileRun, task: TaskRun | None) -> dict[str, Any]:
        if task is None:
            raise RuntimeError("CompileRun TaskRun is missing")
        from casefile.application.compiler.stability import compiler_stability

        shadow = self._shadow_view(run, task)
        artifacts = self.compiler.list_artifacts(run.id)
        calls = list(
            self.session.scalars(
                select(AgentModelCall)
                .where(AgentModelCall.task_run_id == task.id)
                .options(
                    load_only(
                        AgentModelCall.prompt_component_id,
                        AgentModelCall.call_no,
                        AgentModelCall.status,
                    )
                )
            )
        )
        return {
            "compile_run_id": run.id,
            "task_run_id": run.task_run_id,
            "project_id": run.project_id,
            "casefile_id": run.casefile_id,
            "draft_id": run.draft_id,
            "target": run.target_kind,
            "mode": run.compile_mode,
            "source_snapshot_id": run.source_snapshot_id,
            "source_canon_version_id": run.source_canon_version_id,
            "exposure_plan_revision_id": run.exposure_plan_revision_id,
            "compiler_profile_version_id": run.compiler_profile_version_id,
            "compiler_version": run.compiler_version,
            "prose_renderer_shadow": run.prose_renderer_shadow,
            **shadow,
            "stability": compiler_stability(
                task_status=task.status,
                attempt_count=task.attempt_count,
                prose_requested=run.prose_renderer_shadow,
                shadow_status=shadow["prose_shadow"]["status"],
                schema_ids={artifact.schema_id for artifact in artifacts},
                calls=calls,
            ),
            "input_hash": run.input_hash,
            "execution": task_view(task),
            "artifacts": [
                {
                    "artifact_id": artifact.id,
                    "artifact_kind": artifact.artifact_kind,
                    "artifact_key": artifact.artifact_key,
                    "schema_id": artifact.schema_id,
                    "content_hash": artifact.content_hash,
                    "agent_step_run_id": artifact.agent_step_run_id,
                }
                for artifact in artifacts
            ],
            "created_at": run.created_at.isoformat(),
        }


def _scene_batch_count(profile: dict[str, Any]) -> int:
    structure = profile["structure"]
    chapter_count = int(structure["target_chapters"])
    scene_count = int(structure["target_scenes"])
    per_chapter = [0] * chapter_count
    for discourse_order in range(1, scene_count + 1):
        chapter_index = min(
            chapter_count - 1,
            (discourse_order - 1) * chapter_count // scene_count,
        )
        per_chapter[chapter_index] += 1
    return sum(
        (count + SCENE_COMPILER_BATCH_SIZE - 1) // SCENE_COMPILER_BATCH_SIZE
        for count in per_chapter
        if count
    )


__all__ = ["CompilerService"]
