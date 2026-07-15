"""Disposable PostgreSQL verification for the full Alembic foundation chain."""

from __future__ import annotations

import os
import json
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.postgres

BACKEND_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = {
    "alembic_version",
    "approvals",
    "audit_events",
    "canon_versions",
    "casefile_objects",
    "casefile_refs",
    "casefiles",
    "draft_operations",
    "draft_snapshots",
    "drafts",
    "memberships",
    "projects",
    "users",
    "workspace_settings",
    "workspaces",
}


@pytest.fixture(scope="module")
def migrated_engine() -> sa.Engine:
    database_url = os.getenv("CASEFILE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("CASEFILE_TEST_DATABASE_URL is not configured")

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.downgrade(config, "base")
    command.upgrade(config, "head")

    engine = sa.create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(config, "base")


def test_upgrade_creates_tables_and_local_identity(migrated_engine: sa.Engine) -> None:
    assert set(sa.inspect(migrated_engine).get_table_names()) == EXPECTED_TABLES
    with migrated_engine.connect() as connection:
        identity = connection.execute(
            sa.text(
                """
                SELECT u.public_id, w.public_id, m.role
                FROM memberships m
                JOIN users u ON u.id = m.user_id
                JOIN workspaces w ON w.id = m.workspace_id
                WHERE w.public_id = 'ws_local'
                """
            )
        ).one()
    assert identity == ("user_local_owner", "ws_local", "owner")


def test_tenant_canon_and_immutability_guards(migrated_engine: sa.Engine) -> None:
    hash_value = "a" * 64
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        workspace_id = connection.execute(
            sa.text("SELECT id FROM workspaces WHERE public_id = 'ws_local'")
        ).scalar_one()

        other_workspace_id = uuid4()
        other_project_id = uuid4()
        connection.execute(
            sa.text(
                """
                INSERT INTO workspaces (id, public_id, slug, name, status)
                VALUES (:id, 'ws_other', 'other', 'Other Workspace', 'active')
                """
            ),
            {"id": other_workspace_id},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO projects (
                    id, workspace_id, public_id, title, status,
                    created_by_actor_id, updated_by_actor_id
                ) VALUES (
                    :id, :workspace_id, 'project_other', 'Other', 'active',
                    'user_local_owner', 'user_local_owner'
                )
                """
            ),
            {"id": other_project_id, "workspace_id": other_workspace_id},
        )
        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO casefiles (
                            id, workspace_id, project_id, public_id, title, status,
                            created_by_actor_id, updated_by_actor_id
                        ) VALUES (
                            :id, :workspace_id, :project_id, 'case_cross_workspace',
                            'Invalid', 'draft', 'user_local_owner', 'user_local_owner'
                        )
                        """
                    ),
                    {
                        "id": uuid4(),
                        "workspace_id": workspace_id,
                        "project_id": other_project_id,
                    },
                )

        project_id = uuid4()
        casefile_id = uuid4()
        draft_id = uuid4()
        object_uuid = uuid4()
        snapshot_id = uuid4()
        approval_id = uuid4()
        canon_id = uuid4()
        operation_id = uuid4()
        audit_id = uuid4()

        connection.execute(
            sa.text(
                """
                INSERT INTO projects (
                    id, workspace_id, public_id, title, status,
                    created_by_actor_id, updated_by_actor_id
                ) VALUES (
                    :id, :workspace_id, 'project_alpha', 'Alpha', 'active',
                    'user_local_owner', 'user_local_owner'
                )
                """
            ),
            {"id": project_id, "workspace_id": workspace_id},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO casefiles (
                    id, workspace_id, project_id, public_id, title, status,
                    created_by_actor_id, updated_by_actor_id
                ) VALUES (
                    :id, :workspace_id, :project_id, 'case_alpha', 'Alpha', 'draft',
                    'user_local_owner', 'user_local_owner'
                )
                """
            ),
            {"id": casefile_id, "workspace_id": workspace_id, "project_id": project_id},
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO drafts (
                    id, workspace_id, casefile_id, public_id, revision, status,
                    created_by_actor_id, updated_by_actor_id
                ) VALUES (
                    :id, :workspace_id, :casefile_id, 'draft_alpha', 1, 'active',
                    'user_local_owner', 'user_local_owner'
                )
                """
            ),
            {"id": draft_id, "workspace_id": workspace_id, "casefile_id": casefile_id},
        )

        with pytest.raises(sa.exc.IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO casefile_objects (
                            workspace_id, casefile_id, draft_id, object_id, object_type,
                            revision, confidence, confirmation_status,
                            created_by_actor_id, updated_by_actor_id
                        ) VALUES (
                            :workspace_id, :casefile_id, :draft_id, 'evt_invalid', 'event',
                            1, 1.5, 'user_confirmed', 'user_local_owner', 'user_local_owner'
                        )
                        """
                    ),
                    {
                        "workspace_id": workspace_id,
                        "casefile_id": casefile_id,
                        "draft_id": draft_id,
                    },
                )

        connection.execute(
            sa.text(
                """
                INSERT INTO casefile_objects (
                    id, workspace_id, casefile_id, draft_id, object_id, object_type,
                    revision, confidence, confirmation_status,
                    created_by_actor_id, updated_by_actor_id
                ) VALUES (
                    :id, :workspace_id, :casefile_id, :draft_id, 'evt_alpha', 'event',
                    1, 0.9, 'user_confirmed', 'user_local_owner', 'user_local_owner'
                )
                """
            ),
            {
                "id": object_uuid,
                "workspace_id": workspace_id,
                "casefile_id": casefile_id,
                "draft_id": draft_id,
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO draft_operations (
                    id, workspace_id, draft_id, object_id, sequence_no, operation_type,
                    field_path, old_value_jsonb, new_value_jsonb,
                    base_revision, result_revision, actor_id
                ) VALUES (
                    :id, :workspace_id, :draft_id, :object_id, 1, 'replace', '/time/start',
                    '"20:00"'::jsonb, '"19:30"'::jsonb, 1, 2, 'user_local_owner'
                )
                """
            ),
            {
                "id": operation_id,
                "workspace_id": workspace_id,
                "draft_id": draft_id,
                "object_id": object_uuid,
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO draft_snapshots (
                    id, workspace_id, casefile_id, draft_id, public_id,
                    snapshot_revision, schema_version, snapshot_jsonb,
                    content_hash, created_by_actor_id
                ) VALUES (
                    :id, :workspace_id, :casefile_id, :draft_id, 'snapshot_alpha',
                    1, '1.0', CAST(:snapshot AS jsonb), :content_hash, 'user_local_owner'
                )
                """
            ),
            {
                "id": snapshot_id,
                "workspace_id": workspace_id,
                "casefile_id": casefile_id,
                "draft_id": draft_id,
                "snapshot": json.dumps({"casefile_id": "case_alpha"}),
                "content_hash": hash_value,
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO approvals (
                    id, workspace_id, casefile_id, draft_snapshot_id, public_id,
                    status, requested_by_actor_id
                ) VALUES (
                    :id, :workspace_id, :casefile_id, :snapshot_id, 'approval_alpha',
                    'pending', 'user_local_owner'
                )
                """
            ),
            {
                "id": approval_id,
                "workspace_id": workspace_id,
                "casefile_id": casefile_id,
                "snapshot_id": snapshot_id,
            },
        )

        canon_parameters = {
            "id": canon_id,
            "workspace_id": workspace_id,
            "casefile_id": casefile_id,
            "snapshot_id": snapshot_id,
            "approval_id": approval_id,
            "content": json.dumps({"casefile_id": "case_alpha"}),
            "content_hash": hash_value,
        }
        canon_insert = sa.text(
            """
            INSERT INTO canon_versions (
                id, workspace_id, casefile_id, source_snapshot_id, approval_id,
                public_id, version_no, schema_version, content_jsonb, content_hash,
                result_validity, approved_by_actor_id, frozen_at
            ) VALUES (
                :id, :workspace_id, :casefile_id, :snapshot_id, :approval_id,
                'cv_alpha', 1, '1.0', CAST(:content AS jsonb), :content_hash,
                'valid', 'user_local_owner', CURRENT_TIMESTAMP
            )
            """
        )
        with pytest.raises(sa.exc.DBAPIError):
            with connection.begin_nested():
                connection.execute(canon_insert, canon_parameters)

        connection.execute(
            sa.text(
                """
                UPDATE approvals
                SET status = 'approved', revision = 2,
                    decided_by_actor_id = 'user_local_owner', decided_at = CURRENT_TIMESTAMP
                WHERE id = :id
                """
            ),
            {"id": approval_id},
        )
        connection.execute(canon_insert, canon_parameters)

        connection.execute(
            sa.text(
                """
                INSERT INTO audit_events (
                    id, workspace_id, public_id, actor_id, event_type,
                    entity_type, entity_public_id, action
                ) VALUES (
                    :id, :workspace_id, 'audit_alpha', 'user_local_owner',
                    'canon.created', 'canon_version', 'cv_alpha', 'create'
                )
                """
            ),
            {"id": audit_id, "workspace_id": workspace_id},
        )

        immutable_updates = (
            ("canon_versions", canon_id),
            ("draft_snapshots", snapshot_id),
            ("draft_operations", operation_id),
            ("audit_events", audit_id),
        )
        for table_name, row_id in immutable_updates:
            with pytest.raises(sa.exc.DBAPIError):
                with connection.begin_nested():
                    connection.execute(
                        sa.text(f"UPDATE {table_name} SET id = id WHERE id = :id"),
                        {"id": row_id},
                    )

        transaction.rollback()
