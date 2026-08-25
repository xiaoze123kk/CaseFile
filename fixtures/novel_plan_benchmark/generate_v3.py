"""Generate the audited v3 Novel Plan suite with frozen v1 and v2 inputs."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from casefile.domain.narrative_compiler import (
    build_planner_input_bundle,
    build_planner_input_bundle_v2,
    canonical_json_sha256,
    project_narrative_ir_json,
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "fixtures" / "novel_plan_benchmark" / "v3"
V2_GENERATOR = Path(__file__).with_name("generate_v2.py")
SPEC = importlib.util.spec_from_file_location("novel_plan_generate_v2", V2_GENERATOR)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load the v2 Novel Plan fixture generator")
V2 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V2)

FORMAL_PROVIDER = "deepseek"
FORMAL_MODEL_ID = "deepseek-v4-pro"


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _requirement_text(invariants: list[dict[str, Any]]) -> str:
    requirements: list[str] = []
    for invariant in invariants:
        kind = invariant["kind"]
        if kind == "all_presentation_modes":
            requirements.append(f"所有场景的 presentation_mode 必须属于 {invariant['allowed']}。")
        elif kind == "presentation_mode_present":
            requirements.append(f"至少一个场景必须使用 {invariant['value']} presentation_mode。")
        elif kind == "purpose_order":
            requirements.append(f"场景 purpose 必须依次出现 {' -> '.join(invariant['values'])}。")
        elif kind == "purpose_present":
            requirements.append(f"至少一个场景的 purpose 必须为 {invariant['value']}。")
        elif kind == "dependency_chain_min_length":
            requirements.append(f"场景依赖链最少覆盖 {invariant['value']} 个场景。")
        elif kind == "min_distinct_participant_refs":
            requirements.append(f"全计划至少使用 {invariant['value']} 个不同 participant_refs。")
        elif kind == "basis_refs_include_all":
            keys = [f"{item['object_type']}:{item['object_id']}" for item in invariant["refs"]]
            requirements.append(f"全计划 basis_refs 必须覆盖 {keys}。")
        elif kind == "exposure_action_present":
            requirements.append(
                f"Exposure {invariant['entry_key']} 必须出现动作 {invariant['action']}。"
            )
        elif kind == "resolution_actions":
            keys = [item["object_id"] for item in invariant["refs"]]
            requirements.append(f"Resolution {keys} 的终态动作必须属于 {invariant['allowed']}。")
        elif kind == "resolution_in_final_scene":
            requirements.append("所有 Resolution 终态必须只放在最后一个场景。")
        elif kind == "flashback_after_event":
            requirements.append(
                "阅读顺序中必须先呈现 "
                f"{invariant['later_event_ref']['object_id']}，再以 flashback 呈现 "
                f"{invariant['earlier_event_ref']['object_id']}。"
            )
        else:
            raise ValueError(f"Unknown outcome invariant: {kind}")
    return " 能力任务的明确要求：" + " ".join(requirements)


def _audit(invariant: dict[str, Any]) -> dict[str, Any]:
    runtime = invariant["kind"] == "all_presentation_modes"
    audited = dict(invariant)
    audited["expectation_class"] = "runtime_hard" if runtime else "capability"
    audited["input_evidence_paths"] = (
        ["/planning_constraints/allowed_presentation_modes"]
        if invariant["kind"] == "all_presentation_modes"
        else ["/exposure_plan/frozen_payload/entries/0/note"]
    )
    if runtime:
        audited["validator_reason_codes"] = [
            "compiler_story_plan_presentation_mode_invalid"
        ]
    return audited


def main() -> None:
    narrative_ir = project_narrative_ir_json(V2._rich_casefile())
    tasks: list[dict[str, Any]] = []
    for capability in V2.CAPABILITIES:
        for variant in V2.VARIANTS:
            task_id = f"{capability}__{variant}"
            invariants = V2._outcome_invariants(capability, variant)
            exposure = V2._exposure(capability, variant)
            exposure["frozen_payload"]["entries"][0]["note"] += _requirement_text(invariants)
            exposure["content_hash"] = canonical_json_sha256(exposure["frozen_payload"])
            profile = V2._profile(capability, variant)
            inputs = {
                "v1": build_planner_input_bundle(
                    narrative_ir=narrative_ir,
                    exposure=exposure,
                    profile=profile,
                    compile_mode="canonical",
                ),
                "v2": build_planner_input_bundle_v2(
                    narrative_ir=narrative_ir,
                    exposure=exposure,
                    profile=profile,
                    compile_mode="canonical",
                ),
            }
            frozen_inputs: dict[str, dict[str, str]] = {}
            for version, planner_input in inputs.items():
                relative_path = f"inputs/{version}/{task_id}.json"
                _write(OUTPUT / relative_path, planner_input)
                frozen_inputs[version] = {
                    "path": relative_path,
                    "hash": canonical_json_sha256(planner_input),
                }
            reference_path = f"references/{task_id}.json"
            _write(OUTPUT / reference_path, V2._reference(capability, variant))
            tasks.append(
                {
                    "task_id": task_id,
                    "primary_capability": capability,
                    "variant": variant,
                    "planner_inputs": frozen_inputs,
                    "reference": reference_path,
                    "outcome_invariants": [_audit(item) for item in invariants],
                }
            )
    _write(
        OUTPUT / "suite.json",
        {
            "schema_id": "benchmark.novel-plan-suite.v3",
            "suite_id": "novel-plan-capability-v3",
            "formal_qualification": {
                "provider": FORMAL_PROVIDER,
                "planner_model_id": FORMAL_MODEL_ID,
                "quality_grader_model_id": FORMAL_MODEL_ID,
                "trials_per_task": 3,
            },
            "promotion_gate": {
                "g2_passed_trials_min": 48,
                "pass_at_3_tasks_min": 20,
                "all_three_tasks_min": 12,
                "semantic_valid_trials_min": 67,
                "temporal_rejections_max": 5,
                "resolution_missing_max": 0,
                "unsafe_trials_max": 0,
                "infrastructure_failures_max": 0,
            },
            "tasks": tasks,
        },
    )


if __name__ == "__main__":
    main()
