"""Typed Exposure migration compatibility helpers."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Engine


def seed_legacy_exposure_revision(engine: Engine, project_id: int) -> int:
    """Create one pre-v2 Exposure revision before the typed-obligation migration."""

    with engine.begin() as connection:
        plan = connection.execute(
            sa.text(
                """
                SELECT id, project_id, casefile_id, draft_id, created_by_user_id
                  FROM exposure_plans
                 WHERE project_id = :project_id
                 ORDER BY id
                 LIMIT 1
                """
            ),
            {"project_id": project_id},
        ).one()
        revision_id = int(
            connection.scalar(
                sa.text(
                    """
                    INSERT INTO exposure_plan_revisions (
                        project_id, casefile_id, draft_id, plan_id,
                        revision_no, created_by_user_id
                    ) VALUES (
                        :project_id, :casefile_id, :draft_id, :plan_id,
                        1, :created_by_user_id
                    ) RETURNING id
                    """
                ),
                {
                    "project_id": plan.project_id,
                    "casefile_id": plan.casefile_id,
                    "draft_id": plan.draft_id,
                    "plan_id": plan.id,
                    "created_by_user_id": plan.created_by_user_id,
                },
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO exposure_plan_entries (
                    project_id, casefile_id, draft_id, plan_revision_id,
                    entry_key, sequence_no, title, note
                ) VALUES (
                    :project_id, :casefile_id, :draft_id, :revision_id,
                    'exposure_legacy_hash', 1, 'Legacy hash', NULL
                )
                """
            ),
            {
                "project_id": plan.project_id,
                "casefile_id": plan.casefile_id,
                "draft_id": plan.draft_id,
                "revision_id": revision_id,
            },
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
        return revision_id


def assert_legacy_exposure_revision_v1(engine: Engine, revision_id: int) -> None:
    with engine.connect() as connection:
        assert connection.scalar(
            sa.text(
                "SELECT payload_schema_id FROM exposure_plan_revisions WHERE id = :id"
            ),
            {"id": revision_id},
        ) == "casefile.exposure-plan.v1"
