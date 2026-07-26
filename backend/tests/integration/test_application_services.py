"""Real-PostgreSQL transaction tests for aggregate application services."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Never
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from casefile.application.commands import ProjectCreate
from casefile.application.errors import ApplicationError
from casefile.application.services import CaseFileService
from casefile.data_postgres.repositories import ProjectRepository
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _database_url() -> str:
    value = os.getenv("CASEFILE_TEST_DATABASE_URL")
    if not value:
        pytest.skip("CASEFILE_TEST_DATABASE_URL is not configured")
    if not (make_url(value).database or "").endswith("_test"):
        pytest.fail("Refusing service integration test against a non-_test database")
    return value


def _config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT / "src"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture(scope="module")
def service_database() -> Iterator[Engine]:
    database_url = _database_url()
    config = _config(database_url)
    engine = create_engine(database_url)
    with patch.dict(os.environ, {"DATABASE_URL": database_url}):
        command.downgrade(config, "base")
        command.upgrade(config, "head")
    try:
        yield engine
    finally:
        engine.dispose()
        with patch.dict(os.environ, {"DATABASE_URL": database_url}):
            command.downgrade(config, "base")


def _create_user(engine: Engine, name: str) -> int:
    with engine.begin() as connection:
        return int(
            connection.execute(
                text("INSERT INTO users (display_name) VALUES (:name) RETURNING id"),
                {"name": name},
            ).scalar_one()
        )


def test_project_aggregate_is_atomic_and_owner_filtered(service_database: Engine) -> None:
    engine = service_database
    owner_id = _create_user(engine, "Service Owner")
    other_id = _create_user(engine, "Service Other")
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    with factory() as session:
        created = CaseFileService(session).create_project(
            owner_id,
            ProjectCreate(title="Atomic Project", profile={"mode": "test"}),
        )
        project_id = created["id"]

    with engine.connect() as connection:
        lineage = connection.execute(
            text(
                """
                SELECT project.owner_user_id, casefile.schema_version,
                       draft.schema_version, draft.revision
                  FROM projects project
                  JOIN casefiles casefile ON casefile.project_id = project.id
                  JOIN drafts draft
                    ON draft.project_id = project.id
                   AND draft.casefile_id = casefile.id
                 WHERE project.id = :project_id
                """
            ),
            {"project_id": project_id},
        ).one()
    assert tuple(lineage) == (owner_id, "0.1.0", "0.1.0", 1)

    with factory() as session:
        service = CaseFileService(session)
        assert service.list_projects(other_id) == []
        with pytest.raises(ApplicationError) as caught:
            service.get_project(other_id, project_id)
        assert caught.value.status_code == 404

    original_create = ProjectRepository.create

    def create_then_fail(repository: ProjectRepository, **kwargs: Any) -> Never:
        original_create(repository, **kwargs)
        raise RuntimeError("force aggregate rollback")

    with factory() as session, patch.object(
        ProjectRepository, "create", new=create_then_fail
    ):
        with pytest.raises(RuntimeError, match="force aggregate rollback"):
            CaseFileService(session).create_project(
                owner_id,
                ProjectCreate(title="Must Roll Back"),
            )

    with engine.connect() as connection:
        rolled_back = connection.execute(
            text("SELECT count(*) FROM projects WHERE title = 'Must Roll Back'")
        ).scalar_one()
    assert rolled_back == 0
