"""Live-model Eval runner contract tests using the deterministic fake provider."""

from __future__ import annotations

import json

from casefile.agent_runtime import FakeProvider
from casefile.benchmark.chat_live_eval import (
    LiveChatRouterEvalReport,
    dataclass_metrics_to_dict,
    run_live_chat_router_eval,
)
from casefile.benchmark.chat_router_eval import (
    build_eval_fixtures,
    run_fake_baseline,
)


def test_live_eval_runner_uses_the_same_cascade_and_metrics_as_baseline() -> None:
    report = run_live_chat_router_eval(
        FakeProvider(),
        provider_name="fake",
        model_id="fake-live-eval",
        api_key="unused",
        fixtures=build_eval_fixtures(),
        mode="fake",
    )

    baseline = run_fake_baseline()
    assert isinstance(report, LiveChatRouterEvalReport)
    assert report.status == "passed"
    assert report.fixture_count == 30
    assert len(report.rows) == 30
    assert report.metrics is not None
    assert report.metrics.intent_accuracy == baseline.intent_accuracy
    assert report.metrics.route_accuracy == baseline.route_accuracy
    assert report.metrics.dangerous_confusion_recall == 1.0
    assert report.event_count > 0
    assert report.model_call_stages.get("understanding", 0) > 0
    matched_rows = sum(1 for row in report.rows if row["matched"] is True)
    assert matched_rows == round(report.metrics.route_accuracy * 30)


def test_live_eval_report_serializes_to_stable_json_shape() -> None:
    report = run_live_chat_router_eval(
        FakeProvider(),
        provider_name="fake",
        model_id="fake-live-eval",
        api_key="unused",
        fixtures=build_eval_fixtures()[:3],
        mode="fake",
    )

    payload = json.loads(json.dumps(report.as_dict(), ensure_ascii=False))

    assert payload["status"] == "passed"
    assert payload["fixture_count"] == 3
    assert set(payload["metrics"]) == {
        "intent_accuracy",
        "route_accuracy",
        "dangerous_confusion_recall",
        "fallback_rate",
        "preservation_pass_rate",
        "total",
        "fallback_fixture_ids",
        "dangerous_confusions",
    }
    assert set(payload["gates"]) == {
        "route_accuracy_ge_0.90",
        "dangerous_confusions_zero",
        "fallback_rate_lt_0.10",
        "preservation_pass_rate_ge_0.98",
    }
    assert dataclass_metrics_to_dict(report.metrics)["total"] == 3
