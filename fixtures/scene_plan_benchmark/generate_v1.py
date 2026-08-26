"""Deterministically rebuild the audited N4.4 ScenePlan benchmark v1 fixtures."""

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

from casefile.domain.narrative_compiler import (
    build_baseline_scene_plan_candidate,
    build_scene_compiler_input,
    canonical_json_sha256,
    canonicalize_novel_plan,
    canonicalize_scene_plan_candidate,
    scene_plan_semantic_signature,
)

SOURCE_ROOT = ROOT / "fixtures" / "novel_plan_benchmark" / "v4"
OUTPUT_ROOT = ROOT / "fixtures" / "scene_plan_benchmark" / "v1"
VARIANTS = ("basic", "decoy", "dense")
CAPABILITY_SOURCES = {
    "scene_decomposition": "linear_mystery",
    "event_grounding": "competing_hypotheses",
    "reveal_control": "nonlinear_reveal",
    "temporal_grounding": "flashback_chronology",
    "dependency_transfer": "complex_mixed",
    "resolution_execution": "resolution_closure",
    "scene_grounding": "multiple_suspects",
    "provenance_coverage": "false_belief",
}
EVIDENCE_PATHS = {
    "scene_decomposition": ["/novel_plan/scenes"],
    "event_grounding": ["/novel_plan/scenes", "/narrative_ir/objects/events"],
    "reveal_control": ["/novel_plan/scenes"],
    "temporal_grounding": ["/novel_plan/scenes", "/narrative_ir/objects/events"],
    "dependency_transfer": ["/novel_plan/indexes/scene_dependencies"],
    "resolution_execution": [
        "/novel_plan/scenes",
        "/narrative_ir/objects/resolution_specs",
    ],
    "scene_grounding": [
        "/novel_plan/scenes",
        "/narrative_ir/objects/entities",
        "/narrative_ir/objects/locations",
    ],
    "provenance_coverage": ["/novel_plan/scenes", "/narrative_ir/objects"],
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _alternative(candidate: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(candidate)
    first_scene = result["scenes"][0]
    first_beat = first_scene["beats"][0]
    transition = {
        **first_beat,
        "kind": "transition",
        "directive": "明确进入场景目标与行动条件，但不提前执行后续事实动作。",
        "event_ref": None,
        "exposure": None,
        "resolution": None,
    }
    first_scene["beats"] = [transition, *first_scene["beats"]]
    for ordinal, beat in enumerate(first_scene["beats"], start=1):
        beat["ordinal"] = ordinal
    return result


def _mutation_descriptors() -> list[dict[str, str]]:
    return [
        {
            "mutation_id": "candidate_schema_invalid",
            "base_task_id": "scene_decomposition__basic",
            "kind": "remove_candidate_schema",
            "target": "candidate",
            "expected_reason_code": "compiler_scene_plan_candidate_contract_invalid",
            "category": "Contract",
        },
        {
            "mutation_id": "scene_missing",
            "base_task_id": "scene_decomposition__basic",
            "kind": "drop_scene",
            "target": "candidate",
            "expected_reason_code": "compiler_scene_plan_candidate_scene_coverage_invalid",
            "category": "Planning Transfer",
        },
        {
            "mutation_id": "event_missing",
            "base_task_id": "event_grounding__basic",
            "kind": "drop_event_beat",
            "target": "candidate",
            "expected_reason_code": "compiler_scene_plan_candidate_event_coverage_invalid",
            "category": "Planning Transfer",
        },
        {
            "mutation_id": "fabricated_reference",
            "base_task_id": "scene_grounding__basic",
            "kind": "fabricate_participant_ref",
            "target": "candidate",
            "expected_reason_code": "compiler_scene_plan_candidate_reference_invalid",
            "category": "Grounding",
        },
        {
            "mutation_id": "premature_reveal",
            "base_task_id": "reveal_control__basic",
            "kind": "move_exposure_earlier",
            "target": "candidate",
            "expected_reason_code": "compiler_scene_plan_candidate_exposure_invalid",
            "category": "Reveal",
        },
        {
            "mutation_id": "temporal_ref_invalid",
            "base_task_id": "temporal_grounding__dense",
            "kind": "borrow_story_time_ref",
            "target": "candidate",
            "expected_reason_code": "compiler_scene_plan_candidate_temporal_invalid",
            "category": "Temporal",
        },
        {
            "mutation_id": "dependency_cycle",
            "base_task_id": "dependency_transfer__dense",
            "kind": "create_dependency_cycle",
            "target": "input",
            "expected_reason_code": "compiler_scene_plan_input_dependency_cycle",
            "category": "Dependency",
        },
        {
            "mutation_id": "resolution_missing",
            "base_task_id": "resolution_execution__basic",
            "kind": "drop_resolution_beat",
            "target": "candidate",
            "expected_reason_code": "compiler_scene_plan_candidate_resolution_invalid",
            "category": "Resolution",
        },
        {
            "mutation_id": "participant_out_of_scope",
            "base_task_id": "scene_grounding__dense",
            "kind": "borrow_participant_ref",
            "target": "candidate",
            "expected_reason_code": "compiler_scene_plan_candidate_participant_invalid",
            "category": "Grounding",
        },
        {
            "mutation_id": "location_out_of_scope",
            "base_task_id": "scene_grounding__dense",
            "kind": "borrow_location_ref",
            "target": "candidate",
            "expected_reason_code": "compiler_scene_plan_candidate_location_invalid",
            "category": "Grounding",
        },
        {
            "mutation_id": "provenance_tampered",
            "base_task_id": "provenance_coverage__basic",
            "kind": "borrow_basis_ref",
            "target": "candidate",
            "expected_reason_code": "compiler_scene_plan_candidate_provenance_invalid",
            "category": "Provenance",
        },
    ]


def main() -> None:
    source_suite = _read(SOURCE_ROOT / "suite.json")
    source_tasks = {item["task_id"]: item for item in source_suite["tasks"]}
    tasks: list[dict[str, Any]] = []
    for capability, source_capability in CAPABILITY_SOURCES.items():
        for variant in VARIANTS:
            source_task_id = f"{source_capability}__{variant}"
            task_id = f"{capability}__{variant}"
            source_task = source_tasks[source_task_id]
            planner_input = _read(SOURCE_ROOT / source_task["planner_inputs"]["v3"]["path"])
            novel_candidate = _read(SOURCE_ROOT / source_task["reference"])
            component_fingerprint = canonical_json_sha256(
                {
                    "source_task_id": source_task_id,
                    "planner_input_hash": canonical_json_sha256(planner_input),
                    "novel_candidate_hash": canonical_json_sha256(novel_candidate),
                }
            )
            novel_plan = canonicalize_novel_plan(
                novel_candidate,
                planner_input=planner_input,
                planner_version="benchmark.scene-plan-reference.v1",
                component_fingerprint=component_fingerprint,
            )
            bundle = build_scene_compiler_input(
                novel_plan=novel_plan, narrative_ir=planner_input["narrative_ir"]
            )
            reference = build_baseline_scene_plan_candidate(novel_plan)
            canonical = canonicalize_scene_plan_candidate(
                reference, scene_compiler_input=bundle
            )
            input_path = OUTPUT_ROOT / "inputs" / f"{task_id}.json"
            reference_path = OUTPUT_ROOT / "references" / f"{task_id}.json"
            _write(input_path, bundle)
            _write(reference_path, reference)
            task = {
                "task_id": task_id,
                "primary_capability": capability,
                "variant": variant,
                "source_task_id": source_task_id,
                "input": {
                    "path": f"inputs/{task_id}.json",
                    "hash": canonical_json_sha256(bundle),
                },
                "reference": {
                    "path": f"references/{task_id}.json",
                    "hash": canonical_json_sha256(reference),
                    "semantic_signature": scene_plan_semantic_signature(canonical),
                },
                "outcome_invariants": [
                    {
                        "kind": capability,
                        "minimum": 1.0,
                        "expectation_class": "capability",
                        "input_evidence_paths": EVIDENCE_PATHS[capability],
                    }
                ],
            }
            if variant == "basic":
                alternative = _alternative(reference)
                alternative_path = OUTPUT_ROOT / "alternatives" / f"{task_id}.json"
                _write(alternative_path, alternative)
                task["alternative_reference"] = {
                    "path": f"alternatives/{task_id}.json",
                    "hash": canonical_json_sha256(alternative),
                }
            tasks.append(task)

    rubric = {
        "schema_id": "benchmark.scene-plan-g3-rubric.v1",
        "grader_status": "contract_only",
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

    mutation_entries: list[dict[str, Any]] = []
    for descriptor in _mutation_descriptors():
        path = OUTPUT_ROOT / "mutations" / f"{descriptor['mutation_id']}.json"
        payload = {"schema_id": "benchmark.scene-plan-mutation.v1", **descriptor}
        _write(path, payload)
        mutation_entries.append(
            {
                "path": f"mutations/{descriptor['mutation_id']}.json",
                "hash": canonical_json_sha256(payload),
            }
        )

    suite = {
        "schema_id": "benchmark.scene-plan-suite.v1",
        "suite_id": "scene-plan-capability-v1",
        "grader_versions": {
            "g0": "scene-plan-contract-v1",
            "g1": "scene-plan-conformance-v1",
            "g2": "scene-plan-outcome-v1",
            "g3": "scene-plan-quality-contract-v1",
            "g4": "scene-plan-semantic-signature-v1",
        },
        "qualification": {
            "status": "uncalibrated",
            "qualified": False,
            "reason": "live_baseline_not_run",
        },
        "tasks": tasks,
        "mutations": mutation_entries,
        "g3_rubric": {
            "path": "g3_rubric.json",
            "hash": canonical_json_sha256(rubric),
        },
    }
    _write(OUTPUT_ROOT / "suite.json", suite)


if __name__ == "__main__":
    main()
