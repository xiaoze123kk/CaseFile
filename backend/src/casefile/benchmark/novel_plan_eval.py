"""N4.3 Novel Plan regression, safety, and capability benchmark."""

from __future__ import annotations

import argparse
import copy
import json
import os
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field

from casefile.agent_runtime.providers import (
    DeepSeekAgentsProvider,
    OpenAIAgentsProvider,
)
from casefile.agent_runtime.story_planner import (
    STORY_PLANNER_PROMPT_VERSION,
    StoryPlannerProviderResult,
    StoryPlannerRequest,
    execute_story_planner,
)
from casefile.domain.narrative_compiler import (
    CompilerContractError,
    build_planner_input_bundle,
    canonical_json_sha256,
    canonicalize_novel_plan,
    project_narrative_ir_json,
    validate_novel_plan_candidate,
)

ROOT = Path(__file__).resolve().parents[4]
SUITE_ROOT = ROOT / "fixtures" / "novel_plan_benchmark" / "v2"
CAPABILITIES = (
    "linear_mystery",
    "nonlinear_reveal",
    "multiple_suspects",
    "false_belief",
    "competing_hypotheses",
    "resolution_closure",
    "flashback_chronology",
    "complex_mixed",
)
VARIANTS = ("basic", "decoy", "dense")
RUNTIME_VERSION = "novel-plan-benchmark.v2"
GRADER_VERSION = "novel-plan-g0-g3.v1"
QUALITY_GRADER_PROMPT = (
    "你是 NovelPlanIR 质量评审。只评估给定候选，不补写内容。"
    "对 opening、escalation、turn_setup、pov、climax、closure 六项分别给出 0 到 1 分，"
    "只返回含这六个数字字段的 JSON 对象。"
)


class QualityScores(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    opening: float = Field(ge=0, le=1)
    escalation: float = Field(ge=0, le=1)
    turn_setup: float = Field(ge=0, le=1)
    pov: float = Field(ge=0, le=1)
    climax: float = Field(ge=0, le=1)
    closure: float = Field(ge=0, le=1)


@dataclass(slots=True)
class FrozenProvider:
    candidate: dict[str, Any]

    def plan_story(self, request: StoryPlannerRequest) -> StoryPlannerProviderResult:
        return StoryPlannerProviderResult(candidate=copy.deepcopy(self.candidate), usage={})


def planner_input() -> dict[str, Any]:
    document = _read_json(ROOT / "fixtures" / "casefiles" / "restart_loop.casefile.json")
    exposure = {
        "draft_id": 1,
        "plan_revision_id": 1,
        "revision_no": 1,
        "frozen_payload": {
            "entries": [
                {
                    "entry_key": "exposure_restart_log",
                    "sequence_no": 1,
                    "title": "重启日志",
                    "note": None,
                    "refs": [{"object_type": "information_unit", "object_id": "info_restart_log"}],
                }
            ]
        },
        "content_hash": "a" * 64,
    }
    profile = {
        "schema_id": "compiler.novel-profile.v1",
        "structure": {"strategy": "three_act", "target_chapters": 1, "target_scenes": 2},
        "allowed_presentation_modes": ["linear", "flashback"],
        "exposure_policy": "bound_plan",
    }
    return build_planner_input_bundle(
        narrative_ir=project_narrative_ir_json(document),
        exposure=exposure,
        profile=profile,
        compile_mode="canonical",
    )


def validate_suite(*, formal_capability: bool = False) -> dict[str, Any]:
    suite = _read_json(SUITE_ROOT / "suite.json")
    tasks = suite.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 24:
        raise ValueError("Novel Plan capability suite must contain exactly 24 tasks")
    identities = {str(item.get("task_id")) for item in tasks}
    expected = {f"{capability}__{variant}" for capability in CAPABILITIES for variant in VARIANTS}
    if identities != expected:
        raise ValueError("Novel Plan capability matrix is incomplete")
    del formal_capability  # every v2 suite load enforces the formal task contract
    reference_hashes: dict[str, str] = {}
    planner_inputs: dict[str, dict[str, Any]] = {}
    references: dict[str, dict[str, Any]] = {}
    for task in tasks:
        if task.get("primary_capability") not in CAPABILITIES:
            raise ValueError("Task primary_capability is invalid")
        task_id = str(task["task_id"])
        input_name = task.get("planner_input")
        invariants = task.get("outcome_invariants")
        if not isinstance(input_name, str) or not isinstance(invariants, list) or not invariants:
            raise ValueError(
                "Formal Novel Plan task must freeze planner_input and "
                f"outcome_invariants: {task_id}"
            )
        bundle = _read_json(SUITE_ROOT / input_name)
        input_hash = canonical_json_sha256(bundle)
        if task.get("planner_input_hash") != input_hash:
            raise ValueError(f"PlannerInput hash mismatch: {task_id}")
        reference = _read_json(SUITE_ROOT / str(task["reference"]))
        validate_novel_plan_candidate(reference, planner_input=bundle)
        if not _grade_outcome(reference, invariants):
            raise ValueError(f"Reference Solution fails Outcome invariants: {task_id}")
        reference_hashes[task_id] = canonical_json_sha256(reference)
        planner_inputs[task_id] = bundle
        references[task_id] = reference
    return {
        "suite": suite,
        "reference_hashes": reference_hashes,
        "planner_inputs": planner_inputs,
        "references": references,
    }


def run_suite(
    *,
    suite_kind: str,
    mode: str,
    provider_name: str,
    model_id: str,
    quality_grader_model: str | None,
    repeats: int,
    checkpoint_path: Path | None,
    resume: bool,
) -> dict[str, Any]:
    if suite_kind == "capability" and mode == "live":
        if repeats != 3:
            raise ValueError("Formal Capability baseline requires exactly 3 trials per task")
        if not _is_pro(model_id) or not _is_pro(quality_grader_model or ""):
            raise ValueError("Formal Capability Planner and G3 grader must use exact Pro model IDs")
    validated = validate_suite(formal_capability=suite_kind == "capability" and mode == "live")
    suite = validated["suite"]
    first_task_id = str(suite["tasks"][0]["task_id"])
    fingerprint_payload = {
        "suite": suite,
        "suite_kind": suite_kind,
        "runtime": RUNTIME_VERSION,
        "grader": GRADER_VERSION,
        "prompt": STORY_PLANNER_PROMPT_VERSION,
        "candidate_schema": "compiler.novel-plan-candidate.v1",
        "ir_schema": "compiler.novel-plan.v1",
        "provider": provider_name,
        "model_id": model_id,
        "quality_grader_model": quality_grader_model,
        "quality_grader_prompt_hash": canonical_json_sha256({"prompt": QUALITY_GRADER_PROMPT}),
        "repeats": repeats,
        "task_contracts": {
            item["task_id"]: {
                "primary_capability": item["primary_capability"],
                "variant": item["variant"],
                "reference_hash": validated["reference_hashes"][item["task_id"]],
                "planner_input_hash": item["planner_input_hash"],
                "outcome_invariants_hash": canonical_json_sha256(
                    {"invariants": item["outcome_invariants"]}
                ),
            }
            for item in suite["tasks"]
        },
    }
    fingerprint = canonical_json_sha256(fingerprint_payload)
    trials = _resume_trials(checkpoint_path, fingerprint) if resume else []
    completed = {(item["task_id"], item["trial_index"]) for item in trials}
    tasks = suite["tasks"] if suite_kind == "capability" else _synthetic_tasks(suite_kind)
    for task in tasks:
        for trial_index in range(1, repeats + 1):
            key = (task["task_id"], trial_index)
            if key in completed:
                continue
            source_task_id = str(task["task_id"]) if suite_kind == "capability" else first_task_id
            trial = _run_trial(
                task=task,
                trial_index=trial_index,
                suite_kind=suite_kind,
                mode=mode,
                provider_name=provider_name,
                model_id=model_id,
                quality_grader_model=quality_grader_model,
                bundle=validated["planner_inputs"][source_task_id],
                reference=validated["references"][source_task_id],
                outcome_invariants=(
                    task["outcome_invariants"] if suite_kind == "capability" else []
                ),
            )
            trials.append(trial)
            if checkpoint_path is not None:
                _write_atomic(
                    checkpoint_path,
                    {"fingerprint": fingerprint, "trials": trials, "partial": True},
                )
    report = _report(fingerprint_payload, fingerprint, suite_kind, trials)
    if checkpoint_path is not None:
        _write_atomic(checkpoint_path, {**report, "partial": False})
    return report


def _run_trial(
    *,
    task: dict[str, Any],
    trial_index: int,
    suite_kind: str,
    mode: str,
    provider_name: str,
    model_id: str,
    quality_grader_model: str | None,
    bundle: dict[str, Any],
    reference: dict[str, Any],
    outcome_invariants: list[dict[str, Any]],
) -> dict[str, Any]:
    start = time.perf_counter()
    candidate = copy.deepcopy(reference)
    expected_rejection = None
    if suite_kind == "safety":
        candidate, expected_rejection = _safety_mutation(str(task["mutation"]), candidate)
    provider = _provider(mode, provider_name, candidate)
    rounds: list[dict[str, Any]] = []
    contract_valid = False
    semantic_valid = False
    accepted = False
    reason_code = "ok"
    usage: dict[str, int] = {}
    evaluated_candidate: dict[str, Any] | None = None
    try:
        execution = execute_story_planner(
            provider,
            StoryPlannerRequest(
                task_run_id=0,
                prompt_version=STORY_PLANNER_PROMPT_VERSION,
                planner_input=bundle,
                input_hash=canonical_json_sha256(bundle),
                model_id=model_id,
                api_key=_api_key(provider_name) if mode == "live" else "fake",
                max_turns=1,
            ),
        )
        rounds = [
            {
                "call_no": item.call_no,
                "contract_valid": not item.structural_errors,
                "structural_errors": list(item.structural_errors),
                "usage": item.usage,
                "latency_ms": item.latency_ms,
                "raw_output_hash": (
                    sha256(item.raw_output.encode("utf-8")).hexdigest()
                    if item.raw_output is not None
                    else None
                ),
            }
            for item in execution.rounds
        ]
        contract_valid = True
        validate_novel_plan_candidate(execution.candidate, planner_input=bundle)
        semantic_valid = True
        canonicalize_novel_plan(
            execution.candidate,
            planner_input=bundle,
            planner_version="compiler.story-planner.v1",
            component_fingerprint="b" * 64,
        )
        accepted = True
        evaluated_candidate = execution.candidate
        for item in execution.rounds:
            for name, value in item.usage.items():
                if isinstance(value, int):
                    usage[name] = usage.get(name, 0) + value
    except CompilerContractError as error:
        reason_code = error.reason_code
        failed_rounds = getattr(error, "rounds", ())
        if failed_rounds:
            rounds = [
                {
                    "call_no": item.call_no,
                    "contract_valid": not item.structural_errors,
                    "structural_errors": list(item.structural_errors),
                    "usage": item.usage,
                    "latency_ms": item.latency_ms,
                    "raw_output_hash": (
                        sha256(item.raw_output.encode("utf-8")).hexdigest()
                        if item.raw_output is not None
                        else None
                    ),
                }
                for item in failed_rounds
            ]
    except Exception as error:  # provider/network failures are G0, not model failures
        return {
            "task_id": task["task_id"],
            "trial_index": trial_index,
            "passed": False,
            "infrastructure_failure": {"type": type(error).__name__},
            "rounds": rounds,
            "latency_ms": (time.perf_counter() - start) * 1000,
            "usage": usage,
            "provider_invoked": True,
        }
    safety_pass = expected_rejection is None or (not accepted and reason_code == expected_rejection)
    outcome_failures = (
        _outcome_failures(evaluated_candidate, outcome_invariants)
        if expected_rejection is None
        else []
    )
    outcome_pass = accepted and not outcome_failures if expected_rejection is None else safety_pass
    g3 = (
        _live_quality_scores(
            provider_name,
            str(quality_grader_model),
            evaluated_candidate,
        )
        if mode == "live" and evaluated_candidate is not None
        else {"scores": _quality_scores(accepted), "usage": {}, "latency_ms": 0.0}
    )
    return {
        "task_id": task["task_id"],
        "trial_index": trial_index,
        "passed": outcome_pass,
        "infrastructure_failure": None,
        "provider_invoked": True,
        "contract_valid": contract_valid,
        "semantic_valid": semantic_valid,
        "accepted": accepted,
        "reason_code": reason_code,
        "expected_rejection": expected_rejection,
        "outcome_failures": outcome_failures,
        "rounds": rounds,
        "repair_attempts": max(0, len(rounds) - 1),
        "usage": usage,
        "latency_ms": (time.perf_counter() - start) * 1000,
        "graders": {
            "g0_transcript_complete": bool(rounds) or not accepted,
            "g1_safety": safety_pass,
            "g2_outcome": outcome_pass,
            "g3_model_id": quality_grader_model,
            "g3_scores": g3["scores"],
            "g3_usage": g3["usage"],
            "g3_latency_ms": g3["latency_ms"],
        },
    }


def _synthetic_tasks(kind: str) -> list[dict[str, Any]]:
    if kind == "regression":
        return [{"task_id": "reference_replay"}, {"task_id": "canonical_hash_stability"}]
    mutations = (
        "hallucinated_reference",
        "forged_provenance",
        "exposure_order",
        "resolution_missing",
        "dependency_cycle",
        "runtime_identity",
    )
    return [{"task_id": value, "mutation": value} for value in mutations]


def _grade_outcome(
    candidate: dict[str, Any] | None,
    invariants: list[dict[str, Any]],
) -> bool:
    return not _outcome_failures(candidate, invariants)


def _outcome_failures(
    candidate: dict[str, Any] | None,
    invariants: list[dict[str, Any]],
) -> list[str]:
    if candidate is None:
        return ["candidate_missing"]
    scenes = candidate.get("scenes")
    if not isinstance(scenes, list):
        return ["scenes_missing"]
    ordered = sorted(scenes, key=lambda item: int(item.get("discourse_order", 0)))
    failures: list[str] = []
    for invariant in invariants:
        kind = str(invariant.get("kind", ""))
        passed = False
        if kind == "all_presentation_modes":
            allowed = set(invariant.get("allowed", []))
            passed = bool(allowed) and all(
                item.get("presentation_mode") in allowed for item in ordered
            )
        elif kind == "presentation_mode_present":
            passed = any(
                item.get("presentation_mode") == invariant.get("value") for item in ordered
            )
        elif kind == "purpose_present":
            passed = any(item.get("purpose") == invariant.get("value") for item in ordered)
        elif kind == "purpose_order":
            expected_purposes = list(invariant.get("values", []))
            observed_purposes = [str(item.get("purpose")) for item in ordered]
            cursor = 0
            for purpose in observed_purposes:
                if cursor < len(expected_purposes) and purpose == expected_purposes[cursor]:
                    cursor += 1
            passed = bool(expected_purposes) and cursor == len(expected_purposes)
        elif kind == "min_distinct_participant_refs":
            refs = {
                _object_ref_key(ref)
                for scene in ordered
                for ref in scene.get("participant_refs", [])
                if isinstance(ref, dict)
            }
            passed = len(refs) >= int(invariant.get("value", 0))
        elif kind == "basis_refs_include_all":
            observed_basis_refs = {
                _object_ref_key(ref)
                for scene in ordered
                for ref in scene.get("basis_refs", [])
                if isinstance(ref, dict)
            }
            expected_basis_refs = {
                _object_ref_key(ref) for ref in invariant.get("refs", []) if isinstance(ref, dict)
            }
            passed = bool(expected_basis_refs) and expected_basis_refs <= observed_basis_refs
        elif kind == "exposure_action_present":
            passed = any(
                placement.get("entry_key") == invariant.get("entry_key")
                and placement.get("action") == invariant.get("action")
                for scene in ordered
                for placement in scene.get("exposure", [])
                if isinstance(placement, dict)
            )
        elif kind == "resolution_actions":
            expected_resolution_refs = {
                _object_ref_key(ref) for ref in invariant.get("refs", []) if isinstance(ref, dict)
            }
            allowed = set(invariant.get("allowed", []))
            observed_resolution_actions = {
                _object_ref_key(placement["resolution_ref"]): placement.get("action")
                for scene in ordered
                for placement in scene.get("resolutions", [])
                if isinstance(placement, dict) and isinstance(placement.get("resolution_ref"), dict)
            }
            passed = bool(expected_resolution_refs) and all(
                observed_resolution_actions.get(ref) in allowed for ref in expected_resolution_refs
            )
        elif kind == "resolution_in_final_scene":
            resolution_scenes = [scene for scene in ordered if scene.get("resolutions")]
            passed = bool(resolution_scenes) and resolution_scenes == [ordered[-1]]
        elif kind == "dependency_chain_min_length":
            depths: dict[str, int] = {}
            for scene in ordered:
                prerequisites = [str(item) for item in scene.get("prerequisite_scene_ids", [])]
                depths[str(scene.get("scene_id"))] = 1 + max(
                    (depths.get(item, 0) for item in prerequisites),
                    default=0,
                )
            passed = max(depths.values(), default=0) >= int(invariant.get("value", 0))
        elif kind == "flashback_after_event":
            earlier = _object_ref_key(invariant.get("earlier_event_ref", {}))
            later = _object_ref_key(invariant.get("later_event_ref", {}))
            passed = any(
                scene.get("presentation_mode") == "flashback"
                and earlier
                in {
                    _object_ref_key(ref)
                    for ref in scene.get("story_time_refs", [])
                    if isinstance(ref, dict)
                }
                and any(
                    previous.get("discourse_order", 0) < scene.get("discourse_order", 0)
                    and later
                    in {
                        _object_ref_key(ref)
                        for ref in previous.get("story_time_refs", [])
                        if isinstance(ref, dict)
                    }
                    for previous in ordered
                )
                for scene in ordered
            )
        if not passed:
            failures.append(kind or "invariant_kind_missing")
    return failures


def _object_ref_key(value: dict[str, Any]) -> str:
    object_type = value.get("object_type")
    object_id = value.get("object_id")
    if not isinstance(object_type, str) or not isinstance(object_id, str):
        return ""
    return f"{object_type}:{object_id}"


def _safety_mutation(mutation: str, candidate: dict[str, Any]) -> tuple[dict[str, Any], str]:
    scenes = candidate["scenes"]
    if mutation == "hallucinated_reference":
        scenes[0]["event_refs"][0]["object_id"] = "evt_hallucinated"
        return candidate, "compiler_story_plan_reference_invalid"
    if mutation == "forged_provenance":
        scenes[0]["basis_refs"][0]["object_id"] = "evt_forged"
        return candidate, "compiler_story_plan_reference_invalid"
    if mutation == "exposure_order":
        scenes[0]["exposure"][0]["action"] = "reinterpret"
        return candidate, "compiler_story_plan_exposure_before_introduction"
    if mutation == "resolution_missing":
        for scene in scenes:
            scene["resolutions"] = []
        return candidate, "compiler_story_plan_resolution_uncovered"
    if mutation == "dependency_cycle":
        scenes[0]["prerequisite_scene_ids"] = [scenes[-1]["scene_id"]]
        return candidate, "compiler_story_plan_dependency_cycle"
    scenes[0]["intent"] = "保留"
    scenes[0]["compile_run_id"] = 99
    return candidate, "compiler_story_plan_runtime_identity_forbidden"


def _report(
    frozen: dict[str, Any], fingerprint: str, suite_kind: str, trials: list[dict[str, Any]]
) -> dict[str, Any]:
    total = len(trials)
    safe_failures = [
        item for item in trials if item.get("expected_rejection") and item.get("accepted")
    ]
    infra = [item for item in trials if item.get("infrastructure_failure")]
    tasks: dict[str, list[dict[str, Any]]] = {}
    for item in trials:
        tasks.setdefault(str(item["task_id"]), []).append(item)
    pass_at_3 = sum(any(x.get("passed") for x in values) for values in tasks.values())
    all_three = sum(
        len(values) == 3 and all(x.get("passed") for x in values) for values in tasks.values()
    )
    final_contract = sum(bool(item.get("contract_valid")) for item in trials)
    first_contract = sum(
        bool(item.get("rounds")) and bool(item["rounds"][0].get("contract_valid"))
        for item in trials
    )
    semantic = sum(bool(item.get("semantic_valid")) for item in trials)
    outcome_passed = sum(bool(item.get("passed")) for item in trials)
    rejection_counts: dict[str, int] = {}
    for item in trials:
        code = str(item.get("reason_code") or "ok")
        rejection_counts[code] = rejection_counts.get(code, 0) + 1
    gates = {
        "unsafe_trial_rate_zero": not safe_failures,
        "no_unsafe_acceptance": not safe_failures,
        "infrastructure_complete": not infra,
    }
    if suite_kind != "capability":
        gates["all_deterministic_trials_pass"] = all(item.get("passed") for item in trials)
    return {
        "schema_id": "benchmark.novel-plan-report.v1",
        "status": "passed" if all(gates.values()) else "failed",
        "suite_kind": suite_kind,
        "fingerprint": fingerprint,
        "frozen": frozen,
        "trials": trials,
        "metrics": {
            "trial_count": total,
            "first_pass_contract_valid_rate": first_contract / total if total else 0,
            "final_contract_valid_rate": final_contract / total if total else 0,
            "semantic_valid_rate": semantic / total if total else 0,
            "outcome_passed_trial_count": outcome_passed,
            "outcome_pass_rate": outcome_passed / total if total else 0,
            "repair_attempt_count": sum(int(item.get("repair_attempts", 0)) for item in trials),
            "round_contract_valid_rates": _round_contract_rates(trials),
            "round_latency_ms": _round_latency(trials),
            "pass_at_3_task_count": pass_at_3,
            "all_three_pass_task_count": all_three,
            "unsafe_trial_rate": len(safe_failures) / total if total else 0,
            "infrastructure_failure_rate": len(infra) / total if total else 0,
            "failure_rates": rejection_counts,
            "latency_ms_total": sum(float(item.get("latency_ms", 0)) for item in trials),
            "usage_total": _usage_total(trials),
            "g3_usage_total": _g3_usage_total(trials),
            "g3_latency_ms_total": sum(
                float(item.get("graders", {}).get("g3_latency_ms", 0)) for item in trials
            ),
            "g3_quality_distribution": _g3_distribution(trials),
            "task_results": {
                task_id: {
                    "passed_trials": sum(bool(item.get("passed")) for item in values),
                    "pass_at_3": any(item.get("passed") for item in values),
                    "all_three_pass": len(values) == 3
                    and all(item.get("passed") for item in values),
                }
                for task_id, values in sorted(tasks.items())
            },
        },
        "gates": gates,
    }


def _provider(mode: str, provider: str, candidate: dict[str, Any]) -> Any:
    if mode == "fake":
        return FrozenProvider(candidate)
    return OpenAIAgentsProvider() if provider == "openai" else DeepSeekAgentsProvider()


def _api_key(provider: str) -> str:
    name = "OPENAI_API_KEY" if provider == "openai" else "DEEPSEEK_API_KEY"
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is required for live Novel Plan benchmark")
    return value


def _is_pro(model_id: str) -> bool:
    return "pro" in model_id.lower()


def _quality_scores(valid: bool) -> dict[str, float]:
    value = 1.0 if valid else 0.0
    dimensions = ("opening", "escalation", "turn_setup", "pov", "climax", "closure")
    return {key: value for key in dimensions}


def _live_quality_scores(provider: str, model_id: str, candidate: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    client = OpenAI(
        api_key=_api_key(provider),
        base_url="https://api.deepseek.com" if provider == "deepseek" else None,
        max_retries=2,
    )
    response = client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": QUALITY_GRADER_PROMPT},
            {
                "role": "user",
                "content": json.dumps(candidate, ensure_ascii=False, separators=(",", ":")),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("Novel Plan G3 grader returned no content")
    usage = response.usage
    return {
        "scores": QualityScores.model_validate_json(content).model_dump(mode="json"),
        "usage": {
            "requests": 1,
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            "cached_tokens": int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0),
        },
        "latency_ms": (time.perf_counter() - started) * 1000,
    }


def _usage_total(trials: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for trial in trials:
        for round_result in trial.get("rounds", []):
            for key, value in round_result.get("usage", {}).items():
                if isinstance(value, int):
                    result[key] = result.get(key, 0) + value
    return result


def _round_contract_rates(trials: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for index in range(4):
        available = [
            item["rounds"][index] for item in trials if len(item.get("rounds", [])) > index
        ]
        if available:
            result[str(index + 1)] = sum(
                bool(item.get("contract_valid")) for item in available
            ) / len(available)
    return result


def _round_latency(trials: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for index in range(4):
        values = [
            float(item["rounds"][index].get("latency_ms", 0))
            for item in trials
            if len(item.get("rounds", [])) > index
        ]
        if values:
            result[str(index + 1)] = sum(values)
    return result


def _g3_distribution(trials: list[dict[str, Any]]) -> dict[str, Any]:
    graded = [
        trial
        for trial in trials
        if int(trial.get("graders", {}).get("g3_usage", {}).get("requests", 0)) > 0
    ]
    dimensions = ("opening", "escalation", "turn_setup", "pov", "climax", "closure")
    by_dimension: dict[str, dict[str, float]] = {}
    values: list[float] = []
    for dimension in dimensions:
        scores = [
            float(trial["graders"]["g3_scores"][dimension])
            for trial in graded
            if isinstance(
                trial.get("graders", {}).get("g3_scores", {}).get(dimension), (int, float)
            )
        ]
        values.extend(scores)
        by_dimension[dimension] = {
            "count": float(len(scores)),
            "mean": sum(scores) / len(scores) if scores else 0.0,
            "min": min(scores, default=0.0),
            "max": max(scores, default=0.0),
        }
    return {
        "graded_trial_count": len(graded),
        "count": float(len(values)),
        "mean": sum(values) / len(values) if values else 0.0,
        "min": min(values, default=0.0),
        "max": max(values, default=0.0),
        "by_dimension": by_dimension,
    }


def _g3_usage_total(trials: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for trial in trials:
        for key, value in trial.get("graders", {}).get("g3_usage", {}).items():
            if isinstance(value, int):
                result[key] = result.get(key, 0) + value
    return result


def _resume_trials(path: Path | None, fingerprint: str) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    payload = _read_json(path)
    if payload.get("fingerprint") != fingerprint:
        raise ValueError("Checkpoint fingerprint does not match the requested benchmark")
    trials = payload.get("trials", [])
    if not isinstance(trials, list):
        raise ValueError("Checkpoint trials are invalid")
    return trials


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run N4.3 Novel Plan benchmark")
    parser.add_argument(
        "--suite-kind",
        choices=("regression", "safety", "capability"),
        default="regression",
    )
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--provider", choices=("openai", "deepseek"), default="openai")
    parser.add_argument("--model", default="fake-story-planner")
    parser.add_argument("--quality-grader-model")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--checkpoint-path", type=Path)
    parser.add_argument("--resume", action="store_true")
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
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report_path is not None:
        _write_atomic(args.report_path, report)
    if report["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()


__all__ = ["main", "planner_input", "run_suite", "validate_suite"]
