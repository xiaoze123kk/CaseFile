"""Fixture legality, metric purity and FakeProvider baseline gate tests."""

from __future__ import annotations

from casefile.benchmark.chat_router_eval import (
    build_eval_fixtures,
    evaluate_chat_router,
    fake_router_resolver,
    run_fake_baseline,
)

ALLOWED_INTENTS = {
    "question",
    "analysis",
    "explain_issue",
    "edit_request",
    "validate_request",
    "unsupported_action",
    "clarify",
    "out_of_scope",
    "fallback",
}
ALLOWED_COMPONENTS = {"chat", "analysis", "issue", "edit", "gate", "clarify", "scope"}
ALLOWED_HINTS = {
    "entrypoint:preset",
    "entrypoint:issue_action",
    "entrypoint:free_text",
}


def test_fixtures_are_legal_and_cover_dangerous_confusions() -> None:
    fixtures = build_eval_fixtures()

    assert len(fixtures) == 30
    assert len({fixture.fixture_id for fixture in fixtures}) == len(fixtures)
    for fixture in fixtures:
        assert fixture.fixture_id
        assert fixture.message.strip()
        assert fixture.hint.get("entrypoint") in {
            "preset",
            "issue_action",
            "free_text",
        }
        assert fixture.expected_primary_intent in ALLOWED_INTENTS
        assert fixture.expected_prompt_component in ALLOWED_COMPONENTS
        if fixture.hint.get("entrypoint") == "preset":
            assert fixture.hint.get("preset_id") in {
                "inspect",
                "evidence",
                "compare",
                "gate",
            }
        if fixture.dangerous_pair is not None:
            assert fixture.dangerous_pair[0] in {
                "edit_request",
                "unsupported_action",
                "validate_request",
                "analysis",
            }
            assert fixture.expected_primary_intent == fixture.dangerous_pair[0]
    dangerous_pairs = {fixture.dangerous_pair for fixture in fixtures}
    assert ("unsupported_action", "edit_request") in dangerous_pairs
    assert ("validate_request", "analysis") in dangerous_pairs


def test_evaluate_chat_router_metrics_are_pure_and_thresholded() -> None:
    fixtures = build_eval_fixtures()

    report = evaluate_chat_router(fake_router_resolver, fixtures)
    assert report.total == 30
    assert 0.0 <= report.intent_accuracy <= 1.0
    assert 0.0 <= report.route_accuracy <= 1.0
    assert 0.0 <= report.dangerous_confusion_recall <= 1.0
    assert 0.0 <= report.fallback_rate <= 1.0
    assert 0.0 <= report.preservation_pass_rate <= 1.0
    assert len(report.fallback_fixture_ids) == round(report.fallback_rate * 30)
    assert evaluate_chat_router(fake_router_resolver, fixtures) == report


def test_fake_provider_baseline_meets_r2_gate_thresholds() -> None:
    report = run_fake_baseline()

    assert report.route_accuracy >= 0.9
    assert report.dangerous_confusion_recall == 1.0
    assert report.fallback_rate < 0.10
    assert report.preservation_pass_rate == 1.0
    # The only expected fallbacks are the gate and clarify fixtures.
    assert set(report.fallback_fixture_ids) == {
        "free-low-confidence-edit",
        "free-clarify-fallback",
    }


def test_fake_router_resolves_anaphora_without_inventing_refs() -> None:
    fixture = next(
        fixture
        for fixture in build_eval_fixtures()
        if fixture.fixture_id == "free-anaphora"
    )
    resolved = fake_router_resolver(fixture)

    assert resolved.task_understanding is not None
    assert resolved.task_understanding.entities["object_mentions"] == [
        {"resolved_ref": "ent_lucy", "text": "它"}
    ]
    unresolved = fake_router_resolver(
        next(
            fixture
            for fixture in build_eval_fixtures()
            if fixture.fixture_id == "free-unresolved-anaphora"
        )
    )
    assert unresolved.task_understanding is not None
    assert unresolved.task_understanding.entities["object_mentions"] == [
        {"resolved_ref": None, "text": "它"}
    ]
