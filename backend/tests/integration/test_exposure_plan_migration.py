"""Database trigger and immutability checks for Exposure Plan history."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import sessionmaker

from casefile.application.commands import ProjectCreate
from casefile.application.services import CaseFileService

pytestmark = pytest.mark.postgres


@contextmanager
def _expect_database_error(connection: Connection) -> Iterator[None]:
    with pytest.raises(sa.exc.DBAPIError), connection.begin_nested():
        yield


def test_exposure_plan_is_created_for_each_draft_and_history_is_immutable(
    workflow_database: tuple[Engine, int, str],
) -> None:
    engine, actor_id, _master_key = workflow_database
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        project = CaseFileService(session).create_project(
            actor_id,
            ProjectCreate(
                title="Exposure history",
                description=None,
                profile={},
            ),
        )

    with engine.begin() as connection:
        plan = connection.execute(
            sa.text(
                """
                SELECT ep.id, ep.project_id, ep.casefile_id, ep.draft_id,
                       ep.revision, ep.current_revision_id, ep.created_by_user_id
                  FROM exposure_plans AS ep
                 WHERE ep.project_id = :project_id
                """
            ),
            {"project_id": project["id"]},
        ).one()
        assert plan.revision == 0
        assert plan.current_revision_id is None
        assert plan.created_by_user_id == actor_id

        object_registry_id = int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO casefile_objects (
                        project_id, casefile_id, draft_id, object_id, object_type,
                        contract_ordinal, created_by_id, contract_updated_at,
                        confirmation_status
                    ) VALUES (
                        :project_id, :casefile_id, :draft_id,
                        'event_exposure_history', 'event', 1,
                        'user_test', CURRENT_TIMESTAMP::text, 'user_confirmed'
                    ) RETURNING id
                    """
                ),
                {
                    "project_id": plan.project_id,
                    "casefile_id": plan.casefile_id,
                    "draft_id": plan.draft_id,
                },
            ).scalar_one()
        )
        revision_id = int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO exposure_plan_revisions (
                        project_id, casefile_id, draft_id, plan_id,
                        revision_no, created_by_user_id
                    ) VALUES (
                        :project_id, :casefile_id, :draft_id, :plan_id,
                        1, :actor_id
                    ) RETURNING id
                    """
                ),
                {
                    "project_id": plan.project_id,
                    "casefile_id": plan.casefile_id,
                    "draft_id": plan.draft_id,
                    "plan_id": plan.id,
                    "actor_id": actor_id,
                },
            ).scalar_one()
        )
        payload_schema_id = connection.scalar(
            sa.text(
                "SELECT payload_schema_id FROM exposure_plan_revisions WHERE id = :id"
            ),
            {"id": revision_id},
        )
        assert payload_schema_id == "casefile.exposure-plan.v2"
        entry_id = int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO exposure_plan_entries (
                        project_id, casefile_id, draft_id, plan_revision_id,
                        entry_key, sequence_no, title
                    ) VALUES (
                        :project_id, :casefile_id, :draft_id, :revision_id,
                        'exposure_event_exposure_history', 1, 'First reveal'
                    ) RETURNING id
                    """
                ),
                {
                    "project_id": plan.project_id,
                    "casefile_id": plan.casefile_id,
                    "draft_id": plan.draft_id,
                    "revision_id": revision_id,
                },
            ).scalar_one()
        )
        reference_id = int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO exposure_plan_entry_refs (
                        project_id, casefile_id, draft_id, entry_id,
                        object_registry_id, ordinal
                    ) VALUES (
                        :project_id, :casefile_id, :draft_id, :entry_id,
                        :object_registry_id, 1
                    ) RETURNING id
                    """
                ),
                {
                    "project_id": plan.project_id,
                    "casefile_id": plan.casefile_id,
                    "draft_id": plan.draft_id,
                    "entry_id": entry_id,
                    "object_registry_id": object_registry_id,
                },
            ).scalar_one()
        )
        obligation_id = int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO exposure_plan_obligations (
                        project_id, casefile_id, draft_id, plan_revision_id,
                        entry_id, obligation_key, obligation_kind, level, min_distinct
                    ) VALUES (
                        :project_id, :casefile_id, :draft_id, :revision_id,
                        :entry_id, 'obligation_event_coverage',
                        'participant_coverage', 'hard', 1
                    ) RETURNING id
                    """
                ),
                {
                    "project_id": plan.project_id,
                    "casefile_id": plan.casefile_id,
                    "draft_id": plan.draft_id,
                    "revision_id": revision_id,
                    "entry_id": entry_id,
                },
            ).scalar_one()
        )
        obligation_ref_id = int(
            connection.execute(
                sa.text(
                    """
                    INSERT INTO exposure_plan_obligation_refs (
                        project_id, casefile_id, draft_id, plan_revision_id,
                        obligation_id, object_registry_id, ordinal
                    ) VALUES (
                        :project_id, :casefile_id, :draft_id, :revision_id,
                        :obligation_id, :object_registry_id, 1
                    ) RETURNING id
                    """
                ),
                {
                    "project_id": plan.project_id,
                    "casefile_id": plan.casefile_id,
                    "draft_id": plan.draft_id,
                    "revision_id": revision_id,
                    "obligation_id": obligation_id,
                    "object_registry_id": object_registry_id,
                },
            ).scalar_one()
        )
        connection.execute(
            sa.text(
                """
                UPDATE exposure_plans
                   SET revision = 1, current_revision_id = :revision_id
                 WHERE id = :plan_id
                """
            ),
            {"revision_id": revision_id, "plan_id": plan.id},
        )

        for table_name, row_id in (
            ("exposure_plan_revisions", revision_id),
            ("exposure_plan_entries", entry_id),
            ("exposure_plan_entry_refs", reference_id),
            ("exposure_plan_obligations", obligation_id),
            ("exposure_plan_obligation_refs", obligation_ref_id),
        ):
            with _expect_database_error(connection):
                connection.execute(
                    sa.text(f"UPDATE {table_name} SET id = id WHERE id = :row_id"),
                    {"row_id": row_id},
                )
            with _expect_database_error(connection):
                connection.execute(
                    sa.text(f"DELETE FROM {table_name} WHERE id = :row_id"),
                    {"row_id": row_id},
                )
