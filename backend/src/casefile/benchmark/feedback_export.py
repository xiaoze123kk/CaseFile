"""Export ``router.feedback`` TaskEvents back into chat-router Eval fixtures.

The frontend records one human correction per assistant message. This script
reads those append-only events and produces a JSON fixture pack that
``chat_live_eval --extra-fixtures`` (or a future curated suite) can consume.

Usage:
    python -m casefile.benchmark.feedback_export \
        --database-url "$DATABASE_URL" \
        --out reports/feedback-fixtures.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile.agent_runtime.chat_routing import EXECUTION_PROFILES
from casefile.benchmark.chat_router_eval import ChatRouterFixture
from casefile.data_postgres.models import TaskEvent, TaskRun
from casefile.data_postgres.session import create_database_engine, create_session_factory

FEEDBACK_EXPORT_SCHEMA = "casefile-chat-feedback-export-v1"

_DANGEROUS_PAIRS = {
    ("edit_request", "question"),
    ("question", "edit_request"),
    ("unsupported_action", "edit_request"),
    ("validate_request", "analysis"),
    ("explain_issue", "edit_request"),
}


def prompt_component_for_intent(intent: str) -> str:
    profile = EXECUTION_PROFILES.get(intent)
    if not isinstance(profile, dict):
        return "chat"
    component = profile.get("prompt_component")
    return str(component) if isinstance(component, str) else "chat"


def fixture_from_feedback(
    *,
    event_id: int,
    task_run_id: int,
    payload: dict[str, Any],
    input_jsonb: dict[str, Any] | None,
) -> tuple[ChatRouterFixture, dict[str, Any]] | None:
    """Build one replayable fixture from one feedback event, or None if unusable."""

    correct_intent = payload.get("correct_intent")
    if not isinstance(correct_intent, str) or correct_intent not in EXECUTION_PROFILES:
        return None
    original = payload.get("original")
    original = original if isinstance(original, dict) else {}
    query = original.get("query")
    if not isinstance(query, str) or not query.strip():
        return None
    routing_hint = original.get("routing_hint")
    hint = (
        routing_hint
        if isinstance(routing_hint, dict) and routing_hint.get("entrypoint")
        else {"entrypoint": "free_text", "preset_id": None}
    )
    frozen = input_jsonb if isinstance(input_jsonb, dict) else {}
    frozen_focus = frozen.get("focus")
    focus: dict[str, Any]
    if isinstance(frozen_focus, dict):
        focus = {
            "object_ids": frozen_focus.get("object_ids") or [],
            "event_ids": frozen_focus.get("event_ids") or [],
            "validation_issue_ids": frozen_focus.get("validation_issue_ids") or [],
        }
    else:
        focus = {"object_ids": [], "event_ids": [], "validation_issue_ids": []}
    history: tuple[dict[str, str], ...] = tuple(
        {
            "role": str(item["role"]),
            "content": str(item["content"]),
        }
        for item in frozen.get("history", [])
        if isinstance(item, dict)
        and isinstance(item.get("role"), str)
        and isinstance(item.get("content"), str)
    )
    casefile = frozen.get("casefile")
    validation = frozen.get("validation")
    issues = (
        validation.get("issues", [])
        if isinstance(validation, dict) and isinstance(validation.get("issues"), list)
        else []
    )
    observed_intent = (
        str(original["route"].get("intent"))
        if isinstance(original.get("route"), dict)
        and original["route"].get("intent") is not None
        else None
    )
    dangerous_pair = (
        (correct_intent, observed_intent)
        if observed_intent is not None
        and (correct_intent, observed_intent) in _DANGEROUS_PAIRS
        else None
    )
    fixture = ChatRouterFixture(
        fixture_id=f"feedback-{task_run_id}-{event_id}",
        message=query,
        hint=hint,
        expected_primary_intent=correct_intent,
        expected_prompt_component=prompt_component_for_intent(correct_intent),
        focus=focus,
        history=history,
        dangerous_pair=dangerous_pair,
        casefile=casefile if isinstance(casefile, dict) else None,
        validation_issues=tuple(
            issue for issue in issues if isinstance(issue, dict)
        ),
    )
    source = {
        "event_id": event_id,
        "task_run_id": task_run_id,
        "project_id": payload.get("project_id"),
        "message_id": payload.get("message_id"),
        "correct_intent": correct_intent,
        "note": payload.get("note"),
        "observed_intent": observed_intent,
        "observed_route_source": (
            str(original["route"].get("route_source"))
            if isinstance(original.get("route"), dict)
            and original["route"].get("route_source") is not None
            else None
        ),
        "dangerous_pair": list(dangerous_pair) if dangerous_pair else None,
    }
    return fixture, source


def fixture_to_json(fixture: ChatRouterFixture) -> dict[str, Any]:
    payload = asdict(fixture)
    payload["history"] = list(fixture.history)
    payload["validation_issues"] = list(fixture.validation_issues or ())
    return payload


def export_feedback_fixtures(
    factory: sessionmaker[Session],
    *,
    project_id: int | None = None,
) -> dict[str, Any]:
    """Read every ``router.feedback`` event and build an Eval fixture pack."""

    with factory() as session:
        query = (
            select(TaskEvent, TaskRun)
            .join(TaskRun, TaskRun.id == TaskEvent.task_run_id)
            .where(TaskEvent.event_type == "router.feedback")
            .order_by(TaskEvent.id)
        )
        if project_id is not None:
            query = query.where(TaskEvent.project_id == project_id)
        rows = session.execute(query).all()

    fixtures: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for event, task in rows:
        payload = event.payload_jsonb if isinstance(event.payload_jsonb, dict) else {}
        converted = fixture_from_feedback(
            event_id=int(event.id),
            task_run_id=int(task.id),
            payload=payload,
            input_jsonb=task.input_jsonb,
        )
        if converted is None:
            sources.append(
                {
                    "event_id": int(event.id),
                    "task_run_id": int(task.id),
                    "project_id": int(event.project_id),
                    "skipped_reason": "correct_intent_missing_or_invalid",
                    "note": payload.get("note"),
                }
            )
            continue
        fixture, source = converted
        source["project_id"] = int(event.project_id)
        fixtures.append(fixture_to_json(fixture))
        sources.append(source)
    return {
        "schema_version": FEEDBACK_EXPORT_SCHEMA,
        "exported_at": datetime.now(UTC).isoformat(),
        "fixture_count": len(fixtures),
        "skipped_count": len(sources) - len(fixtures),
        "fixtures": fixtures,
        "sources": sources,
    }


def load_exported_fixtures(path: Path | str) -> tuple[ChatRouterFixture, ...]:
    """Load a feedback export (or a bare JSON fixture array) into Eval fixtures."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("fixtures"), list):
        entries = payload["fixtures"]
    elif isinstance(payload, list):
        entries = payload
    else:
        raise ValueError("feedback export must contain a fixtures array")
    fixtures: list[ChatRouterFixture] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fixture = ChatRouterFixture(
            fixture_id=str(entry["fixture_id"]),
            message=str(entry["message"]),
            hint=entry["hint"],
            expected_primary_intent=str(entry["expected_primary_intent"]),
            expected_prompt_component=str(entry["expected_prompt_component"]),
            focus=entry.get("focus"),
            history=tuple(entry.get("history") or ()),
            dangerous_pair=tuple(entry["dangerous_pair"])
            if isinstance(entry.get("dangerous_pair"), list)
            else None,
            casefile=entry.get("casefile"),
            validation_issues=tuple(entry.get("validation_issues") or ()),
        )
        fixtures.append(fixture)
    return tuple(fixtures)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export router.feedback TaskEvents as chat Eval fixtures"
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--project-id", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    arguments = parser.parse_args()

    engine = create_database_engine(arguments.database_url)
    payload = export_feedback_fixtures(
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
    "FEEDBACK_EXPORT_SCHEMA",
    "export_feedback_fixtures",
    "fixture_from_feedback",
    "fixture_to_json",
    "load_exported_fixtures",
    "prompt_component_for_intent",
]
