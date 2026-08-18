"""Live-model batch Eval for the CaseFile chat intent router.

Runs the exact Worker cascade
``rule → provider.understand_intent → confidence gate → rewrite`` against the
30-fixture baseline (plus optional exported feedback fixtures) with a real
OpenAI or DeepSeek provider.

Usage:
    python -m casefile.benchmark.chat_live_eval \
        --provider openai --model gpt-5.6-sol \
        --api-key "$OPENAI_API_KEY" \
        --report-path reports/chat-router-live.json
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from sqlalchemy import select

from casefile.agent_runtime import (
    DeepSeekAgentsProvider,
    FakeProvider,
    OpenAIAgentsProvider,
)
from casefile.agent_runtime.credentials import decrypt_api_key
from casefile.agent_runtime.models import CaseFileChatRequest
from casefile.benchmark.chat_router_eval import (
    ChatRouterEvalReport,
    ChatRouterFixture,
    _request_for_fixture,
    build_eval_fixtures,
    evaluate_chat_router,
)
from casefile.data_postgres.models import UserProviderSetting
from casefile.data_postgres.session import create_database_engine, create_session_factory
from casefile.worker.runtime import _resolve_chat_route

ROUTE_ACCURACY_TARGET = 0.90
DANGEROUS_CONFUSION_TARGET = 1.0
FALLBACK_RATE_TARGET = 0.10
PRESERVATION_PASS_TARGET = 0.98


@dataclass(frozen=True, slots=True)
class LiveChatRouterEvalReport:
    provider: str
    model_id: str
    mode: str
    fixture_count: int
    event_count: int
    model_call_stages: dict[str, int] = field(default_factory=dict)
    metrics: ChatRouterEvalReport | None = None
    gates: dict[str, bool] = field(default_factory=dict)
    rows: tuple[dict[str, Any], ...] = ()
    status: str = "failed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "provider": self.provider,
            "model_id": self.model_id,
            "mode": self.mode,
            "fixture_count": self.fixture_count,
            "event_count": self.event_count,
            "model_call_stages": self.model_call_stages,
            "metrics": (
                None if self.metrics is None else dataclass_metrics_to_dict(self.metrics)
            ),
            "gates": self.gates,
            "rows": list(self.rows),
        }


def dataclass_metrics_to_dict(report: ChatRouterEvalReport) -> dict[str, Any]:
    return {
        "intent_accuracy": report.intent_accuracy,
        "route_accuracy": report.route_accuracy,
        "dangerous_confusion_recall": report.dangerous_confusion_recall,
        "fallback_rate": report.fallback_rate,
        "preservation_pass_rate": report.preservation_pass_rate,
        "total": report.total,
        "fallback_fixture_ids": list(report.fallback_fixture_ids),
        "dangerous_confusions": [
            list(entry) for entry in report.dangerous_confusions
        ],
    }


def _resolver_for_provider(
    provider: Any,
    *,
    model_id: str,
    api_key: str,
) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
    """Build an IntentResolver plus per-row observation and event records."""

    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    def resolve(fixture: ChatRouterFixture) -> CaseFileChatRequest:
        def emit(event_type: str, stage: str, payload: dict[str, Any]) -> None:
            events.append(
                {"event_type": event_type, "stage": stage, "payload": payload}
            )

        request = replace(
            _request_for_fixture(fixture, task_run_id=1),
            model_id=model_id,
            api_key=api_key,
            emit=emit,
        )
        event_count_before = len(events)
        resolved = _resolve_chat_route(request, provider=provider)
        understanding = resolved.task_understanding
        route = resolved.route
        rewrite = resolved.rewrite
        actual_intent = (
            "unresolved" if understanding is None else understanding.primary_intent
        )
        actual_component = (
            "unresolved"
            if route is None
            else str(route.execution_profile.get("prompt_component") or "chat")
        )
        safe_question_fallback = (
            fixture.expected_primary_intent == "question"
            and route is not None
            and route.route_source == "fallback"
        )
        matched = (
            actual_intent == fixture.expected_primary_intent or safe_question_fallback
        ) and actual_component == fixture.expected_prompt_component
        row: dict[str, Any] = {
            "fixture_id": fixture.fixture_id,
            "expected_intent": fixture.expected_primary_intent,
            "expected_component": fixture.expected_prompt_component,
            "actual_intent": actual_intent,
            "actual_component": actual_component,
            "matched": bool(matched),
            "route_source": None if route is None else route.route_source,
            "confidence": None if understanding is None else understanding.confidence,
            "confidence_margin": None if route is None else route.confidence_margin,
            "rewrite_strategy": None if route is None else route.rewrite_strategy,
            "rewrite_decision": None if rewrite is None else rewrite.rewrite_decision,
            "preservation_checks": (
                None if rewrite is None else rewrite.preservation_checks
            ),
            "fallback": False if route is None else route.route_source == "fallback",
            "event_count": len(events) - event_count_before,
        }
        rows.append(row)
        return resolved

    return resolve, rows, events


def run_live_chat_router_eval(
    provider: Any,
    *,
    provider_name: str,
    model_id: str,
    api_key: str,
    fixtures: tuple[ChatRouterFixture, ...] | list[ChatRouterFixture],
    mode: str = "live",
) -> LiveChatRouterEvalReport:
    if len(fixtures) == 0:
        raise ValueError("fixtures must not be empty")
    resolver, rows, events = _resolver_for_provider(
        provider,
        model_id=model_id,
        api_key=api_key,
    )
    metrics = evaluate_chat_router(resolver, list(fixtures))
    stages: dict[str, int] = {}
    for event in events:
        stages[event["stage"]] = stages.get(event["stage"], 0) + 1
    gates = {
        "route_accuracy_ge_0.90": metrics.route_accuracy >= ROUTE_ACCURACY_TARGET,
        "dangerous_confusions_zero": (
            metrics.dangerous_confusion_recall >= DANGEROUS_CONFUSION_TARGET
        ),
        "fallback_rate_lt_0.10": metrics.fallback_rate < FALLBACK_RATE_TARGET,
        "preservation_pass_rate_ge_0.98": (
            metrics.preservation_pass_rate >= PRESERVATION_PASS_TARGET
        ),
    }
    return LiveChatRouterEvalReport(
        provider=provider_name,
        model_id=model_id,
        mode=mode,
        fixture_count=len(fixtures),
        event_count=len(events),
        model_call_stages=stages,
        metrics=metrics,
        gates=gates,
        rows=tuple(rows),
        status="passed" if all(gates.values()) else "failed",
    )


def _resolved_api_key(provider: str, api_key: str | None) -> str:
    if provider == "fake":
        return api_key or "fake"
    if api_key and api_key.strip():
        return api_key.strip()
    env_names = {
        "openai": ("CASEFILE_OPENAI_API_KEY", "OPENAI_API_KEY"),
        "deepseek": ("CASEFILE_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
    }[provider]
    for env_name in env_names:
        value = os.environ.get(env_name)
        if value and value.strip():
            return value.strip()
    raise SystemExit(
        "No API key provided. Pass --api-key or set one of: "
        + ", ".join(env_names)
    )


def _provider(provider_name: str, api_key: str) -> tuple[Any, str]:
    if provider_name == "openai":
        return OpenAIAgentsProvider(), api_key
    if provider_name == "deepseek":
        return DeepSeekAgentsProvider(), api_key
    if provider_name == "fake":
        return FakeProvider(), "fake"
    raise SystemExit(f"Unsupported provider: {provider_name}")


def _saved_provider_credential(
    *,
    database_url: str | None,
    actor_id: int,
    provider_name: str,
    requested_model: str | None,
) -> tuple[str, str] | None:
    """Decrypt the configured provider credential from the application database."""

    if database_url is None:
        return None
    if provider_name == "fake":
        return "fake", requested_model or "fake-live-eval"
    engine = create_database_engine(database_url)
    try:
        with create_session_factory(engine)() as session:
            setting = session.scalar(
                select(UserProviderSetting)
                .where(
                    UserProviderSetting.user_id == actor_id,
                    UserProviderSetting.provider == provider_name,
                    UserProviderSetting.credential_status != "deleted",
                )
                .order_by(
                    UserProviderSetting.validated_at.desc().nulls_last(),
                    UserProviderSetting.id.desc(),
                )
                .limit(1)
            )
            if setting is None:
                raise SystemExit(
                    f"No saved {provider_name} credential for actor {actor_id} "
                    f"in {database_url}"
                )
            if (
                setting.secret_ciphertext is None
                or setting.secret_nonce is None
                or setting.key_version is None
            ):
                raise SystemExit(
                    f"Saved {provider_name} credential is missing encryption material"
                )
            api_key = decrypt_api_key(
                setting.secret_ciphertext,
                setting.secret_nonce,
                user_id=actor_id,
                provider=provider_name,
                key_version=setting.key_version,
            )
            return api_key, requested_model or setting.model_id
    finally:
        engine.dispose()


def _load_fixtures(
    baseline: tuple[ChatRouterFixture, ...],
    extra_paths: list[Path] | None,
) -> tuple[ChatRouterFixture, ...]:
    if not extra_paths:
        return baseline
    from casefile.benchmark.feedback_export import load_exported_fixtures

    merged: dict[str, ChatRouterFixture] = {
        fixture.fixture_id: fixture for fixture in baseline
    }
    for path in extra_paths:
        for fixture in load_exported_fixtures(path):
            merged[fixture.fixture_id] = fixture
    return tuple(merged.values())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the CaseFile chat intent router against a real model"
    )
    parser.add_argument("--provider", choices=("openai", "deepseek", "fake"), default="openai")
    parser.add_argument("--model", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument(
        "--database-url",
        default=None,
        help="Read the saved provider credential from this application database",
    )
    parser.add_argument("--actor-id", type=int, default=1)
    parser.add_argument("--fixture-ids", default=None, help="Comma-separated fixture ids")
    parser.add_argument(
        "--extra-fixtures",
        nargs="*",
        type=Path,
        default=[],
        help="JSON files exported by casefile.benchmark.feedback_export",
    )
    parser.add_argument("--report-path", type=Path)
    arguments = parser.parse_args()

    provider_name = arguments.provider
    saved = _saved_provider_credential(
        database_url=arguments.database_url,
        actor_id=arguments.actor_id,
        provider_name=provider_name,
        requested_model=arguments.model,
    )
    if saved is not None:
        api_key, model_id = saved
    else:
        api_key = _resolved_api_key(provider_name, arguments.api_key)
        model_id = arguments.model or {
            "openai": "gpt-5.6-sol",
            "deepseek": "deepseek-chat",
            "fake": "fake-live-eval",
        }[provider_name]
    fixtures = _load_fixtures(build_eval_fixtures(), arguments.extra_fixtures)
    if arguments.fixture_ids:
        allowed = {item.strip() for item in arguments.fixture_ids.split(",") if item.strip()}
        fixtures = tuple(item for item in fixtures if item.fixture_id in allowed)
        if not fixtures:
            raise SystemExit("--fixture-ids matched no fixtures")

    provider, _ = _provider(provider_name, api_key)
    report = run_live_chat_router_eval(
        provider,
        provider_name=provider_name,
        model_id=model_id,
        api_key=api_key,
        fixtures=fixtures,
        mode="live" if provider_name != "fake" else "fake",
    )
    rendered = json.dumps(report.as_dict(), ensure_ascii=False, indent=2)
    print(rendered)
    if arguments.report_path is not None:
        arguments.report_path.parent.mkdir(parents=True, exist_ok=True)
        arguments.report_path.write_text(rendered + "\n", encoding="utf-8")
    if report.status != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

__all__ = [
    "DANGEROUS_CONFUSION_TARGET",
    "FALLBACK_RATE_TARGET",
    "PRESERVATION_PASS_TARGET",
    "ROUTE_ACCURACY_TARGET",
    "LiveChatRouterEvalReport",
    "dataclass_metrics_to_dict",
    "run_live_chat_router_eval",
]
