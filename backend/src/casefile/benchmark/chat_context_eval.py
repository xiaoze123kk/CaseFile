"""Deterministic context baseline and Boundary scaffolding for casefile_chat.

Phase 1 measures the legacy policy without changing what the model sees and
writes the reference report to ``var/benchmark/context-baseline-v1.json``.
``BoundaryScenario`` only freezes the Phase 3 continuation-comparison shape;
compaction comparison arrives with Thread Memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from casefile.agent_runtime.context import (
    LEGACY_CONTEXT_POLICY_VERSION,
    build_chat_context_manifest,
)

DEFAULT_BASELINE_REPORT_PATH = "var/benchmark/context-baseline-v1.json"


@dataclass(frozen=True, slots=True)
class ContextBaselineSample:
    """One frozen chat payload measured through a context policy."""

    sample_id: str
    frozen_input: dict[str, Any]
    prebuilt_input: str
    input_hash: str
    policy_version: str = LEGACY_CONTEXT_POLICY_VERSION
    provider: str = "openai"
    model_id: str = ""


@dataclass(frozen=True, slots=True)
class BoundaryScenario:
    """Phase 3 continuation fixture: resume the same transcript both ways."""

    scenario_id: str
    prefix_messages: tuple[dict[str, str], ...]
    continuation_message: str
    expected_referenced_object_ids: tuple[str, ...] = ()
    expected_next_intent: str | None = None


@dataclass(frozen=True, slots=True)
class ContextBaselineReport:
    """Reference measurement for the legacy context policy."""

    samples: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    total_samples: int = 0
    fallback_count: int = 0
    peak_input_tokens: int = 0
    total_input_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_samples": self.total_samples,
            "fallback_count": self.fallback_count,
            "peak_input_tokens": self.peak_input_tokens,
            "total_input_tokens": self.total_input_tokens,
            "samples": list(self.samples),
        }


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sample_hash(sample_id: str, payload: dict[str, Any]) -> str:
    return hashlib.sha256(f"{sample_id}:{_json_text(payload)}".encode()).hexdigest()


def _legacy_input_text(frozen_input: dict[str, Any]) -> str:
    return (
        "请根据以下冻结数据回复作者，并仅在必要时提出可审阅的字段修改建议。"
        "author_message 是本轮请求；其余 JSON 字段提供数据和能力边界。\n"
        + _json_text(frozen_input)
    )


def _sample(
    sample_id: str,
    *,
    casefile: dict[str, Any] | None = None,
    history: list[dict[str, str]] | None = None,
    validation_issues: list[dict[str, Any]] | None = None,
    policy_version: str = LEGACY_CONTEXT_POLICY_VERSION,
    provider: str = "openai",
    model_id: str = "gpt-5.6-sol",
) -> ContextBaselineSample:
    frozen_input: dict[str, Any] = {
        "casefile": casefile if casefile is not None else {},
        "history": history or [],
        "message": "请核对当前卷宗里的关键人物。",
        "focus": {"object_ids": [], "event_ids": [], "validation_issue_ids": []},
        "validation": {
            "issues": validation_issues or [],
        },
        "context_policy_version": policy_version,
    }
    return ContextBaselineSample(
        sample_id=sample_id,
        frozen_input=frozen_input,
        prebuilt_input=_legacy_input_text(frozen_input),
        input_hash=_sample_hash(sample_id, frozen_input),
        policy_version=policy_version,
        provider=provider,
        model_id=model_id,
    )


def build_context_baseline_samples() -> tuple[ContextBaselineSample, ...]:
    """Return a stable five-sample suite covering the legacy input shapes."""

    small_casefile = {
        "entities": [
            {"id": "object:person_1", "name": "张三", "description": "仓储管理员。"},
            {"id": "object:person_2", "name": "李四", "description": "装卸组长。"},
        ],
        "events": [
            {"id": "event:fire", "title": "三号库区失火", "description": "夜间发生。"}
        ],
    }
    history = [
        message
        for index in range(12)
        for message in (
            {"role": "user", "content": f"第 {index} 轮问题"},
            {"role": "assistant", "content": f"第 {index} 轮回答"},
        )
    ]
    issues = [
        {
            "issue_id": f"issue:{index}",
            "rule_id": "temporal_exclusivity_violation",
            "severity": "S1",
            "title": f"时间冲突 {index}",
            "message": f"事件时间重叠的说明 {index}。",
            "object_refs": ["event:fire"],
        }
        for index in range(5)
    ]
    return (
        _sample("minimal"),
        _sample("small-casefile", casefile=small_casefile),
        _sample(
            "long-history",
            casefile=small_casefile,
            history=history,
            provider="deepseek",
            model_id="deepseek-v4-flash",
        ),
        _sample(
            "validation-rich",
            casefile=small_casefile,
            validation_issues=issues,
        ),
        _sample(
            "unknown-policy",
            casefile=small_casefile,
            policy_version="missing-context-policy-v9",
        ),
    )


def validate_context_baseline_samples(
    samples: tuple[ContextBaselineSample, ...],
) -> list[str]:
    """Return validation errors; an empty list means the suite is runnable."""

    errors: list[str] = []
    seen_ids: set[str] = set()
    for sample in samples:
        if sample.sample_id in seen_ids:
            errors.append(f"duplicate sample_id: {sample.sample_id}")
        seen_ids.add(sample.sample_id)
        if not sample.prebuilt_input:
            errors.append(f"{sample.sample_id}: prebuilt_input is empty")
        if not sample.input_hash:
            errors.append(f"{sample.sample_id}: input_hash is empty")
    return errors


def evaluate_context_baseline(
    samples: tuple[ContextBaselineSample, ...],
) -> ContextBaselineReport:
    """Measure every sample through its frozen context policy."""

    entries: list[dict[str, Any]] = []
    fallback_count = 0
    peak_tokens = 0
    total_tokens = 0
    for sample in samples:
        result = build_chat_context_manifest(
            policy_version=sample.policy_version,
            frozen_input=sample.frozen_input,
            input_hash=sample.input_hash,
            prebuilt_input=sample.prebuilt_input,
            provider=sample.provider,
            model_id=sample.model_id,
        )
        fallback = result.fallback
        if fallback is not None:
            fallback_count += 1
        manifest = result.manifest.to_jsonable()
        peak_tokens = max(peak_tokens, result.manifest.total_tokens)
        total_tokens += result.manifest.total_tokens
        entries.append(
            {
                "sample_id": sample.sample_id,
                "requested_policy_version": sample.policy_version,
                "provider": sample.provider,
                "model_id": sample.model_id,
                "manifest": manifest,
                "fallback": (
                    None
                    if fallback is None
                    else {"code": fallback.code, "detail": fallback.detail}
                ),
            }
        )
    return ContextBaselineReport(
        samples=tuple(entries),
        total_samples=len(entries),
        fallback_count=fallback_count,
        peak_input_tokens=peak_tokens,
        total_input_tokens=total_tokens,
    )


def boundary_scenario_from_dict(raw: dict[str, Any]) -> BoundaryScenario:
    """Parse one Phase 3 continuation fixture with strict field validation."""

    scenario_id = raw.get("scenario_id")
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError("boundary scenario_id must be a non-empty string")
    prefix_raw = raw.get("prefix_messages")
    if not isinstance(prefix_raw, list):
        raise ValueError(f"{scenario_id}: prefix_messages must be an array")
    prefix: list[dict[str, str]] = []
    for item in prefix_raw:
        if not isinstance(item, dict):
            raise ValueError(f"{scenario_id}: prefix message must be an object")
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content:
            raise ValueError(f"{scenario_id}: prefix message role/content is invalid")
        prefix.append({"role": role, "content": content})
    continuation = raw.get("continuation_message")
    if not isinstance(continuation, str) or not continuation:
        raise ValueError(f"{scenario_id}: continuation_message must be non-empty")
    referenced = raw.get("expected_referenced_object_ids", [])
    if not isinstance(referenced, list) or not all(
        isinstance(value, str) and value for value in referenced
    ):
        raise ValueError(f"{scenario_id}: expected_referenced_object_ids is invalid")
    expected_intent = raw.get("expected_next_intent")
    if expected_intent is not None and (
        not isinstance(expected_intent, str) or not expected_intent
    ):
        raise ValueError(f"{scenario_id}: expected_next_intent is invalid")
    return BoundaryScenario(
        scenario_id=scenario_id,
        prefix_messages=tuple(prefix),
        continuation_message=continuation,
        expected_referenced_object_ids=tuple(referenced),
        expected_next_intent=expected_intent,
    )


def write_context_baseline_report(
    report: ContextBaselineReport,
    report_path: Path,
) -> None:
    """Persist the JSON baseline report next to other ignored benchmark outputs."""

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the deterministic casefile-chat context baseline"
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path(DEFAULT_BASELINE_REPORT_PATH),
        help="JSON report path (default: var/benchmark/context-baseline-v1.json)",
    )
    arguments = parser.parse_args()
    samples = build_context_baseline_samples()
    errors = validate_context_baseline_samples(samples)
    if errors:
        for error in errors:
            print(error)
        raise SystemExit(2)
    report = evaluate_context_baseline(samples)
    write_context_baseline_report(report, arguments.report_path)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))


__all__ = [
    "BoundaryScenario",
    "ContextBaselineReport",
    "ContextBaselineSample",
    "DEFAULT_BASELINE_REPORT_PATH",
    "boundary_scenario_from_dict",
    "build_context_baseline_samples",
    "evaluate_context_baseline",
    "main",
    "validate_context_baseline_samples",
    "write_context_baseline_report",
]
