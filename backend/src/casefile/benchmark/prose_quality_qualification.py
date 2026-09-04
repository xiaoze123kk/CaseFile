"""One-shot N4.5 B3 Quality and Polisher qualification executor."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_judge import (
    FULL_COUNCIL_POLICY,
    PROSE_COUNCIL_NETWORK_RETRIES,
    DeepSeekProseJudgeProvider,
    ProseJudgeProvider,
)
from casefile.agent_runtime.prose_polish_supervisor import (
    ProsePolishSupervisorExecution,
    execute_prose_polish_supervisor,
)
from casefile.agent_runtime.prose_polisher import (
    PROSE_POLISHER_COMPONENT_HASH,
    PROSE_POLISHER_MODEL_ID,
    PROSE_POLISHER_NETWORK_RETRIES,
    PROSE_POLISHER_PROMPT_VERSION,
    DeepSeekProsePolisherProvider,
    ProsePolisherProvider,
)
from casefile.agent_runtime.prose_quality_critic import (
    PROSE_QUALITY_COMPONENT_HASH,
    PROSE_QUALITY_FINDINGS_PROMPT_VERSION,
    PROSE_QUALITY_MODEL_ID,
    PROSE_QUALITY_NETWORK_RETRIES,
    PROSE_QUALITY_PAIRWISE_PROMPT_VERSION,
    DeepSeekProseQualityCriticProvider,
    ProseQualityCriticProvider,
    execute_mirrored_pairwise_quality,
)
from casefile.benchmark.prose_quality_eval import (
    DEFAULT_PRIVATE_QUALIFICATION_SUITE,
    DEFAULT_QUALIFICATION_DESCRIPTOR,
    POLISHER_QUALIFICATION_GATES,
    QUALITY_QUALIFICATION_GATES,
    ROOT,
    canonical_hash,
    load_prose_quality_qualification_suite,
)
from casefile.benchmark.prose_quality_source import quality_source_identity

REPORT_VERSION: Final = "casefile.prose-quality-qualification-report.v1"
EXECUTOR_VERSION: Final = "prose-quality-qualification-executor-v1"
LIVE_CONFIRMATION: Final = "RUN_B3_QUALITY_POLISHER_QUALIFICATION_ONCE"
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_USAGE_KEYS: Final = (
    "requests",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
)


class ProseQualityQualificationError(RuntimeError):
    """The formal B3 attempt violated a preflight or immutable-output boundary."""


@dataclass(frozen=True, slots=True)
class _TaskOutcome:
    row: dict[str, Any]
    infrastructure_failed: bool


def run_prose_quality_qualification(
    *,
    attempt_id: str,
    api_key: str,
    quality_provider: ProseQualityCriticProvider,
    polisher_provider: ProsePolisherProvider,
    judge_provider: ProseJudgeProvider,
    qualification_suite_path: Path = DEFAULT_PRIVATE_QUALIFICATION_SUITE,
    descriptor_path: Path = DEFAULT_QUALIFICATION_DESCRIPTOR,
    output_dir: Path | None = None,
    require_clean_source: bool = True,
    source_probe: Callable[[], dict[str, Any]] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run every frozen private item at most once and emit hash-only evidence."""

    if not _ATTEMPT_ID.fullmatch(attempt_id):
        raise ProseQualityQualificationError("prose_quality_attempt_id_invalid")
    if not api_key:
        raise ProseQualityQualificationError("prose_quality_live_credential_required")
    report_path = output_dir / "report.json" if output_dir is not None else None
    attempt_path = output_dir / "attempt-manifest.json" if output_dir is not None else None
    if (report_path is not None and report_path.exists()) or (
        attempt_path is not None and attempt_path.exists()
    ):
        raise ProseQualityQualificationError("prose_quality_qualification_attempt_already_exists")
    package = load_prose_quality_qualification_suite(qualification_suite_path, descriptor_path)
    if len(package["quality_tasks"]) != 16 or len(package["polisher_tasks"]) != 24:
        raise ProseQualityQualificationError("prose_quality_qualification_task_count_invalid")
    probe = source_probe or (lambda: _git_source_state(ROOT))
    source_before = probe()
    _validate_source_state(source_before)
    if require_clean_source and not source_before["clean"]:
        raise ProseQualityQualificationError("prose_quality_qualification_clean_source_required")
    manifest = _frozen_manifest(
        package,
        attempt_id,
        source_before,
        quality_provider,
        polisher_provider,
        judge_provider,
    )
    attempt = {
        "schema_id": "casefile.prose-quality-qualification-attempt.v1",
        "status": "started",
        **manifest,
    }
    attempt["attempt_manifest_hash"] = canonical_hash(attempt)
    manifest["attempt_manifest_hash"] = attempt["attempt_manifest_hash"]
    if attempt_path is not None:
        _write_json_once(attempt_path, attempt)
    quality_rows: list[dict[str, Any]] = []
    polisher_rows: list[dict[str, Any]] = []
    abort = False
    for index, task in enumerate(package["quality_tasks"], start=1):
        task_id = task["descriptor"]["task_id"]
        if abort:
            quality_rows.append(_not_run_quality(task, "prior_infrastructure_failure"))
            _progress(progress, f"[quality {index}/16] {task_id} status=not_run")
            continue
        _progress(progress, f"[quality {index}/16] {task_id} status=started")
        try:
            outcome = _execute_quality_task(task, quality_provider, api_key)
        except Exception as error:
            outcome = _unexpected_quality(task, error)
        quality_rows.append(outcome.row)
        abort = outcome.infrastructure_failed
        _progress(
            progress,
            f"[quality {index}/16] {task_id} status={outcome.row['status']}",
        )
    for index, task in enumerate(package["polisher_tasks"], start=1):
        task_id = task["descriptor"]["task_id"]
        if abort:
            polisher_rows.append(_not_run_polisher(task, "prior_infrastructure_failure"))
            _progress(progress, f"[polisher {index}/24] {task_id} status=not_run")
            continue
        _progress(progress, f"[polisher {index}/24] {task_id} status=started")
        try:
            outcome = _execute_polisher_task(
                task, quality_provider, polisher_provider, judge_provider, api_key
            )
        except Exception as error:
            outcome = _unexpected_polisher(task, error)
        polisher_rows.append(outcome.row)
        abort = outcome.infrastructure_failed
        _progress(
            progress,
            f"[polisher {index}/24] {task_id} status={outcome.row['status']}",
        )
    package_stable = _package_stable(qualification_suite_path, descriptor_path, manifest)
    source_after = probe()
    _validate_source_state(source_after)
    report = _build_report(
        manifest,
        quality_rows,
        polisher_rows,
        source_after,
        package_stable,
    )
    if report_path is not None:
        _write_json_once(report_path, report)
    return report


def _execute_quality_task(
    task: dict[str, Any], provider: ProseQualityCriticProvider, api_key: str
) -> _TaskOutcome:
    asset = task["asset"]
    execution = execute_mirrored_pairwise_quality(
        provider,
        checklist=asset["checklist"],
        original_render=asset["render_a"],
        polished_render=asset["render_b"],
        profile=asset["profile"],
        preservation_consensus=asset["semantic_consensus_b"],
        model_id=PROSE_QUALITY_MODEL_ID,
        api_key=api_key,
    )
    calls = [_completed_audit("quality_pairwise", call) for call in execution.calls]
    if execution.failed_call is not None:
        calls.append(_failed_audit("quality_pairwise", execution.failed_call))
    status = _status(execution.status)
    gold = asset["gold"]
    overall_correct = False
    mirrored_consistent = False
    dimension_correct = 0
    predicted: str | None = None
    if execution.status == "completed":
        first, second = execution.reports
        predicted = first["overall_preference"]
        overall_correct = predicted == gold["overall_preference"]
        mirrored_consistent = predicted == _swap(second["overall_preference"])
        dimension_correct = sum(
            first_item == gold_item
            for first_item, gold_item in zip(
                first["dimension_preferences"],
                gold["dimension_preferences"],
                strict=True,
            )
        )
    row = {
        "task_id": task["descriptor"]["task_id"],
        "focus": task["descriptor"]["focus"],
        "pair_fingerprint": task["descriptor"]["pair_fingerprint"],
        "status": status,
        "error_code": execution.error_code,
        "attempted": True,
        "render_hashes": [canonical_hash(asset["render_a"]), canonical_hash(asset["render_b"])],
        "gold_overall": gold["overall_preference"],
        "predicted_overall": predicted,
        "overall_correct": overall_correct,
        "mirrored_consistent": mirrored_consistent,
        "dimension_correct": dimension_correct,
        "calls": calls,
    }
    return _TaskOutcome(row, status == "infrastructure_failed")


def _execute_polisher_task(
    task: dict[str, Any],
    quality_provider: ProseQualityCriticProvider,
    polisher_provider: ProsePolisherProvider,
    judge_provider: ProseJudgeProvider,
    api_key: str,
) -> _TaskOutcome:
    asset = task["asset"]
    execution = execute_prose_polish_supervisor(
        quality_provider,
        polisher_provider,
        judge_provider,
        checklist=asset["checklist"],
        profile=asset["profile"],
        original_render=asset["original_render"],
        semantic_consensus=asset["semantic_consensus"],
        quality_model_id=PROSE_QUALITY_MODEL_ID,
        generation_model_id=PROSE_POLISHER_MODEL_ID,
        api_key=api_key,
    )
    status = _status(execution.status)
    calls = _supervisor_audits(execution)
    preservation_passed = bool(
        execution.preservation is not None
        and execution.preservation.status == "completed"
        and execution.preservation.consensus is not None
        and execution.preservation.consensus["scene_verdict"] == "pass"
    )
    polished_accepted = execution.status == "finalized_polished"
    finalized = execution.status in {"finalized_original", "finalized_polished"}
    original_text = [block["text"] for block in asset["original_render"]["blocks"]]
    accepted_text = (
        [block["text"] for block in execution.accepted_render["blocks"]]
        if execution.accepted_render is not None
        else []
    )
    rejected = finalized and not polished_accepted
    exact_rollback = rejected and accepted_text == original_text
    preservation_verdicts = (
        {
            item["check_id"]: item["final_verdict"]
            for item in execution.preservation.consensus["checks"]
        }
        if execution.preservation is not None and execution.preservation.consensus is not None
        else {}
    )
    critical_regressions = (
        [
            check_id
            for check_id in asset["critical_check_ids"]
            if preservation_verdicts.get(check_id) != "pass"
        ]
        if polished_accepted
        else []
    )
    row = {
        "task_id": task["descriptor"]["task_id"],
        "focus": task["descriptor"]["focus"],
        "input_fingerprint": task["descriptor"]["input_fingerprint"],
        "status": status,
        "error_code": execution.error_code,
        "attempted": True,
        "original_render_hash": canonical_hash(asset["original_render"]),
        "polished_render_hash": (
            canonical_hash(execution.polish.render)
            if execution.polish is not None and execution.polish.render is not None
            else None
        ),
        "preservation_consensus_hash": (
            canonical_hash(execution.preservation.consensus)
            if execution.preservation is not None and execution.preservation.consensus is not None
            else None
        ),
        "accepted_render_hash": (
            canonical_hash(execution.accepted_render)
            if execution.accepted_render is not None
            else None
        ),
        "selection_reason": execution.selection_reason,
        "preservation_passed": preservation_passed,
        "quality_non_loss": finalized,
        "polished_accepted": polished_accepted,
        "rejected_polish": rejected,
        "exact_original_rollback": exact_rollback,
        "critical_semantic_regression_check_ids": critical_regressions,
        "calls": calls,
    }
    return _TaskOutcome(row, status == "infrastructure_failed")


def _build_report(
    manifest: dict[str, Any],
    quality_rows: list[dict[str, Any]],
    polisher_rows: list[dict[str, Any]],
    source_after: dict[str, Any],
    package_stable: bool,
) -> dict[str, Any]:
    quality_attempted = sum(row["attempted"] for row in quality_rows)
    polisher_attempted = sum(row["attempted"] for row in polisher_rows)
    overall = sum(row["overall_correct"] for row in quality_rows)
    mirrored = sum(row["mirrored_consistent"] for row in quality_rows)
    preservation = sum(row["preservation_passed"] for row in polisher_rows)
    non_loss = sum(row["quality_non_loss"] for row in polisher_rows)
    accepted = sum(row["polished_accepted"] for row in polisher_rows)
    rejected = sum(row["rejected_polish"] for row in polisher_rows)
    exact_rollback = sum(row["exact_original_rollback"] for row in polisher_rows)
    critical = sum(len(row["critical_semantic_regression_check_ids"]) for row in polisher_rows)
    all_rows = [*quality_rows, *polisher_rows]
    protocol = sum(row["status"] == "protocol_failed" for row in all_rows)
    infrastructure = sum(row["status"] == "infrastructure_failed" for row in all_rows)
    source_stable = source_after == manifest["source_before"]
    rollback_rate = 1.0 if rejected == 0 else exact_rollback / rejected
    gates = {
        "complete_quality_16": len(quality_rows) == quality_attempted == 16,
        "complete_polisher_24": len(polisher_rows) == polisher_attempted == 24,
        "quality_overall_accuracy": overall >= QUALITY_QUALIFICATION_GATES["overall_accuracy_min"],
        "quality_mirrored_consistency": mirrored
        >= QUALITY_QUALIFICATION_GATES["mirrored_consistency_min"],
        "semantic_invalid_zero": True,
        "preservation": preservation >= POLISHER_QUALIFICATION_GATES["preservation_pass_min"],
        "quality_non_loss": non_loss >= POLISHER_QUALIFICATION_GATES["quality_non_loss_min"],
        "polished_accepted": accepted >= POLISHER_QUALIFICATION_GATES["polished_accepted_min"],
        "critical_semantic_regression": critical
        <= POLISHER_QUALIFICATION_GATES["critical_semantic_regression_max"],
        "exact_rollback": rollback_rate
        >= POLISHER_QUALIFICATION_GATES["rejected_exact_rollback_rate_min"],
        "protocol_failure": protocol == 0,
        "infrastructure_failure": infrastructure == 0,
        "source_clean": bool(manifest["source_before"]["clean"]),
        "source_stable": source_stable,
        "qualification_package_stable": package_stable,
        "exact_provider_adapters": (
            manifest["quality_provider_adapter"] == "DeepSeekProseQualityCriticProvider"
            and manifest["polisher_provider_adapter"] == "DeepSeekProsePolisherProvider"
            and manifest["judge_provider_adapter"] == "DeepSeekProseJudgeProvider"
        ),
    }
    qualified = all(gates.values())
    outcome = (
        "passed"
        if qualified
        else "inconclusive_infrastructure"
        if infrastructure or quality_attempted < 16 or polisher_attempted < 24
        else "failed"
    )
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
        "qualification_outcome": outcome,
        "qualification_eligible": True,
        "development_baseline": False,
        "qualified": qualified,
        "quality_holdout_count": 16,
        "quality_attempted_count": quality_attempted,
        "quality_overall_accuracy": {"passed": overall, "total": 16},
        "quality_mirrored_consistency": {"passed": mirrored, "total": 16},
        "polisher_task_count": 24,
        "polisher_attempted_count": polisher_attempted,
        "preservation": {"passed": preservation, "total": 24},
        "quality_non_loss": {"passed": non_loss, "total": 24},
        "polished_accepted": {"passed": accepted, "total": 24},
        "rejected_exact_rollback": {"passed": exact_rollback, "total": rejected},
        "critical_semantic_regression_count": critical,
        "failure_counts": {
            "protocol": protocol,
            "infrastructure": infrastructure,
            "not_run": 40 - quality_attempted - polisher_attempted,
        },
        "logical_model_call_count": len(calls),
        "physical_transport_attempt_count": sum(len(call["transport_attempts"]) for call in calls),
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
    quality_provider: ProseQualityCriticProvider,
    polisher_provider: ProsePolisherProvider,
    judge_provider: ProseJudgeProvider,
) -> dict[str, Any]:
    prompt_ids = {
        "quality_findings": ("prose_quality_critic", PROSE_QUALITY_FINDINGS_PROMPT_VERSION),
        "quality_pairwise": ("prose_quality_pairwise", PROSE_QUALITY_PAIRWISE_PROMPT_VERSION),
        "polisher": ("prose_polisher", PROSE_POLISHER_PROMPT_VERSION),
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
        "attempt_id": attempt_id,
        "source_before": source,
        "descriptor_hash": descriptor["descriptor_hash"],
        "private_suite_hash": canonical_hash(package["suite"]),
        "review_policy": descriptor["review_policy"],
        "review_attestation_hash": descriptor["review_attestation_hash"],
        "quality_model_id": PROSE_QUALITY_MODEL_ID,
        "generation_model_id": PROSE_POLISHER_MODEL_ID,
        "quality_provider_adapter": type(quality_provider).__name__,
        "polisher_provider_adapter": type(polisher_provider).__name__,
        "judge_provider_adapter": type(judge_provider).__name__,
        "prompts": prompts,
        "quality_component_hash": PROSE_QUALITY_COMPONENT_HASH,
        "polisher_component_hash": PROSE_POLISHER_COMPONENT_HASH,
        "preservation_policy_id": FULL_COUNCIL_POLICY.policy_id,
        "preservation_policy_hash": FULL_COUNCIL_POLICY.policy_hash,
        "quality_network_retries": PROSE_QUALITY_NETWORK_RETRIES,
        "polisher_network_retries": PROSE_POLISHER_NETWORK_RETRIES,
        "judge_network_retries": PROSE_COUNCIL_NETWORK_RETRIES,
        "quality_gate_thresholds": QUALITY_QUALIFICATION_GATES,
        "polisher_gate_thresholds": POLISHER_QUALIFICATION_GATES,
    }
    frozen["attempt_fingerprint"] = canonical_hash(frozen)
    return frozen


def _supervisor_audits(execution: ProsePolishSupervisorExecution) -> list[dict[str, Any]]:
    audits: list[dict[str, Any]] = []
    if execution.findings is not None:
        if execution.findings.call is not None:
            audits.append(_completed_audit("quality_findings", execution.findings.call))
        if execution.findings.failed_call is not None:
            audits.append(_failed_audit("quality_findings", execution.findings.failed_call))
    if execution.polish is not None:
        if execution.polish.call is not None:
            audits.append(_completed_audit("polisher", execution.polish.call))
        if execution.polish.failed_call is not None:
            audits.append(_failed_audit("polisher", execution.polish.failed_call))
    if execution.preservation is not None:
        audits.extend(
            _completed_audit("preservation_judge", call) for call in execution.preservation.calls
        )
        if execution.preservation.failed_call is not None:
            audits.append(_failed_audit("preservation_judge", execution.preservation.failed_call))
    if execution.pairwise is not None:
        audits.extend(
            _completed_audit("quality_pairwise", call) for call in execution.pairwise.calls
        )
        if execution.pairwise.failed_call is not None:
            audits.append(_failed_audit("quality_pairwise", execution.pairwise.failed_call))
    return audits


def _completed_audit(component: str, call: Any) -> dict[str, Any]:
    candidate_chars, candidate_blocks = (
        _candidate_size(call.candidate) if component == "polisher" else (None, None)
    )
    return {
        "component": component,
        "status": "completed",
        "error_code": None,
        "request_fingerprint": call.request_fingerprint,
        "prompt_hash": call.prompt_hash,
        "input_hash": call.input_hash,
        "component_input_hash": getattr(call, "component_input_hash", None),
        "output_hash": call.output_hash,
        "candidate_character_count": candidate_chars,
        "candidate_block_count": candidate_blocks,
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
        "candidate_character_count": None,
        "candidate_block_count": None,
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


def _candidate_size(candidate: Any) -> tuple[int | None, int | None]:
    if not isinstance(candidate, dict) or not isinstance(candidate.get("blocks"), list):
        return None, None
    blocks = candidate["blocks"]
    if any(not isinstance(item, dict) or not isinstance(item.get("text"), str) for item in blocks):
        return None, None
    return sum(len(item["text"]) for item in blocks), len(blocks)


def _not_run_quality(task: dict[str, Any], reason: str) -> dict[str, Any]:
    asset = task["asset"]
    return {
        "task_id": task["descriptor"]["task_id"],
        "focus": task["descriptor"]["focus"],
        "pair_fingerprint": task["descriptor"]["pair_fingerprint"],
        "status": "not_run",
        "error_code": reason,
        "attempted": False,
        "render_hashes": [canonical_hash(asset["render_a"]), canonical_hash(asset["render_b"])],
        "gold_overall": asset["gold"]["overall_preference"],
        "predicted_overall": None,
        "overall_correct": False,
        "mirrored_consistent": False,
        "dimension_correct": 0,
        "calls": [],
    }


def _not_run_polisher(task: dict[str, Any], reason: str) -> dict[str, Any]:
    asset = task["asset"]
    return {
        "task_id": task["descriptor"]["task_id"],
        "focus": task["descriptor"]["focus"],
        "input_fingerprint": task["descriptor"]["input_fingerprint"],
        "status": "not_run",
        "error_code": reason,
        "attempted": False,
        "original_render_hash": canonical_hash(asset["original_render"]),
        "polished_render_hash": None,
        "preservation_consensus_hash": None,
        "accepted_render_hash": None,
        "selection_reason": None,
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
    if status == "protocol_failed":
        return "protocol_failed"
    return status


def _swap(value: str) -> str:
    return {"a": "b", "b": "a", "tie": "tie"}[value]


def _package_stable(suite_path: Path, descriptor_path: Path, manifest: dict[str, Any]) -> bool:
    try:
        package = load_prose_quality_qualification_suite(suite_path, descriptor_path)
    except Exception:
        return False
    return bool(
        package["descriptor"]["descriptor_hash"] == manifest["descriptor_hash"]
        and canonical_hash(package["suite"]) == manifest["private_suite_hash"]
    )


def _validate_source_state(source: dict[str, Any]) -> None:
    if set(source) != {"revision", "branch", "clean", "tracked_source_hash"}:
        raise ProseQualityQualificationError("prose_quality_source_state_invalid")
    if (
        not isinstance(source["revision"], str)
        or not source["revision"]
        or not isinstance(source["branch"], str)
        or not source["branch"]
        or not isinstance(source["clean"], bool)
        or not isinstance(source["tracked_source_hash"], str)
        or len(source["tracked_source_hash"]) != 64
    ):
        raise ProseQualityQualificationError("prose_quality_source_state_invalid")


def _git_source_state(repo_root: Path) -> dict[str, Any]:
    try:
        return quality_source_identity(repo_root.resolve())
    except (OSError, subprocess.CalledProcessError) as error:
        raise ProseQualityQualificationError("prose_quality_git_probe_failed") from error


def _write_json_once(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError as error:
        raise ProseQualityQualificationError(
            "prose_quality_qualification_attempt_already_exists"
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
    raise ProseQualityQualificationError("prose_quality_qualification_local_credential_required")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--qualification-suite", type=Path, default=DEFAULT_PRIVATE_QUALIFICATION_SUITE
    )
    parser.add_argument("--descriptor", type=Path, default=DEFAULT_QUALIFICATION_DESCRIPTOR)
    parser.add_argument("--live-confirmation", default="")
    args = parser.parse_args()
    if args.live_confirmation != LIVE_CONFIRMATION:
        raise ProseQualityQualificationError(
            "prose_quality_qualification_explicit_live_confirmation_required"
        )
    report = run_prose_quality_qualification(
        attempt_id=args.attempt_id,
        api_key=_local_api_key(),
        quality_provider=DeepSeekProseQualityCriticProvider(),
        polisher_provider=DeepSeekProsePolisherProvider(),
        judge_provider=DeepSeekProseJudgeProvider(),
        qualification_suite_path=args.qualification_suite,
        descriptor_path=args.descriptor,
        output_dir=args.output_dir,
        require_clean_source=True,
        progress=lambda value: print(value, flush=True),
    )
    print(
        json.dumps(
            {
                "qualification_outcome": report["qualification_outcome"],
                "qualified": report["qualified"],
                "report_hash": report["report_hash"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["qualified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXECUTOR_VERSION",
    "LIVE_CONFIRMATION",
    "ProseQualityQualificationError",
    "REPORT_VERSION",
    "run_prose_quality_qualification",
]
