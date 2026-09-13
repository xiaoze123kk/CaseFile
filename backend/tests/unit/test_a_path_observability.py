"""Deterministic observability regressions for the complete A path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from casefile.agent_runtime.observability import (
    brief_semantic_coverage,
    standardize_generation_cost_usage,
)
from casefile.api.app import create_app
from casefile.api.dependencies import get_actor_user_id, get_session
from casefile.application.a_path_metrics import (
    APathAttemptFact,
    APathEventFact,
    APathMetricsService,
    APathModelCallFact,
    APathOperationFact,
    APathTaskFact,
    derive_a_path_metrics,
)


def test_semantic_coverage_observes_every_frozen_brief_dimension() -> None:
    brief = {
        "author_answer": "大副改写记录，欠压保护触发回航。",
        "author_anchors": [
            {"statement": "大副改写了航海记录。"},
            {"statement": "保护机制避免推进系统失控。"},
        ],
        "creative_constraints": [
            {"statement": "因果答案必须唯一。", "strength": "hard"},
            {"statement": "码头氛围可以更阴冷。", "strength": "soft"},
        ],
        "creative_intent": "围绕午夜回航建立可验证的推理卷宗。",
        "reasoning_proposition": "是谁修改记录，回航为何触发？",
        "core_selling_points": ["航海日志互相咬合", "机械线索可复核"],
        "content_outline": ["建立午夜异象", "核对航海记录", "揭示保护机制"],
    }
    candidate = {
        "title": "午夜回航",
        "description": "围绕午夜回航建立可验证的推理卷宗。",
        "resolution_specs": [
            {
                "question": "是谁修改记录，回航为何触发？",
                "answer": "大副改写记录，欠压保护触发回航。",
            }
        ],
        "claims": [
            {"statement": "大副改写了航海记录。"},
            {"statement": "保护机制避免推进系统失控。"},
        ],
        "constraints": [
            {"statement": "因果答案必须唯一。"},
            {"statement": "码头氛围可以更阴冷。"},
        ],
        "structure_locks": [
            {"description": "航海日志互相咬合，机械线索可复核。"},
            {"description": "建立午夜异象，核对航海记录，揭示保护机制。"},
        ],
    }

    metrics = brief_semantic_coverage(brief, candidate)

    assert metrics["observational_only"] is True
    assert metrics["overall"] == {
        "applicable_fields": 8,
        "covered_fields": 8,
        "source_items": 12,
        "covered_items": 12,
        "coverage_rate": 1.0,
        "item_match_rate": 1.0,
    }
    assert metrics["fields"]["hard_constraints"]["covered_items"] == 1
    assert metrics["fields"]["soft_constraints"]["covered_items"] == 1


def test_semantic_coverage_marks_absent_optional_dimensions_not_applicable() -> None:
    metrics = brief_semantic_coverage(
        {
            "author_answer": None,
            "author_anchors": [],
            "creative_constraints": [],
            "creative_intent": "失真的时间记录",
            "reasoning_proposition": "是谁改写记录？",
            "core_selling_points": [],
            "content_outline": [],
        },
        {"description": "失真的时间记录"},
    )

    assert metrics["fields"]["author_answer"] == {
        "applicable": False,
        "source_items": 0,
        "covered_items": 0,
        "coverage_rate": None,
        "item_match_rate": None,
    }
    assert metrics["overall"]["applicable_fields"] == 2
    assert metrics["overall"]["covered_fields"] == 1


def test_cost_usage_normalization_keeps_cache_reasoning_and_pricing_unknown() -> None:
    metrics = standardize_generation_cost_usage(
        {
            "requests": 5,
            "input_tokens": 120,
            "output_tokens": 30,
            "cached_tokens": 40,
            "reasoning_tokens": 12,
        },
        provider="deepseek",
        model_id="deepseek-v4-flash",
    )

    assert metrics["tokens"] == {
        "input": 120,
        "cached_input": 40,
        "uncached_input": 80,
        "output": 30,
        "reasoning_output": 12,
        "total": 150,
        "total_source": "derived",
    }
    assert metrics["monetary_cost"] == {
        "available": False,
        "amount": None,
        "currency": None,
        "reason": "provider_pricing_not_frozen_with_task_run",
    }
    assert metrics["consistency"] == {
        "reported_total_matches_input_plus_output": True,
        "cached_input_within_input": True,
        "reasoning_output_within_output": True,
    }


def test_a_path_funnel_derives_adoption_and_follow_up_edits_without_new_tables() -> None:
    started = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    metrics = derive_a_path_metrics(
        tasks=[
            APathTaskFact(
                task_run_id=10,
                status="succeeded",
                result_snapshot_id=101,
                usage={"requests": 4, "total_tokens": 100},
                created_at=started,
                completed_at=started + timedelta(seconds=20),
            ),
            APathTaskFact(
                task_run_id=11,
                status="succeeded",
                result_snapshot_id=None,
                usage={"requests": 3, "total_tokens": 80},
                created_at=started,
                completed_at=started + timedelta(seconds=10),
            ),
            APathTaskFact(
                task_run_id=12,
                status="failed",
                result_snapshot_id=None,
                usage={"requests": 1, "total_tokens": 10},
                created_at=started,
                completed_at=started + timedelta(seconds=6),
            ),
        ],
        events=[
            APathEventFact(task_run_id=10, event_type="task.succeeded"),
            APathEventFact(task_run_id=10, event_type="candidate.adopted"),
            APathEventFact(task_run_id=11, event_type="task.succeeded"),
            APathEventFact(task_run_id=12, event_type="task.failed"),
        ],
        operations=[
            APathOperationFact(
                draft_id=101,
                sequence_no=1,
                operation_type="agent_adopt_brief_candidate",
                new_value={"task_run_id": 10},
            ),
            APathOperationFact(
                draft_id=101,
                sequence_no=2,
                operation_type="replace",
                new_value={"title": "人工修订"},
            ),
            APathOperationFact(
                draft_id=102,
                sequence_no=3,
                operation_type="agent_adopt_brief_candidate",
                new_value={"task_run_id": 11},
            ),
        ],
    )

    assert metrics["funnel"] == {
        "task_runs": 3,
        "generated_candidates": 2,
        "adopted_candidates": 2,
        "post_adoption_edited_candidates": 1,
        "generation_success_rate": 0.6667,
        "adoption_rate": 1.0,
        "post_adoption_edit_rate": 0.5,
    }
    assert metrics["post_adoption"] == {
        "adoption_operations": 2,
        "edit_operations": 1,
        "edited_adoptions": 1,
        "operation_types": {"replace": 1},
    }
    assert metrics["usage_totals"] == {
        "requests": 8,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 190,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
    }
    assert metrics["completion_latency_ms"] == {
        "observed_tasks": 3,
        "average": 12000.0,
        "maximum": 20000.0,
    }
    assert metrics["unobservable_stages"][0]["stage"] == "candidate_previewed"


def test_a_path_post_adoption_edits_stay_with_their_own_draft_window() -> None:
    started = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    metrics = derive_a_path_metrics(
        tasks=[
            APathTaskFact(
                task_run_id=10,
                status="succeeded",
                result_snapshot_id=101,
                usage={},
                created_at=started,
                completed_at=started + timedelta(seconds=10),
            ),
            APathTaskFact(
                task_run_id=11,
                status="succeeded",
                result_snapshot_id=102,
                usage={},
                created_at=started,
                completed_at=started + timedelta(seconds=20),
            ),
        ],
        events=[],
        operations=[
            APathOperationFact(
                draft_id=101,
                sequence_no=1,
                operation_type="agent_adopt_brief_candidate",
                new_value={"task_run_id": 10},
            ),
            APathOperationFact(
                draft_id=101,
                sequence_no=2,
                operation_type="replace",
                new_value={"title": "工作稿 A 首次修订"},
            ),
            APathOperationFact(
                draft_id=102,
                sequence_no=3,
                operation_type="agent_adopt_brief_candidate",
                new_value={"task_run_id": 11},
            ),
            # 切回旧稿后的编辑仍属于 A，不能让最新采用的 B 变成“已续编”。
            APathOperationFact(
                draft_id=101,
                sequence_no=4,
                operation_type="replace",
                new_value={"title": "工作稿 A 再次修订"},
            ),
        ],
    )

    assert metrics["funnel"]["post_adoption_edited_candidates"] == 1
    assert metrics["funnel"]["post_adoption_edit_rate"] == 0.5
    assert metrics["post_adoption"] == {
        "adoption_operations": 2,
        "edit_operations": 2,
        "edited_adoptions": 1,
        "operation_types": {"replace": 2},
    }


def test_a_path_usage_sums_immutable_attempts_without_double_counting_task_run() -> None:
    started = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    metrics = derive_a_path_metrics(
        tasks=[
            APathTaskFact(
                task_run_id=10,
                status="succeeded",
                result_snapshot_id=None,
                # TaskRun mirrors only the latest attempt and must not be added again.
                usage={"requests": 2, "total_tokens": 200},
                created_at=started,
                completed_at=started + timedelta(seconds=20),
            ),
            APathTaskFact(
                task_run_id=11,
                status="failed",
                result_snapshot_id=None,
                # Legacy/fallback task with no persisted attempt fact.
                usage={"requests": 1, "total_tokens": 40},
                created_at=started,
                completed_at=started + timedelta(seconds=5),
            ),
        ],
        attempts=[
            APathAttemptFact(
                attempt_id=101,
                task_run_id=10,
                attempt_no=1,
                usage={"requests": 1, "total_tokens": 100},
            ),
            APathAttemptFact(
                attempt_id=102,
                task_run_id=10,
                attempt_no=2,
                usage={"requests": 2, "total_tokens": 200},
            ),
            # An unrelated attempt must not leak into this project's metrics.
            APathAttemptFact(
                attempt_id=991,
                task_run_id=99,
                attempt_no=1,
                usage={"requests": 9, "total_tokens": 900},
            ),
        ],
        events=[],
        operations=[],
    )

    assert metrics["usage_totals"] == {
        "requests": 4,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 340,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
    }
    assert metrics["usage_observations"] == {
        "task_attempts": 2,
        "model_calls": 0,
        "model_call_attempts": 0,
        "model_call_usage_snapshots": 0,
        "task_attempt_fallbacks": 2,
        "task_run_fallbacks": 1,
    }


def test_a_path_usage_prefers_latest_model_call_snapshot_per_component_execution() -> None:
    started = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    metrics = derive_a_path_metrics(
        tasks=[
            APathTaskFact(
                task_run_id=10,
                status="succeeded",
                result_snapshot_id=None,
                usage={"requests": 5, "total_tokens": 500},
                created_at=started,
                completed_at=started + timedelta(seconds=20),
            ),
            APathTaskFact(
                task_run_id=11,
                status="failed",
                result_snapshot_id=None,
                usage={},
                created_at=started,
                completed_at=started + timedelta(seconds=10),
            ),
            APathTaskFact(
                task_run_id=12,
                status="cancelled",
                result_snapshot_id=None,
                usage={},
                created_at=started,
                completed_at=started + timedelta(seconds=8),
            ),
            APathTaskFact(
                task_run_id=13,
                status="failed",
                result_snapshot_id=None,
                usage={"requests": 1, "total_tokens": 40},
                created_at=started,
                completed_at=started + timedelta(seconds=5),
            ),
            APathTaskFact(
                task_run_id=14,
                status="failed",
                result_snapshot_id=None,
                usage={"requests": 7, "total_tokens": 700},
                created_at=started,
                completed_at=started + timedelta(seconds=5),
            ),
        ],
        attempts=[
            APathAttemptFact(
                attempt_id=101,
                task_run_id=10,
                attempt_no=1,
                usage={"requests": 5, "total_tokens": 500},
            ),
            APathAttemptFact(attempt_id=111, task_run_id=11, attempt_no=1, usage={}),
            APathAttemptFact(attempt_id=121, task_run_id=12, attempt_no=1, usage={}),
            APathAttemptFact(
                attempt_id=141,
                task_run_id=14,
                attempt_no=1,
                usage={"requests": 1, "total_tokens": 20},
            ),
        ],
        model_calls=[
            # Structured-output retries are cumulative on the latest usage-bearing
            # row for one component execution, so call 1 must not be added again.
            APathModelCallFact(
                task_run_id=10,
                task_attempt_id=101,
                agent_step_run_id=1001,
                call_no=1,
                usage={"requests": 1, "total_tokens": 50},
            ),
            APathModelCallFact(
                task_run_id=10,
                task_attempt_id=101,
                agent_step_run_id=1001,
                call_no=2,
                usage={"requests": 2, "total_tokens": 200},
            ),
            APathModelCallFact(
                task_run_id=10,
                task_attempt_id=101,
                agent_step_run_id=1002,
                call_no=1,
                usage={"requests": 1, "total_tokens": 50},
            ),
            # These durable calls survive even though their attempts later fail or
            # are cancelled and the Attempt/TaskRun mirrors remain empty.
            APathModelCallFact(
                task_run_id=11,
                task_attempt_id=111,
                agent_step_run_id=1101,
                call_no=1,
                usage={"requests": 2, "total_tokens": 80},
            ),
            APathModelCallFact(
                task_run_id=12,
                task_attempt_id=121,
                agent_step_run_id=1201,
                call_no=1,
                usage={"requests": 1, "total_tokens": 30},
            ),
            # A cross-task lineage mismatch must not enter the project total.
            APathModelCallFact(
                task_run_id=99,
                task_attempt_id=101,
                agent_step_run_id=9901,
                call_no=1,
                usage={"requests": 9, "total_tokens": 900},
            ),
        ],
        events=[],
        operations=[],
    )

    assert metrics["usage_totals"] == {
        "requests": 8,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 420,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
    }
    assert metrics["usage_observations"] == {
        "task_attempts": 4,
        "model_calls": 5,
        "model_call_attempts": 3,
        "model_call_usage_snapshots": 4,
        "task_attempt_fallbacks": 1,
        "task_run_fallbacks": 1,
    }


def test_a_path_metrics_endpoint_is_get_only_and_delegates_to_read_model() -> None:
    expected = {
        "version": "a-path-funnel-v1",
        "funnel": {"task_runs": 0},
    }
    app = create_app(verify_database=False)
    app.dependency_overrides[get_actor_user_id] = lambda: 17
    app.dependency_overrides[get_session] = object

    with patch.object(
        APathMetricsService,
        "project_metrics",
        return_value=expected,
    ) as project_metrics:
        with TestClient(app) as client:
            response = client.get("/api/v1/projects/42/a-path-metrics")
            method_not_allowed = client.post("/api/v1/projects/42/a-path-metrics")

    assert response.status_code == 200
    assert response.json() == expected
    project_metrics.assert_called_once_with(17, 42)
    assert method_not_allowed.status_code == 405
    assert method_not_allowed.json()["code"] == "method_not_allowed"
