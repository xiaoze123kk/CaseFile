"""PostgreSQL migration coverage for many Drafts and one Current Draft."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from alembic import command
from application_services_test_support import (
    _alembic_config,
    _clear_projects_before_downgrade,
    _test_database_url,
)
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.postgres

PREVIOUS_REVISION = "20260808154126"


def test_upgrade_backfills_current_draft_and_allows_draft_scoped_object_ids() -> None:
    database_url = _test_database_url()
    config = _alembic_config(database_url)
    engine = create_engine(database_url)
    try:
        with patch.dict(os.environ, {"DATABASE_URL": database_url}):
            _clear_projects_before_downgrade(database_url)
            command.downgrade(config, "base")
            command.upgrade(config, PREVIOUS_REVISION)

            with engine.begin() as connection:
                owner_id = int(
                    connection.scalar(
                        text(
                            "INSERT INTO users (display_name) "
                            "VALUES ('Migration Owner') RETURNING id"
                        )
                    )
                )
                project_id = int(
                    connection.scalar(
                        text(
                            "INSERT INTO projects (owner_user_id, title) "
                            "VALUES (:owner_id, 'Legacy Project') RETURNING id"
                        ),
                        {"owner_id": owner_id},
                    )
                )
                casefile_id = int(
                    connection.scalar(
                        text(
                            "INSERT INTO casefiles "
                            "(project_id, object_id, title, schema_version) "
                            "VALUES (:project_id, 'case_migration', 'Legacy Draft', '1.0') "
                            "RETURNING id"
                        ),
                        {"project_id": project_id},
                    )
                )
                legacy_draft_id = int(
                    connection.scalar(
                        text(
                            "INSERT INTO drafts "
                            "(project_id, casefile_id, version_id, schema_version) "
                            "VALUES (:project_id, :casefile_id, 'draft_migration_a', '1.0') "
                            "RETURNING id"
                        ),
                        {"project_id": project_id, "casefile_id": casefile_id},
                    )
                )

            command.upgrade(config, "head")

            with engine.begin() as connection:
                current_draft_id = int(
                    connection.scalar(
                        text("SELECT current_draft_id FROM casefiles WHERE id = :id"),
                        {"id": casefile_id},
                    )
                )
                title, document_status = connection.execute(
                    text("SELECT title, document_status FROM drafts WHERE id = :id"),
                    {"id": legacy_draft_id},
                ).one()
                assert current_draft_id == legacy_draft_id
                assert (title, document_status) == ("Legacy Draft", "draft")

                second_draft_id = int(
                    connection.scalar(
                        text(
                            "INSERT INTO drafts "
                            "(project_id, casefile_id, title, version_id, schema_version) "
                            "VALUES (:project_id, :casefile_id, 'Alternative Draft', "
                            "'draft_migration_b', '1.0') RETURNING id"
                        ),
                        {"project_id": project_id, "casefile_id": casefile_id},
                    )
                )
                for draft_id in (legacy_draft_id, second_draft_id):
                    connection.execute(
                        text(
                            "INSERT INTO casefile_objects "
                            "(project_id, casefile_id, draft_id, object_id, object_type, "
                            "contract_ordinal, created_by_id, contract_updated_at, "
                            "confirmation_status) VALUES "
                            "(:project_id, :casefile_id, :draft_id, 'ent_shared', 'entity', "
                            "1, 'migration', CURRENT_TIMESTAMP::text, 'user_confirmed')"
                        ),
                        {
                            "project_id": project_id,
                            "casefile_id": casefile_id,
                            "draft_id": draft_id,
                        },
                    )

                foreign_project_id = int(
                    connection.scalar(
                        text(
                            "INSERT INTO projects (owner_user_id, title) "
                            "VALUES (:owner_id, 'Foreign Project') RETURNING id"
                        ),
                        {"owner_id": owner_id},
                    )
                )
                foreign_casefile_id = int(
                    connection.scalar(
                        text(
                            "INSERT INTO casefiles "
                            "(project_id, object_id, title, schema_version, current_draft_id) "
                            "VALUES (:project_id, 'case_foreign', 'Foreign Draft', '1.0', 0) "
                            "RETURNING id"
                        ),
                        {"project_id": foreign_project_id},
                    )
                )
                foreign_draft_id = int(
                    connection.scalar(
                        text(
                            "INSERT INTO drafts "
                            "(project_id, casefile_id, title, version_id, schema_version) "
                            "VALUES (:project_id, :casefile_id, 'Foreign Draft', "
                            "'draft_migration_foreign', '1.0') RETURNING id"
                        ),
                        {
                            "project_id": foreign_project_id,
                            "casefile_id": foreign_casefile_id,
                        },
                    )
                )
                connection.execute(
                    text(
                        "UPDATE casefiles SET current_draft_id = :draft_id "
                        "WHERE id = :casefile_id"
                    ),
                    {
                        "draft_id": foreign_draft_id,
                        "casefile_id": foreign_casefile_id,
                    },
                )

            with pytest.raises(DBAPIError), engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE casefiles SET current_draft_id = :draft_id "
                        "WHERE id = :casefile_id"
                    ),
                    {"draft_id": foreign_draft_id, "casefile_id": casefile_id},
                )
                connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

            unique_names = {
                item["name"] for item in inspect(engine).get_unique_constraints("casefile_objects")
            }
            assert "uq_casefile_objects_draft_id_object_id" in unique_names
            assert "uq_casefile_objects_casefile_id_object_id" not in unique_names
    finally:
        engine.dispose()
        with patch.dict(os.environ, {"DATABASE_URL": database_url}):
            _clear_projects_before_downgrade(database_url)
            command.downgrade(config, "base")
