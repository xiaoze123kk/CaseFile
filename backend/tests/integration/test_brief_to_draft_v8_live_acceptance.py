"""Opt-in API/Worker/PostgreSQL acceptance for versioned brief-to-draft calls.

The test deliberately runs only when ``CASEFILE_RUN_LIVE_ACCEPTANCE=1``.  It
copies an already encrypted configured provider credential from the local
development database into the disposable ``*_test`` database, so the real
runtime credential path is exercised without exposing or serialising the API
key in the report.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from casefile.api.app import create_app
from casefile.contracts import validate_casefile
from casefile.data_postgres.models import (
    AgentModelCall,
    AgentStepRun,
    CanonVersion,
    DraftSnapshot,
    TaskAttempt,
    User,
    UserProviderSetting,
)
from casefile.worker.runtime import Worker, WorkerConfig

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.live,
]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED_COMPONENTS = {
    "context_pack_builder",
    "case_blueprint_planner",
    "story_world",
    "evidence_logic",
    "resolution_governance",
    "reference_linker",
    "casefile_compiler",
    "quality_repair_gate",
}
_STRATEGIES = ("structure_first", "atmosphere_first", "reasoning_first")
_STRUCTURAL_FAILURE_LAYERS = {
    "pydantic",
    "structured_output",
    "reference_linker",
    "casefile_schema",
    "quality_gate",
    "description_gate",
    "frozen_context",
}
@dataclass(frozen=True, slots=True)
class LiveAcceptanceConfig:
    source_database_url: str
    test_database_url: str
    master_key: str
    provider: str
    prompt_version: str
    repeats: int
    report_path: Path | None


def test_live_brief_to_draft_runtime_acceptance() -> None:
    config = _live_config()
    report: dict[str, Any] = {
        "suite": config.prompt_version.replace("-", "_"),
        "evaluation_scope": "api_worker_postgres",
        "release_gate_eligible": config.repeats == 30,
        "provider": config.provider,
        "runs_requested": config.repeats,
        "runs_attempted": 0,
        "successful_runs": 0,
        "successful_run_details": [],
        "failed_runs": [],
        "invariant_violations": [],
        "execution_metrics": {},
        "status": "running",
    }
    engine: Engine | None = None
    try:
        _reset_test_database(config.test_database_url)
        engine = create_engine(config.test_database_url)
        actor_user_id, model_id = _copy_configured_provider_setting(
            config.source_database_url,
            engine,
            provider=config.provider,
        )
        factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": config.test_database_url,
                "CASEFILE_MASTER_KEY": config.master_key,
            },
        ):
            app = create_app(config.test_database_url)
            worker = Worker(
                factory,
                config=WorkerConfig(
                    worker_id=f"{config.prompt_version}-live-acceptance"
                ),
            )
            with (
                patch(
                    "casefile.application.workflow_service.prompt_version_for_task",
                    return_value=config.prompt_version,
                ),
                TestClient(app) as client,
            ):
                _run_acceptance_suite(
                    client,
                    factory,
                    worker,
                    actor_user_id=actor_user_id,
                    provider=config.provider,
                    model_id=model_id,
                    repeats=config.repeats,
                    report=report,
                )
        _summarize_execution_metrics(report)
        _record_global_write_boundaries(factory, report)
        report["status"] = _report_status(report, expected_runs=config.repeats)
    except Exception as error:
        report["status"] = "blocked"
        report["harness_error"] = type(error).__name__
        raise
    finally:
        _write_report(config.report_path, report)
        if engine is not None:
            engine.dispose()
        _reset_test_database(config.test_database_url, teardown=True)

    assert report["status"] == "passed", json.dumps(report, ensure_ascii=False, indent=2)


def _live_config() -> LiveAcceptanceConfig:
    if os.getenv("CASEFILE_RUN_LIVE_ACCEPTANCE") != "1":
        pytest.skip("Set CASEFILE_RUN_LIVE_ACCEPTANCE=1 to call a real model provider.")
    source_database_url = os.getenv("DATABASE_URL", "").strip()
    test_database_url = os.getenv("CASEFILE_TEST_DATABASE_URL", "").strip()
    master_key = os.getenv("CASEFILE_MASTER_KEY", "").strip()
    if not source_database_url or not test_database_url or not master_key:
        pytest.fail(
            "Live acceptance requires DATABASE_URL, CASEFILE_TEST_DATABASE_URL, "
            "and CASEFILE_MASTER_KEY."
        )
    test_database_name = make_url(test_database_url).database or ""
    if not test_database_name.endswith("_test"):
        pytest.fail("CASEFILE_TEST_DATABASE_URL must target a disposable *_test database.")
    try:
        repeats = int(os.getenv("CASEFILE_LIVE_ACCEPTANCE_REPEATS", "30"))
    except ValueError:
        pytest.fail("CASEFILE_LIVE_ACCEPTANCE_REPEATS must be an integer.")
    if not 1 <= repeats <= 100:
        pytest.fail("CASEFILE_LIVE_ACCEPTANCE_REPEATS must be between 1 and 100.")
    report_value = os.getenv("CASEFILE_LIVE_ACCEPTANCE_REPORT_PATH", "").strip()
    prompt_version = os.getenv(
        "CASEFILE_LIVE_ACCEPTANCE_PROMPT_VERSION", "brief-to-draft-v9"
    ).strip()
    if prompt_version not in {"brief-to-draft-v8", "brief-to-draft-v9"}:
        pytest.fail("Live acceptance prompt version must be brief-to-draft-v8 or v9.")
    return LiveAcceptanceConfig(
        source_database_url=source_database_url,
        test_database_url=test_database_url,
        master_key=master_key,
        provider=os.getenv("CASEFILE_LIVE_ACCEPTANCE_PROVIDER", "deepseek").strip(),
        prompt_version=prompt_version,
        repeats=repeats,
        report_path=Path(report_value) if report_value else None,
    )


def _reset_test_database(database_url: str, *, teardown: bool = False) -> None:
    database_name = make_url(database_url).database or ""
    if not database_name.endswith("_test"):
        raise RuntimeError("Live acceptance refuses to reset a database that is not named *_test.")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()
    if teardown:
        return
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("prepend_sys_path", str(BACKEND_ROOT / "src"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    with patch.dict(os.environ, {"DATABASE_URL": database_url}):
        command.upgrade(config, "head")


def _copy_configured_provider_setting(
    source_database_url: str,
    target_engine: Engine,
    *,
    provider: str,
) -> tuple[int, str]:
    source_engine = create_engine(source_database_url)
    try:
        with source_engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT
                        setting.user_id,
                        setting.model_id,
                        setting.model_is_custom,
                        setting.config_version,
                        setting.secret_ciphertext,
                        setting.secret_nonce,
                        setting.key_version,
                        setting.secret_last_four,
                        setting.default_budget_jsonb
                    FROM user_provider_settings AS setting
                    WHERE setting.provider = :provider
                      AND setting.credential_status <> 'deleted'
                      AND setting.secret_ciphertext IS NOT NULL
                      AND setting.secret_nonce IS NOT NULL
                    ORDER BY setting.updated_at DESC, setting.id DESC
                    LIMIT 1
                    """
                ),
                {"provider": provider},
            ).mappings().one_or_none()
    finally:
        source_engine.dispose()
    if row is None:
        pytest.fail(f"No active {provider} credential is configured in DATABASE_URL.")
    user_id = int(row["user_id"])
    ciphertext = row["secret_ciphertext"]
    nonce = row["secret_nonce"]
    key_version = row["key_version"]
    last_four = row["secret_last_four"]
    if not isinstance(ciphertext, bytes) or not isinstance(nonce, bytes):
        pytest.fail("Configured provider credential material is malformed.")
    if not isinstance(key_version, int) or not isinstance(last_four, str):
        pytest.fail("Configured provider credential metadata is malformed.")
    factory = sessionmaker(bind=target_engine, expire_on_commit=False, autoflush=False)
    with factory.begin() as session:
        session.add(User(id=user_id, display_name="Live Acceptance Owner"))
        setting = UserProviderSetting(
            user_id=user_id,
            provider=provider,
            model_id=str(row["model_id"]),
            model_is_custom=bool(row["model_is_custom"]),
            config_version=max(1, int(row["config_version"])),
            secret_ciphertext=ciphertext,
            secret_nonce=nonce,
            key_version=key_version,
            secret_last_four=last_four,
            credential_status="unverified",
            default_budget_jsonb=(
                dict(row["default_budget_jsonb"])
                if isinstance(row["default_budget_jsonb"], dict)
                else {}
            ),
        )
        session.add(setting)
        session.flush()
        return user_id, setting.model_id


def _run_acceptance_suite(
    client: TestClient,
    factory: sessionmaker[Session],
    worker: Worker,
    *,
    actor_user_id: int,
    provider: str,
    model_id: str,
    repeats: int,
    report: dict[str, Any],
) -> None:
    headers = {"X-CaseFile-User-Id": str(actor_user_id)}
    for run_index in range(repeats):
        strategy = _STRATEGIES[run_index % len(_STRATEGIES)]
        task_id, project_id = _queue_generation_task(
            client,
            headers=headers,
            run_index=run_index,
            strategy=strategy,
            provider=provider,
        )
        started = time.perf_counter()
        did_run = worker.run_once()
        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        report["runs_attempted"] = int(report["runs_attempted"]) + 1
        if not did_run:
            report["failed_runs"].append(
                {
                    "run": run_index + 1,
                    "strategy": strategy,
                    "task_run_id": task_id,
                    "failure_class": "worker_no_claim",
                }
            )
            report["status"] = "blocked"
            break
        task_response = client.get(
            f"/api/v1/projects/{project_id}/tasks/{task_id}",
            headers=headers,
        )
        if task_response.status_code != 200:
            report["failed_runs"].append(
                {
                    "run": run_index + 1,
                    "strategy": strategy,
                    "task_run_id": task_id,
                    "failure_class": "task_read_failed",
                    "http_status": task_response.status_code,
                }
            )
            report["status"] = "blocked"
            break
        task = task_response.json()
        task_record = {
            "run": run_index + 1,
            "strategy": strategy,
            "task_run_id": task_id,
            "status": task["status"],
            "latency_ms": latency_ms,
            "model_id": model_id,
            "execution": _task_execution_metrics(factory, task_id),
        }
        if task["status"] == "succeeded":
            report["successful_runs"] = int(report["successful_runs"]) + 1
            report["successful_run_details"].append(task_record)
            violations = _successful_task_violations(
                client,
                factory,
                headers=headers,
                project_id=project_id,
                task=task,
            )
            if violations:
                report["invariant_violations"].extend(
                    [{**task_record, "violation": item} for item in violations]
                )
            continue
        failure = {
            **task_record,
            "failure_class": _failure_class(task, factory),
            "failure_details": _failure_details(task, factory),
            "diagnostics_complete": _diagnostics_complete(task),
            "components": _failed_components(task),
        }
        report["failed_runs"].append(failure)
        report["invariant_violations"].extend(
            {**task_record, "violation": item}
            for item in _failed_task_write_boundary_violations(factory, project_id, task_id, task)
        )
        if failure["failure_class"] in {
            "provider_authentication",
            "provider_rate_limited",
            "provider_unavailable",
        }:
            report["status"] = "blocked"
            break


def _queue_generation_task(
    client: TestClient,
    *,
    headers: dict[str, str],
    run_index: int,
    strategy: str,
    provider: str,
) -> tuple[int, int]:
    created = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "title": f"Prompt Live Acceptance {run_index + 1}",
            "description": None,
            "profile": {},
        },
    )
    assert created.status_code == 201, created.text
    project_id = int(created.json()["id"])
    source = client.post(
        f"/api/v1/projects/{project_id}/sources",
        headers=headers,
        json={
            "source_kind": "human_original",
            "content_text": "一艘渡轮每晚都会返回同一座码头，航海记录在事故前被人修改。",
        },
    )
    assert source.status_code == 201, source.text
    source_record_id = int(source.json()["source_record_id"])
    brief = client.put(
        f"/api/v1/projects/{project_id}/brief",
        headers=headers,
        json={"expected_revision": 1, "content": _brief(source_record_id)},
    )
    assert brief.status_code == 200, brief.text
    confirmed = client.post(
        f"/api/v1/projects/{project_id}/brief/confirm",
        headers=headers,
        json={"expected_revision": brief.json()["draft_revision"]},
    )
    assert confirmed.status_code == 201, confirmed.text
    queued = client.post(
        f"/api/v1/projects/{project_id}/tasks/generate",
        headers=headers,
        json={
            "brief_version_id": confirmed.json()["brief_version_id"],
            "expected_draft_revision": 1,
            "provider": provider,
            "candidate_strategy": strategy,
        },
    )
    assert queued.status_code == 202, queued.text
    return int(queued.json()["task_run_id"]), project_id


def _brief(source_record_id: int) -> dict[str, object]:
    return {
        "source_record_ids": [source_record_id],
        "creative_intent": "围绕午夜回航建立可验证的目标无关推理卷宗。",
        "reasoning_proposition": "是谁修改了航海记录，回航保护机制为何触发？",
        "resolution_mode": "author_anchored",
        "conclusion_mode": "unique",
        "author_answer": "大副修改了记录，欠压保护触发了回航。",
        "author_anchors": [
            {"anchor_id": "anchor_live_first_officer", "statement": "大副修改了航海记录。"}
        ],
        "boundary_text": "必须保持唯一因果答案。",
        "creative_constraints": [
            {
                "constraint_id": "constraint_live_unique",
                "statement": "因果答案必须唯一。",
                "strength": "hard",
            }
        ],
    }


def _successful_task_violations(
    client: TestClient,
    factory: sessionmaker[Session],
    *,
    headers: dict[str, str],
    project_id: int,
    task: dict[str, Any],
) -> list[str]:
    violations: list[str] = []
    if task.get("result_snapshot_id") is not None:
        violations.append("candidate_was_automatically_adopted")
    component_ids = {step.get("component_id") for step in task.get("component_steps", [])}
    if component_ids != _EXPECTED_COMPONENTS:
        violations.append("component_step_coverage_incomplete")
    latest_steps = _latest_steps_by_component(task)
    if any(step.get("status") not in {"succeeded", "reused"} for step in latest_steps.values()):
        violations.append("successful_task_has_unresolved_component")
    failed_steps = [
        step for step in task["component_steps"] if step.get("status") == "failed"
    ]
    if failed_steps and not _diagnostics_complete_for_steps(failed_steps):
        violations.append("successful_task_has_incomplete_repair_diagnostic")
    draft = client.get(f"/api/v1/projects/{project_id}/draft", headers=headers)
    if draft.status_code != 200 or draft.json().get("content") is not None:
        violations.append("generation_mutated_draft_before_adoption")
    candidates = client.get(f"/api/v1/projects/{project_id}/draft-candidates", headers=headers)
    if candidates.status_code != 200 or task["task_run_id"] not in {
        item.get("task_run_id") for item in candidates.json()
    }:
        violations.append("successful_candidate_not_queryable")
    with factory() as session:
        attempt = session.scalar(
            select(TaskAttempt).where(
                TaskAttempt.task_run_id == task["task_run_id"],
                TaskAttempt.status == "succeeded",
            )
        )
        steps = list(
            session.scalars(
                select(AgentStepRun).where(AgentStepRun.task_run_id == task["task_run_id"])
            )
        )
        calls = list(
            session.scalars(
                select(AgentModelCall).where(AgentModelCall.task_run_id == task["task_run_id"])
            )
        )
    if attempt is None or not isinstance(attempt.candidate_jsonb, dict):
        violations.append("successful_task_missing_immutable_candidate")
    else:
        try:
            validate_casefile(attempt.candidate_jsonb)
        except Exception:
            violations.append("persisted_candidate_failed_casefile_validation")
    if {step.component_id for step in steps} != _EXPECTED_COMPONENTS:
        violations.append("agent_step_runs_not_persisted")
    if len(calls) < 4 or not any(call.status == "succeeded" for call in calls):
        violations.append("agent_model_calls_not_persisted")
    stream = client.get(
        f"/api/v1/projects/{project_id}/tasks/{task['task_run_id']}/stream",
        headers=headers,
    )
    if (
        stream.status_code != 200
        or "event: agent.step.completed" not in stream.text
        or "event: agent.model_call.completed" not in stream.text
    ):
        violations.append("component_events_not_projected_to_sse")
    return violations


def _failed_task_write_boundary_violations(
    factory: sessionmaker[Session],
    project_id: int,
    task_id: int,
    task: dict[str, Any],
) -> list[str]:
    violations: list[str] = []
    if task.get("result_snapshot_id") is not None:
        violations.append("failed_task_created_snapshot")
    with factory() as session:
        attempt = session.scalar(select(TaskAttempt).where(TaskAttempt.task_run_id == task_id))
        snapshot_count = int(
            session.scalar(
                select(func.count())
                .select_from(DraftSnapshot)
                .where(DraftSnapshot.project_id == project_id)
            )
            or 0
        )
    if attempt is not None and attempt.candidate_jsonb is not None:
        violations.append("failed_task_persisted_candidate")
    if snapshot_count:
        violations.append("failed_task_created_draft_snapshot")
    return violations


def _record_global_write_boundaries(
    factory: sessionmaker[Session], report: dict[str, Any]) -> None:
    with factory() as session:
        snapshot_count = int(session.scalar(select(func.count()).select_from(DraftSnapshot)) or 0)
        canon_count = int(session.scalar(select(func.count()).select_from(CanonVersion)) or 0)
    report["write_boundaries"] = {
        "draft_snapshots": snapshot_count,
        "canon_versions": canon_count,
    }
    if snapshot_count:
        report["invariant_violations"].append({"violation": "automatic_snapshot_write"})
    if canon_count:
        report["invariant_violations"].append({"violation": "automatic_canon_write"})


def _task_execution_metrics(
    factory: sessionmaker[Session], task_run_id: int
) -> dict[str, dict[str, Any]]:
    """Collect retry and timing telemetry without serialising prompts or outputs."""

    with factory() as session:
        steps = list(
            session.scalars(
                select(AgentStepRun).where(AgentStepRun.task_run_id == task_run_id)
            )
        )
        calls = list(
            session.scalars(
                select(AgentModelCall).where(AgentModelCall.task_run_id == task_run_id)
            )
        )

    call_summary: dict[str, Any] = {
        "total": len(calls),
        "succeeded": sum(call.status == "succeeded" for call in calls),
        "failed": sum(call.status == "failed" for call in calls),
        "running": sum(call.status == "running" for call in calls),
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "by_component": {},
        "by_protocol": {},
        "failed_by_error_code": {},
        "failed_by_issue_code": {},
    }
    by_component = call_summary["by_component"]
    by_protocol = call_summary["by_protocol"]
    assert isinstance(by_component, dict)
    assert isinstance(by_protocol, dict)
    failed_by_error_code = call_summary["failed_by_error_code"]
    failed_by_issue_code = call_summary["failed_by_issue_code"]
    assert isinstance(failed_by_error_code, dict)
    assert isinstance(failed_by_issue_code, dict)
    for call in calls:
        component = _metric_counter(by_component, call.prompt_component_id)
        protocol = _metric_counter(by_protocol, call.output_protocol)
        for counter in (component, protocol):
            counter["total"] += 1
            counter[call.status] += 1
        for token_key in ("input_tokens", "output_tokens", "total_tokens"):
            value = call.usage_jsonb.get(token_key)
            if isinstance(value, int):
                call_summary[token_key] = int(call_summary[token_key]) + value
                component[token_key] += value
                protocol[token_key] += value
        if call.status == "failed":
            _increment_named_count(
                failed_by_error_code, call.error_code or "unknown_error"
            )
            for issue in call.issues_jsonb:
                if isinstance(issue, dict):
                    _increment_named_count(
                        failed_by_issue_code,
                        str(issue.get("code") or "unknown_issue"),
                    )

    step_summary: dict[str, Any] = {
        "total": len(steps),
        "succeeded": sum(step.status == "succeeded" for step in steps),
        "failed": sum(step.status == "failed" for step in steps),
        "reused": sum(step.status == "reused" for step in steps),
        "running": sum(step.status == "running" for step in steps),
        "duration_ms_total": 0.0,
        "by_component": {},
    }
    step_components = step_summary["by_component"]
    assert isinstance(step_components, dict)
    for step in steps:
        component = _metric_counter(step_components, step.component_id)
        component["total"] += 1
        component[step.status] += 1
        duration_ms = _duration_ms(step.started_at, step.finished_at)
        if duration_ms is not None:
            step_summary["duration_ms_total"] = round(
                float(step_summary["duration_ms_total"]) + duration_ms, 3
            )
            component["duration_ms_total"] = round(
                float(component["duration_ms_total"]) + duration_ms, 3
            )
            component["duration_ms_max"] = max(
                float(component["duration_ms_max"]), duration_ms
            )
    return {"model_calls": call_summary, "component_steps": step_summary}


def _metric_counter(container: dict[str, Any], key: str) -> dict[str, Any]:
    current = container.get(key)
    if isinstance(current, dict):
        return current
    counter: dict[str, Any] = {
        "total": 0,
        "succeeded": 0,
        "failed": 0,
        "reused": 0,
        "running": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "duration_ms_total": 0.0,
        "duration_ms_max": 0.0,
    }
    container[key] = counter
    return counter


def _increment_named_count(container: dict[str, Any], key: str, amount: int = 1) -> None:
    container[key] = int(container.get(key, 0)) + amount


def _duration_ms(started_at: datetime, finished_at: datetime | None) -> float | None:
    if finished_at is None:
        return None
    return round((finished_at - started_at).total_seconds() * 1000, 3)


def _summarize_execution_metrics(report: dict[str, Any]) -> None:
    details = report.get("successful_run_details", [])
    if not isinstance(details, list):
        return
    latencies = sorted(
        float(item["latency_ms"])
        for item in details
        if isinstance(item, dict) and isinstance(item.get("latency_ms"), (int, float))
    )
    summary: dict[str, Any] = {
        "successful_run_latency_ms": _latency_summary(latencies),
        "model_calls": _aggregate_metric_group(details, "model_calls"),
        "component_steps": _aggregate_metric_group(details, "component_steps"),
    }
    report["execution_metrics"] = summary


def _latency_summary(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": round(values[0], 3),
        "p50": round(values[ceil(len(values) * 0.5) - 1], 3),
        "p95": round(values[ceil(len(values) * 0.95) - 1], 3),
        "max": round(values[-1], 3),
        "mean": round(sum(values) / len(values), 3),
    }


def _aggregate_metric_group(
    details: list[object], group_name: str
) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "total": 0,
        "succeeded": 0,
        "failed": 0,
        "reused": 0,
        "running": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "duration_ms_total": 0.0,
        "by_component": {},
        "by_protocol": {},
        "failed_by_error_code": {},
        "failed_by_issue_code": {},
    }
    for detail in details:
        if not isinstance(detail, dict):
            continue
        execution = detail.get("execution")
        if not isinstance(execution, dict):
            continue
        group = execution.get(group_name)
        if not isinstance(group, dict):
            continue
        for key in (
            "total",
            "succeeded",
            "failed",
            "reused",
            "running",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "duration_ms_total",
        ):
            value = group.get(key)
            if isinstance(value, (int, float)):
                totals[key] = totals[key] + value
        for bucket_name in ("by_component", "by_protocol"):
            bucket = group.get(bucket_name)
            output_bucket = totals[bucket_name]
            if not isinstance(bucket, dict) or not isinstance(output_bucket, dict):
                continue
            for name, values in bucket.items():
                if not isinstance(name, str) or not isinstance(values, dict):
                    continue
                target = _metric_counter(output_bucket, name)
                for key, value in values.items():
                    if isinstance(value, (int, float)):
                        if key == "duration_ms_max":
                            target[key] = max(float(target.get(key, 0)), float(value))
                        else:
                            target[key] = target.get(key, 0) + value
        for bucket_name in ("failed_by_error_code", "failed_by_issue_code"):
            bucket = group.get(bucket_name)
            output_bucket = totals[bucket_name]
            if not isinstance(bucket, dict) or not isinstance(output_bucket, dict):
                continue
            for name, value in bucket.items():
                if isinstance(name, str) and isinstance(value, int):
                    _increment_named_count(output_bucket, name, value)
    if isinstance(totals["duration_ms_total"], float):
        totals["duration_ms_total"] = round(float(totals["duration_ms_total"]), 3)
    return totals


def _failed_components(task: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "component_id": step.get("component_id"),
            "failure_layer": step.get("failure_layer"),
            "schema_id": step.get("schema_id"),
            "issues": [
                {
                    "code": issue.get("code"),
                    "path": issue.get("path"),
                    "message": str(issue.get("message", ""))[:240],
                }
                for issue in step.get("issues", [])
                if isinstance(issue, dict)
            ],
        }
        for step in task.get("component_steps", [])
        if step.get("status") == "failed"
    ]


def _diagnostics_complete(task: dict[str, Any]) -> bool:
    failed_steps = [
        step for step in task.get("component_steps", []) if step.get("status") == "failed"
    ]
    return _diagnostics_complete_for_steps(failed_steps)


def _diagnostics_complete_for_steps(failed_steps: list[dict[str, Any]]) -> bool:
    if not failed_steps:
        return False
    for step in failed_steps:
        layer = step.get("failure_layer")
        if not step.get("component_id") or not layer:
            return False
        if layer not in _STRUCTURAL_FAILURE_LAYERS:
            continue
        if not step.get("schema_id"):
            return False
        issues = step.get("issues")
        if not isinstance(issues, list) or not issues:
            return False
        if any(not isinstance(issue, dict) or "path" not in issue for issue in issues):
            return False
    return True


def _latest_steps_by_component(task: dict[str, Any]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for step in task.get("component_steps", []):
        component_id = step.get("component_id")
        if not isinstance(component_id, str):
            continue
        current = latest.get(component_id)
        if current is None or int(step.get("execution_no") or 0) >= int(
            current.get("execution_no") or 0
        ):
            latest[component_id] = step
    return latest


def _failure_class(task: dict[str, Any], factory: sessionmaker[Session]) -> str:
    details = _failure_details(task, factory)
    issue_messages = [
        str(issue.get("message", ""))
        for step in task.get("component_steps", [])
        for issue in step.get("issues", [])
        if isinstance(issue, dict)
    ]
    message = " ".join(
        [
            str(details.get("exception_type", "")),
            str(details.get("message", "")),
            *issue_messages,
        ]
    ).lower()
    if "authentication" in message or "unauthorized" in message or "401" in message:
        return "provider_authentication"
    if "rate limit" in message or "ratelimit" in message or "429" in message:
        return "provider_rate_limited"
    if any(marker in message for marker in ("connection", "timeout", "502", "503")):
        return "provider_unavailable"
    return "candidate_or_runtime_failure"


def _failure_details(task: dict[str, Any], factory: sessionmaker[Session]) -> dict[str, str]:
    task_id = int(task["task_run_id"])
    with factory() as session:
        attempt = session.scalar(select(TaskAttempt).where(TaskAttempt.task_run_id == task_id))
    details = attempt.error_details_jsonb if attempt is not None else {}
    return {
        "error_code": str(task.get("error_code") or ""),
        "exception_type": str(details.get("exception_type") or ""),
        "message": _safe_report_message(str(details.get("message") or "")),
    }


def _safe_report_message(message: str) -> str:
    return message.replace("sk-", "[REDACTED]-")[:240]


def _report_status(report: dict[str, Any], *, expected_runs: int) -> str:
    if report.get("status") == "blocked":
        return "blocked"
    if int(report["runs_attempted"]) != expected_runs:
        return "blocked"
    required_successes = 27 if expected_runs == 30 else expected_runs
    if int(report["successful_runs"]) < required_successes:
        return "failed"
    if report["invariant_violations"]:
        return "failed"
    if any(not item["diagnostics_complete"] for item in report["failed_runs"]):
        return "failed"
    return "passed"


def _write_report(path: Path | None, report: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
