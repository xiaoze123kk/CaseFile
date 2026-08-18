"""Export Audit PatchSet lifecycle decisions back into the fixture library.

Every ``logic_audit`` PatchSet ends in exactly one human decision: ``applied``
(采纳), ``rejected`` (拒绝), or ``undone`` (撤销). This module reads those
append-only lifecycle facts and renders a replayable JSON pack that
``build_outcome_tasks_from_audit_feedback`` converts into outcome Eval Tasks:

* applied  → the model should reproduce the accepted finding/evidence and
  propose the accepted patch;
* rejected/undone → the model should emit a zero-suggestion, zero-finding
  no-op for that frozen input (the human already refused that patch).

No tables are created and no lifecycle rows are mutated; the exporter only
SELECTs from ``agent_patch_sets``, ``agent_patch_operations``, and
``task_runs``.

Usage:
    python -m casefile.benchmark.audit_feedback_export \
        --database-url "$DATABASE_URL" \
        --out reports/audit-feedback-fixtures.json
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile.data_postgres.models import (
    AgentPatchOperation,
    AgentPatchSet,
    TaskRun,
)
from casefile.data_postgres.session import (
    create_database_engine,
    create_session_factory,
)

AUDIT_FEEDBACK_EXPORT_SCHEMA = "casefile-chat-audit-feedback-export-v1"
AUDIT_DECISIONS = ("applied", "rejected", "undone")


def _free_text_hint() -> dict[str, Any]:
    return {"entrypoint": "free_text", "preset_id": None}


def _focus_from_input(input_jsonb: dict[str, Any]) -> dict[str, Any]:
    focus = input_jsonb.get("focus")
    if not isinstance(focus, dict):
        return {"object_ids": [], "event_ids": [], "validation_issue_ids": []}
    return {
        "object_ids": list(focus.get("object_ids") or []),
        "event_ids": list(focus.get("event_ids") or []),
        "validation_issue_ids": list(focus.get("validation_issue_ids") or []),
    }


def _operations_from_rows(operations: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for operation in operations or []:
        target_object_id = operation.get("target_object_id")
        field_path = operation.get("field_path")
        if not isinstance(target_object_id, str) or not isinstance(field_path, str):
            continue
        normalized.append(
            {
                "operation_id": operation.get("operation_id"),
                "target_object_id": target_object_id,
                "field_path": field_path,
                "new_value": operation.get("new_value"),
                "reason": operation.get("reason"),
                "decision": operation.get("decision"),
            }
        )
    return normalized


def audit_feedback_fixture_from_payload(
    *,
    fixture_id: str,
    decision: str,
    task_run_id: int,
    patch_set_id: int,
    input_jsonb: dict[str, Any] | None,
    result_jsonb: dict[str, Any] | None,
    patch_operations: list[dict[str, Any]] | None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build one replayable feedback fixture, or None when unusable.

    The function is pure (no SQLAlchemy objects) so the unit tests can feed it
    hand-rolled rows and the integration test can feed it persisted rows.
    """

    if decision not in AUDIT_DECISIONS:
        return None
    frozen = input_jsonb if isinstance(input_jsonb, dict) else {}
    result = result_jsonb if isinstance(result_jsonb, dict) else {}
    casefile = frozen.get("casefile")
    message = frozen.get("message")
    if not isinstance(casefile, dict) or not casefile:
        return None
    if not isinstance(message, str) or not message.strip():
        return None
    hint = frozen.get("routing_hint")
    if not isinstance(hint, dict) or not hint.get("entrypoint"):
        hint = _free_text_hint()
    validation = frozen.get("validation")
    issues: list[dict[str, Any]] = []
    if isinstance(validation, dict) and isinstance(validation.get("issues"), list):
        issues = [issue for issue in validation["issues"] if isinstance(issue, dict)]
    findings = result.get("audit_findings") or []
    findings = [finding for finding in findings if isinstance(finding, dict)]
    operations = _operations_from_rows(patch_operations)
    fixture_source = dict(source or {})
    fixture_source.update(
        {
            "task_run_id": task_run_id,
            "patch_set_id": patch_set_id,
            "decision": decision,
        }
    )
    return {
        "schema_version": AUDIT_FEEDBACK_EXPORT_SCHEMA,
        "fixture_id": fixture_id,
        "decision": decision,
        "task_run_id": task_run_id,
        "patch_set_id": patch_set_id,
        "message": message,
        "hint": hint,
        "focus": _focus_from_input(frozen),
        "casefile": casefile,
        "validation_issues": issues,
        "answer": str(result.get("answer") or ""),
        "referenced_object_ids": list(result.get("referenced_object_ids") or []),
        "referenced_event_ids": list(result.get("referenced_event_ids") or []),
        "referenced_validation_issue_ids": list(
            result.get("referenced_validation_issue_ids") or []
        ),
        "audit_findings": findings,
        "patch_operations": operations,
        "source": fixture_source,
    }


def export_audit_feedback_fixtures(
    factory: sessionmaker[Session],
    *,
    project_id: int | None = None,
    decisions: tuple[str, ...] = AUDIT_DECISIONS,
) -> dict[str, Any]:
    """Read terminal PatchSet lifecycles and build an audit feedback fixture pack."""

    with factory() as session:
        statement = (
            select(AgentPatchSet, TaskRun)
            .join(TaskRun, TaskRun.id == AgentPatchSet.task_run_id)
            .where(AgentPatchSet.status.in_(decisions))
            .order_by(AgentPatchSet.id)
        )
        if project_id is not None:
            statement = statement.where(AgentPatchSet.project_id == project_id)
        rows = list(session.execute(statement).all())
        patch_set_ids = [int(patch.id) for patch, _task in rows]
        operations_by_patch: dict[int, list[AgentPatchOperation]] = {}
        if patch_set_ids:
            for operation in session.scalars(
                select(AgentPatchOperation)
                .where(AgentPatchOperation.patch_set_id.in_(patch_set_ids))
                .order_by(AgentPatchOperation.patch_set_id, AgentPatchOperation.ordinal)
            ):
                operations_by_patch.setdefault(int(operation.patch_set_id), []).append(
                    operation
                )

    fixtures: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for patch, task in rows:
        input_jsonb = task.input_jsonb if isinstance(task.input_jsonb, dict) else None
        result_jsonb = task.result_jsonb if isinstance(task.result_jsonb, dict) else None
        operations = [
            {
                "operation_id": operation.operation_id,
                "target_object_id": operation.target_object_id,
                "field_path": operation.field_path,
                "new_value": operation.new_value_jsonb,
                "reason": operation.reason,
                "decision": operation.decision,
            }
            for operation in operations_by_patch.get(int(patch.id), ())
        ]
        source: dict[str, Any] = {
            "project_id": int(patch.project_id),
            "reason_summary": patch.reason_summary,
            "applied_at": _iso(patch.applied_at),
            "undone_at": _iso(patch.undone_at),
            "created_at": _iso(patch.created_at),
        }
        fixture = audit_feedback_fixture_from_payload(
            fixture_id=f"audit-feedback-{int(task.id)}-{int(patch.id)}",
            decision=str(patch.status),
            task_run_id=int(task.id),
            patch_set_id=int(patch.id),
            input_jsonb=input_jsonb,
            result_jsonb=result_jsonb,
            patch_operations=operations,
            source=source,
        )
        if fixture is None:
            sources.append(
                {
                    "task_run_id": int(task.id),
                    "patch_set_id": int(patch.id),
                    "project_id": int(patch.project_id),
                    "decision": str(patch.status),
                    "skipped_reason": "frozen_input_or_result_unusable",
                }
            )
            continue
        fixtures.append(fixture)
        sources.append(fixture["source"])
    return {
        "schema_version": AUDIT_FEEDBACK_EXPORT_SCHEMA,
        "exported_at": datetime.now(UTC).isoformat(),
        "fixture_count": len(fixtures),
        "skipped_count": len(sources) - len(fixtures),
        "fixtures": fixtures,
        "sources": sources,
    }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def load_audit_feedback_fixtures(path: Path | str) -> tuple[dict[str, Any], ...]:
    """Load a feedback export (or a bare fixture JSON array) into fixture dicts."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("fixtures"), list):
        entries = payload["fixtures"]
    elif isinstance(payload, list):
        entries = payload
    else:
        raise ValueError("audit feedback export must contain a fixtures array")
    fixtures: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        decision = entry.get("decision")
        if decision not in AUDIT_DECISIONS:
            message = (
                f"audit feedback fixture {entry.get('fixture_id')!r} "
                f"has invalid decision {decision!r}"
            )
            raise ValueError(message)
        fixtures.append(entry)
    return tuple(fixtures)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export audit PatchSet lifecycle decisions as outcome Eval fixtures"
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--project-id", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()

    engine = create_database_engine(arguments.database_url)
    payload = export_audit_feedback_fixtures(
        create_session_factory(engine),
        project_id=arguments.project_id,
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if arguments.out is not None:
        arguments.out.parent.mkdir(parents=True, exist_ok=True)
        arguments.out.write_text(rendered + "\n", encoding="utf-8")
    engine.dispose()


if __name__ == "__main__":
    main()

__all__ = [
    "AUDIT_DECISIONS",
    "AUDIT_FEEDBACK_EXPORT_SCHEMA",
    "audit_feedback_fixture_from_payload",
    "export_audit_feedback_fixtures",
    "load_audit_feedback_fixtures",
]
