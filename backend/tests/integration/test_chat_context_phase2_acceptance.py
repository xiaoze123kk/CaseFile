"""Phase 2 context acceptance: M1 canned 30-task suite under rollout.

Runs the full production path with ``CASEFILE_CHAT_CONTEXT_ROLLOUT`` enabled
(the acceptance script sets it before launching pytest), then verifies three
release gates:

1. all 30 T1 Tasks still pass the deterministic Outcome Grader;
2. every chat TaskRun froze ``casefile-chat-v4`` + ``casefile-chat-context-v1``
   and published a v1 ``context.built`` manifest without a fallback guardrail;
3. aggregate executor input tokens from ``context.built`` drop by >= 50%
   against the legacy full-injection render of the same frozen request.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_VERSION,
    CHAT_CONTEXT_PROMPT_VERSION,
    LEGACY_CONTEXT_POLICY_VERSION,
    build_chat_context_manifest,
)
from casefile.agent_runtime.models import chat_routing_payload_as_dict
from casefile.agent_runtime.prompt import render_chat_executor_prompt
from casefile.benchmark.chat_outcome_eval import build_outcome_tasks
from casefile.data_postgres.models import TaskEvent, TaskRun
from chat_outcome_canned_support import run_canned_trial
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.postgres

_MIN_TOKEN_REDUCTION = 0.5
_REQUIRED_CONTEXT_BLOCKS = frozenset(
    {
        "casefile_skeleton",
        "focus_objects",
        "thread_history",
        "validation_issues",
        "author_message",
    }
)


def _context_event_payload(
    session_factory: sessionmaker,
    task_run_id: int,
    event_type: str,
) -> dict[str, Any] | None:
    with session_factory() as session:
        rows = session.scalars(
            select(TaskEvent)
            .where(
                TaskEvent.task_run_id == task_run_id,
                TaskEvent.event_type == event_type,
            )
            .order_by(TaskEvent.sequence_no.desc())
        ).all()
    if not rows:
        return None
    return rows[0].payload_jsonb


def _legacy_tokens(
    factory: sessionmaker,
    task_run_id: int,
    provider_request: Any,
) -> tuple[int, dict[str, Any], str]:
    with factory() as session:
        task_row = session.get(TaskRun, task_run_id)
        assert task_row is not None
        frozen_input = task_row.input_jsonb
        input_hash = task_row.input_hash
    request = replace(
        provider_request,
        assembled_input=None,
        prompt_version="casefile-chat-v3",
    )
    _instructions, legacy_input = render_chat_executor_prompt(request)
    result = build_chat_context_manifest(
        policy_version=LEGACY_CONTEXT_POLICY_VERSION,
        frozen_input=frozen_input,
        input_hash=input_hash,
        routing=chat_routing_payload_as_dict(request),
        prebuilt_input=legacy_input,
    )
    return result.manifest.total_tokens, frozen_input, input_hash


def test_m1_canned_context_rollout_acceptance(
    workflow_database: tuple[Engine, int, str],
) -> None:
    if os.environ.get("CASEFILE_CHAT_CONTEXT_ROLLOUT") != CHAT_CONTEXT_POLICY_VERSION:
        pytest.skip("run through scripts/acceptance-chat-context-v1.ps1 with rollout enabled")

    engine, actor_id, master_key = workflow_database
    tasks = build_outcome_tasks()
    rows: list[dict[str, Any]] = []
    rollout_tokens = 0
    legacy_tokens = 0
    failed: list[str] = []

    for task in tasks:
        outcome = run_canned_trial(engine, actor_id, master_key, task)
        if not outcome.verdict.passed:
            failed.append(f"{task.task_id}: {outcome.verdict.failures}")
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        with factory() as session:
            task_row = session.get(TaskRun, outcome.chat_task_id)
            assert task_row is not None
            frozen_input = task_row.input_jsonb
            assert task_row.prompt_version == CHAT_CONTEXT_PROMPT_VERSION
            assert (
                frozen_input.get("context_policy_version") == CHAT_CONTEXT_POLICY_VERSION
            )
        built = _context_event_payload(factory, outcome.chat_task_id, "context.built")
        guardrail = _context_event_payload(
            factory,
            outcome.chat_task_id,
            "context.guardrail",
        )
        assert built is not None, (task.task_id, "missing context.built event")
        assert built["policy_version"] == CHAT_CONTEXT_POLICY_VERSION
        assert _REQUIRED_CONTEXT_BLOCKS <= {block["id"] for block in built["blocks"]}
        assert guardrail is None, (task.task_id, "unexpected context.guardrail fallback")

        legacy, frozen_input, _input_hash = _legacy_tokens(
            factory,
            outcome.chat_task_id,
            outcome.provider_request,
        )
        rollout = int(built["total_tokens"])
        legacy_tokens += legacy
        rollout_tokens += rollout
        ratio = rollout / legacy if legacy > 0 else 0.0
        rows.append(
            {
                "task_id": task.task_id,
                "rollout_tokens": rollout,
                "legacy_tokens": legacy,
                "ratio": ratio,
                "prompt_version": task_row.prompt_version,
                "context_policy_version": frozen_input.get("context_policy_version"),
                "passed": outcome.verdict.passed,
            }
        )

    aggregate_ratio = rollout_tokens / legacy_tokens if legacy_tokens > 0 else 0.0
    report: dict[str, Any] = {
        "mode": "m1-canned-rollout",
        "policy_version": CHAT_CONTEXT_POLICY_VERSION,
        "prompt_version": CHAT_CONTEXT_PROMPT_VERSION,
        "task_count": len(rows),
        "passed_count": len(rows) - len(failed),
        "aggregate_token_ratio": aggregate_ratio,
        "token_reduction": 1.0 - aggregate_ratio,
        "rollout_tokens": rollout_tokens,
        "legacy_tokens": legacy_tokens,
        "rows": rows,
        "gates": {
            "m1_pass_rate": len(failed) == 0,
            "token_reduction_gte_50pct": aggregate_ratio <= 1.0 - _MIN_TOKEN_REDUCTION,
            "fallback_free": True,
        },
        "status": (
            "passed"
            if not failed and aggregate_ratio <= 1.0 - _MIN_TOKEN_REDUCTION
            else "failed"
        ),
    }
    report_path = os.environ.get("CASEFILE_CHAT_CONTEXT_ACCEPTANCE_REPORT")
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    assert not failed, failed
    assert aggregate_ratio <= 1.0 - _MIN_TOKEN_REDUCTION, (
        f"aggregate token ratio {aggregate_ratio:.3f} exceeds acceptance bound "
        f"{1.0 - _MIN_TOKEN_REDUCTION:.3f}"
    )
