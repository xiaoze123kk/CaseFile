"""Fixed-denominator scoring for public, paired Quality diagnostics."""

from __future__ import annotations

from collections import Counter
from typing import Any

from casefile.benchmark.prose_quality_eval import GATE_THRESHOLDS
from casefile.domain.narrative_compiler import canonical_json_sha256


def swap_preference(value: str) -> str:
    return {"a": "b", "b": "a", "tie": "tie"}[value]


def score_diagnostic_row(row: dict[str, Any], gold: dict[str, Any]) -> None:
    row.update(
        first_correct=False,
        reverse_correct=False,
        mirrored_consistent=False,
        first_dimension_correct=0,
        reverse_dimension_correct=0,
        first_dimension_results={
            item["dimension"]: False for item in gold["dimension_preferences"]
        },
        reverse_dimension_results={
            item["dimension"]: False for item in gold["dimension_preferences"]
        },
    )
    if row["status"] != "completed":
        return
    first, second = row["predictions"]
    row["first_correct"] = first["overall_preference"] == gold["overall_preference"]
    row["reverse_correct"] = (
        swap_preference(second["overall_preference"]) == gold["overall_preference"]
    )
    row["mirrored_consistent"] = first["overall_preference"] == swap_preference(
        second["overall_preference"]
    )
    for label, prediction in (("first", first), ("reverse", second)):
        row[f"{label}_dimension_results"] = {
            item["dimension"]: (
                item["preference"] if label == "first" else swap_preference(item["preference"])
            )
            == expected["preference"]
            for item, expected in zip(
                prediction["dimension_preferences"], gold["dimension_preferences"], strict=True
            )
        }
        row[f"{label}_dimension_correct"] = sum(
            (item["preference"] if label == "first" else swap_preference(item["preference"]))
            == expected["preference"]
            for item, expected in zip(
                prediction["dimension_preferences"], gold["dimension_preferences"], strict=True
            )
        )


def summarize_arm(rows: list[dict[str, Any]], *, task_count: int = 8) -> dict[str, Any]:
    def metric(key: str, total: int) -> dict[str, int]:
        return {"passed": sum(row[key] for row in rows), "total": total}

    usage: Counter[str] = Counter()
    for row in rows:
        usage.update(row["usage"])
    groups = {}
    for gold in ("a", "b", "tie"):
        selected = [row for row in rows if row["gold_overall"] == gold]
        groups[gold] = {
            "total": len(selected),
            "first_position": dict(
                Counter(
                    row["predictions"][0]["overall_preference"]
                    if row["status"] == "completed"
                    else row["status"]
                    for row in selected
                )
            ),
            "reverse_position": dict(
                Counter(
                    row["predictions"][1]["overall_preference"]
                    if row["status"] == "completed"
                    else row["status"]
                    for row in selected
                )
            ),
            "reverse_mapped_to_source": dict(
                Counter(
                    swap_preference(row["predictions"][1]["overall_preference"])
                    if row["status"] == "completed"
                    else row["status"]
                    for row in selected
                )
            ),
        }
    stability = {}
    for task_id in dict.fromkeys(row["task_id"] for row in rows):
        selected = [row for row in rows if row["task_id"] == task_id]
        completed = [row for row in selected if row["status"] == "completed"]
        stability[task_id] = {
            "completed_trials": len(completed),
            "total": 3,
            "overall_stable": len(completed) == 3
            and len(
                {tuple(p["overall_preference"] for p in row["predictions"]) for row in completed}
            )
            == 1,
            "all_dimensions_stable": len(completed) == 3
            and len({canonical_json_sha256(row["predictions"]) for row in completed}) == 1,
        }
    failures = {
        key: sum(row["status"] == status for row in rows)
        for key, status in (
            ("protocol", "protocol_failed"),
            ("infrastructure", "inconclusive"),
            ("not_run", "not_run"),
        )
    }
    gates = []
    for trial in range(1, 4):
        selected = [
            row for row in rows if row["trial"] == trial and row.get("cohort", "legacy") == "legacy"
        ]
        if not selected:
            continue
        values = {
            "overall_accuracy": sum(row["first_correct"] for row in selected),
            "mirrored_consistency": sum(row["mirrored_consistent"] for row in selected),
            "dimension_accuracy": sum(row["first_dimension_correct"] for row in selected),
        }
        gates.append(
            {
                "trial": trial,
                **values,
                "task_total": 8,
                "dimension_total": 40,
                "passed": all(row["status"] == "completed" for row in selected)
                and all(values[key] >= GATE_THRESHOLDS[f"{key}_min"] for key in values),
            }
        )
    return {
        "first_accuracy": metric("first_correct", task_count * 3),
        "reverse_accuracy": metric("reverse_correct", task_count * 3),
        "mirrored_consistency": metric("mirrored_consistent", task_count * 3),
        "first_dimension_accuracy": metric("first_dimension_correct", task_count * 15),
        "reverse_dimension_accuracy": metric("reverse_dimension_correct", task_count * 15),
        "failure_counts": failures,
        "gold_groups": groups,
        "task_stability": stability,
        "legacy_development_gates": gates,
        "legacy_gate_thresholds": GATE_THRESHOLDS,
        "call_count": sum(row["call_count"] for row in rows),
        "transport_attempt_count": sum(row["transport_attempt_count"] for row in rows),
        "latency_ms": sum(row["latency_ms"] for row in rows),
        "usage": dict(usage),
    }


def diagnostic_comparison(arms: dict[str, Any], *, complete: bool, live: bool) -> dict[str, Any]:
    baseline, candidate = arms["baseline"], arms["candidate"]
    checks = {
        "complete_and_stable": complete,
        "no_failures": not any(sum(arm["failure_counts"].values()) for arm in arms.values()),
        "mirrored_strictly_improved": candidate["mirrored_consistency"]["passed"]
        > baseline["mirrored_consistency"]["passed"],
        **{
            f"{metric}_non_decreasing": candidate[metric]["passed"] >= baseline[metric]["passed"]
            for metric in (
                "first_accuracy",
                "reverse_accuracy",
                "first_dimension_accuracy",
                "reverse_dimension_accuracy",
            )
        },
    }
    return {
        "checks": checks,
        "criteria_met": all(checks.values()),
        "worth_further_validation": live and all(checks.values()),
        "qualified": False,
        "qualification_eligible": False,
    }


def diagnostic_markdown(report: dict[str, Any]) -> str:
    rows = [
        "# B3 公开开发评审对照",
        "",
        f"Attempt：`{report['attempt_id']}`；模式：`{report['mode']}`；状态：`{report['status']}`。",
        "",
        "| 指标 | 基线 | 候选 |",
        "|---|---:|---:|",
    ]
    for key, title in (
        ("first_accuracy", "首位置准确率"),
        ("reverse_accuracy", "反向位置准确率"),
        ("mirrored_consistency", "镜像一致率"),
        ("first_dimension_accuracy", "首位置五维准确率"),
        ("reverse_dimension_accuracy", "反向位置五维准确率"),
    ):
        values = [report["arms"][arm][key] for arm in ("baseline", "candidate")]
        rows.append(
            f"| {title} | {values[0]['passed']}/{values[0]['total']} "
            f"| {values[1]['passed']}/{values[1]['total']} |"
        )
    rows.extend(
        [
            "",
            "满足继续验证条件。"
            if report["comparison"]["worth_further_validation"]
            else "未满足继续验证条件；保持活动 v2。",
            "",
            f"本报告只覆盖 {report['task_count']} 组公开开发样例，每组 3 个 Trial；"
            "重复 Trial 不等于新增独立样本。"
            + (
                "候选两次比较共享单稿评估，不是独立评审员共识。"
                if report["shared_candidate_assessments"]
                else "两组均使用两次位置比较，候选只补充节奏标准。"
            )
            + "Fake 只证明实现行为，所有结果 qualified=false。",
            "",
            f"源码：`{report['source_before']['revision']}`；源码稳定：`{report['source_stable']}`；数据稳定：`{report['data_stable']}`。",
            f"实验指纹：`{report['experiment_hash']}`；报告指纹：`{report['report_hash']}`。",
            "",
            "## 运行与失败",
            "",
        ]
    )
    for arm in ("baseline", "candidate"):
        summary = report["arms"][arm]
        rows.append(
            f"- {arm}：调用 {summary['call_count']}，"
            f"transport {summary['transport_attempt_count']}，"
            f"tokens {summary['usage'].get('total_tokens', 0)}，"
            f"延迟 {summary['latency_ms']} ms；失败/未运行 `{summary['failure_counts']}`。"
        )
    if "cohorts" in report:
        rows.extend(
            [
                "",
                "## 节奏维度分组",
                "",
                "| 样例组 | 协议 | 正向节奏正确 | 反向节奏正确 |",
                "|---|---|---:|---:|",
            ]
        )
        for cohort in ("legacy", "redundant", "functional"):
            for arm in ("baseline", "candidate"):
                selected = [r for r in report["rows"] if r["cohort"] == cohort and r["arm"] == arm]
                counts = [
                    sum(
                        r[f"{side}_dimension_results"]["dramatic_progression_pacing"]
                        for r in selected
                    )
                    for side in ("first", "reverse")
                ]
                rows.append(
                    f"| {cohort} | {arm} | {counts[0]}/{len(selected)} "
                    f"| {counts[1]}/{len(selected)} |"
                )
        rows.extend(["", "未满足的冻结条件："])
        rows.extend(
            f"- `{key}`" for key, passed in report["comparison"]["checks"].items() if not passed
        )
        rows.extend(
            [
                "",
                "新样例是经 Codex 审阅的合成开发场景，语义凭证不是 Live Council 结果。"
                "旧 tradeoff_tie 整体 Gold 存在主观权衡，保留原标注单独统计；"
                "详见 gold-review.json。",
            ]
        )
    rows.extend(
        [
            "",
            "## 逐题结果",
            "",
            "| 样例 | 组 | 首位正确 | 反位正确 | 镜像一致 | 整体跨 Trial 稳定 |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for task_id in report["arms"]["baseline"]["task_stability"]:
        for arm in ("baseline", "candidate"):
            selected = [r for r in report["rows"] if r["task_id"] == task_id and r["arm"] == arm]
            counts = [
                sum(r[key] for r in selected)
                for key in ("first_correct", "reverse_correct", "mirrored_consistent")
            ]
            stable = report["arms"][arm]["task_stability"][task_id]["overall_stable"]
            rows.append(
                f"| {task_id} | {arm} | {counts[0]}/3 | {counts[1]}/3 | {counts[2]}/3 | {stable} |"
            )
    return "\n".join(rows) + "\n"
