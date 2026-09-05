"""Pro-only paired reassessment of immutable, already-preserved public prose."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from typing import Any

from casefile.agent_runtime.prose_judge import FULL_COUNCIL_POLICY
from casefile.agent_runtime.prose_quality_config import QUALITY_PRO_DIAGNOSTIC
from casefile.agent_runtime.prose_quality_critic import (
    FakeProseQualityCriticProvider,
    execute_mirrored_pairwise_quality,
)
from casefile.benchmark.prose_quality_diagnostic import (
    Audit,
    AuditedQuality,
    source_snapshot,
    write_new,
)
from casefile.benchmark.prose_quality_diagnostic_suite import ROOT
from casefile.domain.narrative_compiler import (
    QUALITY_DIMENSIONS,
    canonical_json_sha256,
    resolve_mirrored_quality,
    validate_quality_pair_inputs,
    validate_quality_pairwise_report,
)


def read_verified(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if value.get("content_hash") != canonical_json_sha256(
        {k: v for k, v in value.items() if k != "content_hash"}
    ):
        raise ValueError(f"rescore_source_hash_invalid:{path.name}")
    return value


def pairwise(case: dict[str, Any], provider: Any, api_key: str) -> Any:
    return execute_mirrored_pairwise_quality(
        provider,
        checklist=case["task"]["checklist"],
        profile=case["task"]["profile"],
        original_render=case["original"],
        polished_render=case["polished"],
        preservation_consensus=case["preservation"],
        config=QUALITY_PRO_DIAGNOSTIC,
        model_id=QUALITY_PRO_DIAGNOSTIC.pairwise_model,
        api_key=api_key,
        reverse_first=case["repeat"] % 2 == 1,
    )


def validate_case(task: dict[str, Any], saved: dict[str, Any], repeat: int) -> dict[str, Any]:
    row, execution = saved["row"], saved["execution"]
    if row["task_id"] != task["task_id"] or row["repeat"] != repeat:
        raise ValueError("rescore_source_identity_invalid")
    original, preservation = execution["original_render"], execution["preservation"]
    if original != task["original_render"] or execution["polish"]["status"] != "completed":
        raise ValueError("rescore_source_render_invalid")
    if (
        preservation["status"] != "completed"
        or preservation["policy_hash"] != FULL_COUNCIL_POLICY.policy_hash
        or preservation["consensus"]["council_policy_hash"] != FULL_COUNCIL_POLICY.policy_hash
        or [r["role"] for r in preservation["judge_reports"]] != list(FULL_COUNCIL_POLICY.roles)
        or preservation["consensus"]["judge_report_hashes"]
        != [canonical_json_sha256(r) for r in preservation["judge_reports"]]
    ):
        raise ValueError("rescore_full_preservation_required")
    polished = execution["polish"]["render"]
    validate_quality_pair_inputs(
        checklist=task["checklist"],
        profile=task["profile"],
        original_render=original,
        polished_render=polished,
        preservation_consensus=preservation["consensus"],
    )
    baseline = execution["pairwise"]
    if baseline["status"] != "completed" or len(baseline["calls"]) != 2:
        raise ValueError("rescore_completed_flash_pair_required")
    for report in baseline["reports"]:
        validate_quality_pairwise_report(
            report,
            checklist=task["checklist"],
            profile=task["profile"],
            original_render=original,
            polished_render=polished,
            preservation_consensus=preservation["consensus"],
            position_mapping=report["position_mapping"],
        )
    decision = resolve_mirrored_quality(*baseline["reports"])
    if asdict(decision) != {
        **baseline["decision"],
        "report_hashes": tuple(baseline["decision"]["report_hashes"]),
    }:
        raise ValueError("rescore_flash_decision_invalid")
    case = {
        "task": task,
        "repeat": repeat,
        "original": original,
        "polished": polished,
        "preservation": preservation["consensus"],
        "flash_reports": baseline["reports"],
        "flash_decision": asdict(decision),
        "source_row_hash": saved["content_hash"],
    }
    # Build both exact new requests with zero transport; compare blind inputs before any live call.
    tie = {
        "schema_id": "compiler.prose-quality-pairwise-candidate.v1",
        "overall_preference": "tie",
        "dimension_preferences": [
            {"dimension": d, "preference": "tie"} for d in QUALITY_DIMENSIONS
        ],
    }
    preflight = pairwise(
        case, FakeProseQualityCriticProvider(pairwise_candidates=(tie, tie)), "fake"
    )
    if preflight.status != "completed":
        raise ValueError("rescore_request_preflight_failed")
    for new, old in zip(preflight.calls, baseline["calls"], strict=True):
        old_payload, new_payload = old["request_payload"], new.request_payload
        if (
            old["model_id"] != "deepseek-v4-flash"
            or old["prompt_hash"] != new.prompt_hash
            or old_payload["untrusted_data"] != new_payload["untrusted_data"]
            or old_payload["quality_dimensions"] != new_payload["quality_dimensions"]
            or old_payload["server_bindings"]["candidate_schema_hash"]
            != new_payload["server_bindings"]["candidate_schema_hash"]
        ):
            raise ValueError("rescore_must_change_only_pairwise_model")
    return case


def load_source(root: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    manifest, report = (
        read_verified(root / "attempt-manifest.json"),
        read_verified(root / "report.json"),
    )
    suite = manifest["suite"]
    if (
        report["status"] != "completed"
        or report["source_stable"] is not True
        or report["polisher_pairwise_config"] != "v2-flash"
        or suite["suite_hash"] != report["suite_hash"]
        or suite["suite_hash"]
        != canonical_json_sha256({k: v for k, v in suite.items() if k != "suite_hash"})
        or suite["suite_role"] != "development"
        or len(suite["polisher_tasks"]) != 24
    ):
        raise ValueError("rescore_completed_public_source_required")
    cases, hashes, ids = [], {}, set()
    for name in ("attempt-manifest.json", "report.json"):
        hashes[name] = read_verified(root / name)["content_hash"]
    for task in suite["polisher_tasks"]:
        if not re.fullmatch(r"polisher_[0-9]{2}", task["task_id"]) or task["task_id"] in ids:
            raise ValueError("rescore_duplicate_or_invalid_task")
        ids.add(task["task_id"])
        for repeat in range(3):
            name = f"rows/{task['task_id']}-{repeat}-polish-v2.json"
            saved = read_verified(root / name)
            cases.append(validate_case(task, saved, repeat))
            hashes[name] = saved["content_hash"]
    return cases, hashes


def decision_stats(reports: list[dict[str, Any]]) -> dict[str, Any]:
    def mapped(report: dict[str, Any], preference: str) -> str:
        return "tie" if preference == "tie" else str(report["position_mapping"][preference])

    overall = [mapped(r, r["overall_preference"]) for r in reports]
    dimensions = [[mapped(r, p["preference"]) for p in r["dimension_preferences"]] for r in reports]
    return {
        "overall": overall,
        "mirrored": len(overall) == 2 and overall[0] == overall[1],
        "dimension_mirrored": [
            len(dimensions) == 2 and dimensions[0][i] == dimensions[1][i] for i in range(5)
        ],
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rounds = []
    for repeat in range(3):
        items = [r for r in rows if r["repeat"] == repeat]
        rounds.append(
            {
                "repeat": repeat,
                "total": 24,
                "completed": sum(r["status"] == "completed" for r in items),
                "flash_adopted": sum(r["flash_adopted"] for r in items),
                "pro_adopted": sum(r["pro_adopted"] for r in items),
                "flash_mirrored": sum(r["flash"]["mirrored"] for r in items),
                "pro_mirrored": sum(r["pro"]["mirrored"] for r in items),
                "pro_adoption_threshold_met": len(items) == 24
                and all(r["status"] == "completed" for r in items)
                and sum(r["pro_adopted"] for r in items) >= 18,
            }
        )
    return {
        "rounds": rounds,
        "transitions": dict(Counter(f"{r['flash_reason']} -> {r['pro_reason']}" for r in rows)),
        "changed_decisions": [
            {
                "task_id": r["task_id"],
                "repeat": r["repeat"],
                "flash": r["flash_reason"],
                "pro": r["pro_reason"],
            }
            for r in rows
            if r["flash_reason"] != r["pro_reason"]
        ],
        "qualified": False,
        "interpretation": "paired_diagnostic_not_independent_quality_gold",
    }


def run(*, source_root: Path, output: Path, api_key: str, workers: int = 4) -> dict[str, Any]:
    if not api_key or workers not in range(1, 5):
        raise ValueError("rescore_key_and_bounded_workers_required")
    cases, hashes = load_source(source_root)
    if output.resolve() == source_root.resolve() or output.resolve().is_relative_to(
        source_root.resolve()
    ):
        raise ValueError("rescore_output_must_not_modify_source")
    output.mkdir(parents=True, exist_ok=False)
    source = source_snapshot()
    write_new(
        output / "attempt-manifest.json",
        {
            "source_root": str(source_root.resolve()),
            "source_artifacts": hashes,
            "source": source,
            "config": asdict(QUALITY_PRO_DIAGNOSTIC),
            "case_count": 72,
            "expected_calls": 144,
            "qualified": False,
            "workers": workers,
        },
    )
    audit = Audit(output / "calls")
    provider = AuditedQuality(audit)
    (output / "rows").mkdir()

    def execute(case: dict[str, Any]) -> dict[str, Any]:
        result = pairwise(case, provider, api_key)
        decision = result.decision
        row = {
            "task_id": case["task"]["task_id"],
            "focus": case["task"]["focus"],
            "repeat": case["repeat"],
            "status": result.status,
            "error_code": result.error_code,
            "source_row_hash": case["source_row_hash"],
            "original_hash": canonical_json_sha256(case["original"]),
            "polished_hash": canonical_json_sha256(case["polished"]),
            "preservation_hash": canonical_json_sha256(case["preservation"]),
            "flash_adopted": case["flash_decision"]["accept_polished"],
            "pro_adopted": bool(decision and decision.accept_polished),
            "flash_reason": case["flash_decision"]["selection_reason"],
            "pro_reason": decision.selection_reason if decision else result.status,
            "flash": decision_stats(case["flash_reports"]),
            "pro": decision_stats(list(result.reports))
            if result.status == "completed"
            else decision_stats([]),
        }
        write_new(
            output / "rows" / f"{row['task_id']}-{row['repeat']}.json",
            {"row": row, "execution": asdict(result)},
        )
        print(
            json.dumps(
                {k: row[k] for k in ("task_id", "repeat", "status", "flash_reason", "pro_reason")}
            ),
            flush=True,
        )
        return row

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(execute, cases))
        artifacts_stable = all(
            read_verified(source_root / name)["content_hash"] == digest
            for name, digest in hashes.items()
        )
        code_stable = source_snapshot()["fingerprint"] == source["fingerprint"]
        report = {
            "status": "completed"
            if all(r["status"] == "completed" for r in rows) and artifacts_stable and code_stable
            else "inconclusive",
            "qualified": False,
            "source_artifacts_stable": artifacts_stable,
            "code_stable": code_stable,
            "summary": summarize(rows),
            "rows": rows,
            "logical_calls": audit.count,
            "physical_requests": audit.physical,
            "unknown_usage_count": audit.unknown_usage,
            "known_total_tokens": audit.tokens,
        }
        write_new(output / "report.json", report)
        return report
    except BaseException as error:
        write_new(
            output / "interrupted.json",
            {
                "qualified": False,
                "exception_type": type(error).__name__,
                "logical_calls": audit.count,
                "physical_requests": audit.physical,
                "pending_policy": "no_automatic_retry_or_resume",
            },
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-attempt", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}", args.attempt_id)
        or args.attempt_id == "local"
    ):
        parser.error("unique safe attempt-id required")
    report = run(
        source_root=args.source_attempt,
        output=ROOT / "backend/var/benchmark/prose-quality/pro-rescore-v1" / args.attempt_id,
        api_key=os.environ.get("CASEFILE_DEEPSEEK_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY", ""),
        workers=args.workers,
    )
    print(json.dumps(report["summary"]))
    return 0 if report["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
