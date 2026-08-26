"""Frozen SAT/UNSAT, determinism, and scale gates for the reference PlanningSolver."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from casefile.benchmark.novel_plan_eval import validate_suite
from casefile.domain.narrative_compiler import (
    PlanningSat,
    PlanningUnsat,
    ReferencePlanningSolver,
    canonical_json_sha256,
    compile_planning_problem,
)

ROOT = Path(__file__).resolve().parents[3]
STRESS = json.loads(
    (ROOT / "fixtures" / "novel_plan_solver" / "reference_backend_stress_v1.json").read_text(
        encoding="utf-8"
    )
)


def _problem_and_proposal(scene_count: int) -> tuple[dict[str, Any], dict[str, Any]]:
    validated = validate_suite(planner_input_version="v4")
    task_id = "linear_mystery__basic"
    problem = compile_planning_problem(validated["planner_inputs"][task_id])
    chapter_id = problem["chapter_slots"][0]["chapter_id"]
    problem["hard_constraints"]["structure"]["target_scenes"] = scene_count
    problem["scene_slots"] = [
        {
            "scene_id": f"scene_{ordinal:03d}",
            "chapter_id": chapter_id,
            "discourse_order": ordinal,
        }
        for ordinal in range(1, scene_count + 1)
    ]
    basis_ref = problem["object_refs"][0]
    proposal = {
        "schema_id": "compiler.skeleton-proposal.v1",
        "scenes": [
            {
                **slot,
                "purpose": (
                    "hook"
                    if slot["discourse_order"] == 1
                    else "resolution"
                    if slot["discourse_order"] == scene_count
                    else "investigation"
                ),
                "presentation_mode": (
                    "flashback" if slot["discourse_order"] % 7 == 0 else "linear"
                ),
                "story_time_refs": [],
                "participant_refs": [],
                "basis_refs": [basis_ref],
                "exposure": [],
                "resolutions": [],
                "prerequisite_scene_ids": (
                    [f"scene_{slot['discourse_order'] - 1:03d}"]
                    if slot["discourse_order"] > 1
                    else []
                ),
            }
            for slot in problem["scene_slots"]
        ],
    }
    return problem, proposal


def test_reference_solver_frozen_sat_unsat_and_scale_matrix() -> None:
    solver = ReferencePlanningSolver()
    for case in STRESS["cases"]:
        problem, proposal = _problem_and_proposal(case["scene_count"])
        if case["expected"] == "unsat":
            problem["hard_constraints"]["semantic_obligations"].append(
                {
                    "kind": "basis_ref_coverage",
                    "obligation_key": "obligation_stress_missing_ref",
                    "entry_key": problem["hard_constraints"]["exposure"][
                        "introduce_order"
                    ][0],
                    "level": "hard",
                    "required_refs": [
                        {"object_type": "claim", "object_id": "claim_missing"}
                    ],
                }
            )
        started = perf_counter()
        result = solver.solve(problem, proposal)
        elapsed_ms = (perf_counter() - started) * 1000
        if case["expected"] == "sat":
            assert isinstance(result, PlanningSat), case["case_id"]
            assert len(result.skeleton["scenes"]) == case["scene_count"]
            if "budget_ms" in case:
                assert elapsed_ms < case["budget_ms"]
        else:
            assert result == PlanningUnsat(tuple(case["conflict_keys"]))


def test_reference_solver_canonical_skeleton_is_stable_across_100_runs() -> None:
    problem, proposal = _problem_and_proposal(8)
    solver = ReferencePlanningSolver()
    hashes: set[str] = set()
    for _ in range(STRESS["canonical_repeat_count"]):
        result = solver.solve(copy.deepcopy(problem), copy.deepcopy(proposal))
        assert isinstance(result, PlanningSat)
        hashes.add(canonical_json_sha256(result.skeleton))

    assert len(hashes) == 1
