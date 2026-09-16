"""Task-scoped access to immutable draft operation history."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile.data_postgres.models import DraftOperation, Project


def read_revision_history(
    sessions: sessionmaker[Session],
    *,
    actor_id: int,
    project_id: int,
    casefile_id: int,
    draft_id: int,
    frozen_revision: int,
    from_revision: int,
    to_revision: int,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    if not 1 <= from_revision <= to_revision <= frozen_revision:
        return {"error": "revision_range_outside_frozen_draft"}
    offset, limit = max(0, offset), max(1, min(limit, 10))
    with sessions() as session:
        owner = session.scalar(
            select(Project.id).where(Project.id == project_id, Project.owner_user_id == actor_id)
        )
        if owner is None:
            return {"error": "revision_history_unavailable"}
        rows = list(
            session.scalars(
                select(DraftOperation)
                .where(
                    DraftOperation.project_id == project_id,
                    DraftOperation.casefile_id == casefile_id,
                    DraftOperation.draft_id == draft_id,
                    DraftOperation.result_revision > from_revision,
                    DraftOperation.result_revision <= to_revision,
                )
                .order_by(DraftOperation.sequence_no)
                .offset(offset)
                .limit(limit + 1)
            )
        )
        return {
            "scope": "recorded_operations_not_net_diff",
            "draft_id": draft_id,
            "from_revision": from_revision,
            "to_revision": to_revision,
            "frozen_revision": frozen_revision,
            "next_offset": offset + limit if len(rows) > limit else None,
            "results": [
                {
                    "operation_id": row.id,
                    "sequence_no": row.sequence_no,
                    "base_revision": row.base_revision,
                    "result_revision": row.result_revision,
                    "operation_type": row.operation_type,
                    "field_path": row.field_path,
                    "before": row.old_value_jsonb,
                    "after": row.new_value_jsonb,
                    "actor_kind": row.actor_kind,
                }
                for row in rows[:limit]
            ],
        }
