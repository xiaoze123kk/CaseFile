"""Live-model Eval runner contract tests using the deterministic fake provider."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from casefile.agent_runtime import FakeProvider
from casefile.benchmark import chat_live_eval
from casefile.benchmark.chat_live_eval import (
    LiveChatRouterEvalReport,
    dataclass_metrics_to_dict,
    run_live_chat_router_eval,
)
from casefile.benchmark.chat_router_eval import (
    build_eval_fixtures,
    fake_router_resolver,
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
    assert report.fixture_count == 34
    assert len(report.rows) == 34
    assert report.metrics is not None
    assert report.metrics.intent_accuracy == baseline.intent_accuracy
    assert report.metrics.route_accuracy == baseline.route_accuracy
    assert report.metrics.dangerous_confusion_recall == 1.0
    assert report.event_count > 0
    assert report.model_call_stages.get("understanding", 0) > 0
    matched_rows = sum(1 for row in report.rows if row["matched"] is True)
    assert matched_rows == round(report.metrics.route_accuracy * 34)
    rows = {row["fixture_id"]: row for row in report.rows}
    for fixture_id in ("free-low-confidence-edit", "free-low-confidence-audit"):
        row = rows[fixture_id]
        assert row["actual_intent"] != row["expected_intent"]
        assert row["expected_intent"] == "clarify"
        assert row["actual_component"] == "clarify"
        assert row["matched"] is True


@pytest.mark.parametrize(
    ("target", "component"), [("question", "clarify"), ("clarify", "chat")],
)
def test_live_eval_rejects_wrong_fallback_target_or_component(
    monkeypatch: pytest.MonkeyPatch, target: str, component: str,
) -> None:
    fixture = next(
        item for item in build_eval_fixtures() if item.fixture_id == "free-low-confidence-edit"
    )
    resolved = fake_router_resolver(fixture)
    assert resolved.route is not None
    wrong_route = replace(
        resolved.route,
        execution_profile={"primary_intent": target, "prompt_component": component},
    )
    monkeypatch.setattr(
        chat_live_eval, "resolve_chat_route",
        lambda *_args, **_kwargs: replace(resolved, route=wrong_route),
    )
    report = run_live_chat_router_eval(
        FakeProvider(), provider_name="fake", model_id="fake-live-eval",
        api_key="unused", fixtures=[fixture], mode="fake",
    )

    assert report.rows[0]["matched"] is False
    assert report.metrics is not None
    assert report.metrics.route_accuracy == 0


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
