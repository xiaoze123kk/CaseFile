"""Component diagnostics only; not a production Worker cost/quality qualification.

The historical attempts omitted durable replay and the baseline Continuity stage.
Do not use their totals as production cost improvement evidence.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from casefile.agent_runtime.deepseek_transport import model_checked_client as OpenAI
from casefile.agent_runtime.model_policy import DEEPSEEK_MODEL_ID
from casefile.agent_runtime.prose_auto_edit import execute_auto_edit
from casefile.agent_runtime.prose_judge import (
    FIDELITY_ONLY_POLICY,
    DeepSeekProseJudgeProvider,
)
from casefile.agent_runtime.prose_polish_supervisor import execute_prose_polish_supervisor
from casefile.agent_runtime.prose_polisher import DeepSeekProsePolisherProvider
from casefile.agent_runtime.prose_quality_critic import DeepSeekProseQualityCriticProvider
from casefile.agent_runtime.prose_rewrite_supervisor import execute_bounded_prose_rewrite
from casefile.agent_runtime.prose_rewriter import DeepSeekProseRewriterProvider
from casefile.agent_runtime.prose_writer import DeepSeekProseWriterProvider, execute_prose_writer
from casefile.agent_runtime.usage import prose_response_usage
from casefile.benchmark.prose_writer_eval import load_prose_writer_dev_suite
from casefile.domain.narrative_compiler import (
    build_prose_judge_checklist,
    canonical_json_sha256,
    finalize_scene_render,
)

TASK_INDEXES = (0, 3, 6, 9, 12, 15)


@dataclass
class Tracker:
    delegate: Any
    results: list[Any] = field(default_factory=list)
    remaining_judge_calls: int = 100
    allow_generation_repair: bool = False

    def _call(self, method: str, request: Any) -> Any:
        result = getattr(self.delegate, method)(request)
        self.results.append(result)
        return result

    def write_scene(self, request: Any) -> Any:
        return self._call("write_scene", request)

    def rewrite_scene(self, request: Any) -> Any:
        return self._call("rewrite_scene", request)

    def auto_edit_decide(self, request: Any) -> Any:
        return self.rewrite_scene(request)

    def polish_scene(self, request: Any) -> Any:
        return self._call("polish_scene", request)

    def assess_quality(self, request: Any) -> Any:
        return self._call("assess_quality", request)

    def judge_scene(self, request: Any) -> Any:
        return self._call("judge_scene", request)

    def arbitrate_scene(self, request: Any) -> Any:
        return self._call("arbitrate_scene", request)


def _usage(results: list[Any]) -> dict[str, int]:
    return {
        key: sum(int(getattr(item, "usage", {}).get(key, 0)) for item in results)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }


def _text(render: dict[str, Any]) -> str:
    return "\n\n".join(block["text"] for block in render["blocks"])


def _arm(task: dict[str, Any], mode: str, api_key: str) -> dict[str, Any]:
    writer = Tracker(DeepSeekProseWriterProvider())
    rewriter = Tracker(DeepSeekProseRewriterProvider())
    polisher = Tracker(DeepSeekProsePolisherProvider())
    judge = Tracker(DeepSeekProseJudgeProvider())
    quality = Tracker(DeepSeekProseQualityCriticProvider())
    started = perf_counter()
    written = execute_prose_writer(
        writer,
        scene_plan=task["scene_plan"],
        narrative_ir=task["narrative_ir"],
        profile=task["asset"]["profile"],
        checklist=task["checklist"],
        previous_scene_render=task["asset"]["previous_scene_render"],
        model_id=DEEPSEEK_MODEL_ID,
        api_key=api_key,
        remaining_scene_call_budget=8 if mode == "auto_edit" else 23,
        soft_target_length=mode == "auto_edit",
    )
    accepted = None
    unresolved: list[str] = []
    detail_status: str = written.status
    error = written.error_code
    if written.status == "completed" and written.render is not None and mode == "auto_edit":
        edited = execute_auto_edit(
            rewriter,
            rewriter,
            polisher,
            scene_plan=task["scene_plan"],
            narrative_ir=task["narrative_ir"],
            profile=task["asset"]["profile"],
            checklist=task["checklist"],
            previous_scene_render=task["asset"]["previous_scene_render"],
            writer_render=written.render,
            model_id=DEEPSEEK_MODEL_ID,
            api_key=api_key,
        )
        accepted = edited.accepted_render
        unresolved = list(edited.unresolved_issues)
        detail_status, error = edited.status, edited.error_code
    elif written.status == "completed" and written.render is not None:
        revised = execute_bounded_prose_rewrite(
            rewriter,
            judge,
            scene_plan=task["scene_plan"],
            narrative_ir=task["narrative_ir"],
            profile=task["asset"]["profile"],
            checklist=task["checklist"],
            previous_scene_render=task["asset"]["previous_scene_render"],
            initial_render=written.render,
            model_id=DEEPSEEK_MODEL_ID,
            api_key=api_key,
            remaining_scene_call_budget=22,
            llm_revision=True,
            delivery_mode="product",
        )
        detail_status, error = revised.status, revised.error_code
        if revised.status == "product_accepted" and revised.final_render is not None:
            accepted = finalize_scene_render(
                revised.final_render,
                original_render=revised.final_render,
                checklist=task["checklist"],
                profile=task["asset"]["profile"],
                component_input_hash=canonical_json_sha256(
                    {"mode": mode, "reports": revised.revision_reports}
                ),
                selection_reason="llm_nonfatal_retained",
            ).model_dump(mode="json")
        elif revised.status == "semantic_accepted" and revised.final_render is not None:
            consensus = revised.rounds[-1].council.consensus
            assert consensus is not None
            polished = execute_prose_polish_supervisor(
                quality,
                polisher,
                judge,
                checklist=task["checklist"],
                profile=task["asset"]["profile"],
                original_render=revised.final_render,
                semantic_consensus=consensus,
                preservation_policy=FIDELITY_ONLY_POLICY,
                quality_model_id=DEEPSEEK_MODEL_ID,
                generation_model_id=DEEPSEEK_MODEL_ID,
                api_key=api_key,
            )
            accepted = polished.accepted_render
            detail_status, error = polished.status, polished.error_code
    results = writer.results + rewriter.results + polisher.results + judge.results + quality.results
    text = _text(accepted) if accepted else ""
    return {
        "mode": mode,
        "status": detail_status,
        "error_code": error,
        "completed": accepted is not None,
        "selection_reason": accepted.get("selection_reason") if accepted else None,
        "render_hash": canonical_json_sha256(accepted) if accepted else None,
        "character_count": len(text),
        "call_count": len(results),
        "usage": _usage(results),
        "latency_ms": round((perf_counter() - started) * 1000),
        "unresolved_issues": unresolved,
        "_text": text,
        "_render": accepted,
    }


def _blind_compare(
    api_key: str, left: str, right: str, checklist: dict[str, Any] | None = None
) -> dict[str, Any]:
    client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com", max_retries=0)
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL_ID,
        messages=[
            {
                "role": "system",
                "content": (
                    "比较两份匿名小说稿。只输出一个 JSON 对象，例如 "
                    '{"preference":"a","reason":"A更连贯"}。'
                    "preference 只能是 a、b 或 tie。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请按事实连贯、人物认知、悬念控制、语言、对白和节奏选择 A、B 或平局。\n"
                    + json.dumps(
                        {"candidate_a": left, "candidate_b": right, "checklist": checklist},
                        ensure_ascii=False,
                    )
                ),
            },
        ],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=800,
        extra_body={"thinking": {"type": "disabled"}},
    )
    raw = response.choices[0].message.content or ""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = {}
    if not isinstance(value, dict):
        value = {}
    client.close()
    raw_value = next(
        (
            value[key]
            for key in ("preference", "choice", "winner", "better", "选择", "偏好")
            if key in value
        ),
        "",
    )
    raw_preference = str(raw_value).strip().lower()
    compact_preference = raw_preference.replace(" ", "").replace("-", "_")
    normalized = {
        "a": "a",
        "candidate_a": "a",
        "候选a": "a",
        "b": "b",
        "candidate_b": "b",
        "候选b": "b",
        "tie": "tie",
        "draw": "tie",
        "平局": "tie",
    }.get(compact_preference)
    protocol_valid = normalized is not None
    return {
        **value,
        "raw_preference": raw_value,
        "preference": normalized,
        "protocol_valid": protocol_valid,
        "raw_response": raw,
        "finish_reason": response.choices[0].finish_reason,
        "usage": prose_response_usage(response),
    }


def _three_scene(api_key: str, task: dict[str, Any], arm: Any = None) -> dict[str, Any]:
    arm = arm or _arm
    profile = task["asset"]["profile"]
    previous = None
    previous_issues: list[str] = []
    rows = []
    started = perf_counter()
    for scene in sorted(task["scene_plan"]["scenes"], key=lambda item: item["discourse_order"]):
        checklist = build_prose_judge_checklist(
            scene_plan=task["scene_plan"],
            narrative_ir=task["narrative_ir"],
            profile=profile,
            scene_id=scene["scene_id"],
            previous_scene_render=previous,
        )
        local = {
            **task,
            "checklist": checklist,
            "asset": {**task["asset"], "previous_scene_render": previous},
            "previous_edit_issues": previous_issues,
        }
        row = arm(local, "auto_edit", api_key)
        rows.append({key: value for key, value in row.items() if not key.startswith("_")})
        if not row["completed"]:
            break
        previous = row["_render"]
        previous_issues = row["unresolved_issues"]
    return {
        "completed_scene_count": len([row for row in rows if row["completed"]]),
        "target_scene_count": 3,
        "latency_ms": round((perf_counter() - started) * 1000),
        "rows": rows,
    }


def run(
    output: Path, api_key: str, *, arm: Any = None, frozen: dict[str, Any] | None = None
) -> dict[str, Any]:
    arm = arm or _arm
    if output.exists():
        raise ValueError("comparison_output_already_exists")
    suite = load_prose_writer_dev_suite()
    selected = [suite["tasks"][index] for index in TASK_INDEXES]
    output.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    report = {
        "schema_id": "casefile.prose-auto-edit-comparison.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "model_id": DEEPSEEK_MODEL_ID,
        "qualification": False,
        "scope": (frozen or {}).get("execution", "component_diagnostic_not_production_worker"),
        "repetitions": 1,
        "task_indexes": list(TASK_INDEXES),
        "status": "running",
        "rows": rows,
        "three_scene_smoke": None,
        "frozen": frozen,
    }

    def checkpoint() -> None:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def evaluate(left: str, right: str, checklist: dict[str, Any]) -> dict[str, Any]:
        started = perf_counter()
        try:
            result = _blind_compare(api_key, left, right, checklist)
        except Exception as error:
            result = {
                "preference": None,
                "protocol_valid": False,
                "error_type": type(error).__name__,
                "usage": {"usage_known": False},
                "status": "failed_or_unknown_no_retry",
            }
        result["latency_ms"] = round((perf_counter() - started) * 1000)
        return result

    checkpoint()
    for task in selected:
        row = {
            "task_id": task["descriptor"]["task_id"],
            "ability": task["descriptor"]["ability"],
            "auto_edit": None,
            "full_polish": None,
            "comparison": None,
        }
        rows.append(row)
        checkpoint()
        auto = arm(task, "auto_edit", api_key)
        row["auto_edit"] = {key: value for key, value in auto.items() if not key.startswith("_")}
        checkpoint()
        full = arm(task, "full_polish", api_key)
        row["full_polish"] = {key: value for key, value in full.items() if not key.startswith("_")}
        checkpoint()
        if auto["completed"] and full["completed"]:
            row["comparison"] = {"status": "first_request_started"}
            checkpoint()
            first = evaluate(auto["_text"], full["_text"], task["checklist"])
            row["comparison"] = {"auto_first": first}
            checkpoint()
            row["comparison"]["status"] = "second_request_started"
            checkpoint()
            second = evaluate(full["_text"], auto["_text"], task["checklist"])
            normalized_first = first["preference"]
            normalized_second = {
                "a": "b",
                "b": "a",
                "tie": "tie",
                None: None,
            }[second["preference"]]
            row["comparison"] = {
                "auto_first": first,
                "full_first": second,
                "normalized": (
                    "protocol_invalid"
                    if not first["protocol_valid"] or not second["protocol_valid"]
                    else normalized_first
                    if normalized_first == normalized_second
                    else "order_inconsistent"
                ),
            }
        checkpoint()
    report["three_scene_smoke"] = _three_scene(api_key, selected[0], arm)
    report["status"] = "completed"
    checkpoint()
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    api_key = (
        os.environ.get("CASEFILE_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    ).strip()
    if not api_key:
        raise ValueError("DeepSeek credential is required")
    report = run(args.output, api_key)
    print(json.dumps({"output": str(args.output), "rows": len(report["rows"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
