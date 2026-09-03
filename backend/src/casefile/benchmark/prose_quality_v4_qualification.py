"""One-shot B3 v4 pointwise Quality and bounded Polisher qualification."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_judge import (
    FULL_COUNCIL_POLICY,
    PROSE_COUNCIL_NETWORK_RETRIES,
    DeepSeekProseJudgeProvider,
    ProseJudgeProvider,
)
from casefile.agent_runtime.prose_patch_polisher import (
    PROSE_PATCH_POLISHER_COMPONENT_HASH,
    PROSE_PATCH_POLISHER_MODEL_ID,
    PROSE_PATCH_POLISHER_NETWORK_RETRIES,
    PROSE_PATCH_POLISHER_PROMPT_VERSION,
    DeepSeekProsePatchPolisherProvider,
    ProsePatchPolisherProvider,
)
from casefile.agent_runtime.prose_polish_supervisor_v2 import (
    PROSE_POLISH_SUPERVISOR_V2_VERSION,
    ProsePolishSupervisorV2Execution,
    execute_prose_polish_supervisor_v2,
)
from casefile.agent_runtime.prose_quality_assessor import (
    PROSE_QUALITY_ASSESSMENT_COMPONENT_HASH,
    PROSE_QUALITY_ASSESSMENT_MODEL_ID,
    PROSE_QUALITY_ASSESSMENT_NETWORK_RETRIES,
    PROSE_QUALITY_ASSESSMENT_PROMPT_VERSION,
    DeepSeekProseQualityAssessmentProvider,
    ProseQualityAssessmentProvider,
    execute_quality_assessment,
)
from casefile.benchmark.prose_quality_v4_eval import (
    DEFAULT_PRIVATE_QUALIFICATION_SUITE,
    DEFAULT_QUALIFICATION_DESCRIPTOR,
    POLISHER_V4_QUALIFICATION_GATES,
    QUALITY_V4_QUALIFICATION_GATES,
    ROOT,
    canonical_hash,
    load_prose_quality_v4_qualification_suite,
)
from casefile.domain.narrative_compiler import resolve_quality_delta

REPORT_VERSION: Final = "casefile.prose-quality-qualification-report.v2"
EXECUTOR_VERSION: Final = "prose-quality-qualification-executor-v2"
LIVE_CONFIRMATION: Final = "RUN_B3_V4_QUALITY_PATCH_QUALIFICATION_ONCE"
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_USAGE_KEYS: Final = (
    "requests",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
)


class ProseQualityV4QualificationError(RuntimeError):
    """The formal v4 attempt violated a preflight or immutable-output boundary."""


@dataclass(frozen=True, slots=True)
class _TaskOutcome:
    row: dict[str, Any]
    infrastructure_failed: bool


def run_prose_quality_v4_qualification(
    *,
    attempt_id: str,
    api_key: str,
    assessment_provider: ProseQualityAssessmentProvider,
    polisher_provider: ProsePatchPolisherProvider,
    judge_provider: ProseJudgeProvider,
    qualification_suite_path: Path = DEFAULT_PRIVATE_QUALIFICATION_SUITE,
    descriptor_path: Path = DEFAULT_QUALIFICATION_DESCRIPTOR,
    output_dir: Path | None = None,
    require_clean_source: bool = True,
    source_probe: Callable[[], dict[str, Any]] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the frozen private package once and emit hash-only evidence."""

    if not _ATTEMPT_ID.fullmatch(attempt_id):
        raise ProseQualityV4QualificationError("prose_quality_v4_attempt_id_invalid")
    if not api_key:
        raise ProseQualityV4QualificationError(
            "prose_quality_v4_live_credential_required"
        )
    report_path = output_dir / "report.json" if output_dir is not None else None
    attempt_path = (
        output_dir / "attempt-manifest.json" if output_dir is not None else None
    )
    if (report_path and report_path.exists()) or (attempt_path and attempt_path.exists()):
        raise ProseQualityV4QualificationError(
            "prose_quality_v4_qualification_attempt_already_exists"
        )
    package = load_prose_quality_v4_qualification_suite(
        qualification_suite_path, descriptor_path
    )
    source_before = (source_probe or (lambda: _git_source_state(ROOT)))()
    _validate_source_state(source_before)
    if require_clean_source and not source_before["clean"]:
        raise ProseQualityV4QualificationError(
            "prose_quality_v4_qualification_clean_source_required"
        )
    manifest = _frozen_manifest(
        package,
        attempt_id,
        source_before,
        assessment_provider,
        polisher_provider,
        judge_provider,
    )
    attempt = {
        "schema_id": "casefile.prose-quality-qualification-attempt.v2",
        "status": "started",
        **manifest,
    }
    attempt["attempt_manifest_hash"] = canonical_hash(attempt)
    manifest["attempt_manifest_hash"] = attempt["attempt_manifest_hash"]
    if attempt_path is not None:
        _write_json_once(attempt_path, attempt)

    quality_rows = []
    polisher_rows = []
    abort = False
    for index, task in enumerate(package["quality_tasks"], start=1):
        if abort:
            quality_rows.append(_not_run_quality(task, "prior_infrastructure_failure"))
            _progress(progress, f"[quality {index}/16] status=not_run")
            continue
        _progress(progress, f"[quality {index}/16] status=started")
        try:
            outcome = _execute_quality_task(task, assessment_provider, api_key)
        except Exception as error:
            outcome = _unexpected_quality(task, error)
        quality_rows.append(outcome.row)
        abort = outcome.infrastructure_failed
        _progress(progress, f"[quality {index}/16] status={outcome.row['status']}")
    for index, task in enumerate(package["polisher_tasks"], start=1):
        if abort:
            polisher_rows.append(_not_run_polisher(task, "prior_infrastructure_failure"))
            _progress(progress, f"[polisher {index}/24] status=not_run")
            continue
        _progress(progress, f"[polisher {index}/24] status=started")
        try:
            outcome = _execute_polisher_task(
                task, assessment_provider, polisher_provider, judge_provider, api_key
            )
        except Exception as error:
            outcome = _unexpected_polisher(task, error)
        polisher_rows.append(outcome.row)
        abort = outcome.infrastructure_failed
        _progress(progress, f"[polisher {index}/24] status={outcome.row['status']}")
    source_after = (source_probe or (lambda: _git_source_state(ROOT)))()
    _validate_source_state(source_after)
    package_stable = _package_stable(
        qualification_suite_path, descriptor_path, manifest
    )
    report = _build_report(
        manifest, quality_rows, polisher_rows, source_after, package_stable
    )
    if report_path is not None:
        _write_json_once(report_path, report)
    return report


def _execute_quality_task(
    task: dict[str, Any], provider: ProseQualityAssessmentProvider, api_key: str
) -> _TaskOutcome:
    asset = task["asset"]
    executions = []
    for suffix in ("a", "b"):
        executions.append(
            execute_quality_assessment(
                provider,
                checklist=asset["checklist"],
                render=asset[f"render_{suffix}"],
                profile=asset["profile"],
                semantic_consensus=asset[f"semantic_consensus_{suffix}"],
                model_id=PROSE_QUALITY_ASSESSMENT_MODEL_ID,
                api_key=api_key,
            )
        )
        if executions[-1].status != "completed":
            break
    status = next(
        (_status(item.status) for item in executions if item.status != "completed"),
        "completed" if len(executions) == 2 else "protocol_failed",
    )
    assessments = [item.assessment for item in executions]
    severity_correct = 0
    delta_correct = False
    metamorphic_stable = False
    delta_hash = None
    if status == "completed" and len(assessments) == 2 and all(assessments):
        for role, assessment in zip(("render_a", "render_b"), assessments, strict=True):
            severity_correct += sum(
                actual["severity"] == expected["severity"]
                for actual, expected in zip(
                    assessment["dimensions"],  # type: ignore[index]
                    asset["gold"][role]["dimensions"],
                    strict=True,
                )
            )
        delta = resolve_quality_delta(
            original_assessment=assessments[0],  # type: ignore[arg-type]
            polished_assessment=assessments[1],  # type: ignore[arg-type]
            checklist=asset["checklist"],
            original_render=asset["render_a"],
            polished_render=asset["render_b"],
            profile=asset["profile"],
            original_semantic_consensus=asset["semantic_consensus_a"],
            preservation_consensus=asset["semantic_consensus_b"],
        ).model_dump(mode="json")
        delta_correct = delta["accept_polished"] == asset["gold"]["accept_polished"]
        delta_hash = canonical_hash(delta)
        metamorphic_stable = (
            asset["metamorphic_neutral"]
            and [item["severity"] for item in assessments[0]["dimensions"]]  # type: ignore[index]
            == [item["severity"] for item in assessments[1]["dimensions"]]  # type: ignore[index]
        )
    calls = []
    for execution in executions:
        if execution.call is not None:
            calls.append(_completed_audit("quality_assessment", execution.call))
        if execution.failed_call is not None:
            calls.append(_failed_audit("quality_assessment", execution.failed_call))
    row = {
        "task_id": task["descriptor"]["task_id"],
        "focus": task["descriptor"]["focus"],
        "pair_fingerprint": task["descriptor"]["pair_fingerprint"],
        "status": status,
        "error_code": next(
            (item.error_code for item in executions if item.error_code), None
        ),
        "attempted": True,
        "render_hashes": [
            canonical_hash(asset["render_a"]),
            canonical_hash(asset["render_b"]),
        ],
        "assessment_hashes": [
            canonical_hash(value) if value else None for value in assessments
        ],
        "severity_correct": severity_correct,
        "delta_correct": delta_correct,
        "delta_hash": delta_hash,
        "five_dimension_coverage": status == "completed",
        "evidence_valid": status == "completed",
        "metamorphic_neutral": asset["metamorphic_neutral"],
        "metamorphic_stable": metamorphic_stable,
        "calls": calls,
    }
    return _TaskOutcome(row, status == "infrastructure_failed")


def _execute_polisher_task(
    task: dict[str, Any],
    assessment_provider: ProseQualityAssessmentProvider,
    polisher_provider: ProsePatchPolisherProvider,
    judge_provider: ProseJudgeProvider,
    api_key: str,
) -> _TaskOutcome:
    asset = task["asset"]
    execution = execute_prose_polish_supervisor_v2(
        assessment_provider,
        polisher_provider,
        judge_provider,
        checklist=asset["checklist"],
        profile=asset["profile"],
        original_render=asset["original_render"],
        semantic_consensus=asset["semantic_consensus"],
        quality_model_id=PROSE_QUALITY_ASSESSMENT_MODEL_ID,
        generation_model_id=PROSE_PATCH_POLISHER_MODEL_ID,
        api_key=api_key,
    )
    status = _status(execution.status)
    finalized = execution.status in {"finalized_original", "finalized_polished"}
    polished_accepted = execution.status == "finalized_polished"
    original_text = [item["text"] for item in asset["original_render"]["blocks"]]
    accepted_text = (
        [item["text"] for item in execution.accepted_render["blocks"]]
        if execution.accepted_render
        else []
    )
    rejected = finalized and not polished_accepted
    exact_rollback = rejected and accepted_text == original_text
    patch_valid = bool(
        execution.polish
        and execution.polish.status == "completed"
        and execution.polish.candidate is not None
        and not execution.polish.abstained
    )
    outside_exact = bool(
        patch_valid
        and execution.polish
        and execution.polish.render
        and execution.window_manifest
        and _patch_replay_matches(
            asset["original_render"],
            execution.polish.render,
            cast(dict[str, Any], execution.polish.candidate),
            execution.window_manifest,
        )
    )
    preservation_passed = bool(
        execution.preservation
        and execution.preservation.status == "completed"
        and execution.preservation.consensus
        and execution.preservation.consensus["scene_verdict"] == "pass"
    )
    critical = []
    if polished_accepted and execution.preservation and execution.preservation.consensus:
        verdicts = {
            item["check_id"]: item["final_verdict"]
            for item in execution.preservation.consensus["checks"]
        }
        critical = [
            check_id
            for check_id in asset["critical_check_ids"]
            if verdicts.get(check_id) != "pass"
        ]
    quality_non_loss = finalized and (
        exact_rollback
        or bool(
            polished_accepted
            and execution.quality_delta
            and execution.quality_delta["accept_polished"]
            and not any(
                item["regressed"]
                for item in execution.quality_delta["dimension_deltas"]
            )
        )
    )
    return _TaskOutcome(
        {
            "task_id": task["descriptor"]["task_id"],
            "focus": task["descriptor"]["focus"],
            "input_fingerprint": task["descriptor"]["input_fingerprint"],
            "status": status,
            "error_code": execution.error_code,
            "attempted": True,
            "original_render_hash": canonical_hash(asset["original_render"]),
            "window_manifest_hash": canonical_hash(execution.window_manifest)
            if execution.window_manifest
            else None,
            "patch_hash": canonical_hash(execution.polish.candidate)
            if execution.polish and execution.polish.candidate
            else None,
            "polished_render_hash": canonical_hash(execution.polish.render)
            if execution.polish and execution.polish.render
            else None,
            "preservation_consensus_hash": canonical_hash(
                execution.preservation.consensus
            )
            if execution.preservation and execution.preservation.consensus
            else None,
            "quality_delta_hash": canonical_hash(execution.quality_delta)
            if execution.quality_delta
            else None,
            "accepted_render_hash": canonical_hash(execution.accepted_render)
            if execution.accepted_render
            else None,
            "selection_reason": execution.selection_reason,
            "patch_contract_valid": patch_valid,
            "outside_window_exact": outside_exact,
            "preservation_passed": preservation_passed,
            "quality_non_loss": quality_non_loss,
            "polished_accepted": polished_accepted,
            "rejected_polish": rejected,
            "exact_original_rollback": exact_rollback,
            "critical_semantic_regression_check_ids": critical,
            "calls": _supervisor_audits(execution),
        },
        status == "infrastructure_failed",
    )


def _build_report(
    manifest: dict[str, Any],
    quality_rows: list[dict[str, Any]],
    polisher_rows: list[dict[str, Any]],
    source_after: dict[str, Any],
    package_stable: bool,
) -> dict[str, Any]:
    quality_attempted = sum(item["attempted"] for item in quality_rows)
    polisher_attempted = sum(item["attempted"] for item in polisher_rows)
    severity = sum(item["severity_correct"] for item in quality_rows)
    delta = sum(item["delta_correct"] for item in quality_rows)
    coverage = sum(item["five_dimension_coverage"] for item in quality_rows) * 2
    evidence = sum(item["evidence_valid"] for item in quality_rows) * 2
    metamorphic = sum(item["metamorphic_stable"] for item in quality_rows)
    patch_valid = sum(item["patch_contract_valid"] for item in polisher_rows)
    outside = sum(item["outside_window_exact"] for item in polisher_rows)
    preservation = sum(item["preservation_passed"] for item in polisher_rows)
    non_loss = sum(item["quality_non_loss"] for item in polisher_rows)
    accepted = sum(item["polished_accepted"] for item in polisher_rows)
    rejected = sum(item["rejected_polish"] for item in polisher_rows)
    rollback = sum(item["exact_original_rollback"] for item in polisher_rows)
    critical = sum(
        len(item["critical_semantic_regression_check_ids"]) for item in polisher_rows
    )
    all_rows = [*quality_rows, *polisher_rows]
    protocol = sum(item["status"] == "protocol_failed" for item in all_rows)
    infrastructure = sum(item["status"] == "infrastructure_failed" for item in all_rows)
    rollback_rate = 1.0 if rejected == 0 else rollback / rejected
    source_stable = source_after == manifest["source_before"]
    gates = {
        "complete_quality_16": len(quality_rows) == quality_attempted == 16,
        "complete_polisher_24": len(polisher_rows) == polisher_attempted == 24,
        "severity_exact": severity
        >= QUALITY_V4_QUALIFICATION_GATES["severity_exact_min"],
        "delta_correct": delta >= QUALITY_V4_QUALIFICATION_GATES["delta_correct_min"],
        "five_dimension_coverage": coverage
        >= QUALITY_V4_QUALIFICATION_GATES["five_dimension_coverage_min"],
        "evidence_valid": evidence
        >= QUALITY_V4_QUALIFICATION_GATES["evidence_valid_min"],
        "metamorphic_stability": metamorphic
        >= QUALITY_V4_QUALIFICATION_GATES["metamorphic_stability_min"],
        "patch_contract_valid": patch_valid
        >= POLISHER_V4_QUALIFICATION_GATES["patch_contract_valid_min"],
        "outside_window_exact": outside
        >= POLISHER_V4_QUALIFICATION_GATES["outside_window_exact_min"],
        "preservation": preservation
        >= POLISHER_V4_QUALIFICATION_GATES["preservation_pass_min"],
        "quality_non_loss": non_loss
        >= POLISHER_V4_QUALIFICATION_GATES["quality_non_loss_min"],
        "polished_accepted": accepted
        >= POLISHER_V4_QUALIFICATION_GATES["polished_accepted_min"],
        "critical_semantic_regression": critical
        <= POLISHER_V4_QUALIFICATION_GATES["critical_semantic_regression_max"],
        "exact_rollback": rollback_rate
        >= POLISHER_V4_QUALIFICATION_GATES["rejected_exact_rollback_rate_min"],
        "protocol_failure": protocol == 0,
        "infrastructure_failure": infrastructure == 0,
        "source_clean": bool(manifest["source_before"]["clean"]),
        "source_stable": source_stable,
        "qualification_package_stable": package_stable,
        "exact_provider_adapters": (
            manifest["quality_provider_adapter"]
            == "DeepSeekProseQualityAssessmentProvider"
            and manifest["polisher_provider_adapter"]
            == "DeepSeekProsePatchPolisherProvider"
            and manifest["judge_provider_adapter"] == "DeepSeekProseJudgeProvider"
        ),
    }
    qualified = all(gates.values())
    calls = [call for row in all_rows for call in row["calls"]]
    usage = _empty_usage()
    for call in calls:
        _merge_usage(usage, call.get("usage"))
    report = {
        "schema_id": REPORT_VERSION,
        **manifest,
        "source_after": source_after,
        "source_stable": source_stable,
        "qualification_package_stable": package_stable,
        "qualification_outcome": "passed"
        if qualified
        else "inconclusive_infrastructure"
        if infrastructure or quality_attempted < 16 or polisher_attempted < 24
        else "failed",
        "qualification_eligible": True,
        "development_baseline": False,
        "qualified": qualified,
        "quality_holdout_count": 16,
        "quality_assessment_count": 32,
        "quality_attempted_count": quality_attempted,
        "severity_exact": {"passed": severity, "total": 160},
        "delta_accuracy": {"passed": delta, "total": 16},
        "five_dimension_coverage": {"passed": coverage, "total": 32},
        "evidence_valid": {"passed": evidence, "total": 32},
        "metamorphic_stability": {"passed": metamorphic, "total": 4},
        "polisher_task_count": 24,
        "polisher_attempted_count": polisher_attempted,
        "patch_contract_valid": {"passed": patch_valid, "total": 24},
        "outside_window_exact": {"passed": outside, "total": 24},
        "preservation": {"passed": preservation, "total": 24},
        "quality_non_loss": {"passed": non_loss, "total": 24},
        "polished_accepted": {"passed": accepted, "total": 24},
        "rejected_exact_rollback": {"passed": rollback, "total": rejected},
        "critical_semantic_regression_count": critical,
        "failure_counts": {
            "protocol": protocol,
            "infrastructure": infrastructure,
            "not_run": 40 - quality_attempted - polisher_attempted,
        },
        "logical_model_call_count": len(calls),
        "physical_transport_attempt_count": sum(
            len(call["transport_attempts"]) for call in calls
        ),
        "usage": usage,
        "latency_ms": sum(int(call.get("latency_ms", 0)) for call in calls),
        "gates": gates,
        "quality_rows": quality_rows,
        "polisher_rows": polisher_rows,
    }
    report["report_hash"] = canonical_hash(report)
    return report


def _frozen_manifest(
    package: dict[str, Any],
    attempt_id: str,
    source: dict[str, Any],
    assessment_provider: ProseQualityAssessmentProvider,
    polisher_provider: ProsePatchPolisherProvider,
    judge_provider: ProseJudgeProvider,
) -> dict[str, Any]:
    prompt_ids = {
        "quality_assessment": (
            "prose_quality_assessment",
            PROSE_QUALITY_ASSESSMENT_PROMPT_VERSION,
        ),
        "patch_polisher": (
            "prose_patch_polisher",
            PROSE_PATCH_POLISHER_PROMPT_VERSION,
        ),
        "fidelity": ("prose_fidelity_judge", "prose-fidelity-judge-v6"),
        "adversarial": ("prose_adversarial_judge", "prose-adversarial-judge-v5"),
        "coherence": ("prose_coherence_judge", "prose-coherence-judge-v5"),
        "arbiter": ("prose_arbiter", "prose-arbiter-v5"),
    }
    prompts = {
        name: {
            "version": load_prompt(agent, version).version,
            "hash": load_prompt(agent, version).system_prompt_sha256,
        }
        for name, (agent, version) in prompt_ids.items()
    }
    descriptor = package["descriptor"]
    frozen = {
        "executor_version": EXECUTOR_VERSION,
        "supervisor_version": PROSE_POLISH_SUPERVISOR_V2_VERSION,
        "attempt_id": attempt_id,
        "source_before": source,
        "descriptor_hash": descriptor["descriptor_hash"],
        "private_suite_hash": canonical_hash(package["suite"]),
        "review_policy": descriptor["review_policy"],
        "review_attestation_hash": descriptor["review_attestation_hash"],
        "quality_model_id": PROSE_QUALITY_ASSESSMENT_MODEL_ID,
        "generation_model_id": PROSE_PATCH_POLISHER_MODEL_ID,
        "quality_provider_adapter": type(assessment_provider).__name__,
        "polisher_provider_adapter": type(polisher_provider).__name__,
        "judge_provider_adapter": type(judge_provider).__name__,
        "prompts": prompts,
        "quality_component_hash": PROSE_QUALITY_ASSESSMENT_COMPONENT_HASH,
        "polisher_component_hash": PROSE_PATCH_POLISHER_COMPONENT_HASH,
        "preservation_policy_id": FULL_COUNCIL_POLICY.policy_id,
        "preservation_policy_hash": FULL_COUNCIL_POLICY.policy_hash,
        "quality_network_retries": PROSE_QUALITY_ASSESSMENT_NETWORK_RETRIES,
        "polisher_network_retries": PROSE_PATCH_POLISHER_NETWORK_RETRIES,
        "judge_network_retries": PROSE_COUNCIL_NETWORK_RETRIES,
        "quality_gate_thresholds": QUALITY_V4_QUALIFICATION_GATES,
        "polisher_gate_thresholds": POLISHER_V4_QUALIFICATION_GATES,
    }
    frozen["attempt_fingerprint"] = canonical_hash(frozen)
    return frozen


def _supervisor_audits(
    execution: ProsePolishSupervisorV2Execution,
) -> list[dict[str, Any]]:
    audits = []
    for component, step in (
        ("quality_assessment_before", execution.before_assessment),
        ("patch_polisher", execution.polish),
        ("quality_assessment_after", execution.after_assessment),
    ):
        if step is not None and step.call is not None:
            audits.append(_completed_audit(component, step.call))
        if step is not None and step.failed_call is not None:
            audits.append(_failed_audit(component, step.failed_call))
    if execution.preservation is not None:
        audits.extend(
            _completed_audit("preservation_judge", call)
            for call in execution.preservation.calls
        )
        if execution.preservation.failed_call is not None:
            audits.append(
                _failed_audit(
                    "preservation_judge", execution.preservation.failed_call
                )
            )
    return audits


def _completed_audit(component: str, call: Any) -> dict[str, Any]:
    return {
        "component": component,
        "status": "completed",
        "error_code": None,
        "request_fingerprint": call.request_fingerprint,
        "prompt_hash": call.prompt_hash,
        "input_hash": call.input_hash,
        "component_input_hash": getattr(call, "component_input_hash", None),
        "output_hash": call.output_hash,
        "model_id": call.model_id,
        "prompt_version": call.prompt_version,
        "usage": {key: int(call.usage.get(key, 0)) for key in _USAGE_KEYS},
        "latency_ms": call.latency_ms,
        "recovered": call.recovered,
        "transport_attempts": [_attempt_audit(item) for item in call.transport_attempts],
    }


def _failed_audit(component: str, call: Any) -> dict[str, Any]:
    return {
        "component": component,
        "status": "failed",
        "error_code": call.error_code,
        "request_fingerprint": call.request_fingerprint,
        "prompt_hash": call.prompt_hash,
        "input_hash": call.input_hash,
        "component_input_hash": getattr(call, "component_input_hash", None),
        "output_hash": None,
        "model_id": call.model_id,
        "prompt_version": call.prompt_version,
        "usage": _empty_usage(),
        "latency_ms": sum(item.latency_ms for item in call.transport_attempts),
        "recovered": False,
        "transport_attempts": [_attempt_audit(item) for item in call.transport_attempts],
    }


def _attempt_audit(attempt: Any) -> dict[str, Any]:
    return {
        "attempt_index": attempt.attempt_index,
        "status": attempt.status,
        "latency_ms": attempt.latency_ms,
        "error_code": attempt.error_code,
        "response_observed": attempt.response_observed,
        "usage": None
        if attempt.usage is None
        else {key: int(attempt.usage.get(key, 0)) for key in _USAGE_KEYS},
    }


def _patch_replay_matches(
    original: dict[str, Any],
    polished: dict[str, Any],
    candidate: dict[str, Any],
    manifest: dict[str, Any],
) -> bool:
    windows = {item["window_id"]: item for item in manifest["windows"]}
    edits = {item["window_id"]: item for item in candidate["edits"]}
    polished_by_id = {item["block_id"]: item["text"] for item in polished["blocks"]}
    for block in original["blocks"]:
        expected = block["text"]
        block_windows = [
            value
            for window_id, value in windows.items()
            if value["block_id"] == block["block_id"] and window_id in edits
        ]
        for window in sorted(block_windows, key=lambda item: item["start_char"], reverse=True):
            edit = edits[window["window_id"]]
            expected = (
                expected[: window["start_char"]]
                + edit["replacement_text"]
                + expected[window["end_char"] :]
            )
        if polished_by_id.get(block["block_id"]) != expected:
            return False
    return True


def _not_run_quality(task: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "task_id": task["descriptor"]["task_id"],
        "focus": task["descriptor"]["focus"],
        "pair_fingerprint": task["descriptor"]["pair_fingerprint"],
        "status": "not_run",
        "error_code": reason,
        "attempted": False,
        "render_hashes": [],
        "assessment_hashes": [],
        "severity_correct": 0,
        "delta_correct": False,
        "delta_hash": None,
        "five_dimension_coverage": False,
        "evidence_valid": False,
        "metamorphic_neutral": task["asset"]["metamorphic_neutral"],
        "metamorphic_stable": False,
        "calls": [],
    }


def _not_run_polisher(task: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "task_id": task["descriptor"]["task_id"],
        "focus": task["descriptor"]["focus"],
        "input_fingerprint": task["descriptor"]["input_fingerprint"],
        "status": "not_run",
        "error_code": reason,
        "attempted": False,
        "original_render_hash": canonical_hash(task["asset"]["original_render"]),
        "window_manifest_hash": None,
        "patch_hash": None,
        "polished_render_hash": None,
        "preservation_consensus_hash": None,
        "quality_delta_hash": None,
        "accepted_render_hash": None,
        "selection_reason": None,
        "patch_contract_valid": False,
        "outside_window_exact": False,
        "preservation_passed": False,
        "quality_non_loss": False,
        "polished_accepted": False,
        "rejected_polish": False,
        "exact_original_rollback": False,
        "critical_semantic_regression_check_ids": [],
        "calls": [],
    }


def _unexpected_quality(task: dict[str, Any], error: Exception) -> _TaskOutcome:
    row = _not_run_quality(task, f"qualification_executor_exception:{type(error).__name__}")
    row.update(status="infrastructure_failed", attempted=True)
    return _TaskOutcome(row, True)


def _unexpected_polisher(task: dict[str, Any], error: Exception) -> _TaskOutcome:
    row = _not_run_polisher(task, f"qualification_executor_exception:{type(error).__name__}")
    row.update(status="infrastructure_failed", attempted=True)
    return _TaskOutcome(row, True)


def _status(status: str) -> str:
    if status == "inconclusive":
        return "infrastructure_failed"
    return status


def _package_stable(
    suite_path: Path, descriptor_path: Path, manifest: dict[str, Any]
) -> bool:
    try:
        package = load_prose_quality_v4_qualification_suite(
            suite_path, descriptor_path
        )
    except Exception:
        return False
    return bool(
        package["descriptor"]["descriptor_hash"] == manifest["descriptor_hash"]
        and canonical_hash(package["suite"]) == manifest["private_suite_hash"]
    )


def _validate_source_state(source: dict[str, Any]) -> None:
    if set(source) != {"revision", "branch", "clean", "tracked_source_hash"}:
        raise ProseQualityV4QualificationError("prose_quality_v4_source_state_invalid")
    if (
        not isinstance(source["revision"], str)
        or not source["revision"]
        or not isinstance(source["branch"], str)
        or not source["branch"]
        or not isinstance(source["clean"], bool)
        or not isinstance(source["tracked_source_hash"], str)
        or len(source["tracked_source_hash"]) != 64
    ):
        raise ProseQualityV4QualificationError("prose_quality_v4_source_state_invalid")


def _git_source_state(repo_root: Path) -> dict[str, Any]:
    root = repo_root.resolve()
    return {
        "revision": _git(root, "rev-parse", "HEAD").strip(),
        "branch": _git(root, "branch", "--show-current").strip(),
        "clean": not bool(
            _git(root, "status", "--porcelain", "--untracked-files=normal").strip()
        ),
        "tracked_source_hash": canonical_hash(
            _git(root, "ls-files", "-s", "--", ".").splitlines()
        ),
    }


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True, encoding="utf-8"
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ProseQualityV4QualificationError(
            "prose_quality_v4_git_probe_failed"
        ) from error


def _write_json_once(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError as error:
        raise ProseQualityV4QualificationError(
            "prose_quality_v4_qualification_attempt_already_exists"
        ) from error


def _empty_usage() -> dict[str, int]:
    return {key: 0 for key in _USAGE_KEYS}


def _merge_usage(total: dict[str, int], usage: Any) -> None:
    if isinstance(usage, dict):
        for key in _USAGE_KEYS:
            total[key] += int(usage.get(key, 0))


def _progress(callback: Callable[[str], None] | None, value: str) -> None:
    if callback is not None:
        callback(value)


def _local_api_key() -> str:
    for name in ("CASEFILE_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise ProseQualityV4QualificationError(
        "prose_quality_v4_qualification_local_credential_required"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--qualification-suite",
        type=Path,
        default=DEFAULT_PRIVATE_QUALIFICATION_SUITE,
    )
    parser.add_argument(
        "--descriptor", type=Path, default=DEFAULT_QUALIFICATION_DESCRIPTOR
    )
    parser.add_argument("--live-confirmation", default="")
    args = parser.parse_args()
    if args.live_confirmation != LIVE_CONFIRMATION:
        raise ProseQualityV4QualificationError(
            "prose_quality_v4_qualification_explicit_live_confirmation_required"
        )
    report = run_prose_quality_v4_qualification(
        attempt_id=args.attempt_id,
        api_key=_local_api_key(),
        assessment_provider=DeepSeekProseQualityAssessmentProvider(),
        polisher_provider=DeepSeekProsePatchPolisherProvider(),
        judge_provider=DeepSeekProseJudgeProvider(),
        qualification_suite_path=args.qualification_suite,
        descriptor_path=args.descriptor,
        output_dir=args.output_dir,
        progress=print,
    )
    print(json.dumps({"qualified": report["qualified"], "report_hash": report["report_hash"]}))
    return 0 if report["qualified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXECUTOR_VERSION",
    "LIVE_CONFIRMATION",
    "REPORT_VERSION",
    "ProseQualityV4QualificationError",
    "run_prose_quality_v4_qualification",
]
