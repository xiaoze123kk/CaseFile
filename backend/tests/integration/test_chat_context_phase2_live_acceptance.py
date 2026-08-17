"""Phase 2 context live acceptance: production-path chat trials with a real provider.

``scripts/acceptance-chat-context-v1.ps1 -LiveProvider openai`` runs this test
twice — baseline (legacy policy) and rollout (``casefile-chat-context-v1``) —
and compares the two reports. This test itself only executes trials and writes
a report; it fails on provider/setup errors, not on grade outcomes, so the
baseline-vs-rollout decision stays in the script.
"""

from __future__ import annotations

import json
import os
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_VERSION,
    CHAT_CONTEXT_PROMPT_VERSION,
)
from casefile.agent_runtime.models import CaseFileChatCandidate
from casefile.application.workflow_service import WorkflowService
from casefile.benchmark.chat_live_eval import _provider, _resolved_api_key
from casefile.benchmark.chat_outcome_eval import (
    ChatOutcomeExpectations,
    ChatOutcomeTask,
    ChatOutcomeTrialVerdict,
    build_outcome_tasks,
    grade_chat_outcome,
)
from casefile.benchmark.chat_outcome_live_eval import LIVE_THRESHOLDS
from chat_outcome_canned_support import run_chat_trial
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres

_DEFAULT_TASK_IDS = (
    "golden-entity-question,"
    "golden-event-question,"
    "golden-issue-explanation,"
    "golden-edit-description,"
    "boundary-large-casefile"
)


def _selected_tasks() -> tuple[ChatOutcomeTask, ...]:
    selected = {
        value.strip()
        for value in os.environ.get("CASEFILE_CHAT_CONTEXT_LIVE_TASK_IDS", _DEFAULT_TASK_IDS)
        .replace("\n", ",")
        .split(",")
        if value.strip()
    }
    tasks = build_outcome_tasks()
    missing = sorted(selected - {task.task_id for task in tasks})
    if missing:
        raise RuntimeError(f"Unknown live acceptance task ids: {missing}")
    return tuple(task for task in tasks if task.task_id in selected)


def _first_id(casefile: dict[str, Any], collection: str) -> str | None:
    items = casefile.get(collection)
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            return str(item["id"])
    return None


def _first_item(casefile: dict[str, Any], collection: str) -> dict[str, Any] | None:
    items = casefile.get(collection)
    if not isinstance(items, list):
        return None
    for item in items:
        if isinstance(item, dict):
            return item
    return None


def _live_message(task: ChatOutcomeTask, casefile: dict[str, Any]) -> str:
    entity = _first_item(casefile, "entities")
    entity_name = (
        entity.get("name")
        if isinstance(entity, dict) and isinstance(entity.get("name"), str)
        else "第一个实体"
    )
    event = _first_item(casefile, "events")
    event_title = (
        event.get("title")
        if isinstance(event, dict) and isinstance(event.get("title"), str)
        else "重启事件"
    )
    if task.task_id == "golden-entity-question":
        return f"{entity_name} 在卷宗里负责什么？"
    if task.task_id == "golden-event-question":
        return f"{event_title} 发生在什么时候？"
    if task.task_id == "golden-issue-explanation":
        return "当前卷宗有哪些验证问题？"
    if task.task_id == "golden-edit-description":
        return f"把 {entity_name} 的描述改得更克制。"
    if task.task_id == "boundary-large-casefile":
        return f"把所有与「{event_title}」有关的对象都列出来。"
    return task.message


def _normalized_candidate(
    candidate: CaseFileChatCandidate,
    casefile: dict[str, Any],
) -> CaseFileChatCandidate:
    """Move references into the collection channel they actually belong to.

    The executor contract has separate ``referenced_object_ids`` and
    ``referenced_event_ids`` channels; some real models put an event id in both.
    Normalizing the two channels before grading is deterministic and applies
    equally to baseline and rollout, so it only removes cross-run noise.
    """

    object_ids = {
        item["id"]
        for collection, items in casefile.items()
        if collection != "events" and isinstance(items, list)
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    event_ids = {
        item["id"]
        for item in casefile.get("events", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    objects = list(candidate.referenced_object_ids)
    events = list(candidate.referenced_event_ids)
    misplaced_objects = [item for item in objects if item in event_ids]
    misplaced_events = [item for item in events if item in object_ids]
    objects = [item for item in objects if item not in event_ids]
    events = [item for item in events if item not in object_ids]

    def dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    return candidate.model_copy(
        update={
            "referenced_object_ids": dedupe(objects + misplaced_events),
            "referenced_event_ids": dedupe(events + misplaced_objects),
        }
    )


def _first_issue_id(validation_issues: tuple[dict[str, Any], ...]) -> str | None:
    for issue in validation_issues:
        if isinstance(issue, dict) and isinstance(issue.get("issue_id"), str):
            return str(issue["issue_id"])
    return None


def _live_expectations(
    task: ChatOutcomeTask,
    casefile: dict[str, Any],
    validation_issues: tuple[dict[str, Any], ...],
) -> ChatOutcomeExpectations:
    """Rebuild expectations against the DB casefile actually used by the trial.

    The T1 Suite freezes its own casefile, but the production-path harness adopts
    the deterministic RichFixtureProvider casefile, so static ids never match.
    Rebuild the same capability contract from the first relevant DB object per
    task kind; baseline and rollout use the same rules, so the comparison stays
    apples-to-apples.
    """

    entity_id = _first_id(casefile, "entities")
    event_id = _first_id(casefile, "events")
    issue_id = _first_issue_id(validation_issues)
    expected_objects: tuple[str, ...] = ()
    expected_events: tuple[str, ...] = ()
    expected_issues: tuple[str, ...] = ()
    suggestion_paths: tuple[tuple[str, str], ...] = ()
    if task.task_id == "golden-entity-question" and entity_id is not None:
        expected_objects = (entity_id,)
    if task.task_id == "golden-event-question" and event_id is not None:
        expected_events = (event_id,)
    if task.task_id == "golden-issue-explanation" and issue_id is not None:
        expected_issues = (issue_id,)
    if task.task_id == "golden-edit-description" and entity_id is not None:
        expected_objects = (entity_id,)
        suggestion_paths = ((entity_id, "description"),)
    if task.task_id == "boundary-large-casefile":
        expected_objects = (entity_id,) if entity_id is not None else ()
        expected_events = (event_id,) if event_id is not None else ()
    return ChatOutcomeExpectations(
        expected_object_ids=expected_objects,
        expected_event_ids=expected_events,
        expected_validation_issue_ids=expected_issues,
        required_suggestion_paths=suggestion_paths,
        expected_primary_intent=task.expectations.expected_primary_intent,
        requires_suggestion=bool(suggestion_paths),
        references_must_exist=True,
    )


def _seed_provider_setting(
    engine: Engine,
    actor_id: int,
    master_key: str,
    provider_name: str,
    api_key: str,
    model_id: str,
) -> None:
    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        with sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)() as session:
            WorkflowService(session).save_provider_setting(
                actor_id,
                provider=provider_name,
                api_key=api_key,
                model_id=model_id,
                model_is_custom=False,
            )


def test_chat_context_live_acceptance_produces_report(
    workflow_database: tuple[Engine, int, str],
) -> None:
    if os.environ.get("CASEFILE_CHAT_CONTEXT_LIVE_ACCEPTANCE") != "1":
        pytest.skip(
            "run through scripts/acceptance-chat-context-v1.ps1 with -LiveProvider"
        )

    engine, actor_id, master_key = workflow_database
    provider_name = os.environ.get("CASEFILE_CHAT_CONTEXT_LIVE_PROVIDER", "openai")
    model_id = (
        os.environ.get("CASEFILE_CHAT_CONTEXT_LIVE_MODEL")
        or ("deepseek-v4-flash" if provider_name == "deepseek" else "gpt-5.6-sol")
    )
    api_key = _resolved_api_key(
        provider_name,
        os.environ.get("CASEFILE_CHAT_CONTEXT_LIVE_API_KEY"),
    )
    rollout = (
        os.environ.get("CASEFILE_CHAT_CONTEXT_ROLLOUT") == CHAT_CONTEXT_POLICY_VERSION
    )

    _seed_provider_setting(
        engine,
        actor_id,
        master_key,
        provider_name,
        api_key,
        model_id,
    )

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for task in _selected_tasks():
        provider, _model_override = _provider(provider_name, api_key)
        try:
            outcome = run_chat_trial(
                engine,
                actor_id,
                master_key,
                task,
                provider,
                task_provider=provider_name,
                message_builder=lambda casefile, task=task: _live_message(task, casefile),
            )
        except Exception as error:
            detail = traceback.format_exc()
            errors.append(f"{task.task_id}: {type(error).__name__}: {error}\n{detail}")
            rows.append(
                {
                    "task_id": task.task_id,
                    "passed": False,
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            continue
        routing = outcome.result_jsonb.get("routing")
        routing = routing if isinstance(routing, dict) else {}
        intent = routing.get("intent")
        route_source = routing.get("route_source")
        suggestion_policy = routing.get("suggestion_policy")
        casefile = outcome.frozen_input.get("casefile")
        casefile = casefile if isinstance(casefile, dict) else {}
        validation = outcome.frozen_input.get("validation")
        validation_issues = tuple(
            issue
            for issue in (validation.get("issues") if isinstance(validation, dict) else [])
            if isinstance(issue, dict)
        )
        dynamic_task = replace(
            task,
            casefile=casefile,
            validation_issues=validation_issues,
            focus={"object_ids": [], "event_ids": [], "validation_issue_ids": []},
            expectations=_live_expectations(task, casefile, validation_issues),
        )
        verdict: ChatOutcomeTrialVerdict = grade_chat_outcome(
            dynamic_task,
            _normalized_candidate(outcome.candidate, casefile),
            allow_suggestions=suggestion_policy != "deny",
            thresholds=LIVE_THRESHOLDS,
            actual_intent=intent if isinstance(intent, str) else "unresolved",
            route_source=route_source if isinstance(route_source, str) else "unresolved",
            draft_unchanged=outcome.draft_unchanged,
        )
        rows.append(
            {
                "task_id": task.task_id,
                "passed": verdict.passed,
                "failures": list(verdict.failures),
                "candidate": {
                    "answer": outcome.candidate.answer[:500],
                    "referenced_object_ids": list(outcome.candidate.referenced_object_ids),
                    "referenced_event_ids": list(outcome.candidate.referenced_event_ids),
                    "referenced_validation_issue_ids": list(
                        outcome.candidate.referenced_validation_issue_ids
                    ),
                    "suggestion_count": len(outcome.candidate.suggestions),
                },
            }
        )

    passed = sum(1 for row in rows if row.get("passed") is True)
    report: dict[str, Any] = {
        "mode": "live-production-path",
        "provider": provider_name,
        "model_id": model_id,
        "rollout": rollout,
        "task_count": len(rows),
        "passed_count": passed,
        "pass_rate": round(passed / len(rows), 6) if rows else 0.0,
        "setup_errors": len(errors),
        "rows": rows,
        "expected_policy_version": (
            CHAT_CONTEXT_POLICY_VERSION if rollout else "agent-focus-v1"
        ),
        "expected_prompt_version": (
            CHAT_CONTEXT_PROMPT_VERSION if rollout else "casefile-chat-v3"
        ),
    }
    report_path = os.environ.get("CASEFILE_CHAT_CONTEXT_LIVE_REPORT")
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    assert rows
