"""N4.4 ScenePlan capability references, regression alternatives, and safety mutations."""

from __future__ import annotations

import argparse
import copy
import json
import os
import random
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.provider_adapters.deepseek import DeepSeekAgentsProvider
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.agent_runtime.provider_adapters.openai import OpenAIAgentsProvider
from casefile.agent_runtime.scene_compiler import (
    SCENE_COMPILER_PIPELINE_VERSION,
    SCENE_SEMANTIC_FILL_PROMPT_VERSION,
    SceneCompilerProvider,
    SceneFillBatchRequest,
    SceneFillBatchResult,
    execute_scene_semantic_fill,
)
from casefile.benchmark.eval_core import GraderResult
from casefile.domain.narrative_compiler import (
    SCENE_COMPILER_MODEL_VIEW_PROJECTION_VERSION,
    SCENE_PLAN_V2_SEMANTIC_SIGNATURE_VERSION,
    SCENE_STATE_ENGINE_VERSION,
    CompilerContractError,
    build_scene_compiler_input_v2,
    build_scene_compiler_model_view,
    canonical_json_sha256,
    canonicalize_scene_plan_candidate,
    compile_scene_plan_v2,
    inspect_scene_plan_candidate,
    scene_compiler_model_view_fingerprint,
    scene_plan_semantic_signature,
    scene_plan_v2_semantic_signature,
    validate_scene_compiler_input,
    validate_scene_plan_v2,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
SUITE_ROOT = REPO_ROOT / "fixtures" / "scene_plan_benchmark" / "v2"
PLANNER_SUITE_ROOT = REPO_ROOT / "fixtures" / "novel_plan_benchmark" / "v4"
RUNNER_VERSION = "scene-plan-eval-v3-g3-g4"
DIAGNOSTIC_PAYLOAD_POLICIES = ("hashes", "failed-proposal")
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
G3_PROMPT_VERSION = "scene-plan-g3-pairwise-v2"
G3_SYSTEM_PROMPT = (
    "你是 ScenePlanIR 质量评审，只评分，不补写或修复内容。"
    "输入包含同一冻结上下文下的两个匿名执行方案。"
    "按 rubric 的五个维度分别独立给 plan_a 与 plan_b 打 0 到 1 分；"
    "不要猜测哪个是参考答案，也不要因结构更长而自动给高分。"
    '只返回 JSON：{"plan_a":{五个数字字段},"plan_b":{五个数字字段}}。'
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
    "G4 Semantic Audit",
    "Infrastructure",
)


class SceneQualityScores(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scene_specificity: float = Field(ge=0, le=1)
    dramatic_progression: float = Field(ge=0, le=1)
    beat_coherence: float = Field(ge=0, le=1)
    constraint_clarity: float = Field(ge=0, le=1)
    writer_executability: float = Field(ge=0, le=1)


class SceneQualityComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    plan_a: SceneQualityScores
    plan_b: SceneQualityScores


def validate_suite() -> dict[str, Any]:
    suite = _read_json(SUITE_ROOT / "suite.json")
    if suite.get("schema_id") != "benchmark.scene-plan-suite.v2":
        raise ValueError("ScenePlan suite schema is invalid")
    source_suite = _read_hashed(suite.get("source_suite"), label="source suite")
    if source_suite.get("schema_id") != "benchmark.scene-plan-suite.v1":
        raise ValueError("ScenePlan source suite schema is invalid")
    tasks = suite.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 24:
        raise ValueError("ScenePlan capability suite must contain exactly 24 tasks")
    expected = {f"{capability}__{variant}" for capability in CAPABILITIES for variant in VARIANTS}
    if {str(item.get("task_id")) for item in tasks} != expected:
        raise ValueError("ScenePlan capability matrix is incomplete")

    rubric_contract = suite.get("g3_rubric")
    if not isinstance(rubric_contract, dict):
        raise ValueError("ScenePlan G3 rubric contract is missing")
    rubric = _read_hashed(rubric_contract, label="G3 rubric")
    dimensions = rubric.get("dimensions")
    if rubric.get("grader_status") != "active_pairwise" or not isinstance(dimensions, list):
        raise ValueError("ScenePlan G3 rubric is invalid")
    if (
        rubric.get("judge_protocol") != "blind_pairwise-empty-retry-v2"
        or rubric.get("judge_provider") != "deepseek"
        or rubric.get("judge_model_id") != "deepseek-v4-flash"
    ):
        raise ValueError("ScenePlan G3 judge identity drifted")
    if tuple(item.get("id") for item in dimensions) != G3_DIMENSIONS:
        raise ValueError("ScenePlan G3 dimensions drifted")
    if any(item.get("minimum") != 0.0 or item.get("maximum") != 1.0 for item in dimensions):
        raise ValueError("ScenePlan G3 score range is invalid")
    formal_qualification = suite.get("formal_qualification")
    if (
        not isinstance(formal_qualification, dict)
        or formal_qualification.get("provider") != "deepseek"
        or formal_qualification.get("model_id") != "deepseek-v4-pro"
        or formal_qualification.get("quality_grader_provider") != rubric["judge_provider"]
        or formal_qualification.get("quality_grader_model_id") != rubric["judge_model_id"]
        or formal_qualification.get("trials_per_task") != 3
    ):
        raise ValueError("ScenePlan formal qualification identity drifted")
    promotion_gate = suite.get("promotion_gate")
    required_gate_fields = {
        "passed_trials_min",
        "pass_at_3_tasks_min",
        "all_trials_pass_tasks_min",
        "infrastructure_failures_max",
        "g3_infrastructure_failures_max",
        "g3_bootstrap_seed",
        "g3_bootstrap_iterations",
        "g3_mean_delta_lower_bound_min",
        "g3_dimension_mean_delta_min",
        "g4_audit_failures_max",
    }
    if not isinstance(promotion_gate, dict) or set(promotion_gate) != required_gate_fields:
        raise ValueError("ScenePlan promotion gate contract drifted")

    planner_suite = _read_json(PLANNER_SUITE_ROOT / "suite.json")
    planner_tasks = {str(item["task_id"]): item for item in planner_suite.get("tasks", [])}
    inputs: dict[str, dict[str, Any]] = {}
    runtime_inputs: dict[str, dict[str, Any]] = {}
    model_views: dict[str, dict[str, Any]] = {}
    references: dict[str, dict[str, Any]] = {}
    runtime_references: dict[str, dict[str, Any]] = {}
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
        runtime_bundle = _runtime_bundle(task, bundle, planner_tasks)
        runtime_inputs[task_id] = runtime_bundle
        model_views[task_id] = build_scene_compiler_model_view(runtime_bundle)
        runtime_reference = _read_hashed(
            task.get("runtime_reference"), label=f"runtime reference {task_id}"
        )
        if (
            runtime_reference.get("source", {}).get("scene_compiler_input_hash")
            != runtime_bundle["source"]["input_hash"]
        ):
            raise ValueError(f"ScenePlan runtime reference input drifted: {task_id}")
        if task["runtime_reference"].get("semantic_signature") != scene_plan_v2_semantic_signature(
            runtime_reference
        ):
            raise ValueError(f"ScenePlan runtime reference signature drifted: {task_id}")
        runtime_references[task_id] = runtime_reference
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
        "runtime_inputs": runtime_inputs,
        "model_views": model_views,
        "references": references,
        "runtime_references": runtime_references,
        "alternatives": alternatives,
        "mutations": mutations,
    }


def _runtime_bundle(
    task: dict[str, Any],
    legacy_bundle: dict[str, Any],
    planner_tasks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    source_task_id = str(task.get("source_task_id", ""))
    source_task = planner_tasks.get(source_task_id)
    if source_task is None:
        raise ValueError(f"ScenePlan task has no v2 runtime source: {source_task_id}")
    planner_contracts = source_task.get("planner_inputs")
    if not isinstance(planner_contracts, dict):
        raise ValueError(f"ScenePlan source has no PlannerInput contracts: {source_task_id}")
    planner_contract = planner_contracts.get("v3")
    if not isinstance(planner_contract, dict) or not isinstance(planner_contract.get("path"), str):
        raise ValueError(f"ScenePlan source has no PlannerInput v3: {source_task_id}")
    planner_input = _read_json(PLANNER_SUITE_ROOT / planner_contract["path"])
    if canonical_json_sha256(planner_input) != planner_contract.get("hash"):
        raise ValueError(f"ScenePlan PlannerInput hash drift: {source_task_id}")
    if planner_input.get("narrative_ir") != legacy_bundle.get("narrative_ir"):
        raise ValueError(f"ScenePlan NarrativeIR source drift: {source_task_id}")
    profile_payload = planner_input.get("profile")
    if not isinstance(profile_payload, dict):
        raise ValueError(f"ScenePlan source has no frozen profile: {source_task_id}")
    return build_scene_compiler_input_v2(
        novel_plan=legacy_bundle["novel_plan"],
        narrative_ir=legacy_bundle["narrative_ir"],
        exposure=planner_input.get("exposure_plan"),
        profile={
            "profile_key": "benchmark.scene-plan",
            "profile_schema_id": profile_payload["schema_id"],
            "profile_version": 1,
            "frozen_payload": profile_payload,
            "content_hash": canonical_json_sha256(profile_payload),
        },
    )


def grade_candidate(
    *,
    scene_compiler_input: dict[str, Any],
    candidate: dict[str, Any],
    outcome_invariants: list[dict[str, Any]],
    rubric: dict[str, Any],
) -> dict[str, Any]:
    report = inspect_scene_plan_candidate(candidate, scene_compiler_input=scene_compiler_input)
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
        grader_id="g3_rubric_contract",
        severity="soft",
        passed=tuple(item["id"] for item in rubric["dimensions"]) == G3_DIMENSIONS,
        score=0.0,
        evidence={"status": "not_run", "dimensions": list(G3_DIMENSIONS)},
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


def _run_legacy_suite(*, suite_kind: str) -> dict[str, Any]:
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
        violation["category"] for trial in trials for violation in trial.get("violations", [])
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
            "g3_status": "not_run",
        },
        "metrics": {
            "trial_count": len(trials),
            "passed_trial_count": passed_count,
            "pass_rate": passed_count / len(trials) if trials else 0.0,
            "infrastructure_failure_count": 0,
            "failure_taxonomy": {
                category: failure_taxonomy.get(category, 0) for category in FAILURE_CATEGORIES
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


def run_suite(
    *,
    suite_kind: str,
    mode: str = "fake",
    provider_name: str = "deepseek",
    model_id: str = "fake-scene-compiler",
    quality_grader_model: str | None = None,
    repeats: int = 1,
    checkpoint_path: Path | None = None,
    resume: bool = False,
    task_ids: tuple[str, ...] | None = None,
    provider_factory: Callable[[], SceneCompilerProvider] | None = None,
    api_key_override: str | None = None,
    diagnostic_payload_policy: str = "hashes",
) -> dict[str, Any]:
    """Run the current N4.4 v2 shadow runtime against the audited suite."""

    if suite_kind not in {"capability", "regression", "safety"}:
        raise ValueError("ScenePlan suite kind is invalid")
    if mode not in {"fake", "live"}:
        raise ValueError("ScenePlan benchmark mode is invalid")
    if repeats < 1:
        raise ValueError("ScenePlan repeats must be positive")
    if diagnostic_payload_policy not in DIAGNOSTIC_PAYLOAD_POLICIES:
        raise ValueError("ScenePlan diagnostic payload policy is invalid")
    if suite_kind == "safety" and mode != "fake":
        raise ValueError("ScenePlan safety mutations never call a live Provider")
    if resume and checkpoint_path is None:
        raise ValueError("ScenePlan resume requires a checkpoint path")

    validated = validate_suite()
    suite = validated["suite"]
    all_tasks = list(suite["tasks"])
    selected_tasks = _selected_runtime_tasks(all_tasks, suite_kind, task_ids)
    complete_capability = suite_kind == "capability" and len(selected_tasks) == len(all_tasks)
    formal = mode == "live" and complete_capability
    qualification = suite.get("formal_qualification")
    if formal:
        if not isinstance(qualification, dict):
            raise ValueError("ScenePlan formal qualification contract is missing")
        if repeats != int(qualification["trials_per_task"]):
            raise ValueError("Formal ScenePlan baseline requires exactly 3 trials per task")
        if (
            provider_name != qualification["provider"]
            or model_id != qualification["model_id"]
            or quality_grader_model != qualification["quality_grader_model_id"]
        ):
            raise ValueError(
                "Formal ScenePlan qualification requires the exact Pro generator and Flash G3 judge"
            )

    prompt = load_prompt("scene_compiler_semantic_fill", SCENE_SEMANTIC_FILL_PROMPT_VERSION)
    fingerprint_payload = {
        "suite": suite,
        "suite_kind": suite_kind,
        "runner_version": RUNNER_VERSION,
        "pipeline_version": SCENE_COMPILER_PIPELINE_VERSION,
        "state_engine_version": SCENE_STATE_ENGINE_VERSION,
        "model_view_projection_version": SCENE_COMPILER_MODEL_VIEW_PROJECTION_VERSION,
        "prompt_version": prompt.version,
        "prompt_sha256": prompt.system_prompt_sha256,
        "provider": provider_name,
        "model_id": model_id,
        "quality_grader_provider": "deepseek",
        "quality_grader_model": quality_grader_model,
        "quality_grader_prompt_version": G3_PROMPT_VERSION,
        "quality_grader_prompt_hash": canonical_json_sha256(
            {"system_prompt": G3_SYSTEM_PROMPT, "rubric": validated["rubric"]}
        ),
        "g4_signature_version": SCENE_PLAN_V2_SEMANTIC_SIGNATURE_VERSION,
        "mode": mode,
        "repeats": repeats,
        "diagnostic_payload_policy": diagnostic_payload_policy,
        "selection": [str(task["task_id"]) for task in selected_tasks],
        "runtime_inputs": {
            str(task["task_id"]): {
                "input_hash": validated["runtime_inputs"][str(task["task_id"])]["source"][
                    "input_hash"
                ],
                **scene_compiler_model_view_fingerprint(
                    validated["model_views"][str(task["task_id"])]
                ),
            }
            for task in selected_tasks
        },
    }
    fingerprint = canonical_json_sha256(fingerprint_payload)
    trials = _resume_runtime_trials(checkpoint_path, fingerprint) if resume else []
    completed = {(str(item["task_id"]), int(item["trial_index"])) for item in trials}

    if suite_kind == "safety":
        tasks_by_id = {str(task["task_id"]): task for task in all_tasks}
        for mutation in validated["mutations"]:
            trial_id = str(mutation["mutation_id"])
            if (trial_id, 1) in completed:
                continue
            trial = _run_v2_safety_trial(
                mutation=mutation,
                task=tasks_by_id[str(mutation["base_task_id"])],
                bundle=validated["runtime_inputs"][str(mutation["base_task_id"])],
                model_view=validated["model_views"][str(mutation["base_task_id"])],
            )
            trials.append(trial)
            _write_runtime_checkpoint(checkpoint_path, fingerprint, trials)
    else:
        key = (
            api_key_override
            if api_key_override is not None
            else _runtime_api_key(provider_name)
            if mode == "live"
            else "unused"
        )
        for task in selected_tasks:
            task_id = str(task["task_id"])
            for trial_index in range(1, repeats + 1):
                if (task_id, trial_index) in completed:
                    continue
                provider = (
                    provider_factory()
                    if provider_factory is not None
                    else _runtime_provider(mode, provider_name)
                )
                if suite_kind == "regression":
                    provider = _AlternativeSceneProvider(provider)
                trial = _run_v2_runtime_trial(
                    task=task,
                    trial_index=trial_index,
                    provider=provider,
                    provider_name=provider_name,
                    model_id=model_id,
                    mode=mode,
                    quality_grader_model=quality_grader_model,
                    api_key=key,
                    bundle=validated["runtime_inputs"][task_id],
                    model_view=validated["model_views"][task_id],
                    runtime_reference=validated["runtime_references"][task_id],
                    rubric=validated["rubric"],
                    fingerprint=fingerprint,
                    diagnostic_payload_policy=diagnostic_payload_policy,
                )
                trials.append(trial)
                _write_runtime_checkpoint(checkpoint_path, fingerprint, trials)

    report = _runtime_report(
        fingerprint_payload=fingerprint_payload,
        fingerprint=fingerprint,
        suite_kind=suite_kind,
        trials=trials,
        complete_capability=complete_capability,
        formal=formal,
        promotion_thresholds=suite.get("promotion_gate"),
    )
    if checkpoint_path is not None:
        _write_json(checkpoint_path, {**report, "partial": False})
    return report


class _AlternativeSceneProvider:
    def __init__(self, provider: SceneCompilerProvider) -> None:
        self._provider = provider
        self._original_proposals: list[dict[str, Any]] = []

    def fill_scene_batch(self, request: SceneFillBatchRequest) -> SceneFillBatchResult:
        result = self._provider.fill_scene_batch(request)
        self._original_proposals.append(copy.deepcopy(result.proposal))
        proposal = copy.deepcopy(result.proposal)
        for scene in proposal["scenes"]:
            scene["dramatic_goal"] = f"另一种合法执行：{scene['dramatic_goal']}"
            scene["conflict"] = f"替代阻力组织：{scene['conflict']}"
            scene["outcome"] = f"替代结果表达：{scene['outcome']}"
            for beat in scene["beats"]:
                beat["directive"] = f"替代执行措辞：{beat['directive']}"
        return SceneFillBatchResult(
            proposal=proposal,
            usage=result.usage,
            raw_output=result.raw_output,
        )

    @property
    def original_proposals(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._original_proposals)


class _CapturingSceneProvider:
    def __init__(self, provider: SceneCompilerProvider, *, diagnostic_payload_policy: str) -> None:
        self._provider = provider
        self._diagnostic_payload_policy = diagnostic_payload_policy
        self.calls: list[dict[str, Any]] = []
        self._proposals: list[dict[str, Any]] = []

    def fill_scene_batch(self, request: SceneFillBatchRequest) -> SceneFillBatchResult:
        started = time.perf_counter()
        try:
            result = self._provider.fill_scene_batch(request)
        except Exception as error:
            self.calls.append(
                {
                    "batch_id": request.batch_view["batch_id"],
                    "batch_ordinal": request.batch_view["ordinal"],
                    "input_hash": request.input_hash,
                    "output_hash": None,
                    "raw_output_hash": None,
                    "usage": {},
                    "latency_ms": (time.perf_counter() - started) * 1000,
                    "error_type": type(error).__name__,
                }
            )
            raise
        self.calls.append(
            {
                "batch_id": request.batch_view["batch_id"],
                "batch_ordinal": request.batch_view["ordinal"],
                "input_hash": request.input_hash,
                "output_hash": canonical_json_sha256(result.proposal),
                "raw_output_hash": (
                    None
                    if result.raw_output is None
                    else sha256(result.raw_output.encode("utf-8")).hexdigest()
                ),
                "usage": result.usage,
                "latency_ms": (time.perf_counter() - started) * 1000,
                "error_type": None,
            }
        )
        self._proposals.append(copy.deepcopy(result.proposal))
        return result

    def report_calls(self, *, failed: bool) -> list[dict[str, Any]]:
        calls = copy.deepcopy(self.calls)
        if (
            failed
            and self._diagnostic_payload_policy == "failed-proposal"
            and self._proposals
            and len(self._proposals) == len(calls)
        ):
            calls[-1]["diagnostic_payload"] = self._proposals[-1]
        return calls


def _selected_runtime_tasks(
    tasks: list[dict[str, Any]],
    suite_kind: str,
    task_ids: tuple[str, ...] | None,
) -> list[dict[str, Any]]:
    if suite_kind == "safety":
        if task_ids is not None:
            raise ValueError("ScenePlan task selection is not supported for safety")
        return tasks
    available = {str(task["task_id"]): task for task in tasks}
    if task_ids is not None:
        normalized = tuple(value.strip() for value in task_ids)
        if not normalized or any(not value for value in normalized):
            raise ValueError("ScenePlan task selection contains an empty task ID")
        if len(normalized) != len(set(normalized)):
            raise ValueError("ScenePlan task selection contains duplicates")
        unknown = sorted(set(normalized) - set(available))
        if unknown:
            raise ValueError(f"Unknown ScenePlan task IDs: {', '.join(unknown)}")
        return [available[task_id] for task_id in normalized]
    if suite_kind == "regression":
        return [task for task in tasks if task["variant"] == "basic"]
    return tasks


def _run_v2_runtime_trial(
    *,
    task: dict[str, Any],
    trial_index: int,
    provider: SceneCompilerProvider,
    provider_name: str,
    model_id: str,
    mode: str,
    quality_grader_model: str | None,
    api_key: str,
    bundle: dict[str, Any],
    model_view: dict[str, Any],
    runtime_reference: dict[str, Any],
    rubric: dict[str, Any],
    fingerprint: str,
    diagnostic_payload_policy: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    task_id = str(task["task_id"])
    component_hash = canonical_json_sha256(
        {
            "benchmark_fingerprint": fingerprint,
            "task_id": task_id,
            "trial_index": trial_index,
            "scene_compiler_input_hash": bundle["source"]["input_hash"],
            "model_view_hash": canonical_json_sha256(model_view),
        }
    )
    alternative_provider = provider if isinstance(provider, _AlternativeSceneProvider) else None
    capturing_provider = _CapturingSceneProvider(
        provider, diagnostic_payload_policy=diagnostic_payload_policy
    )
    try:
        execution = execute_scene_semantic_fill(
            capturing_provider,
            task_run_id=trial_index,
            model_view=model_view,
            component_hash=component_hash,
            model_id=model_id,
            api_key=api_key,
        )
        fills = list(execution.proposals)
        scene_plan = compile_scene_plan_v2(scene_compiler_input=bundle, semantic_fills=fills)
        validate_scene_plan_v2(
            scene_plan,
            scene_compiler_input=bundle,
            semantic_fills=fills,
        )
        metrics = _capability_metrics_v2(bundle, scene_plan)
        outcome_failures = [
            str(invariant["kind"])
            for invariant in task["outcome_invariants"]
            if metrics[str(invariant["kind"])] < float(invariant["minimum"])
        ]
        semantic_signature = scene_plan_v2_semantic_signature(scene_plan)
        g4_reference_signature = None
        g4_semantic_equivalent = None
        if alternative_provider is not None:
            reference_scene_plan = compile_scene_plan_v2(
                scene_compiler_input=bundle,
                semantic_fills=alternative_provider.original_proposals,
            )
            g4_reference_signature = scene_plan_v2_semantic_signature(reference_scene_plan)
            g4_semantic_equivalent = semantic_signature == g4_reference_signature
        g4_passed = g4_semantic_equivalent is not False
        g3 = _ungraded_quality_result(quality_grader_model)
        if mode == "live" and quality_grader_model is not None:
            g3_started = time.perf_counter()
            try:
                g3 = _live_quality_comparison(
                    model_id=quality_grader_model,
                    task_id=task_id,
                    trial_index=trial_index,
                    rubric=rubric,
                    model_view=model_view,
                    candidate=scene_plan,
                    reference=runtime_reference,
                )
            except Exception as error:
                g3 = _failed_quality_result(
                    quality_grader_model,
                    error,
                    latency_ms=(time.perf_counter() - g3_started) * 1000,
                )
        stages = capturing_provider.report_calls(failed=False)
        return {
            "trial_id": f"{task_id}:trial-{trial_index}",
            "task_id": task_id,
            "trial_index": trial_index,
            "primary_capability": task["primary_capability"],
            "variant": task["variant"],
            "source": "v2_shadow_runtime",
            "provider": provider_name,
            "model_id": model_id,
            "provider_invoked": True,
            "passed": not outcome_failures and g4_passed,
            "accepted": True,
            "reason_code": "ok" if g4_passed else "compiler_scene_g4_semantic_regression",
            "infrastructure_failure": None,
            "outcome_failures": outcome_failures,
            "metrics": metrics,
            "semantic_signature": semantic_signature,
            "scene_plan_hash": canonical_json_sha256(scene_plan),
            "failure_evidence": None,
            "stages": stages,
            "usage": _stage_usage(stages),
            "latency_ms": (time.perf_counter() - started) * 1000,
            "graders": {
                "g0_contract": True,
                "g1_replay": True,
                "g2_outcome": not outcome_failures,
                "g3_status": g3["status"],
                "g3_model_id": quality_grader_model,
                "g3_scores": g3["scores"],
                "g3_reference_scores": g3["reference_scores"],
                "g3_deltas": g3["deltas"],
                "g3_candidate_slot": g3["candidate_slot"],
                "g3_usage": g3["usage"],
                "g3_latency_ms": g3["latency_ms"],
                "g3_infrastructure_failure": g3["infrastructure_failure"],
                "g3_known_limitation": "same_provider_family_bias",
                "g4_version": SCENE_PLAN_V2_SEMANTIC_SIGNATURE_VERSION,
                "g4_signature": semantic_signature,
                "g4_frozen_reference_signature": scene_plan_v2_semantic_signature(
                    runtime_reference
                ),
                "g4_regression_reference_signature": g4_reference_signature,
                "g4_semantic_equivalent": g4_semantic_equivalent,
                "g4_passed": g4_passed,
            },
        }
    except CompilerContractError as error:
        stages = capturing_provider.report_calls(failed=True)
        return {
            "trial_id": f"{task_id}:trial-{trial_index}",
            "task_id": task_id,
            "trial_index": trial_index,
            "primary_capability": task["primary_capability"],
            "variant": task["variant"],
            "source": "v2_shadow_runtime",
            "provider": provider_name,
            "model_id": model_id,
            "provider_invoked": True,
            "passed": False,
            "accepted": False,
            "reason_code": error.reason_code,
            "failure_evidence": getattr(error, "evidence", None),
            "infrastructure_failure": None,
            "outcome_failures": [str(task["primary_capability"])],
            "metrics": {key: 0.0 for key in CAPABILITIES},
            "stages": stages,
            "usage": _stage_usage(stages),
            "latency_ms": (time.perf_counter() - started) * 1000,
            "graders": {
                "g0_contract": False,
                "g1_replay": False,
                "g2_outcome": False,
                "g3_status": "not_run",
                "g4_signature": False,
            },
        }
    except Exception as error:
        stages = capturing_provider.report_calls(failed=True)
        return {
            "trial_id": f"{task_id}:trial-{trial_index}",
            "task_id": task_id,
            "trial_index": trial_index,
            "primary_capability": task["primary_capability"],
            "variant": task["variant"],
            "source": "v2_shadow_runtime",
            "provider": provider_name,
            "model_id": model_id,
            "provider_invoked": True,
            "passed": False,
            "accepted": False,
            "reason_code": "infrastructure_failure",
            "failure_evidence": None,
            "infrastructure_failure": {"type": type(error).__name__},
            "outcome_failures": [],
            "metrics": {key: 0.0 for key in CAPABILITIES},
            "stages": stages,
            "usage": _stage_usage(stages),
            "latency_ms": (time.perf_counter() - started) * 1000,
            "graders": {
                "g0_contract": False,
                "g1_replay": False,
                "g2_outcome": False,
                "g3_status": "not_run",
                "g4_signature": False,
            },
        }


def _run_v2_safety_trial(
    *,
    mutation: dict[str, Any],
    task: dict[str, Any],
    bundle: dict[str, Any],
    model_view: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    execution = execute_scene_semantic_fill(
        FakeProvider(),
        task_run_id=1,
        model_view=model_view,
        component_hash="f" * 64,
        model_id="fake-scene-compiler",
        api_key="unused",
    )
    fills = copy.deepcopy(list(execution.proposals))
    _apply_v2_mutation(str(mutation["kind"]), bundle=bundle, model_view=model_view, fills=fills)
    reason_code = "accepted"
    try:
        compile_scene_plan_v2(scene_compiler_input=bundle, semantic_fills=fills)
    except CompilerContractError as error:
        reason_code = error.reason_code
    expected = str(mutation["expected_reason_code"])
    passed = reason_code == expected
    return {
        "trial_id": str(mutation["mutation_id"]),
        "task_id": str(mutation["mutation_id"]),
        "base_task_id": str(mutation["base_task_id"]),
        "trial_index": 1,
        "primary_capability": task["primary_capability"],
        "variant": task["variant"],
        "source": "v2_safety_mutation",
        "provider": "fake",
        "model_id": "fake-scene-compiler",
        "provider_invoked": False,
        "passed": passed,
        "accepted": reason_code == "accepted",
        "candidate_accepted": reason_code == "accepted",
        "reason_code": reason_code,
        "expected_reason_code": expected,
        "infrastructure_failure": None,
        "outcome_failures": [] if passed else ["safety_rejection_mismatch"],
        "metrics": {key: 0.0 for key in CAPABILITIES},
        "stages": [],
        "usage": {},
        "latency_ms": (time.perf_counter() - started) * 1000,
        "graders": {
            "g0_contract": passed,
            "g1_replay": passed,
            "g2_outcome": passed,
            "g3_status": "not_applicable",
            "g4_signature": False,
        },
    }


def _apply_v2_mutation(
    kind: str,
    *,
    bundle: dict[str, Any],
    model_view: dict[str, Any],
    fills: list[dict[str, Any]],
) -> None:
    if kind == "remove_fill_schema":
        fills[0].pop("schema_id", None)
        return
    if kind == "drop_fill_scene":
        fills[0]["scenes"].pop()
        return
    if kind in {"drop_event_obligation", "drop_resolution_obligation"}:
        marker = "_event_" if kind == "drop_event_obligation" else "_resolution_"
        for proposal in fills:
            for scene in proposal["scenes"]:
                for index, beat in enumerate(scene["beats"]):
                    if any(marker in key for key in beat["fulfills_obligation_keys"]):
                        scene["beats"].pop(index)
                        _rechain_fill_beats(scene["beats"])
                        return
        raise ValueError(f"No matching Scene Fill obligation for {kind}")
    if kind == "fabricate_actor_ref":
        fills[0]["scenes"][0]["beats"][0]["actor_refs"] = [
            {"object_type": "entity", "object_id": "ent_benchmark_fabricated"}
        ]
        return
    if kind == "leak_forbidden_reveal":
        for batch, proposal in zip(model_view["batches"], fills, strict=True):
            constraints = {item["scene_id"]: item for item in batch["scenes"]}
            for scene_fill in proposal["scenes"]:
                forbidden = constraints[scene_fill["scene_id"]]["forbidden_reveal_entry_keys"]
                if forbidden:
                    scene_fill["beats"][0]["directive"] += f" {forbidden[0]}"
                    return
        raise ValueError("No forbidden reveal is available for the safety mutation")
    if kind == "borrow_story_time_ref":
        _mutate_location_assertion(model_view, fills, borrow="story_time")
        return
    if kind == "forward_dependency":
        beats = fills[0]["scenes"][0]["beats"]
        beats[0]["depends_on"] = [beats[-1]["local_key"] if len(beats) > 1 else "beat_local_future"]
        return
    if kind == "borrow_actor_ref":
        for batch, proposal in zip(model_view["batches"], fills, strict=True):
            constraints = {item["scene_id"]: item for item in batch["scenes"]}
            entity_refs = [
                item["object_ref"]
                for item in batch["object_catalog"]
                if item["object_ref"]["object_type"] == "entity"
            ]
            for scene_fill in proposal["scenes"]:
                allowed = {
                    _ref_key(ref) for ref in constraints[scene_fill["scene_id"]]["participant_refs"]
                }
                borrowed = next((ref for ref in entity_refs if _ref_key(ref) not in allowed), None)
                if borrowed is not None:
                    scene_fill["beats"][0]["actor_refs"] = [borrowed]
                    return
        fills[0]["scenes"][0]["beats"][0]["actor_refs"] = [
            {"object_type": "entity", "object_id": "ent_benchmark_out_of_scope"}
        ]
        return
    if kind == "borrow_location_ref":
        _mutate_location_assertion(model_view, fills, borrow="location")
        return
    if kind == "borrow_basis_ref":
        for batch, proposal in zip(model_view["batches"], fills, strict=True):
            constraints = {item["scene_id"]: item for item in batch["scenes"]}
            catalog = [item["object_ref"] for item in batch["object_catalog"]]
            for scene_fill in proposal["scenes"]:
                constraint = constraints[scene_fill["scene_id"]]
                allowed = {
                    _ref_key(ref)
                    for ref in [
                        *constraint["basis_refs"],
                        *[
                            ref
                            for obligation in constraint["obligations"]
                            for ref in obligation["basis_refs"]
                        ],
                    ]
                }
                borrowed = next((ref for ref in catalog if _ref_key(ref) not in allowed), None)
                if borrowed is not None:
                    scene_fill["beats"][0]["basis_refs"] = [borrowed]
                    return
        raise ValueError("No out-of-scope basis ref is available for the safety mutation")
    raise ValueError(f"Unknown ScenePlan v2 mutation kind: {kind}")


def _mutate_location_assertion(
    model_view: dict[str, Any],
    fills: list[dict[str, Any]],
    *,
    borrow: str,
) -> None:
    for batch, proposal in zip(model_view["batches"], fills, strict=True):
        constraints = {item["scene_id"]: item for item in batch["scenes"]}
        catalog = [item["object_ref"] for item in batch["object_catalog"]]
        for scene_fill in proposal["scenes"]:
            constraint = constraints[scene_fill["scene_id"]]
            if constraint["location_ref"] is None or not constraint["story_time_refs"]:
                continue
            if borrow == "location":
                selected = next(
                    (
                        ref
                        for ref in catalog
                        if ref["object_type"] == "location"
                        and _ref_key(ref) != _ref_key(constraint["location_ref"])
                    ),
                    None,
                )
                story_time_refs = constraint["story_time_refs"]
                location_ref = selected
            else:
                selected = next(
                    (
                        ref
                        for ref in catalog
                        if ref["object_type"] == "event"
                        and _ref_key(ref)
                        not in {_ref_key(item) for item in constraint["story_time_refs"]}
                    ),
                    None,
                )
                story_time_refs = [] if selected is None else [selected]
                location_ref = constraint["location_ref"]
            if selected is None:
                if borrow == "location":
                    beat = scene_fill["beats"][0]
                    beat["location_assertions"] = [
                        {
                            "subject_ref": constraint["participant_refs"][0],
                            "location_ref": {
                                "object_type": "location",
                                "object_id": "loc_benchmark_out_of_scope",
                            },
                            "story_time_refs": constraint["story_time_refs"],
                            "basis_refs": beat["basis_refs"],
                        }
                    ]
                    return
                continue
            beat = scene_fill["beats"][0]
            beat["location_assertions"] = [
                {
                    "subject_ref": constraint["participant_refs"][0],
                    "location_ref": location_ref,
                    "story_time_refs": story_time_refs,
                    "basis_refs": beat["basis_refs"],
                }
            ]
            return
    raise ValueError(f"No out-of-scope {borrow} ref is available for the safety mutation")


def _rechain_fill_beats(beats: list[dict[str, Any]]) -> None:
    previous: str | None = None
    for beat in beats:
        beat["depends_on"] = [] if previous is None else [previous]
        previous = str(beat["local_key"])


def _capability_metrics_v2(bundle: dict[str, Any], scene_plan: dict[str, Any]) -> dict[str, float]:
    planned = sorted(bundle["novel_plan"]["scenes"], key=lambda item: item["discourse_order"])
    actual_scenes = sorted(scene_plan["scenes"], key=lambda item: item["discourse_order"])
    actual_by_id = {item["scene_id"]: item for item in actual_scenes}
    beats_by_scene: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for beat in scene_plan["beats"]:
        beats_by_scene[beat["scene_id"]].append(beat)
    scene_score = _mean(
        [
            float(scene["scene_id"] in actual_by_id and bool(beats_by_scene[scene["scene_id"]]))
            for scene in planned
        ]
    )
    required_events = [_ref_key(ref) for scene in planned for ref in scene["event_refs"]]
    actual_events = [_ref_key(ref) for beat in scene_plan["beats"] for ref in beat["event_refs"]]
    required_exposure = [_placement_key(item) for scene in planned for item in scene["exposure"]]
    actual_exposure = [
        _placement_key(item) for beat in scene_plan["beats"] for item in beat["exposure_actions"]
    ]
    required_resolution = [
        _resolution_key(item) for scene in planned for item in scene["resolutions"]
    ]
    actual_resolution = [
        _resolution_key(item) for beat in scene_plan["beats"] for item in beat["resolution_actions"]
    ]
    temporal_scores = [
        _set_score(
            [_ref_key(ref) for ref in scene["story_time_refs"]],
            [_ref_key(ref) for ref in actual_by_id[scene["scene_id"]]["story_time_refs"]],
        )
        for scene in planned
    ]
    grounding_scores = [
        float(
            actual_by_id[scene["scene_id"]]["participant_refs"] == scene["participant_refs"]
            and actual_by_id[scene["scene_id"]]["location_ref"] == scene["location_ref"]
        )
        for scene in planned
    ]
    provenance_scores = [
        float(bool(beat["basis_refs"]) and bool(beat["source_refs"]))
        for beat in scene_plan["beats"]
    ]
    dependency_score = float(
        scene_plan["indexes"]["scene_dependencies"]
        == bundle["novel_plan"]["indexes"]["scene_dependencies"]
    )
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


def _ungraded_quality_result(model_id: str | None) -> dict[str, Any]:
    return {
        "status": "not_run" if model_id is None else "not_applicable",
        "scores": {},
        "reference_scores": {},
        "deltas": {},
        "candidate_slot": None,
        "usage": {},
        "latency_ms": 0.0,
        "infrastructure_failure": None,
    }


def _failed_quality_result(
    model_id: str, error: Exception, *, latency_ms: float
) -> dict[str, Any]:
    return {
        **_ungraded_quality_result(model_id),
        "status": "infrastructure_failure",
        "latency_ms": latency_ms,
        "infrastructure_failure": {"type": type(error).__name__},
    }


def _live_quality_comparison(
    *,
    model_id: str,
    task_id: str,
    trial_index: int,
    rubric: dict[str, Any],
    model_view: dict[str, Any],
    candidate: dict[str, Any],
    reference: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    candidate_slot = (
        "plan_a"
        if int(canonical_json_sha256([task_id, trial_index])[:2], 16) % 2 == 0
        else "plan_b"
    )
    plans = {
        candidate_slot: candidate,
        "plan_b" if candidate_slot == "plan_a" else "plan_a": reference,
    }
    payload = {
        "rubric": rubric["dimensions"],
        "frozen_context": model_view,
        **plans,
    }
    client = OpenAI(
        api_key=_runtime_api_key("deepseek"),
        base_url="https://api.deepseek.com",
        max_retries=2,
    )
    usage_totals = {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
    }
    content = None
    for _attempt in range(2):
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": G3_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        usage = response.usage
        usage_totals["requests"] += 1
        usage_totals["input_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
        usage_totals["output_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)
        usage_totals["total_tokens"] += int(getattr(usage, "total_tokens", 0) or 0)
        usage_totals["cached_tokens"] += int(
            getattr(usage, "prompt_cache_hit_tokens", 0) or 0
        )
        content = response.choices[0].message.content
        if content:
            break
    if not content:
        raise RuntimeError("ScenePlan G3 grader returned no content after empty-response retry")
    comparison = SceneQualityComparison.model_validate_json(content).model_dump(mode="json")
    reference_slot = "plan_b" if candidate_slot == "plan_a" else "plan_a"
    scores = comparison[candidate_slot]
    reference_scores = comparison[reference_slot]
    return {
        "status": "graded",
        "scores": scores,
        "reference_scores": reference_scores,
        "deltas": {
            dimension: float(scores[dimension]) - float(reference_scores[dimension])
            for dimension in G3_DIMENSIONS
        },
        "candidate_slot": candidate_slot,
        "usage": usage_totals,
        "latency_ms": (time.perf_counter() - started) * 1000,
        "infrastructure_failure": None,
    }


def _runtime_report(
    *,
    fingerprint_payload: dict[str, Any],
    fingerprint: str,
    suite_kind: str,
    trials: list[dict[str, Any]],
    complete_capability: bool,
    formal: bool,
    promotion_thresholds: object,
) -> dict[str, Any]:
    passed_count = sum(bool(item["passed"]) for item in trials)
    infrastructure = [item for item in trials if item["infrastructure_failure"]]
    by_capability: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    failure_taxonomy: Counter[str] = Counter()
    failure_batch_ordinals: Counter[str] = Counter()
    for trial in trials:
        by_capability[str(trial["primary_capability"])].append(trial)
        by_variant[str(trial["variant"])].append(trial)
        by_task[str(trial["task_id"])].append(trial)
        if not trial["passed"]:
            failure_taxonomy[_reason_category(str(trial["reason_code"]))] += 1
            stages = trial.get("stages", [])
            ordinal = "none" if not stages else str(stages[-1]["batch_ordinal"])
            failure_batch_ordinals[ordinal] += 1
    task_results = {
        task_id: {
            "trial_count": len(values),
            "passed_trials": sum(bool(item["passed"]) for item in values),
            "pass_at_k": any(item["passed"] for item in values),
            "all_trials_passed": all(item["passed"] for item in values),
        }
        for task_id, values in sorted(by_task.items())
    }
    accepted_count = sum(bool(item["accepted"]) for item in trials)
    g3_infrastructure = [
        item for item in trials if item.get("graders", {}).get("g3_infrastructure_failure")
    ]
    g3_distribution = _g3_distribution(trials)
    g3_bootstrap = _g3_paired_bootstrap(trials, promotion_thresholds)
    g4_audit_failures = [
        item for item in trials if item.get("graders", {}).get("g4_passed") is False
    ]
    g4_signatures = [
        str(item["semantic_signature"])
        for item in trials
        if isinstance(item.get("semantic_signature"), str)
    ]
    g4_by_task = {
        task_id: len(
            {
                str(item["semantic_signature"])
                for item in values
                if isinstance(item.get("semantic_signature"), str)
            }
        )
        for task_id, values in sorted(by_task.items())
    }
    metrics = {
        "trial_count": len(trials),
        "passed_trial_count": passed_count,
        "pass_rate": passed_count / len(trials) if trials else 0.0,
        "accepted_trial_count": accepted_count,
        "infrastructure_failure_count": len(infrastructure),
        "failure_taxonomy": {
            category: failure_taxonomy.get(category, 0) for category in FAILURE_CATEGORIES
        },
        "failure_batch_ordinals": dict(sorted(failure_batch_ordinals.items())),
        "pass_at_k_task_count": sum(bool(item["pass_at_k"]) for item in task_results.values()),
        "all_trials_pass_task_count": sum(
            bool(item["all_trials_passed"]) for item in task_results.values()
        ),
        "latency_ms_total": sum(float(item["latency_ms"]) for item in trials),
        "usage_total": _usage_total(trials),
        "g3_usage_total": _g3_usage_total(trials),
        "g3_latency_ms_total": sum(
            float(item.get("graders", {}).get("g3_latency_ms", 0.0)) for item in trials
        ),
        "g3_infrastructure_failure_count": len(g3_infrastructure),
        "g3_quality_distribution": g3_distribution,
        "g3_paired_bootstrap": g3_bootstrap,
        "g4_audited_trial_count": len(g4_signatures),
        "g4_audit_failure_count": len(g4_audit_failures),
        "g4_unique_signature_count": len(set(g4_signatures)),
        "g4_unique_signatures_by_task": g4_by_task,
        "g4_regression_equivalence_evaluated_count": sum(
            item.get("graders", {}).get("g4_semantic_equivalent") is not None for item in trials
        ),
        "by_capability": _group_results(by_capability),
        "by_variant": _group_results(by_variant),
        "task_results": task_results,
    }
    promotion = _promotion_gate(
        formal=formal,
        complete_capability=complete_capability,
        thresholds=promotion_thresholds,
        metrics=metrics,
        trials=trials,
    )
    baseline_completed = formal and complete_capability and len(trials) == 72
    qualification = {
        "status": (
            "qualified"
            if promotion["qualified"]
            else "evaluated_not_qualified"
            if promotion["evaluated"]
            else "uncalibrated"
        ),
        "qualified": promotion["qualified"],
        "reason": (
            "all_promotion_checks_passed"
            if promotion["qualified"]
            else "promotion_checks_failed"
            if promotion["evaluated"]
            else "fresh_full_g3_g4_run_required"
        ),
        "baseline_completed": baseline_completed,
    }
    return {
        "schema_id": "benchmark.scene-plan-report.v4",
        "status": "passed" if passed_count == len(trials) else "failed",
        "suite_kind": suite_kind,
        "fingerprint": fingerprint,
        "frozen": fingerprint_payload,
        "metrics": metrics,
        "promotion_gate": promotion,
        "qualification": qualification,
        "trials": trials,
    }


def _promotion_gate(
    *,
    formal: bool,
    complete_capability: bool,
    thresholds: object,
    metrics: dict[str, Any],
    trials: list[dict[str, Any]],
) -> dict[str, Any]:
    if not formal or not complete_capability or not isinstance(thresholds, dict):
        return {
            "evaluated": False,
            "qualified": False,
            "reason": "formal_complete_capability_required",
            "checks": {},
        }
    g3 = metrics["g3_paired_bootstrap"]
    checks = {
        "complete_24x3": len(trials) == 72,
        "passed_trials": metrics["passed_trial_count"] >= thresholds["passed_trials_min"],
        "pass_at_3_tasks": metrics["pass_at_k_task_count"] >= thresholds["pass_at_3_tasks_min"],
        "all_trials_pass_tasks": metrics["all_trials_pass_task_count"]
        >= thresholds["all_trials_pass_tasks_min"],
        "infrastructure_failures": metrics["infrastructure_failure_count"]
        <= thresholds["infrastructure_failures_max"],
        "g3_infrastructure_failures": metrics["g3_infrastructure_failure_count"]
        <= thresholds["g3_infrastructure_failures_max"],
        "g3_all_accepted_trials_graded": metrics["g3_quality_distribution"]["graded_trial_count"]
        == metrics["accepted_trial_count"],
        "g3_bootstrap_complete": bool(g3.get("evaluated")),
        "g3_mean_delta_lower_bound": float(g3.get("mean_delta_ci95_lower", -1.0))
        >= thresholds["g3_mean_delta_lower_bound_min"],
        "g3_dimension_non_regression": bool(g3.get("dimension_mean_deltas"))
        and all(
            float(value) >= thresholds["g3_dimension_mean_delta_min"]
            for value in g3.get("dimension_mean_deltas", {}).values()
        ),
        "g4_all_accepted_trials_audited": metrics["g4_audited_trial_count"]
        == metrics["accepted_trial_count"],
        "g4_audit_failures": metrics["g4_audit_failure_count"]
        <= thresholds["g4_audit_failures_max"],
    }
    return {"evaluated": True, "qualified": all(checks.values()), "checks": checks}


def _g3_distribution(trials: list[dict[str, Any]]) -> dict[str, Any]:
    graded = [trial for trial in trials if trial.get("graders", {}).get("g3_status") == "graded"]
    by_dimension: dict[str, dict[str, float]] = {}
    all_candidate_scores: list[float] = []
    all_reference_scores: list[float] = []
    all_deltas: list[float] = []
    for dimension in G3_DIMENSIONS:
        candidate_scores = [float(item["graders"]["g3_scores"][dimension]) for item in graded]
        reference_scores = [
            float(item["graders"]["g3_reference_scores"][dimension]) for item in graded
        ]
        deltas = [float(item["graders"]["g3_deltas"][dimension]) for item in graded]
        all_candidate_scores.extend(candidate_scores)
        all_reference_scores.extend(reference_scores)
        all_deltas.extend(deltas)
        by_dimension[dimension] = {
            "count": float(len(candidate_scores)),
            "candidate_mean": _mean(candidate_scores),
            "reference_mean": _mean(reference_scores),
            "mean_delta": _mean(deltas),
        }
    return {
        "graded_trial_count": len(graded),
        "candidate_mean": _mean(all_candidate_scores),
        "reference_mean": _mean(all_reference_scores),
        "mean_delta": _mean(all_deltas),
        "by_dimension": by_dimension,
    }


def _g3_paired_bootstrap(trials: list[dict[str, Any]], thresholds: object) -> dict[str, Any]:
    if not isinstance(thresholds, dict):
        return {"evaluated": False, "reason": "thresholds_missing"}
    task_ids = sorted({str(item["task_id"]) for item in trials})
    task_deltas: dict[str, dict[str, float]] = {}
    for task_id in task_ids:
        task_trials = [
            item
            for item in trials
            if item["task_id"] == task_id and item.get("graders", {}).get("g3_status") == "graded"
        ]
        if not task_trials:
            return {
                "evaluated": False,
                "reason": "missing_task_cluster",
                "task_id": task_id,
            }
        task_deltas[task_id] = {
            dimension: _mean(
                [float(item["graders"]["g3_deltas"][dimension]) for item in task_trials]
            )
            for dimension in G3_DIMENSIONS
        }
    dimension_mean_deltas = {
        dimension: _mean([task_deltas[task_id][dimension] for task_id in task_ids])
        for dimension in G3_DIMENSIONS
    }
    task_mean_deltas = {task_id: _mean(list(task_deltas[task_id].values())) for task_id in task_ids}
    seed = int(thresholds.get("g3_bootstrap_seed", 20260827))
    iterations = int(thresholds.get("g3_bootstrap_iterations", 10000))
    generator = random.Random(seed)
    samples = sorted(
        _mean([task_mean_deltas[generator.choice(task_ids)] for _ in task_ids])
        for _ in range(iterations)
    )
    lower_index = max(0, int(0.025 * iterations) - 1)
    upper_index = min(iterations - 1, int(0.975 * iterations))
    return {
        "evaluated": True,
        "seed": seed,
        "iterations": iterations,
        "task_cluster_count": len(task_ids),
        "mean_delta": _mean(list(task_mean_deltas.values())),
        "mean_delta_ci95_lower": samples[lower_index],
        "mean_delta_ci95_upper": samples[upper_index],
        "dimension_mean_deltas": dimension_mean_deltas,
    }


def _g3_usage_total(trials: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for trial in trials:
        for key, value in trial.get("graders", {}).get("g3_usage", {}).items():
            if isinstance(value, int):
                result[key] = result.get(key, 0) + value
    return result


def _reason_category(reason_code: str) -> str:
    if reason_code == "infrastructure_failure":
        return "Infrastructure"
    if "exposure" in reason_code or "reveal" in reason_code:
        return "Reveal"
    if "temporal" in reason_code or "time" in reason_code:
        return "Temporal"
    if "dependency" in reason_code:
        return "Dependency"
    if "resolution" in reason_code:
        return "Resolution"
    if "provenance" in reason_code or "basis" in reason_code:
        return "Provenance"
    if "g4" in reason_code:
        return "G4 Semantic Audit"
    if "actor" in reason_code or "location" in reason_code or "reference" in reason_code:
        return "Grounding"
    if "coverage" in reason_code:
        return "Planning Transfer"
    return "Contract"


def _stage_usage(stages: list[dict[str, Any]]) -> dict[str, float]:
    usage: dict[str, float] = {}
    for stage in stages:
        for key, value in stage["usage"].items():
            if isinstance(value, (int, float)):
                usage[key] = usage.get(key, 0.0) + float(value)
    return usage


def _usage_total(trials: list[dict[str, Any]]) -> dict[str, float]:
    usage: dict[str, float] = {}
    for trial in trials:
        for key, value in trial["usage"].items():
            if isinstance(value, (int, float)):
                usage[key] = usage.get(key, 0.0) + float(value)
    return usage


def _runtime_provider(mode: str, provider_name: str) -> SceneCompilerProvider:
    if mode == "fake":
        return FakeProvider()
    return OpenAIAgentsProvider() if provider_name == "openai" else DeepSeekAgentsProvider()


def _runtime_api_key(provider_name: str) -> str:
    variable = "OPENAI_API_KEY" if provider_name == "openai" else "DEEPSEEK_API_KEY"
    value = os.environ.get(variable, "")
    if not value:
        raise RuntimeError(f"{variable} is required for live ScenePlan benchmark")
    return value


def _resume_runtime_trials(checkpoint_path: Path | None, fingerprint: str) -> list[dict[str, Any]]:
    if checkpoint_path is None or not checkpoint_path.exists():
        return []
    checkpoint = _read_json(checkpoint_path)
    if checkpoint.get("fingerprint") != fingerprint:
        raise ValueError("ScenePlan checkpoint fingerprint mismatch")
    trials = checkpoint.get("trials")
    if not isinstance(trials, list):
        raise ValueError("ScenePlan checkpoint trials are invalid")
    return list(trials)


def _write_runtime_checkpoint(
    checkpoint_path: Path | None,
    fingerprint: str,
    trials: list[dict[str, Any]],
) -> None:
    if checkpoint_path is not None:
        _write_json(
            checkpoint_path,
            {"fingerprint": fingerprint, "trials": trials, "partial": True},
        )


def _capability_metrics(bundle: dict[str, Any], candidate: dict[str, Any]) -> dict[str, float]:
    try:
        planned = sorted(bundle["novel_plan"]["scenes"], key=lambda item: item["discourse_order"])
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


def _apply_mutation(kind: str, *, bundle: dict[str, Any], candidate: dict[str, Any]) -> None:
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
        allowed = {_ref_key(item) for item in bundle["novel_plan"]["scenes"][0]["story_time_refs"]}
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
            index, borrowed = _borrowed_ref_for_scene(bundle, field="basis_refs", collection=None)
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
    kinds = {beat["kind"] for scene in candidate["scenes"] for beat in scene["beats"]}
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
    matched = sum(min(count, actual_counts.get(key, 0)) for key, count in required_counts.items())
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
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the N4.4 ScenePlan v2 shadow-runtime benchmark"
    )
    parser.add_argument(
        "--suite-kind",
        choices=("capability", "regression", "safety"),
        default="capability",
    )
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--provider", choices=("openai", "deepseek"), default="deepseek")
    parser.add_argument("--model", default="fake-scene-compiler")
    parser.add_argument("--quality-grader-model", default="deepseek-v4-flash")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--checkpoint-path", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--diagnostic-payload-policy",
        choices=DIAGNOSTIC_PAYLOAD_POLICIES,
        default="hashes",
    )
    parser.add_argument(
        "--task-ids",
        help="Comma-separated task IDs for a non-formal diagnostic run",
    )
    parser.add_argument("--report-path", type=Path)
    args = parser.parse_args()
    report = run_suite(
        suite_kind=args.suite_kind,
        mode=args.mode,
        provider_name=args.provider,
        model_id=args.model,
        quality_grader_model=args.quality_grader_model,
        repeats=args.repeats,
        checkpoint_path=args.checkpoint_path,
        resume=args.resume,
        task_ids=(tuple(args.task_ids.split(",")) if args.task_ids is not None else None),
        diagnostic_payload_policy=args.diagnostic_payload_policy,
    )
    report_path = args.report_path or (
        REPO_ROOT
        / "backend"
        / "var"
        / "benchmark"
        / f"scene-plan-{args.suite_kind}-{args.mode}-v3.json"
    )
    _write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "passed":
        raise SystemExit(1)


__all__ = ["grade_candidate", "main", "run_suite", "validate_suite"]


if __name__ == "__main__":
    main()
