"""FastAPI dependencies for sessions, local identity, and Draft concurrency."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from casefile.application.errors import ApplicationError
from casefile.data_postgres.repositories import ProjectRepository


def get_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


SessionDependency = Annotated[Session, Depends(get_session)]


def get_actor_user_id(
    session: SessionDependency,
    value: Annotated[str | None, Header(alias="X-CaseFile-User-Id")] = None,
) -> int:
    if value is None:
        raise ApplicationError(
            "identity_required",
            "当前请求缺少本地用户身份。",
            status_code=401,
        )
    try:
        user_id = int(value)
    except ValueError as error:
        raise ApplicationError(
            "identity_invalid",
            "本地用户身份必须是正整数。",
            status_code=401,
        ) from error
    if user_id < 1 or ProjectRepository(session).get_active_user(user_id) is None:
        session.rollback()
        raise ApplicationError(
            "identity_invalid",
            "本地用户不存在或已被停用。",
            status_code=401,
        )
    session.rollback()
    return user_id


ActorDependency = Annotated[int, Depends(get_actor_user_id)]


def get_base_revision(
    value: Annotated[str | None, Header(alias="X-CaseFile-Base-Revision")] = None,
) -> int:
    if value is None:
        raise ApplicationError(
            "base_revision_required",
            "修改草稿前必须提供草稿版本信息。",
            status_code=428,
        )
    try:
        revision = int(value)
    except ValueError as error:
        raise ApplicationError(
            "base_revision_invalid",
            "草稿版本必须是正整数。",
            status_code=422,
        ) from error
    if revision < 1:
        raise ApplicationError(
            "base_revision_invalid",
            "草稿版本必须是正整数。",
            status_code=422,
        )
    return revision


def get_draft_id(
    value: Annotated[str | None, Header(alias="X-CaseFile-Draft-Id")] = None,
) -> int:
    if value is None:
        raise ApplicationError(
            "draft_id_required",
            "修改工作稿前必须提供工作稿标识。",
            status_code=428,
        )
    try:
        draft_id = int(value)
    except ValueError as error:
        raise ApplicationError(
            "draft_id_invalid",
            "工作稿标识必须是正整数。",
            status_code=422,
        ) from error
    if draft_id < 1:
        raise ApplicationError(
            "draft_id_invalid",
            "工作稿标识必须是正整数。",
            status_code=422,
        )
    return draft_id


RevisionDependency = Annotated[int, Depends(get_base_revision)]
DraftIdentityDependency = Annotated[int, Depends(get_draft_id)]
