"""Synchronous PostgreSQL engine, session factory, and revision readiness checks."""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

EXPECTED_DATABASE_REVISION = "20260813160000"
DEFAULT_DATABASE_URL = "postgresql+psycopg://casefile:casefile_local_only@127.0.0.1:55432/casefile"


def create_database_engine(database_url: str | None = None) -> Engine:
    """Create the application engine without changing database state."""

    resolved_url = database_url
    if resolved_url is None:
        resolved_url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    return create_engine(resolved_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory with explicit service-managed transactions."""

    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield one session and guarantee close; services own commit/rollback."""

    with factory() as session:
        yield session


def current_database_revision(engine: Engine) -> str | None:
    """Read the sole Alembic revision, returning None for an unmigrated database."""

    with engine.connect() as connection:
        exists = connection.execute(
            text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
        ).scalar_one()
        if not exists:
            return None
        revisions = (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        )
    if len(revisions) != 1:
        return None
    return str(revisions[0])


def assert_database_ready(engine: Engine) -> None:
    """Fail fast unless the database exactly matches this application build."""

    revision = current_database_revision(engine)
    if revision != EXPECTED_DATABASE_REVISION:
        raise RuntimeError(
            "Database revision mismatch: "
            f"expected {EXPECTED_DATABASE_REVISION}, got {revision or 'unmigrated'}. "
            "Run scripts/bootstrap.ps1 before starting the API."
        )
