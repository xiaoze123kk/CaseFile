"""Frozen 24 x 3 v4 evaluation, disjoint cases in three worker processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from casefile.agent_runtime.chat_delegation import runtime_manifest
from casefile.benchmark.chat_live_eval import _saved_provider_credential
from casefile.benchmark.chat_outcome_live_eval import _atomic_json_write
from casefile.benchmark.chat_outcome_suite import ChatOutcomeTask
from casefile.benchmark.chat_subagent_live_eval import load_prices, run
from casefile.benchmark.chat_subagent_qualification import MODEL_ID, build_suite_tasks

ROOT = Path(__file__).resolve().parents[4]


def source_hashes() -> dict[str, str]:
    paths = [
        *ROOT.joinpath("backend/src/casefile").rglob("*.py"),
        *ROOT.joinpath("backend/src/casefile").rglob("*.md"),
        *ROOT.joinpath("backend/src/casefile").rglob("*.json"),
        *ROOT.joinpath("backend/src/casefile_contracts").rglob("*.py"),
        *ROOT.joinpath("fixtures").rglob("*.json"),
        ROOT / "backend/uv.lock",
    ]
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(set(paths))
    }


def worker(
    output: str, case_ids: tuple[str, ...], budget: str, suite_version: str = "v1"
) -> dict[str, Any]:
    load_dotenv(ROOT / ".env", override=False)
    load_dotenv(ROOT / "backend/.env", override=False)
    os.environ["CASEFILE_CHAT_CONTEXT_LIVE_ACCEPTANCE"] = "1"
    saved = _saved_provider_credential(
        database_url=os.environ["DATABASE_URL"],
        actor_id=1,
        provider_name="deepseek",
        requested_model=MODEL_ID,
    )
    if saved is None or saved[1] != MODEL_ID:
        raise RuntimeError("saved deepseek-flash credential is required")
    return run(
        Path(output),
        api_key=saved[0],
        prices=load_prices(
            ROOT / "backend/src/casefile/benchmark/prices/deepseek-flash-20260916.json"
        ),
        budget_cny=Decimal(budget),
        max_input_tokens_per_trial=180000,
        max_output_tokens_per_trial=24000,
        case_ids=case_ids,
        arms=("subagents_v4",),
        trials=3,
        suite_version=suite_version,
    )


def merge_shards(
    output: Path, case_ids: list[str], suite_version: str = "v1", workers: int = 3
) -> dict[str, Any]:
    rows = []
    for shard in range(workers):
        report = json.loads((output / f"shard-{shard}/subagents_v4.json").read_text("utf-8"))
        if report.get("suite_version") != f"casefile-chat-subagent-{suite_version}":
            raise ValueError("suite version mismatch across shards")
        rows.extend(report["rows"])
    keys = [(row["task_id"], row["trial_no"]) for row in rows]
    expected = {(case_id, trial) for case_id in case_ids for trial in range(1, 4)}
    if len(keys) != 72 or len(set(keys)) != 72 or set(keys) != expected:
        raise ValueError("incomplete or duplicate trial matrix")
    if any(row["protocol"] != "casefile-chat-tools-v9" for row in rows):
        raise ValueError("non-v4 trial in frozen matrix")
    return {
        "model_id": MODEL_ID,
        "trial_count": 72,
        "task_count": 24,
        "trials": 3,
        "comparison": "historical_only"
        if suite_version == "v1"
        else "new_suite_no_direct_comparison",
        "suite_version": f"casefile-chat-subagent-{suite_version}",
        "runtime": runtime_manifest(),
        "input_tokens": sum(row["input_tokens"] for row in rows),
        "output_tokens": sum(row["output_tokens"] for row in rows),
        "rows": sorted(rows, key=lambda row: (row["task_id"], row["trial_no"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--budget-cny", type=Decimal, default=Decimal("12"))
    parser.add_argument("--suite-version", choices=("v1", "v2", "v3"), default="v1")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=3)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    suite_builder: Callable[[], tuple[ChatOutcomeTask, ...]] = build_suite_tasks
    if args.suite_version == "v3":
        from casefile.benchmark.chat_subagent_suite_v3 import (
            build_suite_tasks as build_v3_tasks,
        )
        from casefile.benchmark.chat_subagent_suite_v3 import (
            calibration_report as calibration_v3_report,
        )

        suite_builder = build_v3_tasks
        calibration = calibration_v3_report()
        _atomic_json_write(output / "calibration.json", calibration)
        if not calibration["passed"]:
            raise ValueError("v3 calibration failed before model calls")
    if args.suite_version == "v2":
        from casefile.benchmark.chat_subagent_suite_v2 import (
            build_suite_tasks as build_v2_tasks,
        )
        from casefile.benchmark.chat_subagent_suite_v2 import (
            calibration_report,
        )

        suite_builder = build_v2_tasks
        calibration = calibration_report()
        _atomic_json_write(output / "calibration.json", calibration)
        if not calibration["passed"]:
            raise ValueError("v2 suite calibration failed before any model calls")
    cases = [task.task_id for task in suite_builder()]
    hashes = source_hashes()
    manifest = {
        "started_at": datetime.now(UTC).isoformat(),
        "model_id": MODEL_ID,
        "revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "runtime": runtime_manifest(),
        "case_ids": cases,
        "trials": 3,
        "workers": args.workers,
        "budget_cny": str(args.budget_cny),
        "comparison": (
            "historical_only" if args.suite_version == "v1" else "new_suite_no_direct_comparison"
        ),
        "suite_version": f"casefile-chat-subagent-{args.suite_version}",
        "source_hashes": hashes,
    }
    _atomic_json_write(output / "manifest.json", manifest)
    try:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = [
                pool.submit(
                    worker,
                    str(output / f"shard-{i}"),
                    tuple(cases[i :: args.workers]),
                    str(args.budget_cny / args.workers),
                    args.suite_version,
                )
                for i in range(args.workers)
            ]
            summaries = [future.result() for future in futures]
        report = merge_shards(output, cases, args.suite_version, args.workers)
        _atomic_json_write(output / "subagents-v4.json", report)
        _atomic_json_write(output / "summary.json", {"status": "completed", "shards": summaries})
    finally:
        current = source_hashes()
        changed = [
            path for path in hashes.keys() | current.keys() if hashes.get(path) != current.get(path)
        ]
        _atomic_json_write(
            output / "source-verification.json",
            {
                "unchanged": not changed,
                "changed_paths": changed,
                "finished_at": datetime.now(UTC).isoformat(),
            },
        )
    print(json.dumps({"output_dir": str(output), "trials": 72}, ensure_ascii=False))


if __name__ == "__main__":
    main()
