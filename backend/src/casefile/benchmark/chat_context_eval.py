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
    CHAT_CONTEXT_POLICY_V2_VERSION,
    LEGACY_CONTEXT_POLICY_VERSION,
    ChatThreadMemoryState,
    ThreadMemoryCompactorV1,
    ThreadMemoryDelta,
    ThreadMemoryVerifiedFact,
    build_chat_context_manifest,
    chat_input_payload_from_assembly,
    default_token_estimator_registry,
    estimate_jsonable_tokens,
    thread_memory_state_to_jsonable,
)

DEFAULT_BASELINE_REPORT_PATH = "var/benchmark/context-baseline-v1.json"
DEFAULT_BOUNDARY_REPORT_PATH = "var/benchmark/context-boundary-v1.json"


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
class BoundaryContinuationFixture:
    """One deterministic full-vs-compacted continuation comparison."""

    scenario_id: str
    prefix_messages: tuple[dict[str, str], ...]
    continuation_message: str
    thread_memory_state: dict[str, Any]
    required_state_strings: tuple[str, ...]
    expected_next_actions: tuple[str, ...]
    repeated_work_probes: tuple[str, ...]
    recent_window_turns: int = 2
    casefile: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BoundaryArmMetrics:
    """Deterministic continuation metrics for one context arm."""

    arm: str
    task_success: bool
    state_recall_ratio: float
    action_continuity_ratio: float
    repeated_work_occurrences: int
    repeated_work_rate: float
    peak_input_tokens: int
    total_input_tokens: int
    block_tokens: dict[str, int]
    fallback: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class BoundaryComparison:
    """Full vs compacted arm metrics plus every Phase 3 gate verdict."""

    scenario_id: str
    full: BoundaryArmMetrics
    compacted: BoundaryArmMetrics
    gates: dict[str, bool]
    passed: bool


@dataclass(frozen=True, slots=True)
class BoundaryComparisonReport:
    """Aggregate report for the Boundary continuation eval."""

    comparisons: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    total_scenarios: int = 0
    passed_scenarios: int = 0
    passed: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_scenarios": self.total_scenarios,
            "passed_scenarios": self.passed_scenarios,
            "passed": self.passed,
            "comparisons": list(self.comparisons),
        }


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


def _boundary_frozen_input(
    fixture: BoundaryContinuationFixture,
    history: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "casefile": fixture.casefile,
        "history": history,
        "message": fixture.continuation_message,
        "focus": {"object_ids": [], "event_ids": [], "validation_issue_ids": []},
        "validation": {"issues": []},
        "context_policy_version": CHAT_CONTEXT_POLICY_V2_VERSION,
    }


def _occurrences(text: str, needles: tuple[str, ...]) -> int:
    return sum(text.count(needle) for needle in needles)


def _boundary_arm_metrics(
    scenario_id: str,
    arm: str,
    fixture: BoundaryContinuationFixture,
    frozen_input: dict[str, Any],
    *,
    thread_memory_state: dict[str, Any] | None,
) -> BoundaryArmMetrics:
    has_thread_memory = thread_memory_state is not None
    if arm == "full":
        payload: dict[str, Any] = {
            "casefile": fixture.casefile,
            "thread_history": list(fixture.prefix_messages),
            "author_message": fixture.continuation_message,
            "focus_objects": [],
            "editable_fields_by_collection": {},
            "focus": {"object_ids": [], "event_ids": [], "validation_issue_ids": []},
            "validation": {"issues": []},
            "validation_issues": [],
            "routing": {},
            "thread_memory": None,
        }
        estimator = default_token_estimator_registry().select("openai", "gpt-5.6-sol")
        total_tokens = estimate_jsonable_tokens(payload, estimator)
        fallback = None
        task_success = (
            isinstance(payload.get("thread_history"), list)
            and payload.get("author_message") == fixture.continuation_message
        )
        block_tokens = {"full_raw_context": total_tokens}
    else:
        result = build_chat_context_manifest(
            policy_version=CHAT_CONTEXT_POLICY_V2_VERSION,
            frozen_input=frozen_input,
            input_hash=_sample_hash(f"{scenario_id}:{arm}", frozen_input),
            prebuilt_input=_json_text(frozen_input),
            extra_input=(
                {"thread_memory_state": thread_memory_state}
                if has_thread_memory
                else None
            ),
            provider="openai",
            model_id="gpt-5.6-sol",
        )
        payload = chat_input_payload_from_assembly(
            result.assembly,
            require_thread_memory=has_thread_memory,
        )
        total_tokens = result.manifest.total_tokens
        fallback = result.fallback
        task_success = (
            fallback is None
            and payload.get("author_message") == fixture.continuation_message
            and isinstance(payload.get("thread_history"), list)
            and (not has_thread_memory or isinstance(payload.get("thread_memory"), dict))
        )
        block_tokens = {
            block.id: block.tokens for block in result.assembly.blocks
        }
    text = _json_text(payload)
    required_total = len(fixture.required_state_strings)
    state_recall = (
        0.0
        if required_total == 0
        else min(
            1.0,
            _occurrences(text, fixture.required_state_strings) / required_total,
        )
    )
    action_total = len(fixture.expected_next_actions)
    action_continuity = (
        0.0
        if action_total == 0
        else min(
            1.0,
            _occurrences(text, fixture.expected_next_actions) / action_total,
        )
    )
    repeated_work = _occurrences(text, fixture.repeated_work_probes)
    return BoundaryArmMetrics(
        arm=arm,
        task_success=task_success,
        state_recall_ratio=state_recall,
        action_continuity_ratio=action_continuity,
        repeated_work_occurrences=repeated_work,
        repeated_work_rate=(
            repeated_work / total_tokens if total_tokens > 0 else 0.0
        ),
        peak_input_tokens=total_tokens,
        total_input_tokens=total_tokens,
        block_tokens=block_tokens,
        fallback=(
            None
            if fallback is None
            else {"code": fallback.code, "detail": fallback.detail}
        ),
    )


def evaluate_boundary_fixture(
    fixture: BoundaryContinuationFixture,
) -> BoundaryComparison:
    """Compare one transcript continuation with full vs compacted context."""

    recent_history = list(fixture.prefix_messages[-fixture.recent_window_turns :])
    full = _boundary_arm_metrics(
        fixture.scenario_id,
        "full",
        fixture,
        _boundary_frozen_input(fixture, list(fixture.prefix_messages)),
        thread_memory_state=None,
    )
    compacted = _boundary_arm_metrics(
        fixture.scenario_id,
        "compacted",
        fixture,
        _boundary_frozen_input(fixture, recent_history),
        thread_memory_state=fixture.thread_memory_state,
    )
    gates = {
        "task_success_preserved": full.task_success and compacted.task_success,
        "state_recall_not_degraded": compacted.state_recall_ratio
        >= full.state_recall_ratio,
        "action_continuity_not_degraded": compacted.action_continuity_ratio
        >= full.action_continuity_ratio,
        "repeated_work_not_increased": compacted.repeated_work_occurrences
        <= full.repeated_work_occurrences,
        "peak_tokens_reduced": compacted.peak_input_tokens < full.peak_input_tokens,
        "total_tokens_reduced": compacted.total_input_tokens < full.total_input_tokens,
    }
    return BoundaryComparison(
        scenario_id=fixture.scenario_id,
        full=full,
        compacted=compacted,
        gates=gates,
        passed=all(gates.values()),
    )


def _compacted_prefix_state(
    *,
    compacted_message_seq: int,
    delta: ThreadMemoryDelta,
) -> dict[str, Any]:
    """Run the real deterministic merge path before the continuation."""

    old_state = ChatThreadMemoryState(
        last_compacted_message_seq=0,
    )
    merged = ThreadMemoryCompactorV1().merge(
        old_state,
        delta,
        db_decisions=[],
        last_compacted_message_seq=compacted_message_seq,
    )
    return thread_memory_state_to_jsonable(merged)


def _boundary_prefix(
    *,
    opening_answer: str,
    probe: str,
    total_turns: int = 12,
) -> tuple[dict[str, str], ...]:
    """Long deterministic transcript; the last two turns stay recent-only."""

    messages: list[dict[str, str]] = [
        {"role": "user", "content": "请先给出你的结论。"},
        {"role": "assistant", "content": opening_answer},
    ]
    for index in range(1, total_turns):
        if index < total_turns - 2:
            messages.append(
                {
                    "role": "user",
                    "content": f"请再次复核第 {index} 项。",
                }
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": f"{probe} 第 {index} 项维持原判。",
                }
            )
        else:
            messages.append(
                {"role": "user", "content": f"进入第 {index} 阶段。"}
            )
            messages.append(
                {"role": "assistant", "content": f"第 {index} 阶段已就绪。"}
            )
    return tuple(messages)


def build_boundary_fixtures() -> tuple[BoundaryContinuationFixture, ...]:
    """Return the stable deterministic full-vs-compacted continuation suite."""

    shift_prefix = _boundary_prefix(
        opening_answer=(
            "已核对值班表，结论不变：未发现异常。"
            "张三在失火前已与李四换班。下一步是核对换班交接记录。"
        ),
        probe="已核对值班表，结论不变：未发现异常。",
    )
    timeline_prefix = _boundary_prefix(
        opening_answer=(
            "object:person_1 与 object:person_2 于 22:00 换班，"
            "event:fire 时间线已记录。下一步是更新 event:fire 时间线。"
        ),
        probe="换班时间 22:00 已核对两次，event:fire 无变化。",
    )
    small_casefile = {
        "entities": [
            {"id": "object:person_1", "name": "张三", "description": "仓储管理员。"},
            {"id": "object:person_2", "name": "李四", "description": "装卸组长。"},
        ],
        "events": [
            {"id": "event:fire", "title": "三号库区失火", "description": "夜间发生。"}
        ],
    }
    return (
        BoundaryContinuationFixture(
            scenario_id="shift-handover",
            prefix_messages=shift_prefix,
            continuation_message="基于已有结论，请给出下一步行动。",
            thread_memory_state=_compacted_prefix_state(
                compacted_message_seq=20,
                delta=ThreadMemoryDelta(
                    constraints=["张三在失火前已与李四换班。"],
                    verified_facts=[
                        ThreadMemoryVerifiedFact(
                            fact="已核对值班表，结论不变：未发现异常。",
                            source_message_id=2,
                        )
                    ],
                    next_actions=["核对换班交接记录"],
                    evidence_refs=["thread://7/message/2"],
                ),
            ),
            required_state_strings=(
                "张三在失火前已与李四换班。",
                "已核对值班表，结论不变：未发现异常。",
            ),
            expected_next_actions=("核对换班交接记录",),
            repeated_work_probes=(
                "已核对值班表，结论不变：未发现异常。",
                "请再次复核第",
            ),
            casefile=small_casefile,
        ),
        BoundaryContinuationFixture(
            scenario_id="timeline-repair",
            prefix_messages=timeline_prefix,
            continuation_message="请继续按时间线推进。",
            thread_memory_state=_compacted_prefix_state(
                compacted_message_seq=20,
                delta=ThreadMemoryDelta(
                    constraints=["换班时间 22:00 不得改写。"],
                    verified_facts=[
                        ThreadMemoryVerifiedFact(
                            fact="object:person_1 与 object:person_2 于 22:00 换班。",
                            source_message_id=2,
                        )
                    ],
                    next_actions=["更新 event:fire 时间线"],
                    evidence_refs=["patchset://9", "thread://8/message/2"],
                ),
            ),
            required_state_strings=(
                "object:person_1 与 object:person_2 于 22:00 换班。",
                "event:fire",
            ),
            expected_next_actions=("更新 event:fire 时间线",),
            repeated_work_probes=(
                "请再次复核第",
                "换班时间 22:00 已核对两次，event:fire 无变化。",
            ),
            casefile=small_casefile,
        ),
    )


def evaluate_boundary_fixtures(
    fixtures: tuple[BoundaryContinuationFixture, ...],
) -> BoundaryComparisonReport:
    """Evaluate every boundary fixture and aggregate the gates."""

    comparisons: list[dict[str, Any]] = []
    passed_scenarios = 0
    for fixture in fixtures:
        comparison = evaluate_boundary_fixture(fixture)
        passed_scenarios += int(comparison.passed)
        comparisons.append(
            {
                "scenario_id": comparison.scenario_id,
                "passed": comparison.passed,
                "gates": dict(comparison.gates),
                "full": {
                    "task_success": comparison.full.task_success,
                    "state_recall_ratio": comparison.full.state_recall_ratio,
                    "action_continuity_ratio": (
                        comparison.full.action_continuity_ratio
                    ),
                    "repeated_work_occurrences": (
                        comparison.full.repeated_work_occurrences
                    ),
                    "peak_input_tokens": comparison.full.peak_input_tokens,
                    "total_input_tokens": comparison.full.total_input_tokens,
                },
                "compacted": {
                    "task_success": comparison.compacted.task_success,
                    "state_recall_ratio": comparison.compacted.state_recall_ratio,
                    "action_continuity_ratio": (
                        comparison.compacted.action_continuity_ratio
                    ),
                    "repeated_work_occurrences": (
                        comparison.compacted.repeated_work_occurrences
                    ),
                    "peak_input_tokens": comparison.compacted.peak_input_tokens,
                    "total_input_tokens": comparison.compacted.total_input_tokens,
                },
            }
        )
    return BoundaryComparisonReport(
        comparisons=tuple(comparisons),
        total_scenarios=len(fixtures),
        passed_scenarios=passed_scenarios,
        passed=passed_scenarios == len(fixtures),
    )


def write_boundary_report(
    report: BoundaryComparisonReport,
    report_path: Path,
) -> None:
    """Persist the Boundary continuation comparison report."""

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
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
        description="Run the deterministic casefile-chat context baseline and boundary eval"
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path(DEFAULT_BASELINE_REPORT_PATH),
        help="Baseline JSON report path (default: var/benchmark/context-baseline-v1.json)",
    )
    parser.add_argument(
        "--boundary-report-path",
        type=Path,
        default=None,
        help="Boundary continuation JSON report path (default: disabled)",
    )
    parser.add_argument(
        "--gate-boundary",
        action="store_true",
        help="Exit non-zero unless every Boundary continuation gate passes",
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

    boundary_report = evaluate_boundary_fixtures(build_boundary_fixtures())
    if arguments.boundary_report_path is not None:
        write_boundary_report(boundary_report, arguments.boundary_report_path)
    boundary_output = boundary_report.as_dict()
    if arguments.gate_boundary or arguments.boundary_report_path is not None:
        print(json.dumps(boundary_output, ensure_ascii=False, indent=2))
    if arguments.gate_boundary and not boundary_report.passed:
        raise SystemExit(3)


__all__ = [
    "BoundaryArmMetrics",
    "BoundaryComparison",
    "BoundaryComparisonReport",
    "BoundaryContinuationFixture",
    "BoundaryScenario",
    "ContextBaselineReport",
    "ContextBaselineSample",
    "DEFAULT_BASELINE_REPORT_PATH",
    "DEFAULT_BOUNDARY_REPORT_PATH",
    "boundary_scenario_from_dict",
    "build_boundary_fixtures",
    "build_context_baseline_samples",
    "evaluate_boundary_fixture",
    "evaluate_boundary_fixtures",
    "evaluate_context_baseline",
    "main",
    "validate_context_baseline_samples",
    "write_boundary_report",
    "write_context_baseline_report",
]


if __name__ == "__main__":
    main()
