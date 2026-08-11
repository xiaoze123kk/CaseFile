"""FastAPI application factory and the first CaseFile API routes."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from casefile.api.brief_intake import brief_intake_router
from casefile.api.ideas import ideas_router
from casefile.api.dependencies import (
    ActorDependency,
    RevisionDependency,
    SessionDependency,
)
from casefile.api.schemas import (
    ObjectPatchRequest,
    ProjectCreateRequest,
    ProjectUpdateRequest,
)
from casefile.api.workbench import workbench_router
from casefile.api.workflow import workflow_router
from casefile.application.errors import ApplicationError
from casefile.application.services import CaseFileService
from casefile.application.v1_editing import V1EditingService
from casefile.data_postgres.session import (
    EXPECTED_DATABASE_REVISION,
    assert_database_ready,
    create_database_engine,
    create_session_factory,
    current_database_revision,
)

_DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:3000",
    "http://localhost:3000",
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
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_exception_handler(ApplicationError, _application_error_handler)
    application.add_exception_handler(RequestValidationError, _validation_error_handler)
    application.add_exception_handler(StarletteHTTPException, _http_error_handler)
    application.add_exception_handler(SQLAlchemyError, _database_error_handler)
    application.add_exception_handler(Exception, _unexpected_error_handler)
    application.include_router(_health_router())
    application.include_router(_api_router())
    application.include_router(brief_intake_router())
    application.include_router(ideas_router())
    application.include_router(workflow_router())
    application.include_router(workbench_router())
    return application


def _cors_origins() -> list[str]:
    """Return exact browser origins, with an opt-in list for isolated local harnesses."""

    configured = os.getenv("CASEFILE_CORS_ORIGINS", "")
    origins = list(_DEFAULT_CORS_ORIGINS)
    for raw_origin in configured.split(","):
        origin = raw_origin.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "CASEFILE_CORS_ORIGINS must contain comma-separated HTTP(S) origins"
            )
        if origin not in origins:
            origins.append(origin)
    return origins


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
            "message": _validation_message(item),
            "type": item["type"],
        }
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "code": "request_invalid",
            "message": "提交内容不符合接口要求，请检查后重试。",
            "details": {"errors": details},
        },
    )


async def _http_error_handler(_: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, StarletteHTTPException)
    codes = {404: "not_found", 405: "method_not_allowed"}
    messages = {
        404: "没有找到请求的数据。",
        405: "当前操作不受支持。",
    }
    message = messages.get(error.status_code, "请求未能完成，请稍后重试。")
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
        message = "当前修改与已保存的数据冲突，请刷新后重试。"
    elif isinstance(error, OperationalError):
        status_code = 503
        code = "database_unavailable"
        message = "数据库暂时不可用，请稍后重试。"
    else:
        status_code = 500
        code = "database_error"
        message = "数据库请求失败，请稍后重试。"
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "details": {}},
    )


async def _unexpected_error_handler(_: Request, error: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "code": "internal_error",
            "message": "请求暂时无法完成，请稍后重试。",
            "details": {},
        },
    )


def _validation_message(item: dict[str, Any]) -> str:
    """Convert Pydantic's English validation details into author-facing Chinese."""

    messages = {
        "missing": "缺少必填字段。",
        "extra_forbidden": "包含未被允许的字段。",
        "string_type": "字段应为文本。",
        "string_too_short": "文本长度不足。",
        "string_too_long": "文本长度超出限制。",
        "int_parsing": "字段应为整数。",
        "int_type": "字段应为整数。",
        "bool_type": "字段应为布尔值。",
        "list_type": "字段应为列表。",
        "dict_type": "字段应为对象。",
        "greater_than_equal": "数值低于允许的最小值。",
        "less_than_equal": "数值高于允许的最大值。",
        "literal_error": "字段值不在允许范围内。",
        "enum": "字段值不在允许范围内。",
    }
    return messages.get(str(item.get("type") or ""), "字段值无效，请检查后重试。")


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
        return JSONResponse(content={"status": "ready", "database_revision": revision})

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

    @router.post("/projects/{project_id}/unarchive")
    def unarchive_project(
        project_id: int, actor: ActorDependency, session: SessionDependency
    ) -> dict[str, Any]:
        return CaseFileService(session).unarchive_project(actor, project_id)

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

    @router.patch("/projects/{project_id}/draft/objects/{object_id}")
    def patch_v1_object(
        project_id: int,
        object_id: str,
        payload: ObjectPatchRequest,
        response: Response,
        actor: ActorDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        result, revision = V1EditingService(session).patch_object(
            actor,
            project_id,
            object_id,
            expected_revision=payload.expected_revision,
            changes=payload.changes,
        )
        _set_revision(response, revision)
        return result

    @router.post("/projects/{project_id}/draft/snapshots")
    def create_snapshot(
        project_id: int,
        response: Response,
        actor: ActorDependency,
        revision: RevisionDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        result, created = CaseFileService(session).create_snapshot(actor, project_id, revision)
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
