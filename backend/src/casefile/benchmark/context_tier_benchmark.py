"""Deterministic multi-tier context policy benchmark for Step 4.3.

Runs the same five frozen chat payloads through every policy tier and
produces a comparison report:

* ``legacy_full``  - agent-focus-v1 full injection (reference arm)
* ``context_v1``   - casefile-chat-context-v1 deterministic layout
* ``compaction``   - casefile-chat-context-v2 (+ rolling Thread Memory)
* ``dashboard_tools`` - casefile-chat-context-v3 (+ dashboard and Context Tools)

Acceptance gates are deterministic and fail-closed: every known policy must
fall back zero times, peak/total input tokens must never regress against the
legacy reference by more than the configured allowance, and dashboard tiers
must project a non-negative remaining budget with no guardrail violations.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_V2_VERSION,
    CHAT_CONTEXT_POLICY_V3_VERSION,
    CHAT_CONTEXT_POLICY_VERSION,
    LEGACY_CONTEXT_POLICY_VERSION,
    build_chat_context_manifest,
    empty_thread_memory_state,
    thread_memory_state_to_jsonable,
)
from casefile.benchmark.chat_context_eval import (
    ContextBaselineSample,
    build_context_baseline_samples,
)

DEFAULT_TIER_REPORT_PATH = "var/benchmark/context-tiers-v1.json"

CONTEXT_TIERS: tuple[dict[str, str], ...] = (
    {
        "tier_id": "legacy_full",
        "label": "legacy full injection",
        "policy_version": LEGACY_CONTEXT_POLICY_VERSION,
    },
    {
        "tier_id": "context_v1",
        "label": "deterministic context v1",
        "policy_version": CHAT_CONTEXT_POLICY_VERSION,
    },
    {
        "tier_id": "compaction",
        "label": "v1 + rolling Thread Memory",
        "policy_version": CHAT_CONTEXT_POLICY_V2_VERSION,
    },
    {
        "tier_id": "dashboard_tools",
        "label": "compaction + dashboard + Context Tools",
        "policy_version": CHAT_CONTEXT_POLICY_V3_VERSION,
    },
)

PEAK_TOKEN_ALLOWANCE = 1.2
TOTAL_TOKEN_ALLOWANCE = 1.2


@dataclass(frozen=True, slots=True)
class TierSampleRow:
    tier_id: str
    sample_id: str
    total_tokens: int
    block_tokens: dict[str, int]
    fallback: dict[str, Any] | None
    dashboard: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier_id": self.tier_id,
            "sample_id": self.sample_id,
            "total_tokens": self.total_tokens,
            "block_tokens": dict(self.block_tokens),
            "fallback": self.fallback,
            "dashboard": dict(self.dashboard),
        }


@dataclass(frozen=True, slots=True)
class TierAggregate:
    tier_id: str
    peak_input_tokens: int
    total_input_tokens: int
    fallback_count: int
    guardrail_violations: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier_id": self.tier_id,
            "peak_input_tokens": self.peak_input_tokens,
            "total_input_tokens": self.total_input_tokens,
            "fallback_count": self.fallback_count,
            "guardrail_violations": self.guardrail_violations,
        }


@dataclass(frozen=True, slots=True)
class TierComparisonReport:
    tiers: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    rows: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    failures: tuple[dict[str, str], ...] = field(default_factory=tuple)
    passed: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "tiers": list(self.tiers),
            "rows": list(self.rows),
            "failures": list(self.failures),
        }


def evaluate_context_tiers(
    samples: tuple[ContextBaselineSample, ...] | None = None,
) -> TierComparisonReport:
    """Measure every sample through every policy tier and apply A/B gates."""

    baseline_samples = (
        build_context_baseline_samples() if samples is None else samples
    )
    rows: list[TierSampleRow] = []
    failures: list[dict[str, str]] = []
    legacy_totals: dict[str, int] = {}

    tier_aggregates: list[TierAggregate] = []
    for tier in CONTEXT_TIERS:
        policy_version = tier["policy_version"]
        peak_tokens = 0
        total_tokens = 0
        fallback_count = 0
        violations = 0
        for sample in baseline_samples:
            if sample.sample_id == "unknown-policy":
                continue
            extra_input: dict[str, Any] = {
                "editable_fields_by_collection": {},
            }
            if policy_version in {
                CHAT_CONTEXT_POLICY_V2_VERSION,
                CHAT_CONTEXT_POLICY_V3_VERSION,
            }:
                extra_input["thread_memory_state"] = (
                    thread_memory_state_to_jsonable(empty_thread_memory_state())
                )
            frozen_input = {
                **sample.frozen_input,
                "context_policy_version": policy_version,
            }
            result = build_chat_context_manifest(
                policy_version=policy_version,
                frozen_input=frozen_input,
                input_hash=sample.input_hash,
                prebuilt_input=(
                    sample.prebuilt_input
                    if policy_version == LEGACY_CONTEXT_POLICY_VERSION
                    else None
                ),
                extra_input=extra_input,
                provider=sample.provider,
                model_id=sample.model_id,
            )
            fallback = result.fallback
            if fallback is not None:
                fallback_count += 1
                failures.append(
                    {
                        "tier_id": tier["tier_id"],
                        "sample_id": sample.sample_id,
                        "failure": "unexpected_fallback",
                        "detail": fallback.code,
                    }
                )
            manifest = result.manifest.to_jsonable()
            block_tokens = {
                str(block["id"]): int(block["tokens"])
                for block in manifest.get("blocks", [])
            }
            total = int(manifest["total_tokens"])
            peak_tokens = max(peak_tokens, total)
            total_tokens += total
            dashboard = result.dashboard
            violations += len(dashboard.get("guardrail_violations", []))
            remaining = dashboard.get("remaining_tokens")
            if policy_version != LEGACY_CONTEXT_POLICY_VERSION:
                if remaining is None or remaining < 0:
                    failures.append(
                        {
                            "tier_id": tier["tier_id"],
                            "sample_id": sample.sample_id,
                            "failure": "budget_violated",
                            "detail": f"remaining_tokens={remaining}",
                        }
                    )
                if dashboard.get("guardrail_violations"):
                    failures.append(
                        {
                            "tier_id": tier["tier_id"],
                            "sample_id": sample.sample_id,
                            "failure": "guardrail_violation",
                            "detail": json.dumps(
                                dashboard["guardrail_violations"],
                                ensure_ascii=False,
                            ),
                        }
                    )
            rows.append(
                TierSampleRow(
                    tier_id=tier["tier_id"],
                    sample_id=sample.sample_id,
                    total_tokens=total,
                    block_tokens=block_tokens,
                    fallback=(
                        None
                        if fallback is None
                        else {"code": fallback.code, "detail": fallback.detail}
                    ),
                    dashboard=dashboard,
                )
            )
            if tier["tier_id"] == "legacy_full":
                legacy_totals[sample.sample_id] = total
        tier_aggregates.append(
            TierAggregate(
                tier_id=tier["tier_id"],
                peak_input_tokens=peak_tokens,
                total_input_tokens=total_tokens,
                fallback_count=fallback_count,
                guardrail_violations=violations,
            )
        )

    for row in rows:
        if row.tier_id == "legacy_full":
            continue
        reference = legacy_totals.get(row.sample_id)
        if reference is None:
            continue
        allowance = max(
            reference * TOTAL_TOKEN_ALLOWANCE,
            reference + 100,
        )
        if row.total_tokens > allowance:
            failures.append(
                {
                    "tier_id": row.tier_id,
                    "sample_id": row.sample_id,
                    "failure": "total_token_regression",
                    "detail": (
                        f"{row.total_tokens} > legacy {reference} "
                        f"allowance {allowance:.0f}"
                    ),
                }
            )

    legacy_peak = max(
        (aggregate.peak_input_tokens for aggregate in tier_aggregates
         if aggregate.tier_id == "legacy_full"),
        default=0,
    )
    for aggregate in tier_aggregates:
        if aggregate.tier_id == "legacy_full":
            continue
        if aggregate.peak_input_tokens > legacy_peak * PEAK_TOKEN_ALLOWANCE:
            failures.append(
                {
                    "tier_id": aggregate.tier_id,
                    "sample_id": "",
                    "failure": "peak_token_regression",
                    "detail": (
                        f"{aggregate.peak_input_tokens} > legacy {legacy_peak} "
                        f"x {PEAK_TOKEN_ALLOWANCE}"
                    ),
                }
            )

    return TierComparisonReport(
        tiers=tuple(aggregate.as_dict() for aggregate in tier_aggregates),
        rows=tuple(row.as_dict() for row in rows),
        failures=tuple(failures),
        passed=not failures,
    )


def write_context_tier_report(
    report: TierComparisonReport,
    path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the deterministic multi-tier context A/B benchmark"
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=Path(DEFAULT_TIER_REPORT_PATH),
        help="Comparison report path (default: var/benchmark/context-tiers-v1.json)",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit non-zero unless every tier A/B gate passes",
    )
    arguments = parser.parse_args()
    report = evaluate_context_tiers()
    write_context_tier_report(report, arguments.report_path)
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    if arguments.gate and not report.passed:
        raise SystemExit(2)


__all__ = [
    "CONTEXT_TIERS",
    "DEFAULT_TIER_REPORT_PATH",
    "PEAK_TOKEN_ALLOWANCE",
    "TOTAL_TOKEN_ALLOWANCE",
    "TierAggregate",
    "TierComparisonReport",
    "TierSampleRow",
    "evaluate_context_tiers",
    "main",
    "write_context_tier_report",
]

if __name__ == "__main__":
    main()
