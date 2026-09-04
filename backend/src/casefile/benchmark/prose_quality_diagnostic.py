"""One frozen public 8 x 3 baseline/candidate experiment, never qualification."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from casefile.agent_runtime import prose_quality_critic as quality_runtime
from casefile.agent_runtime.prompt_repository import load_prompt
from casefile.agent_runtime.prose_quality_critic import (
    PROSE_QUALITY_COMPONENT_HASH,
    PROSE_QUALITY_MODEL_ID,
    PROSE_QUALITY_PAIRWISE_PROMPT_VERSION,
    PROSE_QUALITY_REQUEST_PROTOCOL,
    DeepSeekProseQualityCriticProvider,
    FakeProseQualityCriticProvider,
    PairwiseQualityPolicy,
    ProseQualityCriticProvider,
    ProseQualityInfrastructureError,
    ProseQualityProtocolError,
    ProseQualityProviderResult,
    ProseQualityRequest,
    execute_mirrored_pairwise_quality,
)
from casefile.agent_runtime.prose_quality_diagnostic import (
    DIAGNOSTIC_PARAMETERS,
    diagnostic_component,
    execute_diagnostic_quality,
)
from casefile.benchmark.prose_quality_diagnostic_report import (
    diagnostic_comparison,
    diagnostic_markdown,
    score_diagnostic_row,
    summarize_arm,
    swap_preference,
)
from casefile.benchmark.prose_quality_eval import (
    DEFAULT_ATTESTATION,
    ROOT,
    load_prose_quality_dev_suite,
)
from casefile.benchmark.prose_quality_source import quality_source_identity
from casefile.domain.narrative_compiler import QUALITY_DIMENSIONS, canonical_json_sha256

EXPERIMENT_PATH = ROOT / "fixtures/prose_quality_benchmark/diagnostic-v1/experiment.json"
LIVE_ROOT = ROOT / "backend/var/benchmark/prose-quality/diagnostic-v1/live"
FAKE_ROOT = ROOT / "backend/var/benchmark/prose-quality/diagnostic-v1/fake"
Arm = Literal["baseline", "candidate"]
Mode = Literal["fake", "live"]
Experiment = Literal["independent-v1", "pacing-v1"]


class QualityDiagnosticError(RuntimeError):
    """Preflight or experiment identity failed before any further calls."""


def expected_experiment(package: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the exact prospective design without modifying fixtures."""
    schedule: list[dict[str, Any]] = []
    actual_parameters = {
        "model_id": quality_runtime.PROSE_QUALITY_MODEL_ID,
        "temperature": quality_runtime.PROSE_QUALITY_TEMPERATURE,
        "thinking_enabled": quality_runtime.PROSE_QUALITY_THINKING_ENABLED,
        "max_output_tokens": quality_runtime.PROSE_QUALITY_MAX_OUTPUT_TOKENS,
        "max_turns": quality_runtime.PROSE_QUALITY_MAX_TURNS,
        "network_retries": quality_runtime.PROSE_QUALITY_NETWORK_RETRIES,
    }
    if actual_parameters != DIAGNOSTIC_PARAMETERS:
        raise QualityDiagnosticError("quality_diagnostic_parameters_drift")
    for index, task in enumerate(package["tasks"]):
        for trial in range(1, 4):
            order = (
                ("baseline", "candidate")
                if (index + trial - 1) % 2 == 0
                else ("candidate", "baseline")
            )
            schedule.extend(
                {"task_id": task["asset"]["task_id"], "trial": trial, "arm": arm} for arm in order
            )
    descriptor = {
        "schema_id": "casefile.prose-quality-diagnostic-experiment.v1",
        "experiment_id": "b3-public-independent-assessment-v1",
        "suite_hash": package["suite"]["suite_hash"],
        "attestation_hash": canonical_json_sha256(
            json.loads(DEFAULT_ATTESTATION.read_text(encoding="utf-8"))
        ),
        "task_fingerprints": [task["descriptor"] for task in package["tasks"]],
        "task_count": 8,
        "trials_per_task": 3,
        "call_budget": {"baseline": 48, "candidate": 96, "total": 144},
        "parameters": DIAGNOSTIC_PARAMETERS,
        "baseline": {
            "component_hash": PROSE_QUALITY_COMPONENT_HASH,
            "protocol": PROSE_QUALITY_REQUEST_PROTOCOL,
            "prompt_version": PROSE_QUALITY_PAIRWISE_PROMPT_VERSION,
            "prompt_hash": load_prompt(
                "prose_quality_pairwise", PROSE_QUALITY_PAIRWISE_PROMPT_VERSION
            ).system_prompt_sha256,
        },
        "candidate": diagnostic_component(),
        "schedule": schedule,
        "comparison_policy": (
            "strict-mirror-improvement-both-position-overall-and-dimensions-nonloss-zero-failure-v1"
        ),
        "failure_policy": "protocol-stop-arm-trial-infrastructure-stop-attempt-no-retry-v1",
        "reuse_policy": "no-cross-trial-reuse-shared-assessments-within-candidate-only",
        "qualified": False,
        "qualification_eligible": False,
    }
    return {**descriptor, "experiment_hash": canonical_json_sha256(descriptor)}


def load_experiment(
    experiment: Experiment = "independent-v1",
) -> tuple[dict[str, Any], dict[str, Any]]:
    if experiment == "pacing-v1":
        from casefile.benchmark.prose_quality_pacing import load_pacing_experiment

        return load_pacing_experiment()
    if experiment != "independent-v1":
        raise QualityDiagnosticError("quality_diagnostic_unknown_experiment")
    package = load_prose_quality_dev_suite()
    descriptor = json.loads(EXPERIMENT_PATH.read_text(encoding="utf-8"))
    if descriptor != expected_experiment(package):
        raise QualityDiagnosticError("quality_diagnostic_experiment_drift")
    return descriptor, package


def _write_once(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


class DiagnosticFakeProvider:
    """Gold is used only by the explicit Fake implementation, never the live adapter."""

    def __init__(self, gold: dict[str, Any]) -> None:
        self.gold = gold

    def assess_quality(self, request: ProseQualityRequest) -> ProseQualityProviderResult:
        if request.request_kind == "assessment":
            candidate = {
                "schema_id": "compiler.prose-quality-single-assessment.v1",
                "dimensions": [
                    {
                        "dimension": dim,
                        "severity": "none",
                        "observation": "未发现缺陷。",
                        "evidence_ids": [],
                    }
                    for dim in QUALITY_DIMENSIONS
                ],
            }
        else:
            reverse = request.position_mapping == {"a": "polished", "b": "original"}

            def mapped(value: str) -> str:
                return swap_preference(value) if reverse else value

            candidate = {
                "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
                "overall_preference": mapped(self.gold["overall_preference"]),
                "dimension_preferences": [
                    {"dimension": item["dimension"], "preference": mapped(item["preference"])}
                    for item in self.gold["dimension_preferences"]
                ],
            }
        return FakeProseQualityCriticProvider(pairwise_candidates=(candidate,)).assess_quality(
            request
        )


class DiagnosticCallRecorder:
    """Count every issued call, including malformed responses and transport failures."""

    def __init__(self, provider: ProseQualityCriticProvider, *, limit: int) -> None:
        self.provider = provider
        self.limit = limit
        self.records: list[dict[str, Any]] = []

    def assess_quality(self, request: ProseQualityRequest) -> ProseQualityProviderResult:
        if len(self.records) >= self.limit:
            raise ProseQualityProtocolError("quality_diagnostic_call_budget_exceeded")
        if any(getattr(request, key) != value for key, value in DIAGNOSTIC_PARAMETERS.items()):
            raise ProseQualityProtocolError("quality_diagnostic_request_parameters_drift")
        record: dict[str, Any] = {
            "request_kind": request.request_kind,
            "request_fingerprint": request.request_fingerprint,
            "component_input_hash": request.component_input_hash,
            "input_hash": request.input_hash,
            "model_id": request.model_id,
            "prompt_version": request.prompt_version,
            "prompt_hash": request.prompt_hash,
            "status": "started",
            "usage": {},
            "transport_attempt_count": 0,
            "latency_ms": 0,
        }
        self.records.append(record)
        try:
            result = self.provider.assess_quality(request)
        except ProseQualityInfrastructureError as error:
            attempts = error.failed_call.transport_attempts if error.failed_call else ()
            record.update(
                status="inconclusive",
                error_code=str(error),
                transport_attempt_count=len(attempts),
                latency_ms=sum(a.latency_ms for a in attempts),
            )
            raise
        except Exception as error:
            record.update(
                status="inconclusive",
                error_code=f"quality_diagnostic_unexpected:{type(error).__name__}",
            )
            raise ProseQualityInfrastructureError(record["error_code"]) from error
        record.update(
            status="response_observed",
            output_hash=result.output_hash,
            usage=result.usage,
            latency_ms=result.latency_ms,
            transport_attempt_count=len(result.transport_attempts),
        )
        return result


def _execute_row(
    row: dict[str, Any],
    asset: dict[str, Any],
    provider: ProseQualityCriticProvider,
    api_key: str,
    pairwise_policy: PairwiseQualityPolicy | None = None,
) -> None:
    recorder = DiagnosticCallRecorder(
        provider, limit=2 if row["arm"] == "baseline" or pairwise_policy else 4
    )
    arguments = {
        "checklist": asset["checklist"],
        "original_render": asset["render_a"],
        "polished_render": asset["render_b"],
        "profile": asset["profile"],
        "preservation_consensus": asset["semantic_consensus_b"],
        "api_key": api_key,
    }
    if row["arm"] == "baseline" or pairwise_policy is not None:
        execution = execute_mirrored_pairwise_quality(
            recorder,
            **arguments,
            model_id=PROSE_QUALITY_MODEL_ID,
            pairwise_policy=pairwise_policy if row["arm"] == "candidate" else None,
        )
    else:
        execution = execute_diagnostic_quality(
            recorder, **arguments, original_consensus=asset["semantic_consensus_a"]
        )
    row.update(
        status=execution.status,
        error_code=execution.error_code,
        predictions=[
            {
                "overall_preference": result["overall_preference"],
                "dimension_preferences": result["dimension_preferences"],
            }
            for result in execution.reports
        ],
        calls=recorder.records,
        call_count=len(recorder.records),
        transport_attempt_count=sum(r["transport_attempt_count"] for r in recorder.records),
        latency_ms=sum(r["latency_ms"] for r in recorder.records),
        decision=asdict(execution.decision) if execution.decision else None,
    )
    usage: Counter[str] = Counter()
    for call in recorder.records:
        usage.update(call["usage"])
    row["usage"] = dict(usage)


def run_quality_diagnostic(
    *,
    attempt_id: str,
    mode: Mode = "fake",
    experiment: Experiment = "independent-v1",
    api_key: str = "",
    output_dir: Path | None = None,
    fake_provider_factory: Callable[[dict[str, Any]], ProseQualityCriticProvider] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Claim a fresh attempt, run the frozen schedule once and retain all denominators."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", attempt_id) or mode not in (
        "fake",
        "live",
    ):
        raise QualityDiagnosticError("quality_diagnostic_attempt_or_mode_invalid")
    if mode == "live" and (not api_key.strip() or fake_provider_factory is not None):
        raise QualityDiagnosticError("quality_diagnostic_live_binding_invalid")
    descriptor, package = load_experiment(experiment)
    pairwise_policy = None
    live_root, fake_root = LIVE_ROOT, FAKE_ROOT
    if experiment == "pacing-v1":
        from casefile.benchmark.prose_quality_pacing import pacing_policy

        pairwise_policy = pacing_policy()
        pacing_output = ROOT / "backend/var/benchmark/prose-quality/pacing-v1"
        live_root, fake_root = pacing_output / "live", pacing_output / "fake"
    source_before = quality_source_identity(ROOT)
    if mode == "live" and not source_before["clean"]:
        raise QualityDiagnosticError("quality_diagnostic_clean_source_required")
    destination = output_dir or ((live_root if mode == "live" else fake_root) / attempt_id)
    if mode == "live" and destination.resolve() != (live_root / attempt_id).resolve():
        raise QualityDiagnosticError("quality_diagnostic_live_output_invalid")
    # Both claims are exclusive; a new attempt name cannot rerun the same Live design.
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {
        "attempt_id": attempt_id,
        "mode": mode,
        "experiment_hash": descriptor["experiment_hash"],
        "source_before": source_before,
        "qualified": False,
        "adapter": "DeepSeekProseQualityCriticProvider" if mode == "live" else "Fake",
    }
    if mode == "live":
        _write_once(live_root / f"consumed-{descriptor['experiment_hash']}.json", manifest)
    _write_once(destination / "attempt-manifest.json", manifest)
    tasks = {task["asset"]["task_id"]: task for task in package["tasks"]}
    rows: list[dict[str, Any]] = []
    aborted = False
    live_provider = DeepSeekProseQualityCriticProvider() if mode == "live" else None
    schedule_count = len(descriptor["schedule"])
    for index, scheduled in enumerate(descriptor["schedule"], start=1):
        task = tasks[scheduled["task_id"]]
        row = {
            **scheduled,
            "cohort": task["asset"].get("group", "legacy"),
            "gold_overall": task["asset"]["gold"]["overall_preference"],
            "pair_fingerprint": task["descriptor"]["pair_fingerprint"],
            "status": "not_run",
            "error_code": "prior_infrastructure_failure",
            "predictions": [],
            "decision": None,
            "calls": [],
            "call_count": 0,
            "transport_attempt_count": 0,
            "latency_ms": 0,
            "usage": {},
        }
        if not aborted:
            if progress:
                progress(
                    f"[{index}/{schedule_count}] {row['task_id']} "
                    f"trial={row['trial']} arm={row['arm']} started"
                )
            provider = live_provider or (
                fake_provider_factory(task)
                if fake_provider_factory
                else DiagnosticFakeProvider(task["asset"]["gold"])
            )
            _execute_row(
                row, task["asset"], provider, api_key if mode == "live" else "fake", pairwise_policy
            )
            aborted = row["status"] == "inconclusive"
        score_diagnostic_row(row, task["asset"]["gold"])
        rows.append(row)
        _write_once(destination / f"row-{index:02d}.json", row)
        if progress:
            progress(
                f"[{index}/{schedule_count}] {row['task_id']} "
                f"trial={row['trial']} arm={row['arm']} "
                f"status={row['status']} calls={row['call_count']}"
            )
    source_after = quality_source_identity(ROOT)
    data_stable = False
    try:
        descriptor_after, _ = load_experiment(experiment)
        data_stable = descriptor_after == descriptor
    except (ValueError, OSError, RuntimeError):
        pass
    source_stable = source_before == source_after
    arms = {
        arm: summarize_arm(
            [row for row in rows if row["arm"] == arm], task_count=descriptor["task_count"]
        )
        for arm in ("baseline", "candidate")
    }
    complete = (
        not aborted
        and data_stable
        and source_stable
        and all(row["status"] == "completed" for row in rows)
    )
    report = {
        "schema_id": "casefile.prose-quality-diagnostic-report.v1",
        **manifest,
        "status": "completed" if complete else "inconclusive",
        "execution_complete": not aborted,
        "completion_reason": "infrastructure_abort" if aborted else "all_scheduled",
        "source_after": source_after,
        "source_stable": source_stable,
        "data_stable": data_stable,
        "experiment": descriptor,
        "task_count": descriptor["task_count"],
        "trials_per_task": 3,
        "arm_trial_count": descriptor["task_count"] * 3,
        "independent_task_count": descriptor["task_count"],
        "shared_candidate_assessments": experiment == "independent-v1",
        "semantic_invalid_count": 0,
        "arms": arms,
        "rows": rows,
        "qualification_eligible": False,
        "comparison": diagnostic_comparison(arms, complete=complete, live=mode == "live"),
    }
    if experiment == "pacing-v1":
        from casefile.benchmark.prose_quality_pacing import pacing_comparison

        report["schema_id"] = "casefile.prose-quality-pacing-report.v1"
        report["comparison"] = pacing_comparison(rows, complete=complete, live=mode == "live")
        report["cohorts"] = {
            cohort: {
                arm: summarize_arm(
                    [r for r in rows if r["cohort"] == cohort and r["arm"] == arm], task_count=count
                )
                for arm in ("baseline", "candidate")
            }
            for cohort, count in (("legacy", 8), ("redundant", 2), ("functional", 2))
        }
    report["report_hash"] = canonical_json_sha256(report)
    _write_once(destination / "report.json", report)
    with (destination / "report.md").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(diagnostic_markdown(report))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument(
        "--experiment", choices=("independent-v1", "pacing-v1"), default="independent-v1"
    )
    args = parser.parse_args()
    api_key = ""
    if args.mode == "live":
        api_key = (
            os.environ.get("CASEFILE_DEEPSEEK_API_KEY", "").strip()
            or os.environ.get("DEEPSEEK_API_KEY", "").strip()
        )
    report = run_quality_diagnostic(
        attempt_id=args.attempt_id,
        mode=args.mode,
        experiment=args.experiment,
        api_key=api_key,
        progress=lambda value: print(value, flush=True),
    )
    print(
        json.dumps(
            {key: report[key] for key in ("status", "comparison", "report_hash")},
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
