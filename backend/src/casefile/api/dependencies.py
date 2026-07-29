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
            "X-CaseFile-User-Id is required for this local API",
            status_code=401,
        )
    try:
        user_id = int(value)
    except ValueError as error:
        raise ApplicationError(
            "identity_invalid",
            "X-CaseFile-User-Id must be a positive integer",
            status_code=401,
        ) from error
    if user_id < 1 or ProjectRepository(session).get_active_user(user_id) is None:
        session.rollback()
        raise ApplicationError(
            "identity_invalid",
            "The local API user does not exist or is disabled",
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
            "X-CaseFile-Base-Revision is required for Draft mutations",
            status_code=428,
        )
    try:
        revision = int(value)
    except ValueError as error:
        raise ApplicationError(
            "base_revision_invalid",
            "X-CaseFile-Base-Revision must be a positive integer",
            status_code=422,
        ) from error
    if revision < 1:
        raise ApplicationError(
            "base_revision_invalid",
            "X-CaseFile-Base-Revision must be a positive integer",
            status_code=422,
        )
    return revision


RevisionDependency = Annotated[int, Depends(get_base_revision)]
