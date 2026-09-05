"""Audited public B3 live diagnostics, independent of private qualification attempts."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from threading import Lock
from typing import Any

from casefile.agent_runtime.prose_judge import (
    FULL_COUNCIL_POLICY,
    DeepSeekProseJudgeProvider,
    execute_semantic_council,
)
from casefile.agent_runtime.prose_polish_supervisor import execute_prose_polish_supervisor
from casefile.agent_runtime.prose_polisher import DeepSeekProsePolisherProvider
from casefile.agent_runtime.prose_quality_config import (
    QUALITY_PRO_DIAGNOSTIC,
    QUALITY_V2,
    ProseQualityConfig,
    quality_config,
)
from casefile.agent_runtime.prose_quality_critic import (
    DeepSeekProseQualityCriticProvider,
    execute_mirrored_pairwise_quality,
    execute_quality_findings,
)
from casefile.benchmark.prose_quality_diagnostic_report import (
    polish_row,
    quality_row,
    summarize_preservation,
    summarize_quality,
)
from casefile.benchmark.prose_quality_diagnostic_suite import (
    DEFAULT_SUITE,
    ROOT,
    load_diagnostic_suite,
)
from casefile.domain.narrative_compiler import canonical_json_sha256


def write_new(path: Path, value: dict[str, Any]) -> None:
    content = {**value, "content_hash": canonical_json_sha256(value)}
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(content, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


class Audit:
    """Write requests before dispatch and results before parsing; never serialize keys."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir()
        self.lock = Lock()
        self.count = 0
        self.physical = 0
        self.unknown_usage = 0
        self.tokens = 0

    def invoke(self, method: Any, request: Any) -> Any:
        with self.lock:
            self.count += 1
            index = self.count
        # Do not materialize api_key even in the intermediate serialized request.
        from dataclasses import fields

        safe = {
            field.name: getattr(request, field.name)
            for field in fields(request)
            if field.name != "api_key"
        }
        write_new(self.root / f"{index:05d}-request.json", safe)
        try:
            result = method(request)
        except Exception as error:
            failed = getattr(error, "failed_call", None)
            write_new(
                self.root / f"{index:05d}-failure.json",
                {
                    "exception_type": type(error).__name__,
                    "failed_call": asdict(failed) if failed else None,
                    "request_fingerprint": request.request_fingerprint,
                },
            )
            if failed:
                self.account(failed.transport_attempts)
            raise
        write_new(self.root / f"{index:05d}-response.json", asdict(result))
        self.account(result.transport_attempts)
        return result

    def account(self, attempts: Any) -> None:
        with self.lock:
            for attempt in attempts:
                self.physical += 1
                if attempt.usage is None:
                    self.unknown_usage += 1
                else:
                    self.tokens += attempt.usage.get("total_tokens", 0)


class AuditedQuality(DeepSeekProseQualityCriticProvider):
    def __init__(self, audit: Audit) -> None:
        super().__init__()
        self.audit = audit

    def assess_quality(self, request: Any) -> Any:
        return self.audit.invoke(super().assess_quality, request)


class AuditedJudge(DeepSeekProseJudgeProvider):
    def __init__(self, audit: Audit) -> None:
        super().__init__()
        self.audit = audit

    def judge_scene(self, request: Any) -> Any:
        return self.audit.invoke(super().judge_scene, request)

    def arbitrate_scene(self, request: Any) -> Any:
        return self.audit.invoke(super().arbitrate_scene, request)


class AuditedPolisher(DeepSeekProsePolisherProvider):
    def __init__(self, audit: Audit) -> None:
        super().__init__()
        self.audit = audit

    def polish_scene(self, request: Any) -> Any:
        return self.audit.invoke(super().polish_scene, request)


def source_snapshot() -> dict[str, Any]:
    tracked = subprocess.check_output(
        [
            "git",
            "ls-files",
            "backend/src",
            "fixtures/prose_quality_benchmark",
            "scripts/prose-quality-benchmark.ps1",
        ],
        cwd=ROOT,
        text=True,
    ).splitlines()
    # Include untracked experiment files as well: an uncommitted dev run is diagnostic only.
    paths = set(tracked)
    paths.update(
        p.relative_to(ROOT).as_posix()
        for base in (
            ROOT / "backend/src/casefile/benchmark",
            ROOT / "backend/src/casefile/agent_runtime",
            ROOT / "fixtures/prose_quality_benchmark",
        )
        for p in base.rglob("*")
        if p.is_file() and p.suffix in {".py", ".json", ".md"} and "__pycache__" not in p.parts
    )
    from hashlib import sha256

    files = {
        name: sha256((ROOT / name).read_bytes()).hexdigest()
        for name in sorted(paths)
        if (ROOT / name).is_file()
    }
    return {
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "files": files,
        "fingerprint": canonical_json_sha256(files),
    }


def run_quality_task(
    task: dict[str, Any],
    *,
    provider: Any,
    api_key: str,
    root: Path,
    configs: tuple[ProseQualityConfig, ...],
) -> list[dict[str, Any]]:
    rows = []
    for repeat in range(3):
        for config in configs if repeat % 2 == 0 else tuple(reversed(configs)):
            execution = execute_mirrored_pairwise_quality(
                provider,
                checklist=task["checklist"],
                profile=task["profile"],
                original_render=task["render_a"],
                polished_render=task["render_b"],
                preservation_consensus=task["semantic_consensus_b"],
                model_id=config.pairwise_model,
                config=config,
                api_key=api_key,
                reverse_first=(repeat + int(task["task_id"].split("_")[-1])) % 2 == 1,
            )
            row = quality_row(task, execution, repeat, config.config_id)
            write_new(
                root / f"{task['task_id']}-{repeat}-{config.config_id}.json",
                {"row": row, "execution": asdict(execution)},
            )
            rows.append(row)
            print(
                json.dumps(
                    {
                        "phase": "quality",
                        "task": task["task_id"],
                        "repeat": repeat,
                        "candidate": config.config_id,
                        "status": execution.status,
                    }
                ),
                flush=True,
            )
    return rows


def run_polisher_task(
    task: dict[str, Any],
    *,
    quality: Any,
    polisher: Any,
    judge: Any,
    api_key: str,
    root: Path,
    config: ProseQualityConfig,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    originals, polished = [], []
    for repeat in range(3):
        execution = execute_semantic_council(
            judge,
            checklist=task["checklist"],
            render=task["original_render"],
            profile=task["profile"],
            policy=FULL_COUNCIL_POLICY,
            model_id="deepseek-v4-pro",
            api_key=api_key,
        )
        row = {
            "task_id": task["task_id"],
            "repeat": repeat,
            "status": execution.status,
            "passed": bool(execution.consensus and execution.consensus["scene_verdict"] == "pass"),
            "verdicts": {c["check_id"]: c["final_verdict"] for c in execution.consensus["checks"]}
            if execution.consensus
            else {},
        }
        originals.append(row)
        write_new(
            root / f"{task['task_id']}-{repeat}-original.json",
            {"row": row, "execution": asdict(execution)},
        )
        print(json.dumps({"phase": "original", **row}), flush=True)
    findings = execute_quality_findings(
        quality,
        checklist=task["checklist"],
        render=task["original_render"],
        profile=task["profile"],
        semantic_consensus=task["semantic_consensus"],
        model_id="deepseek-v4-flash",
        api_key=api_key,
    )
    write_new(root / f"{task['task_id']}-frozen-findings.json", asdict(findings))
    if findings.status != "completed":
        # Fixed denominator, no selective retry of failed frozen findings.
        for repeat in range(3):
            polished.append(
                {
                    "task_id": task["task_id"],
                    "focus": task["focus"],
                    "repeat": repeat,
                    "status": findings.status,
                    "error_code": findings.error_code,
                    "verdicts": {},
                    "preservation": False,
                    "quality_non_loss": False,
                    "adopted": False,
                    "rejected": False,
                    "exact_rollback": False,
                    "critical_regressions": [],
                    "selection_reason": None,
                }
            )
        return originals, polished
    for repeat in range(3):
        result = execute_prose_polish_supervisor(
            quality,
            polisher,
            judge,
            checklist=task["checklist"],
            profile=task["profile"],
            original_render=task["original_render"],
            semantic_consensus=task["semantic_consensus"],
            quality_model_id="deepseek-v4-flash",
            generation_model_id="deepseek-v4-pro",
            api_key=api_key,
            quality_config=config,
            frozen_findings=findings.report,
            reverse_first=repeat % 2 == 1,
        )
        polish_result = polish_row(task, result, repeat)
        polished.append(polish_result)
        write_new(
            root / f"{task['task_id']}-{repeat}-polish-v2.json",
            {"row": polish_result, "execution": asdict(result)},
        )
        print(
            json.dumps(
                {
                    "phase": "polish",
                    "task": task["task_id"],
                    "repeat": repeat,
                    "status": result.status,
                }
            ),
            flush=True,
        )
    return originals, polished


def run_development(
    *,
    suite_path: Path,
    output: Path,
    api_key: str,
    candidates: str = "both",
    repeats: int = 3,
    workers: int = 4,
) -> dict[str, Any]:
    suite = load_diagnostic_suite(suite_path)
    if (
        repeats != 3
        or candidates not in {"both", QUALITY_V2.config_id, QUALITY_PRO_DIAGNOSTIC.config_id}
        or workers not in range(1, 5)
    ):
        raise ValueError("diagnostic_experiment_not_frozen")
    if not api_key:
        raise ValueError("diagnostic_api_key_missing")
    output.mkdir(parents=True, exist_ok=False)
    source = source_snapshot()
    configs = (
        (QUALITY_V2, QUALITY_PRO_DIAGNOSTIC)
        if candidates == "both"
        else (quality_config(candidates),)
    )
    write_new(
        output / "attempt-manifest.json",
        {
            "suite_hash": suite["suite_hash"],
            "source": source,
            "configs": [asdict(c) for c in configs],
            "repeats": repeats,
            "qualified": False,
            "workers": workers,
            "recovery": "no_resume_no_selective_retry",
            "suite": suite,
        },
    )
    audit = Audit(output / "calls")
    quality, judge, polisher = AuditedQuality(audit), AuditedJudge(audit), AuditedPolisher(audit)
    rows_dir = output / "rows"
    rows_dir.mkdir()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            quality_batches = list(
                pool.map(
                    lambda task: run_quality_task(
                        task, provider=quality, api_key=api_key, root=rows_dir, configs=configs
                    ),
                    suite["quality_tasks"],
                )
            )
        quality_rows = [r for batch in quality_batches for r in batch]
        quality_summary = summarize_quality(quality_rows)
        write_new(output / "quality-summary.json", quality_summary)
        selected = quality_config(quality_summary["selected"] or QUALITY_V2.config_id)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            batches = list(
                pool.map(
                    lambda task: run_polisher_task(
                        task,
                        quality=quality,
                        judge=judge,
                        polisher=polisher,
                        api_key=api_key,
                        root=rows_dir,
                        config=selected,
                    ),
                    suite["polisher_tasks"],
                )
            )
        originals = [r for batch in batches for r in batch[0]]
        polished = [r for batch in batches for r in batch[1]]
        preservation = summarize_preservation(originals, polished)
        stable = source_snapshot()["fingerprint"] == source["fingerprint"]
        report = {
            "status": "completed",
            "qualified": False,
            "suite_hash": suite["suite_hash"],
            "source_stable": stable,
            "quality": quality_summary,
            "preservation": preservation,
            "polisher_pairwise_config": selected.config_id,
            "quality_rows": quality_rows,
            "original_rows": originals,
            "polisher_rows": polished,
            "development_passed": bool(
                candidates == "both"
                and stable
                and quality_summary["selected"]
                and preservation["passed"]
            ),
            "logical_calls": audit.count,
            "physical_requests": audit.physical,
            "unknown_usage_count": audit.unknown_usage,
            "total_tokens": audit.tokens,
        }
        write_new(output / "report.json", report)
        return report
    except BaseException as error:
        write_new(
            output / "interrupted.json",
            {
                "qualified": False,
                "status": "inconclusive_infrastructure",
                "exception_type": type(error).__name__,
                "logical_calls": audit.count,
                "physical_requests": audit.physical,
                "unknown_usage_count": audit.unknown_usage,
                "total_tokens": audit.tokens,
                "pending_requests": "request_without_response_is_unknown_no_automatic_replay",
            },
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--candidates", default="both")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", args.attempt_id)
        or args.attempt_id == "local"
    ):
        parser.error("unique safe attempt-id required")
    api_key = os.environ.get("CASEFILE_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    report = run_development(
        suite_path=args.suite,
        output=ROOT / "backend/var/benchmark/prose-quality/diagnostic-v1" / args.attempt_id,
        api_key=api_key,
        candidates=args.candidates,
        repeats=args.repeats,
        workers=args.workers,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "qualified": False,
                "development_passed": report["development_passed"],
            }
        )
    )
    return 0 if report["development_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
