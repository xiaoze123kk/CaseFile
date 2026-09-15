"""Budgeted public Writer/Rewriter quality acceptance, never private qualification."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, fields
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from casefile.agent_runtime.model_policy import DEEPSEEK_MODEL_ID
from casefile.agent_runtime.prose_judge import (
    FIDELITY_ONLY_POLICY,
    DeepSeekProseJudgeProvider,
    FakeProseJudgeProvider,
    build_server_evidence_catalog,
    execute_semantic_council,
)
from casefile.agent_runtime.prose_quality_critic import (
    DeepSeekProseQualityCriticProvider,
    execute_quality_findings,
)
from casefile.agent_runtime.prose_rewriter import (
    DeepSeekProseRewriterProvider,
    execute_prose_rewriter,
)
from casefile.agent_runtime.prose_runtime import prose_runtime_binding
from casefile.agent_runtime.prose_writer import DeepSeekProseWriterProvider, execute_prose_writer
from casefile.agent_runtime.usage import response_usage_details
from casefile.benchmark.prose_cost_budget import (
    BudgetExhausted,
    CostBudget,
    PriceSnapshot,
    summarize_calls,
)
from casefile.benchmark.prose_cost_smoke import write_json
from casefile.benchmark.source_identity import read_git_identity
from casefile.domain.narrative_compiler import (
    build_prose_judge_checklist,
    canonical_json_sha256,
    compile_scene_plan_v2,
    finalize_scene_render,
    normalize_scene_render_candidate,
    validate_narrative_ir,
)

ROOT = Path(__file__).resolve().parents[4]
SUITE = ROOT / "fixtures/prose_skill_acceptance/v1/suite.json"


def load_cases() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    if suite["content_hash"] != canonical_json_sha256(
        {k: v for k, v in suite.items() if k != "content_hash"}
    ):
        raise ValueError("Acceptance suite hash mismatch")
    if "\ufffd" in json.dumps(suite, ensure_ascii=False):
        raise ValueError("Damaged acceptance text")
    validate_narrative_ir(suite["narrative_ir"], source_document=suite["document"])
    assert (
        compile_scene_plan_v2(
            scene_compiler_input=suite["scene_compiler_input"],
            semantic_fills=suite["semantic_fills"],
        )
        == suite["scene_plan"]
    )
    previous = None
    cases = []
    for scene, text in zip(suite["scene_plan"]["scenes"], suite["reference_texts"], strict=True):
        common = dict(
            scene_plan=suite["scene_plan"],
            narrative_ir=suite["narrative_ir"],
            profile=suite["profile"],
            previous_scene_render=previous,
        )
        checklist = build_prose_judge_checklist(**common, scene_id=scene["scene_id"])
        candidate = {"schema_id": "compiler.scene-render-candidate.v1", "blocks": [{"text": text}]}
        render = normalize_scene_render_candidate(
            candidate,
            checklist=checklist,
            profile=suite["profile"],
            component_input_hash=canonical_json_sha256(candidate),
        ).model_dump(mode="json")
        bad = {
            "schema_id": "compiler.scene-render-candidate.v1",
            "blocks": [
                {"text": "本场景中的第七次重启并没有发生，备用系统始终未执行重启动作。" + text}
            ],
        }
        bad_render = normalize_scene_render_candidate(
            bad,
            checklist=checklist,
            profile=suite["profile"],
            component_input_hash=canonical_json_sha256(bad),
        ).model_dump(mode="json")
        evidence = build_server_evidence_catalog(bad_render)[0]["evidence_id"]
        assessments = []
        for i, check in enumerate(checklist["checks"]):
            verdict = "fail" if i < 2 else "pass"
            needs_evidence = (check["polarity"] == "required" and verdict == "pass") or (
                check["polarity"] == "forbidden" and verdict == "fail"
            )
            assessments.append(
                {
                    "check_id": check["check_id"],
                    "verdict": verdict,
                    "evidence_ids": [evidence] if needs_evidence else [],
                    "rationale": "人为注入了否定重启发生的矛盾句，需修复事件实现和模态。"
                    if i < 2
                    else "作者参考正文保留该项要求。",
                }
            )
        gold = execute_semantic_council(
            FakeProseJudgeProvider(
                judge_reports=(
                    {"schema_id": "compiler.prose-judge-candidate.v1", "assessments": assessments},
                )
            ),
            checklist=checklist,
            render=bad_render,
            profile=suite["profile"],
            policy=FIDELITY_ONLY_POLICY,
            model_id=DEEPSEEK_MODEL_ID,
            api_key="fake",
        )
        if gold.status != "completed":
            raise ValueError(f"Invalid authored defect oracle: {gold.error_code}")
        cases.append(
            {
                "scene_id": scene["scene_id"],
                "common": common,
                "checklist": checklist,
                "bad_render": bad_render,
                "gold": gold,
            }
        )
        previous = finalize_scene_render(
            render,
            original_render=render,
            checklist=checklist,
            profile=suite["profile"],
            component_input_hash=canonical_json_sha256(render),
            selection_reason="quick_draft_unreviewed",
        ).model_dump(mode="json")
    return suite, cases


class Audit:
    def __init__(self, root: Path, prices: PriceSnapshot, limit: Decimal) -> None:
        self.root = root
        self.budget = CostBudget(prices, limit)
        self.rows: list[dict[str, Any]] = []
        self.stopped = False
        self.context: dict[str, Any] = {}

    def save(self) -> None:
        write_json(
            self.root / "calls.json",
            {
                "rows": self.rows,
                "budget_charged_upper_cny": str(self.budget.charged),
                "reservations": self.budget.reservations,
                "summary": summarize_calls(self.rows),
            },
        )

    def wrap(self, provider: Any) -> Any:
        original = provider._create_completion

        def call(request: Any) -> Any:
            safe = {
                f.name: getattr(request, f.name) for f in fields(request) if f.name != "api_key"
            }
            call_id = f"call-{len(self.rows) + 1:04d}"
            if self.stopped or len(self.rows) >= 100:
                self.stopped = True
                raise BudgetExhausted("Acceptance call limit reached")
            body = {
                "model": request.model_id,
                "max_tokens": request.max_output_tokens,
                "request": safe,
            }
            try:
                self.budget.reserve(call_id, body)
            except BudgetExhausted:
                self.stopped = True
                raise
            row = {
                "call_id": call_id,
                **self.context,
                "status": "pending",
                "request": safe,
                "started_at": datetime.now(UTC).isoformat(),
            }
            self.rows.append(row)
            self.save()
            try:
                response = original(request)
            except Exception as error:
                self.budget.settle(call_id, None)
                row.update(status="failed", error_type=type(error).__name__)
                self.save()
                raise
            row.update(
                status="completed",
                ended_at=datetime.now(UTC).isoformat(),
                response_model_id=getattr(response, "model", None),
                usage_details=response_usage_details(response),
                response=response.model_dump(mode="json"),
            )
            self.budget.settle(call_id, row["usage_details"])
            estimate = self.budget.prices.estimate(
                row["usage_details"], row["started_at"], row["ended_at"]
            )
            row["estimated_cost_cny"] = str(estimate) if estimate is not None else None
            self.save()
            return response

        provider._create_completion = call
        return provider


def run(root: Path, prices: PriceSnapshot, limit: Decimal, api_key: str) -> dict[str, Any]:
    suite, cases = load_cases()
    if prices.model_id != DEEPSEEK_MODEL_ID or not api_key:
        raise ValueError("Model/credential preflight failed")
    root.mkdir(parents=True, exist_ok=False)
    audit = Audit(root, prices, limit)
    writer = audit.wrap(DeepSeekProseWriterProvider())
    rewriter = audit.wrap(DeepSeekProseRewriterProvider())
    judge = audit.wrap(DeepSeekProseJudgeProvider())
    critic = audit.wrap(DeepSeekProseQualityCriticProvider())
    write_json(
        root / "manifest.json",
        {
            "suite_hash": suite["content_hash"],
            "suite": suite,
            "runtime": prose_runtime_binding(3),
            "source": read_git_identity(ROOT),
            "prices": asdict(prices),
            "budget_cny": str(limit),
            "repeats": 2,
            "planned_outputs": 24,
            "formal_qualification": False,
            "reviewer_independence": False,
            "initial_defects": "authored contradiction with typed gold oracle",
            "previous_context": "fixed authored references; not live end-to-end novel completion",
        },
    )
    rows = []
    for case in cases:
        for role in ("writer", "rewriter"):
            for arm, version in (
                ("baseline", "prose-writer-v4" if role == "writer" else "prose-rewriter-v7"),
                ("current", "prose-writer-v6" if role == "writer" else "prose-rewriter-v9"),
            ):
                for repetition in (1, 2):
                    if audit.stopped:
                        break
                    row: dict[str, Any] = {
                        "scene_id": case["scene_id"],
                        "role": role,
                        "arm": arm,
                        "repetition": repetition,
                        "prompt_version": version,
                    }
                    audit.context = {**row, "stage": role}
                    kwargs = dict(
                        **case["common"],
                        checklist=case["checklist"],
                        model_id=DEEPSEEK_MODEL_ID,
                        api_key=api_key,
                        remaining_scene_call_budget=22,
                        prompt_version=version,
                    )
                    execution: Any
                    if role == "writer":
                        execution = execute_prose_writer(writer, **kwargs)
                    else:
                        gold = case["gold"]
                        execution = execute_prose_rewriter(
                            rewriter,
                            **kwargs,
                            current_render=case["bad_render"],
                            consensus=gold.consensus,
                            judge_reports=gold.judge_reports,
                            revision_decision={
                                "mode": "local_revision",
                                "instruction": ("去掉或纠正否认本场重启发生的矛盾，"
                                    "保留原有事实、信息边界和有效句段。返回完整正文。"),
                            },
                        )
                    row["generation"] = asdict(execution)
                    row["strict_pass"] = False
                    if execution.render is not None and not audit.stopped:
                        audit.context = {**audit.context, "stage": "fidelity"}
                        council = execute_semantic_council(
                            judge,
                            checklist=case["checklist"],
                            render=execution.render,
                            profile=suite["profile"],
                            policy=FIDELITY_ONLY_POLICY,
                            model_id=DEEPSEEK_MODEL_ID,
                            api_key=api_key,
                        )
                        row["fidelity"] = asdict(council)
                        row["strict_pass"] = (
                            council.consensus is not None
                            and council.consensus["scene_verdict"] == "pass"
                        )
                        if row["strict_pass"] and not audit.stopped:
                            assert council.consensus is not None
                            audit.context = {**audit.context, "stage": "quality"}
                            quality = execute_quality_findings(
                                critic,
                                checklist=case["checklist"],
                                render=execution.render,
                                profile=suite["profile"],
                                semantic_consensus=council.consensus,
                                model_id=DEEPSEEK_MODEL_ID,
                                api_key=api_key,
                            )
                            row["quality"] = asdict(quality)
                    rows.append(row)
                    report = {
                        "status": "budget_stopped" if audit.stopped else "running",
                        "formal_qualification": False,
                        "planned_outputs": 24,
                        "outputs": len(rows),
                        "by_arm": {
                            a: {
                                "outputs": sum(r["arm"] == a for r in rows),
                                "structural_pass": sum(
                                    r["arm"] == a and r["generation"]["status"] == "completed"
                                    for r in rows
                                ),
                                "strict_pass": sum(
                                    r["arm"] == a and r["strict_pass"] for r in rows
                                ),
                            }
                            for a in ("baseline", "current")
                        },
                        "rows": rows,
                        "usage": summarize_calls(audit.rows),
                        "budget_charged_upper_cny": str(audit.budget.charged),
                    }
                    write_json(root / "report.json", report)
                    print(
                        f"{case['scene_id']} {role} {arm} {repetition}: "
                        f"{execution.status}, strict={row['strict_pass']}",
                        flush=True,
                    )
    report["status"] = "completed" if len(rows) == 24 and not audit.stopped else "incomplete"
    write_json(root / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--prices", type=Path)
    parser.add_argument("--budget-cny", type=Decimal, default=Decimal("19"))
    args = parser.parse_args()
    if not args.live:
        suite, cases = load_cases()
        print(f"Offline acceptance preflight: {len(cases)} clean scenes, {suite['content_hash']}")
        return 0
    if args.output_dir is None or args.prices is None:
        parser.error("Live mode requires output directory and verified prices")
    values = json.loads(args.prices.read_text(encoding="utf-8"))
    for key in ("cached_per_million", "uncached_per_million", "output_per_million"):
        values[key] = Decimal(values[key])
    report = run(
        args.output_dir,
        PriceSnapshot(**values),
        args.budget_cny,
        os.environ.get("CASEFILE_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "",
    )
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
