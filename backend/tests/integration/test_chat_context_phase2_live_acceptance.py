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
from pathlib import Path
from typing import Any

import pytest
from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_VERSION,
    CHAT_CONTEXT_PROMPT_VERSION,
)
from casefile.benchmark.chat_live_eval import _provider, _resolved_api_key
from casefile.benchmark.chat_outcome_eval import (
    ChatOutcomeTask,
    ChatOutcomeTrialVerdict,
    build_outcome_tasks,
    grade_chat_outcome,
)
from casefile.benchmark.chat_outcome_live_eval import LIVE_THRESHOLDS
from chat_outcome_canned_support import run_chat_trial
from sqlalchemy import Engine

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


def test_chat_context_live_acceptance_produces_report(
    workflow_database: tuple[Engine, int, str],
) -> None:
    if os.environ.get("CASEFILE_CHAT_CONTEXT_LIVE_ACCEPTANCE") != "1":
        pytest.skip(
            "run through scripts/acceptance-chat-context-v1.ps1 with -LiveProvider"
        )

    engine, actor_id, master_key = workflow_database
    provider_name = os.environ.get("CASEFILE_CHAT_CONTEXT_LIVE_PROVIDER", "openai")
    model_id = os.environ.get("CASEFILE_CHAT_CONTEXT_LIVE_MODEL") or "saved-credential"
    api_key = _resolved_api_key(
        provider_name,
        os.environ.get("CASEFILE_CHAT_CONTEXT_LIVE_API_KEY"),
    )
    rollout = (
        os.environ.get("CASEFILE_CHAT_CONTEXT_ROLLOUT") == CHAT_CONTEXT_POLICY_VERSION
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
            )
        except Exception as error:
            errors.append(f"{task.task_id}: {type(error).__name__}: {error}")
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
        verdict: ChatOutcomeTrialVerdict = grade_chat_outcome(
            task,
            outcome.candidate,
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
    assert not errors, errors
