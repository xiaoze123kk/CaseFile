"""Reviewed public pacing cases and a prospective, single-rubric experiment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_quality_critic import (
    PROSE_QUALITY_COMPONENT_HASH,
    PairwiseQualityPolicy,
)
from casefile.benchmark.prose_quality_eval import ROOT, load_prose_quality_dev_suite
from casefile.domain.narrative_compiler import (
    QUALITY_DIMENSIONS,
    canonical_json_sha256,
    validate_quality_pair_inputs,
    validate_semantic_acceptance,
)

PACING_ROOT = ROOT / "fixtures/prose_quality_benchmark/pacing-v1"
PACING_PROMPT = "prose-quality-pairwise-v4"
PACING_TASK_IDS = (
    "pacing_archive_redundant",
    "pacing_archive_suspense",
    "pacing_ferry_redundant",
    "pacing_ferry_suspense",
)
PACING_DIMENSION = "dramatic_progression_pacing"


def pacing_component() -> dict[str, Any]:
    value = {
        "component_version": "prose-quality-pacing-rubric-v1",
        "base_component_hash": PROSE_QUALITY_COMPONENT_HASH,
        "prompt_version": PACING_PROMPT,
        "prompt_hash": load_prompt("prose_quality_pairwise", PACING_PROMPT).system_prompt_sha256,
        "changed_dimension": PACING_DIMENSION,
        "calls_per_trial": 2,
    }
    return {**value, "component_hash": canonical_json_sha256(value)}


def pacing_policy() -> PairwiseQualityPolicy:
    return PairwiseQualityPolicy(PACING_PROMPT, pacing_component()["component_hash"])


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("pacing_json_object_required")
    return value


def load_pacing_package() -> dict[str, Any]:
    legacy = load_prose_quality_dev_suite()
    tasks = list(legacy["tasks"])
    for task_id in PACING_TASK_IDS:
        path = PACING_ROOT / "tasks" / f"{task_id}.json"
        asset = _read(path)
        if asset["task_id"] != task_id or asset["content_hash"] != canonical_json_sha256(
            {k: v for k, v in asset.items() if k != "content_hash"}
        ):
            raise ValueError("pacing_asset_hash_invalid")
        checklist = asset["checklist"]
        if checklist["source"]["scene_plan_hash"] != canonical_json_sha256(
            asset["synthetic_source"]
        ):
            raise ValueError("pacing_synthetic_source_invalid")
        gold = asset["gold"]
        if [item["dimension"] for item in gold["dimension_preferences"]] != list(
            QUALITY_DIMENSIONS
        ) or any(
            p not in ("a", "b", "tie")
            for p in [
                gold["overall_preference"],
                *[item["preference"] for item in gold["dimension_preferences"]],
            ]
        ):
            raise ValueError("pacing_gold_invalid")
        for side in ("a", "b"):
            render = asset[f"render_{side}"]
            consensus = asset[f"semantic_consensus_{side}"]
            validate_semantic_acceptance(
                consensus, checklist=checklist, render=render, profile=asset["profile"]
            )
            reviews = asset["semantic_reviews"][side]
            if [r["check_id"] for r in reviews] != [
                c["check_id"] for c in checklist["checks"]
            ] or consensus["judge_report_hashes"] != [canonical_json_sha256(reviews)]:
                raise ValueError("pacing_review_binding_invalid")
            text = render["blocks"][0]["text"]
            for review, fact in zip(reviews, asset["synthetic_source"]["facts"], strict=True):
                if (
                    review["text"] != fact
                    or text[review["start_char"] : review["end_char"]] != fact
                ):
                    raise ValueError("pacing_review_evidence_invalid")
        validate_quality_pair_inputs(
            checklist=checklist,
            original_render=asset["render_a"],
            polished_render=asset["render_b"],
            profile=asset["profile"],
            preservation_consensus=asset["semantic_consensus_b"],
        )
        if (
            asset["review"]["semantic_origin"] != "reviewed_synthetic_fixture_not_live_council"
            or asset["review"]["reviewer_independence"] is not False
            or asset["review"]["qualification_eligible"] is not False
        ):
            raise ValueError("pacing_review_origin_invalid")
        descriptor = {
            "task_id": task_id,
            "focus": asset["group"],
            "task_asset": {
                "path": path.relative_to(ROOT).as_posix(),
                "hash": canonical_json_sha256(asset),
            },
            "pair_fingerprint": canonical_json_sha256(
                {"a": asset["render_a"], "b": asset["render_b"], "gold": gold}
            ),
        }
        tasks.append({"asset": asset, "descriptor": descriptor})
    return {
        "suite": {"suite_hash": canonical_json_sha256([task["descriptor"] for task in tasks])},
        "legacy": legacy,
        "tasks": tasks,
    }


def expected_pacing_experiment(package: dict[str, Any]) -> dict[str, Any]:
    from casefile.benchmark.prose_quality_diagnostic import expected_experiment

    base = expected_experiment(package["legacy"])
    base.pop("experiment_hash")
    base.update(
        schema_id="casefile.prose-quality-pacing-experiment.v1",
        experiment_id="b3-public-pacing-rubric-v1",
        suite_hash=package["suite"]["suite_hash"],
        task_count=12,
        task_fingerprints=[task["descriptor"] for task in package["tasks"]],
        call_budget={"baseline": 72, "candidate": 72, "total": 144},
        candidate=pacing_component(),
        review_hash=canonical_json_sha256(_read(PACING_ROOT / "gold-review.json")),
        comparison_policy="redundancy-both-position-improvement-functional-and-legacy-nonloss-v1",
        reuse_policy="no-response-reuse-two-independent-position-calls",
        schedule=[],
    )
    for index, task in enumerate(package["tasks"]):
        for trial in range(1, 4):
            order = (
                ("baseline", "candidate")
                if (index + trial - 1) % 2 == 0
                else ("candidate", "baseline")
            )
            base["schedule"].extend(
                {"task_id": task["asset"]["task_id"], "trial": trial, "arm": arm} for arm in order
            )
    return {**base, "experiment_hash": canonical_json_sha256(base)}


def load_pacing_experiment() -> tuple[dict[str, Any], dict[str, Any]]:
    package = load_pacing_package()
    descriptor = _read(PACING_ROOT / "experiment.json")
    if descriptor != expected_pacing_experiment(package):
        raise ValueError("pacing_experiment_drift")
    return descriptor, package


def pacing_comparison(rows: list[dict[str, Any]], *, complete: bool, live: bool) -> dict[str, Any]:
    def count(arm: str, cohort: str, key: str, dimension: str | None = None) -> int:
        values = [r for r in rows if r["arm"] == arm and (r["cohort"] == cohort or cohort == "all")]
        return sum(r[key][dimension] if dimension else r[key] for r in values)

    checks = {
        "complete_and_stable": complete,
        "zero_failures": all(r["status"] == "completed" for r in rows),
    }
    for side in ("first", "reverse"):
        key = f"{side}_dimension_results"
        checks[f"new_{side}_pacing_improves"] = sum(
            count("candidate", c, key, PACING_DIMENSION) for c in ("redundant", "functional")
        ) > sum(count("baseline", c, key, PACING_DIMENSION) for c in ("redundant", "functional"))
        checks[f"functional_{side}_pacing_nonloss"] = count(
            "candidate", "functional", key, PACING_DIMENSION
        ) >= count("baseline", "functional", key, PACING_DIMENSION)
        checks[f"redundant_{side}_pacing_improves"] = count(
            "candidate", "redundant", key, PACING_DIMENSION
        ) > count("baseline", "redundant", key, PACING_DIMENSION)
        checks[f"legacy_{side}_overall_nonloss"] = count(
            "candidate", "legacy", f"{side}_correct"
        ) >= count("baseline", "legacy", f"{side}_correct")
        checks[f"legacy_{side}_pacing_nonloss"] = count(
            "candidate", "legacy", key, PACING_DIMENSION
        ) >= count("baseline", "legacy", key, PACING_DIMENSION)
        for dimension in QUALITY_DIMENSIONS:
            if dimension != PACING_DIMENSION:
                checks[f"{side}_{dimension}_nonloss"] = count(
                    "candidate", "all", key, dimension
                ) >= count("baseline", "all", key, dimension)
                checks[f"legacy_{side}_{dimension}_nonloss"] = count(
                    "candidate", "legacy", key, dimension
                ) >= count("baseline", "legacy", key, dimension)
    checks["legacy_mirror_nonloss"] = count("candidate", "legacy", "mirrored_consistent") >= count(
        "baseline", "legacy", "mirrored_consistent"
    )
    checks["new_mirror_nonloss"] = sum(
        count("candidate", c, "mirrored_consistent") for c in ("redundant", "functional")
    ) >= sum(count("baseline", c, "mirrored_consistent") for c in ("redundant", "functional"))
    return {
        "checks": checks,
        "criteria_met": all(checks.values()),
        "worth_further_validation": live and all(checks.values()),
        "qualified": False,
        "qualification_eligible": False,
    }
