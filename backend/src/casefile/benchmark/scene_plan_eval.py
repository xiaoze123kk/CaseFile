"""N4.4 ScenePlan capability references, regression alternatives, and safety mutations."""

from __future__ import annotations

import argparse
import copy
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from casefile.benchmark.eval_core import GraderResult
from casefile.domain.narrative_compiler import (
    canonical_json_sha256,
    canonicalize_scene_plan_candidate,
    inspect_scene_plan_candidate,
    scene_plan_semantic_signature,
    validate_scene_compiler_input,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
SUITE_ROOT = REPO_ROOT / "fixtures" / "scene_plan_benchmark" / "v1"
RUNNER_VERSION = "scene-plan-eval-v1"
CAPABILITIES = (
    "scene_decomposition",
    "event_grounding",
    "reveal_control",
    "temporal_grounding",
    "dependency_transfer",
    "resolution_execution",
    "scene_grounding",
    "provenance_coverage",
)
VARIANTS = ("basic", "decoy", "dense")
G3_DIMENSIONS = (
    "scene_specificity",
    "dramatic_progression",
    "beat_coherence",
    "constraint_clarity",
    "writer_executability",
)
FAILURE_CATEGORIES = (
    "Contract",
    "Planning Transfer",
    "Reveal",
    "Temporal",
    "Dependency",
    "Resolution",
    "Grounding",
    "Provenance",
    "Infrastructure",
)


def validate_suite() -> dict[str, Any]:
    suite = _read_json(SUITE_ROOT / "suite.json")
    if suite.get("schema_id") != "benchmark.scene-plan-suite.v1":
        raise ValueError("ScenePlan suite schema is invalid")
    tasks = suite.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 24:
        raise ValueError("ScenePlan capability suite must contain exactly 24 tasks")
    expected = {
        f"{capability}__{variant}"
        for capability in CAPABILITIES
        for variant in VARIANTS
    }
    if {str(item.get("task_id")) for item in tasks} != expected:
        raise ValueError("ScenePlan capability matrix is incomplete")

    rubric_contract = suite.get("g3_rubric")
    if not isinstance(rubric_contract, dict):
        raise ValueError("ScenePlan G3 rubric contract is missing")
    rubric = _read_hashed(rubric_contract, label="G3 rubric")
    dimensions = rubric.get("dimensions")
    if rubric.get("grader_status") != "contract_only" or not isinstance(dimensions, list):
        raise ValueError("ScenePlan G3 rubric is invalid")
    if tuple(item.get("id") for item in dimensions) != G3_DIMENSIONS:
        raise ValueError("ScenePlan G3 dimensions drifted")
    if any(item.get("minimum") != 0.0 or item.get("maximum") != 1.0 for item in dimensions):
        raise ValueError("ScenePlan G3 score range is invalid")

    inputs: dict[str, dict[str, Any]] = {}
    references: dict[str, dict[str, Any]] = {}
    alternatives: dict[str, dict[str, Any]] = {}
    for task in tasks:
        task_id = str(task["task_id"])
        capability, variant = task_id.rsplit("__", 1)
        if task.get("primary_capability") != capability or task.get("variant") != variant:
            raise ValueError(f"ScenePlan task identity drifted: {task_id}")
        bundle = _read_hashed(task.get("input"), label=f"input {task_id}")
        validate_scene_compiler_input(bundle)
        reference = _read_hashed(task.get("reference"), label=f"reference {task_id}")
        _validate_invariants(task.get("outcome_invariants"), bundle, task_id)
        grade = grade_candidate(
            scene_compiler_input=bundle,
            candidate=reference,
            outcome_invariants=task["outcome_invariants"],
            rubric=rubric,
        )
        if not grade["passed"]:
            raise ValueError(f"ScenePlan reference does not pass graders: {task_id}")
        if task["reference"].get("semantic_signature") != grade["semantic_signature"]:
            raise ValueError(f"ScenePlan reference signature drifted: {task_id}")
        if variant == "basic":
            alternative_contract = task.get("alternative_reference")
            alternative = _read_hashed(
                alternative_contract, label=f"alternative reference {task_id}"
            )
            alternative_grade = grade_candidate(
                scene_compiler_input=bundle,
                candidate=alternative,
                outcome_invariants=task["outcome_invariants"],
                rubric=rubric,
            )
            if not alternative_grade["passed"]:
                raise ValueError(f"ScenePlan alternative does not pass graders: {task_id}")
            if canonical_json_sha256(alternative) == canonical_json_sha256(reference):
                raise ValueError(f"ScenePlan alternative duplicates reference: {task_id}")
            alternatives[task_id] = alternative
        elif task.get("alternative_reference") is not None:
            raise ValueError(f"Only basic tasks may freeze alternatives: {task_id}")
        if variant == "decoy" and not _has_unused_visible_sources(bundle, reference):
            raise ValueError(f"ScenePlan decoy has no unused visible source: {task_id}")
        if variant == "dense" and not _is_dense_reference(reference):
            raise ValueError(f"ScenePlan dense task lacks interacting constraints: {task_id}")
        inputs[task_id] = bundle
        references[task_id] = reference

    mutations: list[dict[str, Any]] = []
    for mutation_contract in suite.get("mutations", []):
        mutation = _read_hashed(mutation_contract, label="mutation")
        if mutation.get("schema_id") != "benchmark.scene-plan-mutation.v1":
            raise ValueError("ScenePlan mutation schema is invalid")
        if mutation.get("base_task_id") not in inputs:
            raise ValueError("ScenePlan mutation references an unknown task")
        mutations.append(mutation)
    if len(mutations) < 10:
        raise ValueError("ScenePlan safety suite must contain at least 10 mutations")
    return {
        "suite": suite,
        "rubric": rubric,
        "inputs": inputs,
        "references": references,
        "alternatives": alternatives,
        "mutations": mutations,
    }


def grade_candidate(
    *,
    scene_compiler_input: dict[str, Any],
    candidate: dict[str, Any],
    outcome_invariants: list[dict[str, Any]],
    rubric: dict[str, Any],
) -> dict[str, Any]:
    report = inspect_scene_plan_candidate(
        candidate, scene_compiler_input=scene_compiler_input
    )
    metrics = _capability_metrics(scene_compiler_input, candidate)
    g0 = GraderResult(
        grader_id="g0_contract",
        severity="hard",
        passed=report.contract_valid,
        score=1.0 if report.contract_valid else 0.0,
        evidence={"reason_codes": [item.code for item in report.violations]},
    )
    g1 = GraderResult(
        grader_id="g1_conformance",
        severity="hard",
        passed=report.succeeded,
        score=1.0 if report.succeeded else 0.0,
        evidence={
            "violations": [
                {
                    "code": item.code,
                    "category": item.category,
                    "scene_id": item.scene_id,
                    "beat_ordinal": item.beat_ordinal,
                    "evidence": item.evidence,
                }
                for item in report.violations
            ]
        },
    )
    invariant_results = [
        {
            "kind": item["kind"],
            "score": metrics[item["kind"]],
            "minimum": float(item["minimum"]),
            "passed": metrics[item["kind"]] >= float(item["minimum"]),
        }
        for item in outcome_invariants
    ]
    g2 = GraderResult(
        grader_id="g2_outcome",
        severity="hard",
        passed=all(item["passed"] for item in invariant_results),
        score=(
            sum(item["score"] for item in invariant_results) / len(invariant_results)
            if invariant_results
            else 1.0
        ),
        evidence={"invariants": invariant_results, "metrics": metrics},
    )
    g3 = GraderResult(
        grader_id="g3_quality_contract",
        severity="soft",
        passed=tuple(item["id"] for item in rubric["dimensions"]) == G3_DIMENSIONS,
        score=0.0,
        evidence={"status": "contract_only", "dimensions": list(G3_DIMENSIONS)},
    )
    signature: str | None = None
    if report.succeeded:
        canonical = canonicalize_scene_plan_candidate(
            candidate, scene_compiler_input=scene_compiler_input
        )
        signature = scene_plan_semantic_signature(canonical)
    graders = (g0, g1, g2, g3)
    return {
        "passed": all(item.passed for item in graders if item.severity == "hard"),
        "graders": [item.as_dict() for item in graders],
        "violations": list(g1.evidence["violations"]),
        "metrics": metrics,
        "semantic_signature": signature,
    }


def run_suite(*, suite_kind: str) -> dict[str, Any]:
    if suite_kind not in {"capability", "regression", "safety"}:
        raise ValueError("ScenePlan suite kind is invalid")
    validated = validate_suite()
    suite = validated["suite"]
    trials: list[dict[str, Any]] = []
    if suite_kind == "capability":
        for task in suite["tasks"]:
            task_id = task["task_id"]
            grade = grade_candidate(
                scene_compiler_input=validated["inputs"][task_id],
                candidate=validated["references"][task_id],
                outcome_invariants=task["outcome_invariants"],
                rubric=validated["rubric"],
            )
            trials.append(_trial(task, "reference", grade))
    elif suite_kind == "regression":
        tasks = {item["task_id"]: item for item in suite["tasks"]}
        for task_id, alternative in validated["alternatives"].items():
            task = tasks[task_id]
            grade = grade_candidate(
                scene_compiler_input=validated["inputs"][task_id],
                candidate=alternative,
                outcome_invariants=task["outcome_invariants"],
                rubric=validated["rubric"],
            )
            trials.append(_trial(task, "alternative_reference", grade))
    else:
        tasks = {item["task_id"]: item for item in suite["tasks"]}
        for mutation in validated["mutations"]:
            task_id = mutation["base_task_id"]
            bundle = copy.deepcopy(validated["inputs"][task_id])
            candidate = copy.deepcopy(validated["references"][task_id])
            _apply_mutation(mutation["kind"], bundle=bundle, candidate=candidate)
            grade = grade_candidate(
                scene_compiler_input=bundle,
                candidate=candidate,
                outcome_invariants=tasks[task_id]["outcome_invariants"],
                rubric=validated["rubric"],
            )
            reason_codes = {item["code"] for item in grade["violations"]}
            expected = mutation["expected_reason_code"]
            safety_passed = expected in reason_codes
            trials.append(
                {
                    "trial_id": mutation["mutation_id"],
                    "task_id": task_id,
                    "primary_capability": tasks[task_id]["primary_capability"],
                    "variant": tasks[task_id]["variant"],
                    "source": "safety_mutation",
                    "candidate_accepted": grade["passed"],
                    "expected_reason_code": expected,
                    **grade,
                    "passed": safety_passed,
                }
            )
    fingerprint = canonical_json_sha256(
        {"runner_version": RUNNER_VERSION, "suite_kind": suite_kind, "suite": suite}
    )
    failure_taxonomy: Counter[str] = Counter(
        violation["category"]
        for trial in trials
        for violation in trial.get("violations", [])
    )
    by_capability: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trial in trials:
        by_capability[trial["primary_capability"]].append(trial)
        by_variant[trial["variant"]].append(trial)
    passed_count = sum(bool(item["passed"]) for item in trials)
    report = {
        "schema_id": "benchmark.scene-plan-report.v1",
        "status": "passed" if passed_count == len(trials) else "failed",
        "suite_kind": suite_kind,
        "fingerprint": fingerprint,
        "frozen": {
            "runner_version": RUNNER_VERSION,
            "suite_id": suite["suite_id"],
            "suite_hash": canonical_json_sha256(suite),
            "grader_versions": suite["grader_versions"],
            "g3_status": "contract_only",
        },
        "metrics": {
            "trial_count": len(trials),
            "passed_trial_count": passed_count,
            "pass_rate": passed_count / len(trials) if trials else 0.0,
            "infrastructure_failure_count": 0,
            "failure_taxonomy": {
                category: failure_taxonomy.get(category, 0)
                for category in FAILURE_CATEGORIES
            },
            "by_capability": _group_results(by_capability),
            "by_variant": _group_results(by_variant),
        },
        "qualification": {
            "status": "uncalibrated",
            "qualified": False,
            "reason": "live_baseline_not_run",
        },
        "trials": trials,
    }
    return report


def _capability_metrics(bundle: dict[str, Any], candidate: dict[str, Any]) -> dict[str, float]:
    try:
        planned = sorted(
            bundle["novel_plan"]["scenes"], key=lambda item: item["discourse_order"]
        )
        actual = candidate.get("scenes", []) if isinstance(candidate, dict) else []
    except (KeyError, TypeError):
        return {key: 0.0 for key in CAPABILITIES}
    actual_by_id = {
        item.get("scene_id"): item
        for item in actual
        if isinstance(item, dict) and isinstance(item.get("beats"), list)
    }
    planned_ids = [item["scene_id"] for item in planned]
    actual_ids = [item.get("scene_id") for item in actual if isinstance(item, dict)]
    scene_score = (
        sum(
            scene_id in actual_by_id and bool(actual_by_id[scene_id]["beats"])
            for scene_id in planned_ids
        )
        / len(planned_ids)
        if planned_ids
        else 1.0
    )
    dependency_score = 1.0 if actual_ids == planned_ids else 0.0
    required_events: list[str] = []
    actual_events: list[str] = []
    required_exposure: list[str] = []
    actual_exposure: list[str] = []
    required_resolution: list[str] = []
    actual_resolution: list[str] = []
    temporal_scores: list[float] = []
    grounding_scores: list[float] = []
    provenance_scores: list[float] = []
    for scene in planned:
        scene_candidate = actual_by_id.get(scene["scene_id"], {"beats": []})
        beats = scene_candidate["beats"]
        required_events.extend(_ref_key(item) for item in scene["event_refs"])
        actual_events.extend(
            _ref_key(beat["event_ref"])
            for beat in beats
            if isinstance(beat, dict) and beat.get("event_ref") is not None
        )
        required_exposure.extend(_placement_key(item) for item in scene["exposure"])
        actual_exposure.extend(
            _placement_key(beat["exposure"])
            for beat in beats
            if isinstance(beat, dict) and beat.get("exposure") is not None
        )
        required_resolution.extend(_resolution_key(item) for item in scene["resolutions"])
        actual_resolution.extend(
            _resolution_key(beat["resolution"])
            for beat in beats
            if isinstance(beat, dict) and beat.get("resolution") is not None
        )
        required_times = [_ref_key(item) for item in scene["story_time_refs"]]
        actual_times = [
            _ref_key(item)
            for beat in beats
            if isinstance(beat, dict)
            for item in beat.get("story_time_refs", [])
        ]
        temporal_scores.append(_set_score(required_times, actual_times))
        required_grounding = [
            *(_ref_key(item) for item in scene["participant_refs"]),
            *([] if scene["location_ref"] is None else [_ref_key(scene["location_ref"])]),
        ]
        actual_grounding = [
            _ref_key(item)
            for beat in beats
            if isinstance(beat, dict)
            for item in beat.get("participant_refs", [])
        ]
        actual_grounding.extend(
            _ref_key(beat["location_ref"])
            for beat in beats
            if isinstance(beat, dict) and beat.get("location_ref") is not None
        )
        grounding_scores.append(_set_score(required_grounding, actual_grounding))
        required_basis = [_ref_key(item) for item in scene["basis_refs"]]
        actual_basis = [
            _ref_key(item)
            for beat in beats
            if isinstance(beat, dict)
            for item in beat.get("basis_refs", [])
        ]
        provenance_scores.append(_set_score(required_basis, actual_basis))
    return {
        "scene_decomposition": scene_score,
        "event_grounding": _multiset_score(required_events, actual_events),
        "reveal_control": _multiset_score(required_exposure, actual_exposure),
        "temporal_grounding": _mean(temporal_scores),
        "dependency_transfer": dependency_score,
        "resolution_execution": _multiset_score(required_resolution, actual_resolution),
        "scene_grounding": _mean(grounding_scores),
        "provenance_coverage": _mean(provenance_scores),
    }


def _apply_mutation(
    kind: str, *, bundle: dict[str, Any], candidate: dict[str, Any]
) -> None:
    if kind == "remove_candidate_schema":
        candidate.pop("schema_id", None)
        return
    if kind == "drop_scene":
        candidate["scenes"].pop()
        return
    if kind in {"drop_event_beat", "drop_resolution_beat"}:
        target_kind = "event" if kind == "drop_event_beat" else "resolution"
        beat = next(
            beat
            for scene in candidate["scenes"]
            for beat in scene["beats"]
            if beat["kind"] == target_kind
        )
        beat["kind"] = "transition"
        beat["event_ref"] = None
        beat["exposure"] = None
        beat["resolution"] = None
        return
    if kind == "fabricate_participant_ref":
        candidate["scenes"][0]["beats"][0]["participant_refs"].append(
            {"object_type": "entity", "object_id": "ent_benchmark_fabricated"}
        )
        return
    if kind == "move_exposure_earlier":
        exposure = next(
            beat["exposure"]
            for scene in candidate["scenes"][1:]
            for beat in scene["beats"]
            if beat["exposure"] is not None
        )
        first_scene = candidate["scenes"][0]
        new_beat = copy.deepcopy(first_scene["beats"][0])
        new_beat.update(
            {
                "kind": "exposure",
                "directive": "提前披露未来场景信息。",
                "event_ref": None,
                "exposure": exposure,
                "resolution": None,
            }
        )
        first_scene["beats"].append(new_beat)
        _renumber(first_scene)
        return
    if kind == "borrow_story_time_ref":
        target_scene = candidate["scenes"][0]
        allowed = {
            _ref_key(item)
            for item in bundle["novel_plan"]["scenes"][0]["story_time_refs"]
        }
        borrowed = next(
            item
            for scene in bundle["novel_plan"]["scenes"][1:]
            for item in scene["story_time_refs"]
            if _ref_key(item) not in allowed
        )
        target_scene["beats"][0]["story_time_refs"] = [borrowed]
        return
    if kind == "create_dependency_cycle":
        scenes = bundle["novel_plan"]["scenes"]
        first_id = scenes[0]["scene_id"]
        last_id = scenes[-1]["scene_id"]
        scenes[0]["prerequisite_scene_ids"] = [last_id]
        bundle["novel_plan"]["indexes"]["scene_dependencies"][first_id] = [last_id]
        bundle["source"]["novel_plan_hash"] = canonical_json_sha256(bundle["novel_plan"])
        bundle["source"]["input_hash"] = canonical_json_sha256(
            {"novel_plan": bundle["novel_plan"], "narrative_ir": bundle["narrative_ir"]}
        )
        return
    if kind in {"borrow_participant_ref", "borrow_location_ref", "borrow_basis_ref"}:
        if kind == "borrow_participant_ref":
            index, borrowed = _borrowed_ref_for_scene(
                bundle, field="participant_refs", collection="entities"
            )
            beat = candidate["scenes"][index]["beats"][0]
            beat["participant_refs"].append(borrowed)
        elif kind == "borrow_location_ref":
            index, borrowed = _borrowed_ref_for_scene(
                bundle, field="location_ref", collection="locations"
            )
            beat = candidate["scenes"][index]["beats"][0]
            beat["location_ref"] = borrowed
        else:
            index, borrowed = _borrowed_ref_for_scene(
                bundle, field="basis_refs", collection=None
            )
            beat = candidate["scenes"][index]["beats"][0]
            beat["basis_refs"].append(borrowed)
        return
    raise ValueError(f"Unknown ScenePlan mutation kind: {kind}")


def _known_ref(
    bundle: dict[str, Any], collection: str | None, excluded: set[str]
) -> dict[str, str]:
    collections = bundle["narrative_ir"]["objects"]
    candidates = (
        collections[collection]
        if collection is not None
        else [item for values in collections.values() for item in values]
    )
    return next(
        item["object_ref"] for item in candidates if _ref_key(item["object_ref"]) not in excluded
    )


def _borrowed_ref_for_scene(
    bundle: dict[str, Any], *, field: str, collection: str | None
) -> tuple[int, dict[str, str]]:
    for index, scene in enumerate(bundle["novel_plan"]["scenes"]):
        raw_allowed = scene[field]
        values = raw_allowed if isinstance(raw_allowed, list) else [raw_allowed]
        allowed = {_ref_key(item) for item in values if item is not None}
        try:
            return index, _known_ref(bundle, collection, allowed)
        except StopIteration:
            continue
    raise ValueError(f"No out-of-scope source is available for {field}")


def _validate_invariants(invariants: Any, bundle: dict[str, Any], task_id: str) -> None:
    if not isinstance(invariants, list) or not invariants:
        raise ValueError(f"ScenePlan task has no outcome invariant: {task_id}")
    for invariant in invariants:
        if invariant.get("kind") not in CAPABILITIES:
            raise ValueError(f"ScenePlan invariant kind is invalid: {task_id}")
        if invariant.get("expectation_class") != "capability":
            raise ValueError(f"ScenePlan invariant expectation class is invalid: {task_id}")
        paths = invariant.get("input_evidence_paths")
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"ScenePlan invariant is a hidden oracle: {task_id}")
        for path in paths:
            _resolve_json_pointer(bundle, path)


def _resolve_json_pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError(f"Invalid JSON pointer: {pointer!r}")
    current = document
    for raw in pointer[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            if not key.isdecimal() or int(key) >= len(current):
                raise ValueError(f"JSON pointer does not resolve: {pointer}")
            current = current[int(key)]
        elif isinstance(current, dict) and key in current:
            current = current[key]
        else:
            raise ValueError(f"JSON pointer does not resolve: {pointer}")
    return current


def _read_hashed(contract: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(contract, dict) or not isinstance(contract.get("path"), str):
        raise ValueError(f"ScenePlan {label} contract is invalid")
    payload = _read_json(SUITE_ROOT / contract["path"])
    if canonical_json_sha256(payload) != contract.get("hash"):
        raise ValueError(f"ScenePlan {label} hash drift")
    return payload


def _has_unused_visible_sources(bundle: dict[str, Any], candidate: dict[str, Any]) -> bool:
    visible = {
        _ref_key(item["object_ref"])
        for values in bundle["narrative_ir"]["objects"].values()
        for item in values
    }
    used = {
        _ref_key(ref)
        for scene in candidate["scenes"]
        for beat in scene["beats"]
        for ref in [
            *beat["participant_refs"],
            *beat["story_time_refs"],
            *beat["basis_refs"],
            *([] if beat["location_ref"] is None else [beat["location_ref"]]),
        ]
    }
    return bool(visible - used)


def _is_dense_reference(candidate: dict[str, Any]) -> bool:
    kinds = {
        beat["kind"] for scene in candidate["scenes"] for beat in scene["beats"]
    }
    return len(candidate["scenes"]) >= 3 and len(kinds) >= 2


def _trial(task: dict[str, Any], source: str, grade: dict[str, Any]) -> dict[str, Any]:
    return {
        "trial_id": f"{task['task_id']}:{source}",
        "task_id": task["task_id"],
        "primary_capability": task["primary_capability"],
        "variant": task["variant"],
        "source": source,
        **grade,
    }


def _group_results(groups: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        key: {
            "trial_count": len(values),
            "passed_trial_count": sum(bool(item["passed"]) for item in values),
        }
        for key, values in sorted(groups.items())
    }


def _multiset_score(required: list[str], actual: list[str]) -> float:
    if not required and not actual:
        return 1.0
    required_counts = Counter(required)
    actual_counts = Counter(actual)
    matched = sum(
        min(count, actual_counts.get(key, 0)) for key, count in required_counts.items()
    )
    return matched / max(sum(required_counts.values()), sum(actual_counts.values()), 1)


def _set_score(required: list[str], actual: list[str]) -> float:
    required_set = set(required)
    actual_set = set(actual)
    if not required_set and not actual_set:
        return 1.0
    return len(required_set & actual_set) / max(len(required_set), len(actual_set), 1)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 1.0


def _renumber(scene: dict[str, Any]) -> None:
    for ordinal, beat in enumerate(scene["beats"], start=1):
        beat["ordinal"] = ordinal


def _ref_key(ref: dict[str, str]) -> str:
    return f"{ref['object_type']}:{ref['object_id']}"


def _placement_key(value: dict[str, str]) -> str:
    return f"{value['entry_key']}:{value['action']}"


def _resolution_key(value: dict[str, Any]) -> str:
    return f"{_ref_key(value['resolution_ref'])}:{value['action']}"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"ScenePlan JSON root must be an object: {path}")
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the N4.4 ScenePlan benchmark")
    parser.add_argument(
        "--suite-kind",
        choices=("capability", "regression", "safety"),
        default="capability",
    )
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    report = run_suite(suite_kind=args.suite_kind)
    report_path = args.report_path or (
        REPO_ROOT
        / "backend"
        / "var"
        / "benchmark"
        / f"scene-plan-{args.suite_kind}-v1.json"
    )
    _write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)


__all__ = ["grade_candidate", "main", "run_suite", "validate_suite"]


if __name__ == "__main__":
    main()
