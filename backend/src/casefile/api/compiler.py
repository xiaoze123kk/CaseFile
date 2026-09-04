"""HTTP routes for Compiler Profiles and durable CompileRuns."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from casefile.api.dependencies import ActorDependency, SessionDependency
from casefile.api.schemas import (
    CompilerProfileCreateRequest,
    CompilerProfileVersionCreateRequest,
    CompileRunCreateRequest,
)
from casefile.application.compiler import CompilerService


def compiler_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["narrative-compiler"])

    @router.post("/projects/{project_id}/compiler-profiles", status_code=201)
    def create_profile(
        project_id: int,
        payload: CompilerProfileCreateRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return CompilerService(session).create_profile(
            actor,
            project_id,
            profile_key=payload.profile_key,
            name=payload.name,
            schema_id=payload.schema_id,
            payload=payload.payload,
        )

    @router.post(
        "/projects/{project_id}/compiler-profiles/{profile_id}/versions",
        status_code=201,
    )
    def append_profile_version(
        project_id: int,
        profile_id: int,
        payload: CompilerProfileVersionCreateRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return CompilerService(session).append_profile_version(
            actor,
            project_id,
            profile_id,
            expected_current_version_id=payload.expected_current_version_id,
            schema_id=payload.schema_id,
            payload=payload.payload,
        )

    @router.get("/projects/{project_id}/compiler-profiles")
    def list_profiles(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> list[dict[str, Any]]:
        return CompilerService(session).list_profiles(actor, project_id)

    @router.get("/projects/{project_id}/compiler-profiles/{profile_id}")
    def get_profile(
        project_id: int,
        profile_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return CompilerService(session).get_profile(actor, project_id, profile_id)

    @router.post("/projects/{project_id}/compile-runs", status_code=201)
    def create_run(
        project_id: int,
        payload: CompileRunCreateRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return CompilerService(session).create_run(
            actor,
            project_id,
            mode=payload.mode,
            expected_draft_id=payload.expected_draft_id,
            expected_draft_revision=payload.expected_draft_revision,
            canon_version_id=payload.canon_version_id,
            exposure_plan_revision_id=payload.exposure_plan_revision_id,
            compiler_profile_version_id=payload.compiler_profile_version_id,
            planner_provider=payload.planner_provider,
            prose_renderer_shadow=payload.prose_renderer_shadow,
        )

    @router.get("/projects/{project_id}/compile-runs")
    def list_runs(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> list[dict[str, Any]]:
        return CompilerService(session).list_runs(actor, project_id)

    @router.get("/projects/{project_id}/compile-runs/{compile_run_id}")
    def get_run(
        project_id: int,
        compile_run_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return CompilerService(session).get_run(actor, project_id, compile_run_id)

    @router.get("/projects/{project_id}/compile-runs/{compile_run_id}/artifacts/{artifact_id}")
    def get_artifact(
        project_id: int,
        compile_run_id: int,
        artifact_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return CompilerService(session).get_artifact(actor, project_id, compile_run_id, artifact_id)

    return router


__all__ = ["compiler_router"]
