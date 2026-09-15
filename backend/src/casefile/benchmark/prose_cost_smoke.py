"""Explicit, serial A/B cost smoke. Never a qualification run or an answer cache."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any

from casefile.agent_runtime.prose_generation import generation_issue
from casefile.agent_runtime.prose_rewriter import (
    DeepSeekProseRewriterProvider,
    FakeProseRewriterProvider,
    ProseRewriterInfrastructureError,
)
from casefile.agent_runtime.prose_skills import digest
from casefile.agent_runtime.prose_writer import (
    DeepSeekProseWriterProvider,
    FakeProseWriterProvider,
    ProseWriterInfrastructureError,
)
from casefile.benchmark.prose_cost_budget import (
    BudgetExhausted,
    CostBudget,
    PriceSnapshot,
    cache_observations,
    summarize_calls,
)
from casefile.benchmark.prose_cost_fixtures import smoke_request, smoke_tasks
from casefile.benchmark.source_identity import read_git_identity
from casefile.domain.narrative_compiler import (
    normalize_scene_render_candidate,
    normalize_scene_rewrite_candidate,
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, value: Any) -> None:
    # Replace only our own report file; attempts/inputs remain individually addressable.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8", newline="\n"
    )
    temporary.replace(path)


def validate_output(task: dict[str, Any], request: Any, candidate: Any) -> str | None:
    try:
        issue = generation_issue(
            candidate, request, "prose_rewrite" if task["role"] == "rewriter" else "prose_writer"
        )
        if issue:
            return str(issue["code"])
        kwargs = dict(
            checklist=task["checklist"],
            profile=task["asset"]["profile"],
            component_input_hash=request.component_input_hash,
        )
        if task["role"] == "writer":
            normalize_scene_render_candidate(candidate, **kwargs)
        else:
            normalize_scene_rewrite_candidate(
                candidate,
                **kwargs,
                current_render=task["asset"]["initial_render"],
                rewrite_round=request.rewrite_round,
            )
    except (ValueError, RuntimeError, TypeError) as error:
        return str(error)
    return None


def run_smoke(
    output_dir: Path,
    *,
    mode: str = "fake",
    prices: PriceSnapshot | None = None,
    budget_cny: Decimal = Decimal("20"),
    api_key: str = "",
) -> dict[str, Any]:
    if mode not in {"fake", "live"}:
        raise ValueError("Unknown smoke mode")
    if mode == "live" and (prices is None or not api_key):
        raise ValueError("Live smoke requires verified prices and local credential")
    tasks = smoke_tasks()
    if (
        prices is not None
        and prices.model_id != smoke_request(tasks[0], "prose-writer-v4", "").model_id
    ):
        raise ValueError("Pricing model differs from frozen runtime model")
    output_dir.mkdir(parents=True, exist_ok=False)
    budget = CostBudget(prices, budget_cny) if prices else None
    rows: list[dict[str, Any]] = []
    prepared = []
    for task in tasks:
        versions = (
            ("prose-writer-v4", "prose-writer-v6")
            if task["role"] == "writer"
            else ("prose-rewriter-v7", "prose-rewriter-v9")
        )
        for arm, version in zip(("baseline", "candidate"), versions, strict=True):
            request = smoke_request(task, version, api_key)
            provider = (
                DeepSeekProseWriterProvider()
                if task["role"] == "writer"
                else DeepSeekProseRewriterProvider()
            )
            body = provider.completion_body(request)
            prepared.append((task, arm, request, provider, body))
    manifest = {
        "schema_version": 1,
        "mode": mode,
        "created_at": now(),
        "budget_cny": str(budget_cny),
        "prices": asdict(prices) if prices else None,
        "qualification": False,
        "repetitions": 2,
        "max_transport_retries": 1,
        "source": read_git_identity(Path(__file__).resolve().parents[4]),
        "tasks": [{"kind": t["kind"], "input_hash": digest(t), "input": t} for t in tasks],
        "requests": [
            {
                "task_id": t["descriptor"]["task_id"],
                "arm": arm,
                "prompt_version": r.prompt_version,
                "request_fingerprint": r.request_fingerprint,
                "wire_hash": digest(body),
                "body": body,
                "skill": r.prompt_metadata,
            }
            for t, arm, r, _, body in prepared
        ],
    }
    write_json(output_dir / "manifest.json", manifest)
    manifest_hash = digest(json.loads((output_dir / "manifest.json").read_text(encoding="utf-8")))
    status = "running"
    stop_reason: str | None = None

    def checkpoint() -> dict[str, Any]:
        report = {
            "mode": mode,
            "status": status,
            "stop_reason": stop_reason,
            "qualification": False,
            "quality_review": "pending_manual_review",
            "manifest_hash": manifest_hash,
            "planned_observations": 12,
            "completed_observations": sum(r["status"] == "completed" for r in rows),
            "missing_observations": [
                f"{task['kind']}/{arm}/{rep}"
                for task, arm, _, _, _ in prepared
                for rep in (1, 2)
                if not any(
                    r["kind"] == task["kind"]
                    and r["arm"] == arm
                    and r["repetition"] == rep
                    and r["status"] == "completed"
                    for r in rows
                )
            ],
            "structural_failures_by_arm": {
                arm: sum(bool(r.get("validation_error")) for r in rows if r["arm"] == arm)
                for arm in ("baseline", "candidate")
            },
            "budget_charged_upper_cny": str(budget.charged) if budget else "0",
            "outstanding_reservations": budget.reservations if budget else {},
            "summary": summarize_calls(rows),
            "cache_observations": cache_observations(rows),
            "by_arm": {
                arm: summarize_calls([r for r in rows if r["arm"] == arm])
                for arm in ("baseline", "candidate")
            },
            "by_group": {
                f"{role}/{arm}/{observation}": summarize_calls(
                    [
                        r
                        for r in rows
                        if (r["role"], r["arm"], r["observation"]) == (role, arm, observation)
                    ]
                )
                for role in ("writer", "rewriter")
                for arm in ("baseline", "candidate")
                for observation in ("first_observed", "repeat")
            },
            "rows": rows,
        }
        write_json(output_dir / "report.json", report)
        return report

    try:
        for task, arm, request, provider, body in prepared:
            for repetition in (1, 2):
                for attempt in (1, 2):
                    call_id = f"{task['kind']}-{arm}-{repetition}-{attempt}"
                    if mode == "live" and budget is not None:
                        budget.reserve(call_id, body)
                    row: dict[str, Any] = {
                        "call_id": call_id,
                        "role": task["role"],
                        "kind": task["kind"],
                        "arm": arm,
                        "repetition": repetition,
                        "attempt": attempt,
                        "observation": "first_observed" if repetition == 1 else "repeat",
                        "requested_model_id": request.model_id,
                        "response_model_id": None,
                        "wire_hash": digest(body),
                        "request_fingerprint": request.request_fingerprint,
                        "stable_prefix_hash": sha256(
                            body["messages"][0]["content"].encode()
                        ).hexdigest(),
                        "skill": request.prompt_metadata,
                        "started_at": now(),
                        "status": "pending",
                    }
                    rows.append(row)
                    # Persist the reservation and pending attempt before network I/O.
                    checkpoint()
                    started = perf_counter()
                    try:
                        if mode == "fake":
                            candidates = (
                                (task["asset"]["fake_candidate"],)
                                if task["role"] == "writer"
                                else (task["asset"]["fake_rewrite_candidates"][0],)
                            )
                            result = (
                                FakeProseWriterProvider(candidates=candidates).write_scene(request)
                                if task["role"] == "writer"
                                else FakeProseRewriterProvider(candidates=candidates).rewrite_scene(
                                    request
                                )
                            )
                        else:
                            result = (
                                provider.write_scene(request)
                                if isinstance(provider, DeepSeekProseWriterProvider)
                                else provider.rewrite_scene(request)
                            )
                        row.update(
                            status="completed",
                            ended_at=now(),
                            latency_ms=result.latency_ms,
                            response_model_id=result.response_model_id,
                            usage_details=result.usage_details or None,
                            output=result.candidate,
                            raw_response=result.raw_response,
                            validation_error=validate_output(task, request, result.candidate),
                        )
                        if budget is not None and mode == "live":
                            budget.settle(call_id, result.usage_details)
                            estimate = (
                                prices.estimate(
                                    result.usage_details, row["started_at"], row["ended_at"]
                                )
                                if prices
                                else None
                            )
                            row["estimated_cost_cny"] = (
                                str(estimate) if estimate is not None else None
                            )
                        checkpoint()
                        break
                    except (
                        ProseWriterInfrastructureError,
                        ProseRewriterInfrastructureError,
                    ) as error:
                        row.update(
                            status="failed",
                            ended_at=now(),
                            error_code=str(error),
                            latency_ms=round((perf_counter() - started) * 1000),
                        )
                        if budget is not None:
                            budget.settle(call_id, None)
                        checkpoint()
                        if attempt == 2 or not any(
                            code in str(error)
                            for code in ("Timeout", "Connection", "RateLimit", "InternalServer")
                        ):
                            break
        status = (
            "completed" if sum(r["status"] == "completed" for r in rows) == 12 else "incomplete"
        )
    except BudgetExhausted as error:
        status, stop_reason = "budget_stopped", str(error)
    finally:
        if status == "running":
            status, stop_reason = "interrupted", "Run interrupted; no automatic resume"
        report = checkpoint()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fake", "live"), default="fake")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--budget-cny", type=Decimal, default=Decimal("20"))
    args = parser.parse_args()
    prices = None
    if args.prices:
        data = json.loads(args.prices.read_text(encoding="utf-8"))
        for name in ("cached_per_million", "uncached_per_million", "output_per_million"):
            data[name] = Decimal(str(data[name]))
        prices = PriceSnapshot(**data)
    api_key = (
        os.environ.get("CASEFILE_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    )
    report = run_smoke(
        args.output_dir, mode=args.mode, prices=prices, budget_cny=args.budget_cny, api_key=api_key
    )
    print(
        json.dumps({"status": report["status"], "summary": report["summary"]}, ensure_ascii=False)
    )
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
