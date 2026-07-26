"""FastAPI application factory and the first CaseFile API routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from casefile.api.dependencies import (
    ActorDependency,
    RevisionDependency,
    SessionDependency,
)
from casefile.api.schemas import (
    EntityWriteRequest,
    EventWriteRequest,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    ReferenceReplaceRequest,
)
from casefile.application.errors import ApplicationError
from casefile.application.services import CaseFileService
from casefile.data_postgres.session import (
    EXPECTED_DATABASE_REVISION,
    assert_database_ready,
    create_database_engine,
    create_session_factory,
    current_database_revision,
)


def create_app(database_url: str | None = None, *, verify_database: bool = True) -> FastAPI:
    """Create an injectable app without opening a connection until lifespan startup."""

    engine = create_database_engine(database_url)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if verify_database:
            assert_database_ready(engine)
        yield
        engine.dispose()

    application = FastAPI(
        title="CaseFile API",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.engine = engine
    application.state.session_factory = session_factory
    application.add_exception_handler(ApplicationError, _application_error_handler)
    application.add_exception_handler(RequestValidationError, _validation_error_handler)
    application.add_exception_handler(StarletteHTTPException, _http_error_handler)
    application.add_exception_handler(SQLAlchemyError, _database_error_handler)
    application.add_exception_handler(Exception, _unexpected_error_handler)
    application.include_router(_health_router())
    application.include_router(_api_router())
    return application


async def _application_error_handler(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, ApplicationError)
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "message": error.message, "details": error.details},
    )


async def _validation_error_handler(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    details = [
        {
            "path": "/".join(str(part) for part in item["loc"]),
            "message": item["msg"],
            "type": item["type"],
        }
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "code": "request_invalid",
            "message": "The request did not match the API contract",
            "details": {"errors": details},
        },
    )


async def _http_error_handler(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, StarletteHTTPException)
    codes = {404: "not_found", 405: "method_not_allowed"}
    message = error.detail if isinstance(error.detail, str) else "The HTTP request failed"
    return JSONResponse(
        status_code=error.status_code,
        content={
            "code": codes.get(error.status_code, "http_error"),
            "message": message,
            "details": {},
        },
        headers=error.headers,
    )


async def _database_error_handler(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, SQLAlchemyError)
    if isinstance(error, IntegrityError):
        status_code = 409
        code = "resource_conflict"
        message = "The requested change conflicts with current persisted state"
    elif isinstance(error, OperationalError):
        status_code = 503
        code = "database_unavailable"
        message = "The database is temporarily unavailable"
    else:
        status_code = 500
        code = "database_error"
        message = "The database request failed"
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "details": {}},
    )


async def _unexpected_error_handler(_: Request, error: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "code": "internal_error",
            "message": "The request could not be completed",
            "details": {},
        },
    )


def _health_router() -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health/ready")
    def ready(request: Request) -> JSONResponse:
        engine = request.app.state.engine
        try:
            revision = current_database_revision(engine)
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "database_revision": None},
            )
        if revision != EXPECTED_DATABASE_REVISION:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "database_revision": revision},
            )
        return JSONResponse(
            content={"status": "ready", "database_revision": revision}
        )

    return router


def _api_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["casefile"])

    @router.post("/projects", status_code=201)
    def create_project(
        payload: ProjectCreateRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return CaseFileService(session).create_project(actor, payload.command())

    @router.get("/projects")
    def list_projects(actor: ActorDependency, session: SessionDependency) -> list[dict[str, Any]]:
        return CaseFileService(session).list_projects(actor)

    @router.get("/projects/{project_id}")
    def get_project(
        project_id: int, actor: ActorDependency, session: SessionDependency
    ) -> dict[str, Any]:
        return CaseFileService(session).get_project(actor, project_id)

    @router.patch("/projects/{project_id}")
    def update_project(
        project_id: int,
        payload: ProjectUpdateRequest,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        changes = payload.model_dump(exclude_unset=True)
        return CaseFileService(session).update_project(actor, project_id, changes)

    @router.post("/projects/{project_id}/archive")
    def archive_project(
        project_id: int, actor: ActorDependency, session: SessionDependency
    ) -> dict[str, Any]:
        return CaseFileService(session).archive_project(actor, project_id)

    @router.get("/projects/{project_id}/draft")
    def get_draft(
        project_id: int,
        response: Response,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        result = CaseFileService(session).get_draft(actor, project_id)
        _set_revision(response, result["revision"])
        return result

    @router.post("/projects/{project_id}/draft/entities", status_code=201)
    def create_entity(
        project_id: int,
        payload: EntityWriteRequest,
        response: Response,
        actor: ActorDependency,
        revision: RevisionDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        result, new_revision = CaseFileService(session).create_entity(
            actor, project_id, revision, payload.command()
        )
        _set_revision(response, new_revision)
        return result

    @router.get("/projects/{project_id}/draft/entities")
    def list_entities(
        project_id: int, actor: ActorDependency, session: SessionDependency
    ) -> list[dict[str, Any]]:
        return CaseFileService(session).list_entities(actor, project_id)

    @router.get("/projects/{project_id}/draft/entities/{object_id}")
    def get_entity(
        project_id: int,
        object_id: str,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return CaseFileService(session).get_entity(actor, project_id, object_id)

    @router.put("/projects/{project_id}/draft/entities/{object_id}")
    def update_entity(
        project_id: int,
        object_id: str,
        payload: EntityWriteRequest,
        response: Response,
        actor: ActorDependency,
        revision: RevisionDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        result, new_revision = CaseFileService(session).update_entity(
            actor, project_id, object_id, revision, payload.command()
        )
        _set_revision(response, new_revision)
        return result

    @router.delete("/projects/{project_id}/draft/entities/{object_id}", status_code=204)
    def delete_entity(
        project_id: int,
        object_id: str,
        actor: ActorDependency,
        revision: RevisionDependency,
        session: SessionDependency,
    ) -> Response:
        new_revision = CaseFileService(session).delete_entity(
            actor, project_id, object_id, revision
        )
        return Response(
            status_code=204,
            headers={"X-CaseFile-Draft-Revision": str(new_revision)},
        )

    @router.put("/projects/{project_id}/draft/entities/{object_id}/adjacent-locations")
    def set_adjacent_locations(
        project_id: int,
        object_id: str,
        payload: ReferenceReplaceRequest,
        response: Response,
        actor: ActorDependency,
        revision: RevisionDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        result, new_revision = CaseFileService(session).set_adjacent_locations(
            actor, project_id, object_id, revision, payload.object_ids
        )
        _set_revision(response, new_revision)
        return result

    @router.post("/projects/{project_id}/draft/events", status_code=201)
    def create_event(
        project_id: int,
        payload: EventWriteRequest,
        response: Response,
        actor: ActorDependency,
        revision: RevisionDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        result, new_revision = CaseFileService(session).create_event(
            actor, project_id, revision, payload.command()
        )
        _set_revision(response, new_revision)
        return result

    @router.get("/projects/{project_id}/draft/events")
    def list_events(
        project_id: int, actor: ActorDependency, session: SessionDependency
    ) -> list[dict[str, Any]]:
        return CaseFileService(session).list_events(actor, project_id)

    @router.get("/projects/{project_id}/draft/events/{object_id}")
    def get_event(
        project_id: int,
        object_id: str,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return CaseFileService(session).get_event(actor, project_id, object_id)

    @router.put("/projects/{project_id}/draft/events/{object_id}")
    def update_event(
        project_id: int,
        object_id: str,
        payload: EventWriteRequest,
        response: Response,
        actor: ActorDependency,
        revision: RevisionDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        result, new_revision = CaseFileService(session).update_event(
            actor, project_id, object_id, revision, payload.command()
        )
        _set_revision(response, new_revision)
        return result

    @router.delete("/projects/{project_id}/draft/events/{object_id}", status_code=204)
    def delete_event(
        project_id: int,
        object_id: str,
        actor: ActorDependency,
        revision: RevisionDependency,
        session: SessionDependency,
    ) -> Response:
        new_revision = CaseFileService(session).delete_event(
            actor, project_id, object_id, revision
        )
        return Response(
            status_code=204,
            headers={"X-CaseFile-Draft-Revision": str(new_revision)},
        )

    @router.put("/projects/{project_id}/draft/events/{object_id}/actors")
    def set_event_actors(
        project_id: int,
        object_id: str,
        payload: ReferenceReplaceRequest,
        response: Response,
        actor: ActorDependency,
        revision: RevisionDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        result, new_revision = CaseFileService(session).set_event_actors(
            actor, project_id, object_id, revision, payload.object_ids
        )
        _set_revision(response, new_revision)
        return result

    @router.post("/projects/{project_id}/draft/snapshots")
    def create_snapshot(
        project_id: int,
        response: Response,
        actor: ActorDependency,
        revision: RevisionDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        result, created = CaseFileService(session).create_snapshot(
            actor, project_id, revision
        )
        response.status_code = 201 if created else 200
        _set_revision(response, result["revision"])
        return result

    @router.get("/projects/{project_id}/draft/snapshots")
    def list_snapshots(
        project_id: int, actor: ActorDependency, session: SessionDependency
    ) -> list[dict[str, Any]]:
        return CaseFileService(session).list_snapshots(actor, project_id)

    @router.get("/projects/{project_id}/draft/snapshots/{snapshot_id}")
    def get_snapshot(
        project_id: int,
        snapshot_id: int,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return CaseFileService(session).get_snapshot(actor, project_id, snapshot_id)

    return router


def _set_revision(response: Response, revision: int) -> None:
    response.headers["X-CaseFile-Draft-Revision"] = str(revision)


app = create_app()
