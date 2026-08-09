"""HTTP routes for the analyst-workbench read model."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from casefile.api.dependencies import ActorDependency, SessionDependency
from casefile.application.workbench_read_model import WorkbenchReadModel


def workbench_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["analyst-workbench"])

    @router.get("/projects/{project_id}/workbench-context")
    def get_workbench_context(
        project_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return WorkbenchReadModel(session).get_context(actor, project_id)

    return router
