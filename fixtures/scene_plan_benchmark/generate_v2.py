"""Build the active G3/G4 ScenePlan benchmark suite from frozen v1 audit inputs."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from casefile.agent_runtime.provider_adapters.fake import FakeProvider  # noqa: E402
from casefile.agent_runtime.scene_compiler import (  # noqa: E402
    execute_scene_semantic_fill,
)
from casefile.domain.narrative_compiler import (  # noqa: E402
    build_scene_compiler_input_v2,
    build_scene_compiler_model_view,
    canonical_json_sha256,
    compile_scene_plan_v2,
    scene_plan_v2_semantic_signature,
    validate_scene_plan_v2,
)

V1_ROOT = ROOT / "fixtures" / "scene_plan_benchmark" / "v1"
PLANNER_ROOT = ROOT / "fixtures" / "novel_plan_benchmark" / "v4"
OUTPUT_ROOT = ROOT / "fixtures" / "scene_plan_benchmark" / "v2"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON root must be an object: {path}")
    return value


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _v1_contract(contract: dict[str, Any]) -> dict[str, Any]:
    return {**contract, "path": f"../v1/{contract['path']}"}


def _runtime_bundle(
    task: dict[str, Any],
    legacy_bundle: dict[str, Any],
    planner_tasks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    source_task = planner_tasks[str(task["source_task_id"])]
    planner_contract = source_task["planner_inputs"]["v3"]
    planner_input = _read(PLANNER_ROOT / planner_contract["path"])
    if canonical_json_sha256(planner_input) != planner_contract["hash"]:
        raise ValueError(f"PlannerInput hash drift: {task['source_task_id']}")
    profile = planner_input["profile"]
    return build_scene_compiler_input_v2(
        novel_plan=legacy_bundle["novel_plan"],
        narrative_ir=legacy_bundle["narrative_ir"],
        exposure=planner_input.get("exposure_plan"),
        profile={
            "profile_key": "benchmark.scene-plan",
            "profile_schema_id": profile["schema_id"],
            "profile_version": 1,
            "frozen_payload": profile,
            "content_hash": canonical_json_sha256(profile),
        },
    )


def _runtime_reference(bundle: dict[str, Any]) -> dict[str, Any]:
    model_view = build_scene_compiler_model_view(bundle)
    execution = execute_scene_semantic_fill(
        FakeProvider(),
        task_run_id=1,
        model_view=model_view,
        component_hash="f" * 64,
        model_id="fake-scene-compiler",
        api_key="unused",
    )
    fills = list(execution.proposals)
    scene_plan = compile_scene_plan_v2(
        scene_compiler_input=bundle,
        semantic_fills=fills,
    )
    validate_scene_plan_v2(
        scene_plan,
        scene_compiler_input=bundle,
        semantic_fills=fills,
    )
    return scene_plan


def main() -> None:
    source_suite = _read(V1_ROOT / "suite.json")
    planner_suite = _read(PLANNER_ROOT / "suite.json")
    planner_tasks = {str(item["task_id"]): item for item in planner_suite["tasks"]}
    tasks: list[dict[str, Any]] = []
    for source_task in source_suite["tasks"]:
        task = copy.deepcopy(source_task)
        task["input"] = _v1_contract(task["input"])
        task["reference"] = _v1_contract(task["reference"])
        if "alternative_reference" in task:
            task["alternative_reference"] = _v1_contract(task["alternative_reference"])
        legacy_bundle = _read(V1_ROOT / source_task["input"]["path"])
        runtime_bundle = _runtime_bundle(source_task, legacy_bundle, planner_tasks)
        runtime_reference = _runtime_reference(runtime_bundle)
        task_id = str(task["task_id"])
        reference_path = OUTPUT_ROOT / "runtime_references" / f"{task_id}.json"
        _write(reference_path, runtime_reference)
        task["runtime_reference"] = {
            "path": f"runtime_references/{task_id}.json",
            "hash": canonical_json_sha256(runtime_reference),
            "semantic_signature": scene_plan_v2_semantic_signature(runtime_reference),
        }
        tasks.append(task)

    rubric = {
        "schema_id": "benchmark.scene-plan-g3-rubric.v2",
        "grader_status": "active_pairwise",
        "judge_protocol": "blind_pairwise-empty-retry-v2",
        "judge_provider": "deepseek",
        "judge_model_id": "deepseek-v4-flash",
        "known_limitations": [
            "same_provider_family_bias",
            "model_judge_not_human_literary_review",
        ],
        "dimensions": [
            {
                "id": "scene_specificity",
                "minimum": 0.0,
                "maximum": 1.0,
                "criterion": "Beat 是否把场景目标转成具体、来源支持的行动。",
            },
            {
                "id": "dramatic_progression",
                "minimum": 0.0,
                "maximum": 1.0,
                "criterion": "Beat 顺序是否形成明确推进，而非重复 NovelPlan 摘要。",
            },
            {
                "id": "beat_coherence",
                "minimum": 0.0,
                "maximum": 1.0,
                "criterion": "相邻 Beat 的行动、发现和结果是否连贯。",
            },
            {
                "id": "constraint_clarity",
                "minimum": 0.0,
                "maximum": 1.0,
                "criterion": "允许与禁止的事实、揭露和时序边界是否明确。",
            },
            {
                "id": "writer_executability",
                "minimum": 0.0,
                "maximum": 1.0,
                "criterion": "Writer 仅凭 ScenePlan 是否能写作而无需重新规划剧情。",
            },
        ],
    }
    _write(OUTPUT_ROOT / "g3_rubric.json", rubric)

    suite = {
        "schema_id": "benchmark.scene-plan-suite.v2",
        "suite_id": "scene-plan-capability-v2",
        "source_suite": {
            "path": "../v1/suite.json",
            "hash": canonical_json_sha256(source_suite),
        },
        "formal_qualification": {
            "provider": "deepseek",
            "model_id": "deepseek-v4-pro",
            "quality_grader_provider": "deepseek",
            "quality_grader_model_id": "deepseek-v4-flash",
            "trials_per_task": 3,
        },
        "promotion_gate": {
            "passed_trials_min": 71,
            "pass_at_3_tasks_min": 24,
            "all_trials_pass_tasks_min": 23,
            "infrastructure_failures_max": 0,
            "g3_infrastructure_failures_max": 0,
            "g3_bootstrap_seed": 20260827,
            "g3_bootstrap_iterations": 10000,
            "g3_mean_delta_lower_bound_min": -0.03,
            "g3_dimension_mean_delta_min": -0.05,
            "g4_audit_failures_max": 0,
        },
        "grader_versions": {
            "g0": "scene-plan-contract-v2",
            "g1": "scene-plan-conformance-v2",
            "g2": "scene-plan-outcome-v2",
            "g3": "scene-plan-quality-pairwise-v2",
            "g4": "scene-plan-v2-semantic-signature-v1",
        },
        "qualification": {
            "status": "gate_frozen",
            "qualified": False,
            "reason": "fresh_full_g3_g4_run_required",
        },
        "tasks": tasks,
        "mutations": [_v1_contract(item) for item in source_suite["mutations"]],
        "g3_rubric": {
            "path": "g3_rubric.json",
            "hash": canonical_json_sha256(rubric),
        },
    }
    _write(OUTPUT_ROOT / "suite.json", suite)


if __name__ == "__main__":
    main()
