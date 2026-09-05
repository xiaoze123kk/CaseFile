"""One-shot N4.5 B2 Rewrite qualification execution and hash-only evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_judge import (
    FIDELITY_ONLY_POLICY,
    PROSE_COUNCIL_MODEL_ID,
    PROSE_COUNCIL_NETWORK_RETRIES,
    DeepSeekProseJudgeProvider,
    ProseCouncilExecution,
    ProseJudgeFailedCall,
    ProseJudgeProvider,
    ProseJudgeProviderResult,
    execute_semantic_council,
)
from casefile.agent_runtime.prose_rewriter import (
    PROSE_REWRITER_COMPONENT_HASH,
    PROSE_REWRITER_MAX_CALLS_PER_SCENE,
    PROSE_REWRITER_MODEL_ID,
    PROSE_REWRITER_NETWORK_RETRIES,
    PROSE_REWRITER_PROMPT_VERSION,
    DeepSeekProseRewriterProvider,
    ProseRewriterExecution,
    ProseRewriterFailedCall,
    ProseRewriterProvider,
    ProseRewriterProviderResult,
    execute_prose_rewriter,
)
from casefile.benchmark.prose_rewrite_eval import (
    DEFAULT_PRIVATE_QUALIFICATION_SUITE,
    DEFAULT_QUALIFICATION_DESCRIPTOR,
    GATE_THRESHOLDS,
    ROOT,
    canonical_hash,
    load_prose_rewrite_qualification_suite,
)

REPORT_VERSION: Final = "casefile.prose-rewrite-qualification-report.v1"
EXECUTOR_VERSION: Final = "prose-rewrite-qualification-executor-v1"
FIDELITY_JUDGE_PROMPT_VERSION: Final = "prose-fidelity-judge-v6"
LIVE_CONFIRMATION: Final = "RUN_B2_REWRITE_QUALIFICATION_ONCE"
SCENE_CALL_BUDGET: Final = 4
_ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_USAGE_KEYS: Final = (
    "requests",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
)


class ProseRewriteQualificationError(RuntimeError):
    """The formal B2 attempt failed a preflight or immutable-output boundary."""


@dataclass(frozen=True, slots=True)
class _TaskOutcome:
    row: dict[str, Any]
    infrastructure_failed: bool


def run_prose_rewrite_qualification(
    *,
    attempt_id: str,
    api_key: str,
    rewriter_provider: ProseRewriterProvider,
    judge_provider: ProseJudgeProvider,
    qualification_suite_path: Path = DEFAULT_PRIVATE_QUALIFICATION_SUITE,
    descriptor_path: Path = DEFAULT_QUALIFICATION_DESCRIPTOR,
    output_dir: Path | None = None,
    require_clean_source: bool = True,
    source_probe: Callable[[], dict[str, Any]] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run each frozen private task at most once and emit no prose in evidence."""

    if not _ATTEMPT_ID.fullmatch(attempt_id):
        raise ProseRewriteQualificationError("prose_rewrite_attempt_id_invalid")
    if not api_key:
        raise ProseRewriteQualificationError("prose_rewrite_live_credential_required")
    report_path = output_dir / "report.json" if output_dir is not None else None
    attempt_path = (
        output_dir / "attempt-manifest.json" if output_dir is not None else None
    )
    if (report_path is not None and report_path.exists()) or (
        attempt_path is not None and attempt_path.exists()
    ):
        raise ProseRewriteQualificationError(
            "prose_rewrite_qualification_attempt_already_exists"
        )
    package = load_prose_rewrite_qualification_suite(
        qualification_suite_path, descriptor_path
    )
    if len(package.get("tasks", ())) != 24:
        raise ProseRewriteQualificationError(
            "prose_rewrite_qualification_task_count_invalid"
        )
    probe = source_probe or (lambda: _git_source_state(ROOT))
    source_before = probe()
    _validate_source_state(source_before)
    if require_clean_source and not source_before["clean"]:
        raise ProseRewriteQualificationError(
            "prose_rewrite_qualification_clean_source_required"
        )

    manifest = _frozen_manifest(
        package,
        attempt_id,
        source_before,
        rewriter_provider=rewriter_provider,
        judge_provider=judge_provider,
    )
    attempt_manifest = {
        "schema_id": "casefile.prose-rewrite-qualification-attempt.v1",
        "status": "started",
        **manifest,
    }
    attempt_manifest["attempt_manifest_hash"] = canonical_hash(attempt_manifest)
    manifest["attempt_manifest_hash"] = attempt_manifest["attempt_manifest_hash"]
    if attempt_path is not None:
        _write_json_once(attempt_path, attempt_manifest)
    rows: list[dict[str, Any]] = []
    abort_remaining = False
    for index, task in enumerate(package["tasks"], start=1):
        task_id = task["descriptor"]["task_id"]
        if abort_remaining:
            rows.append(_not_run_row(task, "prior_infrastructure_failure"))
            if progress is not None:
                progress(f"[{index}/24] {task_id} status=not_run")
            continue
        if progress is not None:
            progress(f"[{index}/24] {task_id} status=started")
        try:
            outcome = _execute_task(
                task,
                rewriter_provider=rewriter_provider,
                judge_provider=judge_provider,
                api_key=api_key,
            )
        except Exception as error:
            outcome = _unexpected_failure(task, error)
        rows.append(outcome.row)
        abort_remaining = outcome.infrastructure_failed
        if progress is not None:
            progress(f"[{index}/24] {task_id} status={outcome.row['status']}")

    package_stable = _qualification_package_stable(
        qualification_suite_path,
        descriptor_path,
        manifest,
    )
    source_after = probe()
    _validate_source_state(source_after)
    report = _build_report(
        package=package,
        manifest=manifest,
        rows=rows,
        source_after=source_after,
        package_stable=package_stable,
    )
    if report_path is not None:
        _write_report_once(report_path, report)
    return report


def _execute_task(
    task: dict[str, Any],
    *,
    rewriter_provider: ProseRewriterProvider,
    judge_provider: ProseJudgeProvider,
    api_key: str,
) -> _TaskOutcome:
    asset = task["asset"]
    current_render = asset["initial_render"]
    current_consensus = asset["initial_consensus"]
    current_reports: tuple[dict[str, Any], ...] = (
        asset["initial_judge_report"],
    )
    initial_passed = tuple(asset["initial_passed_check_ids"])
    original_issues = tuple(asset["original_issue_check_ids"])
    critical = set(asset["critical_check_ids"])
    audits: list[dict[str, Any]] = []
    round_hashes: list[dict[str, Any]] = []
    new_critical: set[str] = set()
    remaining = SCENE_CALL_BUDGET
    final_consensus: dict[str, Any] | None = None
    final_render: dict[str, Any] | None = None
    status = "semantic_rejected"
    error_code: str | None = None
    rescue_round: int | None = None

    for rewrite_round in range(1, PROSE_REWRITER_MAX_CALLS_PER_SCENE + 1):
        rewrite = execute_prose_rewriter(
            rewriter_provider,
            scene_plan=task["scene_plan"],
            narrative_ir=task["narrative_ir"],
            profile=asset["profile"],
            checklist=task["checklist"],
            previous_scene_render=asset["previous_scene_render"],
            current_render=current_render,
            consensus=current_consensus,
            judge_reports=current_reports,
            model_id=PROSE_REWRITER_MODEL_ID,
            api_key=api_key,
            remaining_scene_call_budget=remaining,
        )
        remaining -= _rewriter_logical_call_count(rewrite)
        audits.append(_rewriter_audit(rewrite, rewrite_round))
        if rewrite.status != "completed" or rewrite.render is None:
            status = _qualification_status(rewrite.status)
            error_code = rewrite.error_code
            break

        final_render = rewrite.render
        council = execute_semantic_council(
            judge_provider,
            checklist=task["checklist"],
            render=final_render,
            profile=asset["profile"],
            policy=FIDELITY_ONLY_POLICY,
            model_id=PROSE_COUNCIL_MODEL_ID,
            api_key=api_key,
        )
        remaining -= _council_logical_call_count(council)
        audits.extend(_council_audits(council, rewrite_round))
        round_hashes.append(
            {
                "rewrite_round": rewrite_round,
                "render_hash": canonical_hash(final_render),
                "consensus_hash": (
                    canonical_hash(council.consensus)
                    if council.consensus is not None
                    else None
                ),
                "judge_report_hashes": [
                    canonical_hash(report) for report in council.judge_reports
                ],
                "unresolved_check_ids": (
                    sorted(
                        check_id
                        for check_id, verdict in _verdicts(council.consensus).items()
                        if verdict != "pass"
                    )
                    if council.consensus is not None
                    else []
                ),
            }
        )
        if council.status != "completed" or council.consensus is None:
            status = _qualification_status(council.status)
            error_code = council.error_code
            break

        final_consensus = council.consensus
        verdicts = _verdicts(final_consensus)
        new_critical.update(
            check_id
            for check_id in initial_passed
            if check_id in critical and verdicts.get(check_id) != "pass"
        )
        if final_consensus["scene_verdict"] == "pass":
            status = "semantic_accepted"
            rescue_round = rewrite_round
            break
        current_render = final_render
        current_consensus = final_consensus
        current_reports = council.judge_reports

    final_verdicts = _verdicts(final_consensus) if final_consensus else {}
    original_removed = bool(final_verdicts) and all(
        final_verdicts.get(check_id) == "pass" for check_id in original_issues
    )
    preservation = bool(final_verdicts) and all(
        final_verdicts.get(check_id) == "pass" for check_id in initial_passed
    )
    unresolved_original = [
        check_id
        for check_id in original_issues
        if final_verdicts.get(check_id) != "pass"
    ]
    new_issues = [
        check_id
        for check_id in initial_passed
        if final_verdicts and final_verdicts.get(check_id) != "pass"
    ]
    row = {
        "task_id": task["descriptor"]["task_id"],
        "defect_family": task["descriptor"]["defect_family"],
        "variant": task["descriptor"]["variant"],
        "input_fingerprint": task["descriptor"]["input_fingerprint"],
        "status": status,
        "error_code": error_code,
        "attempted": True,
        "rewrite_count": sum(audit["component"] == "rewriter" for audit in audits),
        "rescue_round": rescue_round,
        "original_issue_removed": original_removed,
        "unresolved_original_issue_check_ids": unresolved_original,
        "preservation_passed": preservation,
        "new_issue_check_ids": new_issues,
        "new_critical_issue_check_ids": sorted(new_critical),
        "initial_render_hash": canonical_hash(asset["initial_render"]),
        "initial_consensus_hash": canonical_hash(asset["initial_consensus"]),
        "final_render_hash": (
            canonical_hash(final_render) if final_render is not None else None
        ),
        "final_consensus_hash": (
            canonical_hash(final_consensus) if final_consensus is not None else None
        ),
        "rounds": round_hashes,
        "calls": audits,
    }
    return _TaskOutcome(row=row, infrastructure_failed=status == "infrastructure_failed")


def _not_run_row(task: dict[str, Any], reason: str) -> dict[str, Any]:
    asset = task["asset"]
    return {
        "task_id": task["descriptor"]["task_id"],
        "defect_family": task["descriptor"]["defect_family"],
        "variant": task["descriptor"]["variant"],
        "input_fingerprint": task["descriptor"]["input_fingerprint"],
        "status": "not_run",
        "error_code": reason,
        "attempted": False,
        "rewrite_count": 0,
        "rescue_round": None,
        "original_issue_removed": False,
        "unresolved_original_issue_check_ids": [],
        "preservation_passed": False,
        "new_issue_check_ids": [],
        "new_critical_issue_check_ids": [],
        "initial_render_hash": canonical_hash(asset["initial_render"]),
        "initial_consensus_hash": canonical_hash(asset["initial_consensus"]),
        "final_render_hash": None,
        "final_consensus_hash": None,
        "rounds": [],
        "calls": [],
    }


def _unexpected_failure(task: dict[str, Any], error: Exception) -> _TaskOutcome:
    row = _not_run_row(task, f"qualification_executor_exception:{type(error).__name__}")
    row["status"] = "infrastructure_failed"
    row["attempted"] = True
    return _TaskOutcome(row=row, infrastructure_failed=True)


def _build_report(
    *,
    package: dict[str, Any],
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    source_after: dict[str, Any],
    package_stable: bool,
) -> dict[str, Any]:
    final_rescue = sum(row["status"] == "semantic_accepted" for row in rows)
    round_one = sum(row["rescue_round"] == 1 for row in rows)
    round_two = sum(row["rescue_round"] == 2 for row in rows)
    preservation = sum(row["preservation_passed"] for row in rows)
    new_critical = sum(len(row["new_critical_issue_check_ids"]) for row in rows)
    protocol_failures = sum(row["status"] == "protocol_failed" for row in rows)
    infrastructure_failures = sum(
        row["status"] == "infrastructure_failed" for row in rows
    )
    semantic_failures = sum(row["status"] == "semantic_rejected" for row in rows)
    attempted = sum(row["attempted"] for row in rows)
    extra_rewrites = sum(max(0, int(row["rewrite_count"]) - 2) for row in rows)
    calls = [call for row in rows for call in row["calls"]]
    usage = _empty_usage()
    for call in calls:
        _merge_usage(usage, call.get("usage"))
    source_stable = source_after == manifest["source_before"]
    gates = {
        "complete_24": len(rows) == 24 and attempted == 24,
        "final_rescue": final_rescue >= GATE_THRESHOLDS["final_rescue_min"],
        "preservation": preservation
        >= GATE_THRESHOLDS["preservation_task_min"],
        "new_critical_issue": new_critical
        <= GATE_THRESHOLDS["new_critical_issue_max"],
        "extra_rewrite_call": extra_rewrites
        <= GATE_THRESHOLDS["extra_rewrite_call_max"],
        "protocol_failure": protocol_failures
        <= GATE_THRESHOLDS["protocol_failure_max"],
        "infrastructure_failure": infrastructure_failures
        <= GATE_THRESHOLDS["infrastructure_failure_max"],
        "source_clean": bool(manifest["source_before"]["clean"]),
        "source_stable": source_stable,
        "qualification_package_stable": package_stable,
        "exact_provider_adapters": (
            manifest["rewriter_provider_adapter"]
            == "DeepSeekProseRewriterProvider"
            and manifest["judge_provider_adapter"]
            == "DeepSeekProseJudgeProvider"
        ),
    }
    qualified = all(gates.values())
    if qualified:
        outcome = "passed"
    elif infrastructure_failures or attempted < 24:
        outcome = "inconclusive_infrastructure"
    else:
        outcome = "failed"
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
        "task_count": 24,
        "attempted_task_count": attempted,
        "round_one_rescue": {"passed": round_one, "total": 24},
        "round_two_incremental_rescue": {"passed": round_two, "total": 24},
        "final_rescue": {"passed": final_rescue, "total": 24},
        "preservation_tasks": {"passed": preservation, "total": 24},
        "new_critical_issue_count": new_critical,
        "extra_rewrite_call_count": extra_rewrites,
        "failure_counts": {
            "semantic": semantic_failures,
            "protocol": protocol_failures,
            "infrastructure": infrastructure_failures,
            "not_run": 24 - attempted,
        },
        "logical_model_call_count": len(calls),
        "physical_transport_attempt_count": sum(
            len(call["transport_attempts"]) for call in calls
        ),
        "usage": usage,
        "latency_ms": sum(int(call.get("latency_ms", 0)) for call in calls),
        "gates": gates,
        "rows": rows,
    }
    report["report_hash"] = canonical_hash(report)
    return report


def _qualification_package_stable(
    suite_path: Path,
    descriptor_path: Path,
    manifest: dict[str, Any],
) -> bool:
    try:
        package = load_prose_rewrite_qualification_suite(suite_path, descriptor_path)
    except Exception:
        return False
    return bool(
        package["descriptor"]["descriptor_hash"] == manifest["descriptor_hash"]
        and canonical_hash(package["suite"]) == manifest["private_suite_hash"]
    )


def _frozen_manifest(
    package: dict[str, Any],
    attempt_id: str,
    source: dict[str, Any],
    *,
    rewriter_provider: ProseRewriterProvider,
    judge_provider: ProseJudgeProvider,
) -> dict[str, Any]:
    descriptor = package["descriptor"]
    suite = package["suite"]
    rewriter_prompt = load_prompt("prose_rewriter", PROSE_REWRITER_PROMPT_VERSION)
    judge_prompt = load_prompt(
        "prose_fidelity_judge", FIDELITY_JUDGE_PROMPT_VERSION
    )
    if load_prompt("prose_fidelity_judge").version != FIDELITY_JUDGE_PROMPT_VERSION:
        raise ProseRewriteQualificationError(
            "prose_rewrite_qualification_judge_prompt_not_frozen"
        )
    frozen = {
        "executor_version": EXECUTOR_VERSION,
        "attempt_id": attempt_id,
        "source_before": source,
        "descriptor_hash": descriptor["descriptor_hash"],
        "private_suite_hash": canonical_hash(suite),
        "review_policy": descriptor["review_policy"],
        "review_attestation_hash": descriptor["review_attestation_hash"],
        "model_id": PROSE_REWRITER_MODEL_ID,
        "rewriter_provider_adapter": type(rewriter_provider).__name__,
        "judge_provider_adapter": type(judge_provider).__name__,
        "rewriter_prompt_version": rewriter_prompt.version,
        "rewriter_prompt_hash": rewriter_prompt.system_prompt_sha256,
        "judge_prompt_version": judge_prompt.version,
        "judge_prompt_hash": judge_prompt.system_prompt_sha256,
        "rewriter_component_hash": PROSE_REWRITER_COMPONENT_HASH,
        "council_policy_id": FIDELITY_ONLY_POLICY.policy_id,
        "council_policy_hash": FIDELITY_ONLY_POLICY.policy_hash,
        "scene_call_budget": SCENE_CALL_BUDGET,
        "max_rewrites_per_scene": PROSE_REWRITER_MAX_CALLS_PER_SCENE,
        "rewriter_network_retries": PROSE_REWRITER_NETWORK_RETRIES,
        "judge_network_retries": PROSE_COUNCIL_NETWORK_RETRIES,
        "gate_thresholds": GATE_THRESHOLDS,
    }
    frozen["attempt_fingerprint"] = canonical_hash(frozen)
    return frozen


def _rewriter_audit(
    execution: ProseRewriterExecution, rewrite_round: int
) -> dict[str, Any]:
    if execution.call is not None:
        return _completed_call_audit("rewriter", execution.call, rewrite_round)
    if execution.failed_call is not None:
        return _failed_call_audit("rewriter", execution.failed_call, rewrite_round)
    return {
        "component": "rewriter",
        "rewrite_round": rewrite_round,
        "status": execution.status,
        "error_code": execution.error_code,
        "request_fingerprint": None,
        "prompt_hash": None,
        "input_hash": None,
        "output_hash": None,
        "model_id": PROSE_REWRITER_MODEL_ID,
        "prompt_version": PROSE_REWRITER_PROMPT_VERSION,
        "usage": _empty_usage(),
        "latency_ms": 0,
        "transport_attempts": [],
    }


def _council_audits(
    execution: ProseCouncilExecution, rewrite_round: int
) -> list[dict[str, Any]]:
    audits = [
        _completed_call_audit("judge", call, rewrite_round)
        for call in execution.calls
    ]
    if execution.failed_call is not None:
        audits.append(
            _failed_call_audit("judge", execution.failed_call, rewrite_round)
        )
    if not audits:
        audits.append(
            {
                "component": "judge",
                "rewrite_round": rewrite_round,
                "status": execution.status,
                "error_code": execution.error_code,
                "request_fingerprint": None,
                "prompt_hash": None,
                "input_hash": None,
                "output_hash": None,
                "model_id": PROSE_COUNCIL_MODEL_ID,
                "prompt_version": FIDELITY_JUDGE_PROMPT_VERSION,
                "usage": _empty_usage(),
                "latency_ms": 0,
                "transport_attempts": [],
            }
        )
    return audits


def _completed_call_audit(
    component: Literal["rewriter", "judge"],
    call: ProseRewriterProviderResult | ProseJudgeProviderResult,
    rewrite_round: int,
) -> dict[str, Any]:
    candidate_character_count, candidate_block_count = _candidate_size(
        call.candidate if component == "rewriter" else None
    )
    return {
        "component": component,
        "rewrite_round": rewrite_round,
        "status": "completed",
        "error_code": None,
        "request_fingerprint": call.request_fingerprint,
        "prompt_hash": call.prompt_hash,
        "input_hash": call.input_hash,
        "component_input_hash": getattr(call, "component_input_hash", None),
        "output_hash": call.output_hash,
        "candidate_character_count": candidate_character_count,
        "candidate_block_count": candidate_block_count,
        "model_id": call.model_id,
        "prompt_version": call.prompt_version,
        "usage": {key: int(call.usage.get(key, 0)) for key in _USAGE_KEYS},
        "latency_ms": call.latency_ms,
        "recovered": call.recovered,
        "transport_attempts": [
            _transport_attempt_audit(attempt) for attempt in call.transport_attempts
        ],
    }


def _candidate_size(candidate: Any) -> tuple[int | None, int | None]:
    if not isinstance(candidate, dict) or not isinstance(candidate.get("blocks"), list):
        return None, None
    blocks = candidate["blocks"]
    if any(
        not isinstance(block, dict) or not isinstance(block.get("text"), str)
        for block in blocks
    ):
        return None, None
    return sum(len(block["text"]) for block in blocks), len(blocks)


def _failed_call_audit(
    component: Literal["rewriter", "judge"],
    call: ProseRewriterFailedCall | ProseJudgeFailedCall,
    rewrite_round: int,
) -> dict[str, Any]:
    return {
        "component": component,
        "rewrite_round": rewrite_round,
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
        "latency_ms": sum(attempt.latency_ms for attempt in call.transport_attempts),
        "transport_attempts": [
            _transport_attempt_audit(attempt) for attempt in call.transport_attempts
        ],
    }


def _transport_attempt_audit(attempt: Any) -> dict[str, Any]:
    return {
        "attempt_index": attempt.attempt_index,
        "status": attempt.status,
        "latency_ms": attempt.latency_ms,
        "error_code": attempt.error_code,
        "response_observed": attempt.response_observed,
        "usage": (
            None
            if attempt.usage is None
            else {key: int(attempt.usage.get(key, 0)) for key in _USAGE_KEYS}
        ),
    }


def _qualification_status(status: str) -> str:
    if status == "inconclusive":
        return "infrastructure_failed"
    if status == "protocol_failed":
        return "protocol_failed"
    return "semantic_rejected"


def _verdicts(consensus: dict[str, Any] | None) -> dict[str, str]:
    if consensus is None:
        return {}
    return {
        item["check_id"]: item["final_verdict"] for item in consensus["checks"]
    }


def _rewriter_logical_call_count(execution: ProseRewriterExecution) -> int:
    return int(execution.call is not None or execution.failed_call is not None)


def _council_logical_call_count(execution: ProseCouncilExecution) -> int:
    return len(execution.calls) + int(execution.failed_call is not None)


def _empty_usage() -> dict[str, int]:
    return {key: 0 for key in _USAGE_KEYS}


def _merge_usage(total: dict[str, int], usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    for key in _USAGE_KEYS:
        total[key] += int(usage.get(key, 0))


def _validate_source_state(source: dict[str, Any]) -> None:
    if set(source) != {"revision", "branch", "clean", "tracked_source_hash"}:
        raise ProseRewriteQualificationError(
            "prose_rewrite_qualification_source_state_invalid"
        )
    if (
        not isinstance(source["revision"], str)
        or not source["revision"]
        or not isinstance(source["branch"], str)
        or not source["branch"]
        or not isinstance(source["clean"], bool)
        or not isinstance(source["tracked_source_hash"], str)
        or len(source["tracked_source_hash"]) != 64
    ):
        raise ProseRewriteQualificationError(
            "prose_rewrite_qualification_source_state_invalid"
        )


def _git_source_state(repo_root: Path) -> dict[str, Any]:
    root = repo_root.resolve()
    revision = _git(root, "rev-parse", "HEAD").strip()
    branch = _git(root, "branch", "--show-current").strip()
    dirty = _git(root, "status", "--porcelain", "--untracked-files=normal")
    tracked = _git(root, "ls-files", "-s", "--", ".")
    return {
        "revision": revision,
        "branch": branch,
        "clean": not bool(dirty.strip()),
        "tracked_source_hash": canonical_hash(tracked.splitlines()),
    }


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True, encoding="utf-8"
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ProseRewriteQualificationError(
            "prose_rewrite_qualification_git_probe_failed"
        ) from error


def _write_json_once(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError as error:
        raise ProseRewriteQualificationError(
            "prose_rewrite_qualification_attempt_already_exists"
        ) from error


def _write_report_once(path: Path, report: dict[str, Any]) -> None:
    _write_json_once(path, report)


def _local_api_key() -> str:
    for name in ("CASEFILE_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise ProseRewriteQualificationError(
        "prose_rewrite_qualification_local_credential_required"
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
        raise ProseRewriteQualificationError(
            "prose_rewrite_qualification_explicit_live_confirmation_required"
        )
    report = run_prose_rewrite_qualification(
        attempt_id=args.attempt_id,
        api_key=_local_api_key(),
        rewriter_provider=DeepSeekProseRewriterProvider(),
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
    "FIDELITY_JUDGE_PROMPT_VERSION",
    "LIVE_CONFIRMATION",
    "ProseRewriteQualificationError",
    "REPORT_VERSION",
    "run_prose_rewrite_qualification",
]
