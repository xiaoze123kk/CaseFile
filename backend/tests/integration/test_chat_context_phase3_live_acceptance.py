"""Phase 3 live acceptance: real-provider continuation after rolling compaction.

``scripts/acceptance-chat-context-v2.ps1 -LiveProvider deepseek`` runs this
test twice — baseline (legacy policy, full history) and rollout
(``casefile-chat-context-v2``, warmup reply compacted into Thread Memory) —
then compares the two reports. This test only executes two-turn trials and
writes a report; the baseline-vs-rollout decision stays in the script.
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
from application_services_test_support import (
    RichFixtureProvider,
    _adopt_candidate,
    _prepare_task,
)
from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_V2_VERSION,
    CHAT_CONTEXT_PROMPT_V2_VERSION,
)
from casefile.agent_runtime.models import CaseFileChatCandidate
from casefile.application.services import CaseFileService
from casefile.application.workflow_service import WorkflowService
from casefile.benchmark.chat_live_eval import _provider, _resolved_api_key
from casefile.benchmark.chat_outcome_canned import persisted_candidate_from_result
from casefile.benchmark.chat_outcome_eval import (
    ChatOutcomeExpectations,
    ChatOutcomeTask,
    ChatOutcomeTrialVerdict,
    build_outcome_tasks,
    grade_chat_outcome,
)
from casefile.benchmark.chat_outcome_live_eval import LIVE_THRESHOLDS
from casefile.data_postgres.models import AgentThreadContextState, TaskEvent, TaskRun
from casefile.worker.runtime import Worker, WorkerConfig
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres

_DEFAULT_TASK_IDS = (
    "golden-entity-question,"
    "golden-event-question,"
    "golden-issue-explanation,"
    "golden-edit-description,"
    "boundary-large-casefile"
)

_WARMUP_MESSAGE = "请通读整个卷宗，给出三到五条最关键的事实或结论。"


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
    """Move references into the collection channel they actually belong to."""

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


def _run_two_turn_trial(
    engine: Engine,
    actor_id: int,
    master_key: str,
    task: ChatOutcomeTask,
    provider_name: str,
    provider: Any,
    *,
    compacted: bool,
) -> tuple[dict[str, Any], str | None]:
    """Run warmup + task turn and grade the second persisted outcome."""

    with patch.dict(os.environ, {"CASEFILE_MASTER_KEY": master_key}):
        project_id, generation_task_id = _prepare_task(engine, actor_id)
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        generation_worker = Worker(
            factory,
            config=WorkerConfig(worker_id=f"phase3-live-gen-{task.task_id}"),
            provider_factory=lambda _task: RichFixtureProvider(),
        )
        assert generation_worker.run_once() is True
        adopted = _adopt_candidate(engine, actor_id, project_id, generation_task_id)
        draft_id = int(adopted["draft_id"])

        chat_worker = Worker(
            factory,
            config=WorkerConfig(worker_id=f"phase3-live-chat-{task.task_id}"),
            provider_factory=lambda _task: provider,
        )
        with factory() as session:
            revision = int(
                CaseFileService(session).get_draft(actor_id, project_id)["revision"]
            )
            draft_before = CaseFileService(session).get_draft(actor_id, project_id)
            casefile_before = draft_before["content"]
            workflow = WorkflowService(session)
            thread = workflow.create_agent_thread(
                actor_id,
                project_id,
                expected_draft_id=draft_id,
                expected_draft_revision=revision,
                title=None,
            )
            thread_id = int(thread["thread_id"])
            workflow.send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=revision,
                content=_WARMUP_MESSAGE,
                provider=provider_name,
                routing_hint=None,
            )
        assert chat_worker.run_once() is True

        with factory() as session:
            workflow = WorkflowService(session)
            queued = workflow.send_agent_message(
                actor_id,
                project_id,
                thread_id,
                expected_draft_id=draft_id,
                expected_draft_revision=revision,
                content=_live_message(task, casefile_before),
                provider=provider_name,
                routing_hint=task.hint,
            )
        task_run_id = int(queued["task"]["task_run_id"])
        assert chat_worker.run_once() is True

        with factory() as session:
            task_row = session.get(TaskRun, task_run_id)
            assert task_row is not None
            assert task_row.status == "succeeded", (
                task_row.status,
                task_row.error_code,
                task_row.error_details_jsonb,
            )
            frozen_input = task_row.input_jsonb
            result_jsonb = task_row.result_jsonb
            state_id = None
            compaction_events: list[dict[str, Any]] = []
            if compacted:
                thread_task_ids = list(
                    session.scalars(
                        select(TaskRun.id).where(TaskRun.agent_thread_id == thread_id)
                    )
                )
                if thread_task_ids:
                    compaction_events = [
                        {
                            "event_type": event.event_type,
                            "payload": event.payload_jsonb,
                        }
                        for event in session.scalars(
                            select(TaskEvent)
                            .where(
                                TaskEvent.task_run_id.in_(thread_task_ids),
                                TaskEvent.event_type.in_(
                                    (
                                        "context.compacted",
                                        "context.compaction_failed",
                                        "context.compaction_skipped",
                                    )
                                ),
                            )
                            .order_by(TaskEvent.task_run_id, TaskEvent.sequence_no)
                        )
                    ]
                state_row = session.scalar(
                    select(AgentThreadContextState)
                    .where(AgentThreadContextState.thread_id == thread_id)
                    .order_by(AgentThreadContextState.id.desc())
                    .limit(1)
                )
                if state_row is not None:
                    state_id = int(state_row.id)
                built_events = list(
                    session.scalars(
                        select(TaskEvent)
                        .where(
                            TaskEvent.task_run_id == task_run_id,
                            TaskEvent.event_type == "context.built",
                        )
                        .order_by(TaskEvent.sequence_no)
                    )
                )
                if built_events:
                    blocks = built_events[0].payload_jsonb.get("blocks", [])
                    if not any(
                        isinstance(block, dict) and block.get("id") == "thread_memory"
                        for block in blocks
                    ):
                        return {
                            "task_id": task.task_id,
                            "passed": False,
                            "error": "context.built has no thread_memory block",
                        }, None
        with factory() as session:
            workflow = WorkflowService(session)
            messages = workflow.list_agent_messages(actor_id, project_id, thread_id)
            assistants = [
                message for message in messages if message["role"] == "assistant"
            ]
            assert assistants and assistants[-1]["status"] == "completed"
            patch_operations = (
                assistants[-1]["patch_set"]["operations"]
                if assistants[-1]["patch_set"] is not None
                else []
            )
            draft_after = CaseFileService(session).get_draft(actor_id, project_id)
        assert isinstance(frozen_input, dict)
        assert isinstance(result_jsonb, dict)
        routing = result_jsonb.get("routing")
        validation = frozen_input.get("validation")
        casefile = frozen_input.get("casefile")
        casefile = casefile if isinstance(casefile, dict) else {}
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
        candidate = _normalized_candidate(
            persisted_candidate_from_result(result_jsonb, patch_operations),
            casefile,
        )
        verdict: ChatOutcomeTrialVerdict = grade_chat_outcome(
            dynamic_task,
            candidate,
            allow_suggestions=(
                routing.get("suggestion_policy") != "deny"
                if isinstance(routing, dict)
                else True
            ),
            thresholds=LIVE_THRESHOLDS,
            actual_intent=(
                routing.get("intent")
                if isinstance(routing, dict) and isinstance(routing.get("intent"), str)
                else "unresolved"
            ),
            route_source=(
                routing.get("route_source")
                if isinstance(routing, dict) and isinstance(routing.get("route_source"), str)
                else "unresolved"
            ),
            draft_unchanged=(
                int(draft_after["revision"]) == revision
                and draft_after["content"] == draft_before["content"]
            ),
        )
        return {
            "task_id": task.task_id,
            "passed": verdict.passed,
            "failures": list(verdict.failures),
            "answer_text": candidate.answer,
            "referenced_object_ids": list(candidate.referenced_object_ids),
            "referenced_event_ids": list(candidate.referenced_event_ids),
            "expected_object_ids": list(dynamic_task.expectations.expected_object_ids),
            "expected_event_ids": list(dynamic_task.expectations.expected_event_ids),
            "suggestion_valid_count": verdict.suggestion_valid_count,
            "suggestion_total_count": verdict.suggestion_total_count,
            "state_id": state_id,
            "compaction_events": compaction_events,
            "prompt_version": task_row.prompt_version,
        }, None


def test_chat_context_phase3_live_acceptance_produces_report(
    workflow_database: tuple[Engine, int, str],
) -> None:
    if os.environ.get("CASEFILE_CHAT_CONTEXT_LIVE_ACCEPTANCE") != "1":
        pytest.skip("run through scripts/acceptance-chat-context-v2.ps1 with -LiveProvider")

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
    rollout = os.environ.get("CASEFILE_CHAT_CONTEXT_ROLLOUT")
    compacted = rollout == CHAT_CONTEXT_POLICY_V2_VERSION
    if rollout not in {"agent-focus-v1", CHAT_CONTEXT_POLICY_V2_VERSION}:
        raise RuntimeError(f"Unsupported CASEFILE_CHAT_CONTEXT_ROLLOUT for phase 3 live: {rollout}")

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
    provider, _model_override = _provider(provider_name, api_key)
    with patch.dict(
        os.environ,
        {
            "CASEFILE_CHAT_COMPACTION_HISTORY_TOKENS": "1",
            "CASEFILE_CHAT_COMPACTION_MIN_MESSAGES": "2",
        },
    ):
        for task in _selected_tasks():
            try:
                row, error = _run_two_turn_trial(
                    engine,
                    actor_id,
                    master_key,
                    task,
                    provider_name,
                    provider,
                    compacted=compacted,
                )
            except Exception as error_instance:
                detail = traceback.format_exc()
                errors.append(
                    f"{task.task_id}: {type(error_instance).__name__}: {error_instance}\n{detail}"
                )
                rows.append(
                    {
                        "task_id": task.task_id,
                        "passed": False,
                        "error": f"{type(error_instance).__name__}: {error_instance}",
                    }
                )
                continue
            if error is not None:
                errors.append(f"{task.task_id}: {error}")
            rows.append(row)

    passed = sum(1 for row in rows if row.get("passed") is True)
    compacted_count = sum(1 for row in rows if row.get("state_id") is not None)
    report: dict[str, Any] = {
        "mode": "live-two-turn-continuation",
        "provider": provider_name,
        "model_id": model_id,
        "rollout": rollout,
        "compacted": compacted,
        "task_count": len(rows),
        "passed_count": passed,
        "pass_rate": round(passed / len(rows), 6) if rows else 0.0,
        "setup_errors": len(errors),
        "compacted_threads": compacted_count,
        "rows": rows,
        "expected_policy_version": (
            CHAT_CONTEXT_POLICY_V2_VERSION if compacted else "agent-focus-v1"
        ),
        "expected_prompt_version": (
            CHAT_CONTEXT_PROMPT_V2_VERSION if compacted else "casefile-chat-v3"
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
    assert rows, "live acceptance produced no trials"
