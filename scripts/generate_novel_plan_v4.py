"""Author-time freezer for Novel Plan v4 assets; benchmark runtime never derives obligations."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from casefile.domain.narrative_compiler import (
    build_planner_input_bundle_v3,
    canonical_json_sha256,
    validate_novel_plan_candidate,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "fixtures" / "novel_plan_benchmark" / "v3"
TARGET = ROOT / "fixtures" / "novel_plan_benchmark" / "v4"


def main() -> None:
    suite = _read(SOURCE / "suite.json")
    target_suite = copy.deepcopy(suite)
    target_suite["schema_id"] = "benchmark.novel-plan-suite.v4"
    target_suite["suite_id"] = "novel-plan-capability-v4"
    target_suite["promotion_gate"] = {
        "g2_passed_trials_min": 65,
        "pass_at_3_tasks_min": 24,
        "all_three_tasks_min": 18,
        "semantic_valid_trials_min": 67,
        "structural_exhaustion_max": 0,
        "resolution_missing_max": 0,
        "unsafe_trials_max": 0,
        "infrastructure_failures_max": 0,
        "repair_g2_regression_max": 0,
        "solver_g2_regression_max": 0,
        "g3_bootstrap_seed": 20260826,
        "g3_bootstrap_iterations": 10000,
        "g3_mean_delta_lower_bound_min": -0.03,
        "g3_dimension_mean_delta_min": -0.05,
    }
    baseline = _read(TARGET / "g3_baseline_v3.json")
    target_suite["g3_baseline"] = {
        "path": "g3_baseline_v3.json",
        "hash": canonical_json_sha256(baseline),
        "source_fingerprint": baseline["source_fingerprint"],
    }
    (TARGET / "inputs" / "v3").mkdir(parents=True, exist_ok=True)
    (TARGET / "references").mkdir(parents=True, exist_ok=True)

    for task, target_task in zip(suite["tasks"], target_suite["tasks"], strict=True):
        task_id = str(task["task_id"])
        source_input = _read(SOURCE / task["planner_inputs"]["v2"]["path"])
        exposure = copy.deepcopy(source_input["exposure_plan"])
        if exposure is None or not exposure["frozen_payload"].get("entries"):
            raise ValueError(f"v4 capability input requires Exposure entries: {task_id}")
        entry = exposure["frozen_payload"]["entries"][0]
        obligations = _obligations(task, source_input, entry)
        entry["planning_obligations"] = obligations
        exposure["content_hash"] = canonical_json_sha256(exposure["frozen_payload"])
        bundle = build_planner_input_bundle_v3(
            narrative_ir=source_input["narrative_ir"],
            exposure=exposure,
            profile=source_input["profile"],
            compile_mode="canonical",
        )
        input_path = TARGET / "inputs" / "v3" / f"{task_id}.json"
        _write(input_path, bundle)
        target_task["planner_inputs"] = {
            "v3": {
                "path": f"inputs/v3/{task_id}.json",
                "hash": canonical_json_sha256(bundle),
            }
        }

        reference = _read(SOURCE / task["reference"])
        _satisfy_typed_obligations(reference, obligations, entry["entry_key"])
        validate_novel_plan_candidate(reference, planner_input=bundle)
        reference_path = TARGET / "references" / f"{task_id}.json"
        _write(reference_path, reference)
        target_task["reference"] = f"references/{task_id}.json"

    _write(TARGET / "suite.json", target_suite)


def _obligations(
    task: dict[str, Any],
    bundle: dict[str, Any],
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    task_id = str(task["task_id"])
    object_refs = sorted(
        (
            envelope["object_ref"]
            for values in bundle["narrative_ir"]["objects"].values()
            for envelope in values
        ),
        key=lambda ref: (ref["object_type"], ref["object_id"]),
    )
    participant_refs = [ref for ref in object_refs if ref["object_type"] == "entity"]
    obligations: list[dict[str, Any]] = []
    for invariant in task["outcome_invariants"]:
        kind = invariant["kind"]
        if kind == "min_distinct_participant_refs":
            minimum = int(invariant["value"])
            if len(participant_refs) < minimum:
                raise ValueError(f"Insufficient eligible participant refs: {task_id}")
            obligations.append(
                {
                    "kind": "participant_coverage",
                    "obligation_key": f"obligation_{task_id}_participants",
                    "level": "hard",
                    "eligible_refs": participant_refs,
                    "min_distinct": minimum,
                }
            )
        elif kind == "basis_refs_include_all":
            required = invariant["refs"]
            obligation_kind = (
                "hypothesis_coverage"
                if all(ref["object_type"] == "hypothesis" for ref in required)
                else "basis_ref_coverage"
            )
            obligations.append(
                {
                    "kind": obligation_kind,
                    "obligation_key": f"obligation_{task_id}_basis",
                    "level": "hard",
                    "required_refs": required,
                }
            )
    if not obligations:
        obligations.append(
            {
                "kind": "basis_ref_coverage",
                "obligation_key": f"obligation_{task_id}_context",
                "level": "soft",
                "required_refs": entry["refs"][:1],
            }
        )
    return obligations


def _satisfy_typed_obligations(
    candidate: dict[str, Any],
    obligations: list[dict[str, Any]],
    entry_key: str,
) -> None:
    scene = next(
        item
        for item in candidate["scenes"]
        if any(placement["entry_key"] == entry_key for placement in item["exposure"])
    )
    for obligation in obligations:
        if obligation["level"] != "hard":
            continue
        if obligation["kind"] == "participant_coverage":
            scene["participant_refs"] = _merge_refs(
                scene["participant_refs"],
                obligation["eligible_refs"][: obligation["min_distinct"]],
            )
        else:
            scene["basis_refs"] = _merge_refs(
                scene["basis_refs"], obligation["required_refs"]
            )


def _merge_refs(first: list[dict[str, str]], second: list[dict[str, str]]) -> list[dict[str, str]]:
    values = {(item["object_type"], item["object_id"]): item for item in [*first, *second]}
    return [values[key] for key in sorted(values)]


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
