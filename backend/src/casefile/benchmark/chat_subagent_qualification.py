"""Frozen three-arm qualification gate for CaseFile Chat read-only subagents."""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from casefile.agent_runtime.model_policy import DEEPSEEK_MODEL_ID
from casefile.benchmark.chat_outcome_eval import ChatOutcomeTask, build_outcome_tasks

SUITE_PATH = (
    Path(__file__).parents[4] / "fixtures" / "chat_subagent_benchmark" / "v1" / "suite.json"
)
MODEL_ID = DEEPSEEK_MODEL_ID
TRIALS = 3


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubagentBenchmarkCase(_StrictModel):
    case_id: str = Field(pattern=r"^(simple|investigation|audit)-[0-9]{2}$")
    category: Literal["simple", "investigation", "audit"]
    base_task_id: str = Field(min_length=1)
    message: str | None = Field(default=None, min_length=1)


class SubagentBenchmarkSuite(_StrictModel):
    schema_version: Literal[1] = 1
    suite_version: Literal["casefile-chat-subagent-v1"] = "casefile-chat-subagent-v1"
    cases: list[SubagentBenchmarkCase] = Field(min_length=24, max_length=24)

    @model_validator(mode="after")
    def validate_matrix(self) -> SubagentBenchmarkSuite:
        ids = [item.case_id for item in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("chat subagent benchmark case ids must be unique")
        counts = {
            category: sum(item.category == category for item in self.cases)
            for category in ("simple", "investigation", "audit")
        }
        if counts != {"simple": 8, "investigation": 8, "audit": 8}:
            raise ValueError("chat subagent benchmark requires an 8/8/8 category matrix")
        return self


@dataclass(frozen=True, slots=True)
class ArmMetrics:
    completion_rate: float
    evidence_accuracy: float
    audit_false_positive_rate: float
    mean_cost_cny: float
    p95_latency_ms: float
    delegation_rate: float
    partial_failure_rate: float


def load_suite(path: Path = SUITE_PATH) -> SubagentBenchmarkSuite:
    return SubagentBenchmarkSuite.model_validate_json(path.read_text(encoding="utf-8"))


def build_suite_tasks(path: Path = SUITE_PATH) -> tuple[ChatOutcomeTask, ...]:
    suite = load_suite(path)
    base = {task.task_id: task for task in build_outcome_tasks()}
    tasks: list[ChatOutcomeTask] = []
    for item in suite.cases:
        source = base.get(item.base_task_id)
        if source is None:
            raise ValueError(f"unknown chat outcome base task: {item.base_task_id}")
        tasks.append(
            replace(
                source,
                task_id=item.case_id,
                message=item.message or source.message,
            )
        )
    return tuple(tasks)


def qualify_reports(
    baseline: dict[str, Any],
    matched_single: dict[str, Any],
    subagents: dict[str, Any],
    *,
    input_price_per_million_cny: Decimal,
    output_price_per_million_cny: Decimal,
) -> dict[str, Any]:
    suite = load_suite()
    categories = {item.case_id: item.category for item in suite.cases}
    arms = {
        "baseline": _validate_rows(baseline, "casefile-chat-tools-v6", categories),
        "matched_single": _validate_rows(matched_single, "casefile-chat-tools-v6", categories),
        "subagents": _validate_rows(subagents, "casefile-chat-tools-v8", categories),
    }
    prices = (input_price_per_million_cny, output_price_per_million_cny)
    metrics = {name: _metrics(rows, categories, prices) for name, rows in arms.items()}
    complex_ids = {case_id for case_id, category in categories.items() if category != "simple"}
    simple_ids = {case_id for case_id, category in categories.items() if category == "simple"}
    complex_rates = {name: _completion_rate(rows, complex_ids) for name, rows in arms.items()}
    simple_rates = {name: _completion_rate(rows, simple_ids) for name, rows in arms.items()}
    complex_costs = {name: _mean_cost(rows, complex_ids, prices) for name, rows in arms.items()}
    complex_p95 = {name: _p95(rows, complex_ids) for name, rows in arms.items()}
    simple_costs = {name: _mean_cost(rows, simple_ids, prices) for name, rows in arms.items()}
    simple_p95 = {name: _p95(rows, simple_ids) for name, rows in arms.items()}
    gates = {
        "complex_quality_vs_baseline": complex_rates["subagents"]
        >= complex_rates["baseline"] + 0.05,
        "complex_quality_vs_matched_single": complex_rates["subagents"]
        >= complex_rates["matched_single"] + 0.05,
        "evidence_accuracy_non_regression": metrics["subagents"].evidence_accuracy
        >= max(metrics["baseline"].evidence_accuracy, metrics["matched_single"].evidence_accuracy),
        "audit_false_positive_non_regression": metrics["subagents"].audit_false_positive_rate
        <= min(
            metrics["baseline"].audit_false_positive_rate,
            metrics["matched_single"].audit_false_positive_rate,
        ),
        "complex_cost": complex_costs["subagents"] <= complex_costs["baseline"] * 1.5,
        "complex_p95": complex_p95["subagents"] <= complex_p95["baseline"] * 1.2,
        "simple_quality": simple_rates["subagents"] >= simple_rates["baseline"],
        "simple_cost": simple_costs["subagents"] <= simple_costs["baseline"] * 1.1,
        "simple_p95": simple_p95["subagents"] <= simple_p95["baseline"] * 1.1,
    }
    paired = {
        comparison: _paired_completion_interval(arms["subagents"], arms[comparison], complex_ids)
        for comparison in ("baseline", "matched_single")
    }
    qualified = all(gates.values()) and all(value["lower"] > 0 for value in paired.values())
    return {
        "suite_version": suite.suite_version,
        "model_id": MODEL_ID,
        "trials": TRIALS,
        "metrics": {name: asdict(value) for name, value in metrics.items()},
        "complex_completion_rates": complex_rates,
        "simple_completion_rates": simple_rates,
        "paired_completion_difference_95ci": paired,
        "gates": gates,
        "qualified": qualified,
        "default_rollout": "enabled" if qualified else "disabled",
    }


def _validate_rows(
    report: dict[str, Any],
    expected_protocol: str,
    categories: Mapping[str, str],
) -> list[dict[str, Any]]:
    if report.get("model_id") != MODEL_ID or int(report.get("trials", 0)) != TRIALS:
        raise ValueError("qualification report has the wrong model or trial count")
    rows = [dict(item) for item in report.get("rows", []) if isinstance(item, dict)]
    expected = {(case_id, trial) for case_id in categories for trial in range(1, TRIALS + 1)}
    actual = {(str(row.get("task_id")), int(row.get("trial_no", 0))) for row in rows}
    if actual != expected or len(rows) != len(expected):
        raise ValueError("qualification report is incomplete or contains extra trials")
    if any(row.get("protocol") != expected_protocol for row in rows):
        raise ValueError("qualification report has a mismatched frozen toolset")
    return rows


def _metrics(
    rows: list[dict[str, Any]],
    categories: Mapping[str, str],
    prices: tuple[Decimal, Decimal],
) -> ArmMetrics:
    evidence_valid = sum(int(row.get("audit_finding_evidence_valid_count", 0)) for row in rows)
    evidence_total = sum(int(row.get("audit_finding_evidence_total_count", 0)) for row in rows)
    clean_rows = [row for row in rows if row.get("task_id") in {"audit-07", "audit-08"}]
    delegated = sum(int(row.get("tool_metrics", {}).get("subagent_tasks", 0)) > 0 for row in rows)
    partial = sum(
        int(row.get("tool_metrics", {}).get("subagent_partial", 0))
        + int(row.get("tool_metrics", {}).get("subagent_failed", 0))
        > 0
        for row in rows
    )
    all_ids = set(categories)
    return ArmMetrics(
        completion_rate=_completion_rate(rows, all_ids),
        evidence_accuracy=evidence_valid / evidence_total if evidence_total else 1.0,
        audit_false_positive_rate=(
            sum(int(row.get("audit_finding_count", 0)) > 0 for row in clean_rows) / len(clean_rows)
            if clean_rows
            else 0.0
        ),
        mean_cost_cny=_mean_cost(rows, all_ids, prices),
        p95_latency_ms=_p95(rows, all_ids),
        delegation_rate=delegated / len(rows),
        partial_failure_rate=partial / len(rows),
    )


def _completion_rate(rows: list[dict[str, Any]], task_ids: set[str]) -> float:
    selected = [bool(row.get("passed")) for row in rows if row.get("task_id") in task_ids]
    return mean(selected) if selected else 0.0


def _row_cost(row: dict[str, Any], prices: tuple[Decimal, Decimal]) -> float:
    input_price, output_price = prices
    value = (
        Decimal(int(row.get("input_tokens", 0))) * input_price
        + Decimal(int(row.get("output_tokens", 0))) * output_price
    ) / Decimal(1_000_000)
    return float(value)


def _mean_cost(
    rows: list[dict[str, Any]],
    task_ids: set[str],
    prices: tuple[Decimal, Decimal],
) -> float:
    values = [_row_cost(row, prices) for row in rows if row.get("task_id") in task_ids]
    return mean(values) if values else 0.0


def _p95(rows: list[dict[str, Any]], task_ids: set[str]) -> float:
    values = sorted(
        float(row.get("elapsed_ms", 0)) for row in rows if row.get("task_id") in task_ids
    )
    if not values:
        return 0.0
    return values[max(0, int(len(values) * 0.95 + 0.999999) - 1)]


def _paired_completion_interval(
    candidate: list[dict[str, Any]],
    control: list[dict[str, Any]],
    task_ids: set[str],
) -> dict[str, float]:
    def by_task(rows: list[dict[str, Any]]) -> dict[str, float]:
        return {
            task_id: mean(bool(row.get("passed")) for row in rows if row.get("task_id") == task_id)
            for task_id in sorted(task_ids)
        }

    left, right = by_task(candidate), by_task(control)
    differences = [left[task_id] - right[task_id] for task_id in sorted(task_ids)]
    rng = random.Random(20260916)
    samples = sorted(mean(rng.choice(differences) for _ in differences) for _ in range(2000))
    return {
        "estimate": mean(differences),
        "lower": samples[int(len(samples) * 0.025)],
        "upper": samples[int(len(samples) * 0.975)],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--matched-single", type=Path, required=True)
    parser.add_argument("--subagents", type=Path, required=True)
    parser.add_argument("--input-price", type=Decimal, required=True)
    parser.add_argument("--output-price", type=Decimal, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = qualify_reports(
        json.loads(args.baseline.read_text(encoding="utf-8")),
        json.loads(args.matched_single.read_text(encoding="utf-8")),
        json.loads(args.subagents.read_text(encoding="utf-8")),
        input_price_per_million_cny=args.input_price,
        output_price_per_million_cny=args.output_price,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
