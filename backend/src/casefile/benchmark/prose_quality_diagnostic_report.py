"""Fixed-denominator diagnostics; development selection never grants qualification."""

from collections import Counter
from typing import Any

from casefile.agent_runtime.prose_quality_config import QUALITY_PRO_DIAGNOSTIC, QUALITY_V2
from casefile.domain.narrative_compiler import QUALITY_DIMENSIONS


def quality_row(
    task: dict[str, Any], execution: Any, repeat: int, candidate: str
) -> dict[str, Any]:
    reports = execution.reports
    completed = execution.status == "completed" and len(reports) == 2

    def mapped(report: dict[str, Any], preference: str) -> str:
        return "tie" if preference == "tie" else str(report["position_mapping"][preference])

    overall = [mapped(r, r["overall_preference"]) for r in reports] if completed else []
    dimensions = (
        [[mapped(r, p["preference"]) for p in r["dimension_preferences"]] for r in reports]
        if completed
        else []
    )
    gold = {"a": "original", "b": "polished", "tie": "tie"}[task["gold"]["overall_preference"]]
    return {
        "task_id": task["task_id"],
        "focus": task["focus"],
        "repeat": repeat,
        "candidate": candidate,
        "status": execution.status,
        "error_code": execution.error_code,
        "gold": task["gold"]["overall_preference"],
        "overall": overall,
        "correct": completed and overall[0] == gold,
        "mirrored": completed and overall[0] == overall[1],
        "dimensions": dimensions,
        "dimension_mirrored": [
            completed and dimensions[0][i] == dimensions[1][i] for i in range(5)
        ],
    }


def summarize_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for config in (QUALITY_V2, QUALITY_PRO_DIAGNOSTIC):
        selected = [r for r in rows if r["candidate"] == config.config_id]
        repetitions = []
        for repeat in range(3):
            items = [r for r in selected if r["repeat"] == repeat]
            correct, mirrored = sum(r["correct"] for r in items), sum(r["mirrored"] for r in items)
            failures = dict(Counter(r["status"] for r in items if r["status"] != "completed"))
            repetitions.append(
                {
                    "repeat": repeat,
                    "total": 24,
                    "completed": len(items) - sum(failures.values()),
                    "correct": correct,
                    "mirrored": mirrored,
                    "failures": failures,
                    "groups": {
                        g: {
                            "correct": sum(r["correct"] for r in items if r["gold"] == g),
                            "total": 8,
                        }
                        for g in ("a", "b", "tie")
                    },
                    "dimension_mirrored": {
                        d: sum(r["dimension_mirrored"][i] for r in items)
                        for i, d in enumerate(QUALITY_DIMENSIONS)
                    },
                    "passed": len(items) == 24
                    and not failures
                    and correct >= 21
                    and mirrored >= 23,
                }
            )
        consistent = 0
        for task_id in {r["task_id"] for r in selected}:
            trials = [r for r in selected if r["task_id"] == task_id]
            if (
                len(trials) == 3
                and all(r["status"] == "completed" for r in trials)
                and all(r["overall"] == trials[0]["overall"] for r in trials)
            ):
                consistent += 1
        results[config.config_id] = {
            "repetitions": repetitions,
            "repeat_consistent": consistent,
            "repeat_total": 24,
            "passed": all(r["passed"] for r in repetitions),
        }
    eligible = [name for name, result in results.items() if result["passed"]]
    eligible.sort(
        key=lambda name: (
            min(r["mirrored"] for r in results[name]["repetitions"]),
            min(r["correct"] for r in results[name]["repetitions"]),
            name == QUALITY_V2.config_id,
        ),
        reverse=True,
    )
    return {
        "candidates": results,
        "selected": eligible[0] if eligible else None,
        "qualified": False,
    }


def polish_row(task: dict[str, Any], execution: Any, repeat: int) -> dict[str, Any]:
    preservation = execution.preservation
    verdicts = (
        {r["check_id"]: r["final_verdict"] for r in preservation.consensus["checks"]}
        if preservation and preservation.consensus
        else {}
    )
    finalized = execution.status in {"finalized_original", "finalized_polished"}
    adopted = execution.status == "finalized_polished"
    accepted = execution.accepted_render
    exact = bool(
        accepted
        and [b["text"] for b in accepted["blocks"]]
        == [b["text"] for b in task["original_render"]["blocks"]]
    )
    critical = [
        c["check_id"]
        for c in task["checklist"]["checks"]
        if c["kind"] in {"event_modality", "reveal_control", "pov_knowledge", "major_hallucination"}
    ]
    return {
        "task_id": task["task_id"],
        "focus": task["focus"],
        "repeat": repeat,
        "status": execution.status,
        "error_code": execution.error_code,
        "verdicts": verdicts,
        "preservation": bool(
            preservation
            and preservation.status == "completed"
            and preservation.consensus
            and preservation.consensus["scene_verdict"] == "pass"
        ),
        "quality_non_loss": finalized,
        "adopted": adopted,
        "rejected": finalized and not adopted,
        "exact_rollback": finalized and not adopted and exact,
        "critical_regressions": [c for c in critical if verdicts.get(c) != "pass"]
        if adopted
        else [],
        "selection_reason": execution.selection_reason,
    }


def summarize_preservation(
    originals: list[dict[str, Any]], polished: list[dict[str, Any]]
) -> dict[str, Any]:
    unstable, original_failed = [], []
    for task_id in sorted({r["task_id"] for r in originals}):
        items = [r for r in originals if r["task_id"] == task_id]
        if len(items) != 3 or not all(r["status"] == "completed" and r["passed"] for r in items):
            original_failed.append(task_id)
        semantic = [r for r in items if r["status"] == "completed"]
        if semantic and any(r["verdicts"] != semantic[0]["verdicts"] for r in semantic):
            unstable.append(task_id)
    repetitions = []
    for repeat in range(3):
        items = [r for r in polished if r["repeat"] == repeat]
        preservation = sum(r["preservation"] for r in items)
        adopted = sum(r["adopted"] for r in items)
        non_loss = sum(r["quality_non_loss"] for r in items)
        failures = dict(
            Counter(
                r["status"]
                for r in items
                if r["status"] not in {"finalized_original", "finalized_polished"}
            )
        )
        regressions = sum(len(r["critical_regressions"]) for r in items)
        rollback_ok = all(r["exact_rollback"] for r in items if r["rejected"])
        repetitions.append(
            {
                "repeat": repeat,
                "total": 24,
                "preservation": preservation,
                "adopted": adopted,
                "quality_non_loss": non_loss,
                "failures": failures,
                "critical_regressions": regressions,
                "exact_rollback": rollback_ok,
                "rollback_reasons": dict(
                    Counter(r["selection_reason"] for r in items if r["rejected"])
                ),
                "passed": len(items) == 24
                and preservation == 24
                and adopted >= 18
                and non_loss >= 22
                and not failures
                and regressions == 0
                and rollback_ok,
            }
        )
    attribution = []
    for row in polished:
        if row["status"] in {"protocol_failed", "inconclusive", "not_run"}:
            category = "protocol_or_infrastructure"
        elif row["task_id"] in unstable:
            category = "original_judge_unstable"
        elif row["task_id"] in original_failed:
            category = "original_full_council_failed"
        elif not row["preservation"]:
            category = "suspected_polish_damage"
        else:
            category = "preserved"
        attribution.append(
            {
                "task_id": row["task_id"],
                "repeat": row["repeat"],
                "category": category,
                "failed_checks": [k for k, v in row["verdicts"].items() if v != "pass"],
            }
        )
    original_gate = len(originals) == 72 and not original_failed
    return {
        "original_gate": original_gate,
        "original_failed": original_failed,
        "original_unstable": unstable,
        "repetitions": repetitions,
        "attribution": attribution,
        "passed": original_gate and all(r["passed"] for r in repetitions),
        "quality_non_loss_definition": "finalized_original_or_polished_including_exact_rollback",
    }
