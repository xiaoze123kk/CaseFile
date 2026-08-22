"""L3 read-only feedback metrics for the CaseFile chat Agent.

Aggregates the human-in-the-loop lifecycle of ``agent_patch_sets`` into
adoption, rejection, undo, staleness, and post-adoption rewrite rates. No
tables are created and no rows are written; the module only SELECTs from the
existing collaboration facts.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from casefile.data_postgres.models import (
    AgentPatchOperation,
    AgentPatchSet,
    DraftOperation,
)
from casefile.data_postgres.session import create_database_engine


@dataclass(frozen=True, slots=True)
class ChatFeedbackMetricsReport:
    """One read-only snapshot of human adoption feedback."""

    patch_set_total: int
    pending: int
    applied: int
    undone: int
    rejected: int
    stale: int
    apply_rate: float
    reject_rate: float
    undo_rate: float
    stale_rate: float
    post_apply_rewrite_rate: float
    project_id: int | None = None
    status: str = "completed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "project_id": self.project_id,
            "patch_set_total": self.patch_set_total,
            "pending": self.pending,
            "applied": self.applied,
            "undone": self.undone,
            "rejected": self.rejected,
            "stale": self.stale,
            "apply_rate": self.apply_rate,
            "reject_rate": self.reject_rate,
            "undo_rate": self.undo_rate,
            "stale_rate": self.stale_rate,
            "post_apply_rewrite_rate": self.post_apply_rewrite_rate,
        }


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def run_chat_feedback_metrics(
    engine: Engine,
    *,
    project_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> ChatFeedbackMetricsReport:
    """SELECT-only aggregation over one window of AgentPatchSet lifecycles."""

    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        statement = select(AgentPatchSet)
        if project_id is not None:
            statement = statement.where(AgentPatchSet.project_id == project_id)
        if since is not None:
            statement = statement.where(AgentPatchSet.created_at >= since)
        if until is not None:
            statement = statement.where(AgentPatchSet.created_at < until)
        patch_sets = list(session.scalars(statement))

        pending = sum(patch.status == "pending" for patch in patch_sets)
        applied = sum(patch.status == "applied" for patch in patch_sets)
        undone = sum(patch.status == "undone" for patch in patch_sets)
        rejected = sum(patch.status == "rejected" for patch in patch_sets)
        stale = sum(patch.status == "stale" for patch in patch_sets)
        terminal_total = applied + undone + rejected + stale

        target_ids_by_patch: dict[int, set[int]] = {}
        applied_or_undone = [
            patch
            for patch in patch_sets
            if patch.status in {"applied", "undone"} and patch.applied_to_revision is not None
        ]
        if applied_or_undone:
            patch_set_ids = [patch.id for patch in applied_or_undone]
            operations = session.scalars(
                select(AgentPatchOperation).where(
                    AgentPatchOperation.patch_set_id.in_(patch_set_ids)
                )
            )
            for operation in operations:
                if operation.target_object_id is not None:
                    target_ids_by_patch.setdefault(operation.patch_set_id, set()).add(
                        operation.target_object_id
                    )

        rewrite_count = 0
        for patch in applied_or_undone:
            target_ids = target_ids_by_patch.get(patch.id)
            if not target_ids:
                continue
            rewritten = session.scalar(
                select(DraftOperation.id)
                .where(
                    DraftOperation.draft_id == patch.draft_id,
                    DraftOperation.casefile_object_id.in_(target_ids),
                    DraftOperation.base_revision >= patch.applied_to_revision,
                    DraftOperation.operation_type.notin_(("agent_patch_apply", "agent_patch_undo")),
                )
                .limit(1)
            )
            if rewritten is not None:
                rewrite_count += 1

    return ChatFeedbackMetricsReport(
        project_id=project_id,
        patch_set_total=len(patch_sets),
        pending=pending,
        applied=applied,
        undone=undone,
        rejected=rejected,
        stale=stale,
        apply_rate=_rate(applied + undone, terminal_total),
        reject_rate=_rate(rejected, terminal_total),
        undo_rate=_rate(undone, applied + undone),
        stale_rate=_rate(stale, len(patch_sets)),
        post_apply_rewrite_rate=_rate(rewrite_count, len(applied_or_undone)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate CaseFile chat adoption feedback from the database"
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--project-id", type=int, default=None)
    parser.add_argument("--since", default=None, help="ISO timestamp, inclusive")
    parser.add_argument("--until", default=None, help="ISO timestamp, exclusive")
    parser.add_argument("--report-path", default=None)
    arguments = parser.parse_args()

    engine = create_database_engine(arguments.database_url)
    try:
        since = None if arguments.since is None else datetime.fromisoformat(arguments.since)
        until = None if arguments.until is None else datetime.fromisoformat(arguments.until)
        report = run_chat_feedback_metrics(
            engine,
            project_id=arguments.project_id,
            since=since,
            until=until,
        )
    finally:
        engine.dispose()
    rendered = json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
    print(rendered)
    if arguments.report_path is not None:
        from pathlib import Path

        path = Path(arguments.report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")


__all__ = [
    "ChatFeedbackMetricsReport",
    "run_chat_feedback_metrics",
]

if __name__ == "__main__":
    main()
