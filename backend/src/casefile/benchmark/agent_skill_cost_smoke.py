"""Small engineering-only live smoke for stable prefixes, protocols and cache usage."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, cast

from casefile.agent_runtime.deepseek_transport import model_checked_client
from casefile.agent_runtime.model_call_audit import observe_model_calls
from casefile.agent_runtime.model_policy import DEEPSEEK_MODEL_ID
from casefile.agent_runtime.prompt_repository import load_prompt, packaged_prompt_repository
from casefile.agent_runtime.skill_assembly import request_identity
from casefile.benchmark.agent_engineering_budget import EngineeringBudget, historical_budget
from casefile.benchmark.prose_cost_budget import BudgetExhausted, PriceSnapshot, summarize_calls

Protocol = Literal["json", "stream", "tool"]
FIXTURES = (
    ("brief", "brief_polish", None, "json"),
    ("brief_to_draft", "brief_to_draft", "planner", "json"),
    ("chat", "casefile_chat", "router", "json"),
    ("planning", "story_planner_skeleton", None, "json"),
    ("judge", "prose_fidelity_judge", None, "json"),
    ("novel_stream", "novel_collaboration", None, "stream"),
    ("context_tool", "casefile_chat_context_compactor", "compact", "tool"),
)


def read_prices(path: Path) -> PriceSnapshot:
    value = json.loads(path.read_text(encoding="utf-8"))
    return PriceSnapshot(
        model_id=value["model_id"],
        verified_at=value["verified_at"],
        source=value["source"],
        cached_per_million=Decimal(value["cached_per_million"]),
        uncached_per_million=Decimal(value["uncached_per_million"]),
        output_per_million=Decimal(value["output_per_million"]),
        currency=value["currency"],
        conversion_basis=value["conversion_basis"],
    )


def instructions(agent: str, component: str | None, *, candidate: bool) -> tuple[str, str]:
    repository = packaged_prompt_repository()
    version = repository.current_version(agent)
    definition = load_prompt(agent, version) if candidate else repository.load(agent, version)
    text = definition.component_prompts[component] if component else definition.system_prompt
    return version, text


def request_body(prompt: str, protocol: Protocol) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": DEEPSEEK_MODEL_ID,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "工程连通性检查。只返回一个包含 ok=true 的最小 JSON。"},
        ],
        "max_tokens": 64,
        "temperature": 0,
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    if protocol == "json":
        body["response_format"] = {"type": "json_object"}
    if protocol == "stream":
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    if protocol == "tool":
        body.update(
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "submit_smoke",
                        "description": "Submit the engineering smoke result.",
                        "parameters": {
                            "type": "object",
                            "properties": {"ok": {"type": "boolean"}},
                            "required": ["ok"],
                            "additionalProperties": False,
                        },
                        "strict": True,
                    },
                }
            ],
            tool_choice={"type": "function", "function": {"name": "submit_smoke"}},
        )
    return body


def execute(api_key: str, body: dict[str, Any], protocol: Protocol) -> None:
    base_url = "https://api.deepseek.com/beta" if protocol == "tool" else "https://api.deepseek.com"
    with model_checked_client(
        api_key=api_key, base_url=base_url, max_retries=1, timeout=90
    ) as client:
        response = client.chat.completions.create(**body)
        if protocol == "stream":
            for _chunk in response:
                pass


def usage_summaries(finished: list[dict[str, Any]]) -> dict[str, Any]:
    def normalized(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "usage_details": row["usage"],
                "estimated_cost_cny": row["estimated_cost_cny"],
                "latency_ms": row["elapsed_ms"],
                "attempt": row["transport_retry_index"] + 1,
                "stage": row["binding"].get("component"),
            }
            for row in rows
        ]

    observations = {
        f"{arm}:{observation}": summarize_calls(
            normalized(
                [
                    row
                    for row in finished
                    if row["binding"].get("arm") == arm
                    and row["binding"].get("observation") == observation
                ]
            )
        )
        for arm in ("baseline", "candidate")
        for observation in ("first_observed", "repeat")
    }
    case_names = sorted({str(row["binding"].get("agent")) for row in finished})
    by_case = {
        name: summarize_calls(
            normalized([row for row in finished if row["binding"].get("agent") == name])
        )
        for name in case_names
    }
    return {
        "summary": summarize_calls(normalized(finished)),
        "by_observation": observations,
        "by_agent": by_case,
        "response_models": sorted(
            {row["response_model"] for row in finished if row.get("response_model")}
        ),
    }


def smoke(
    output: Path,
    *,
    live: bool,
    prices: PriceSnapshot,
    history_reports: list[Path],
    api_key: str,
) -> dict[str, Any]:
    if output.exists():
        raise ValueError("Refusing to overwrite an Agent Skill smoke directory")
    output.mkdir(parents=True)
    history = historical_budget(history_reports)
    cases = []
    for name, agent, component, protocol_value in FIXTURES:
        protocol = cast(Protocol, protocol_value)
        old_version, baseline = instructions(agent, component, candidate=False)
        new_version, candidate = instructions(agent, component, candidate=True)
        if old_version != new_version or baseline != candidate:
            raise ValueError("Engineering release changed model instructions")
        cases.append(
            {
                "name": name,
                "agent": agent,
                "component": component,
                "protocol": protocol,
                "prompt_version": new_version,
                "baseline_prefix_hash": request_identity(request_body(baseline, protocol))[
                    "stable_prefix_hash"
                ],
                "candidate_prefix_hash": request_identity(request_body(candidate, protocol))[
                    "stable_prefix_hash"
                ],
            }
        )
    manifest = {
        "schema_version": 1,
        "mode": "live" if live else "offline",
        "model_id": prices.model_id,
        "prices": asdict(prices),
        "history": history,
        "cases": cases,
        "repetitions": 2,
        "transport_retries": 1,
        "qualification": False,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    if not live:
        return {"status": "offline_ready", "planned_calls": len(cases) * 4, **manifest}
    if not api_key:
        raise ValueError("Live mode requires CASEFILE_DEEPSEEK_API_KEY or DEEPSEEK_API_KEY")
    ledger = EngineeringBudget(output / "ledger.json", prices, history)
    status, stop_reason = "completed", None
    try:
        for case in cases:
            agent = cast(str, case["agent"])
            component = cast(str | None, case["component"])
            protocol = cast(Protocol, case["protocol"])
            for arm in ("baseline", "candidate"):
                _, prompt = instructions(agent, component, candidate=arm == "candidate")
                body = request_body(prompt, protocol)
                for repetition in (1, 2):
                    binding = {
                        "agent": agent,
                        "component": component or "system",
                        "protocol": protocol,
                        "prompt_version": case["prompt_version"],
                        "arm": arm,
                        "observation": "first_observed" if repetition == 1 else "repeat",
                    }
                    with observe_model_calls(ledger, binding=binding):
                        execute(api_key, body, protocol)
    except BudgetExhausted as error:
        status, stop_reason = "budget_stopped", str(error)
    finished = [row for row in ledger.state["calls"] if row["event"] == "finished"]

    usage = usage_summaries(finished)
    report = {
        "status": status,
        "stop_reason": stop_reason,
        "planned_logical_calls": len(cases) * 4,
        "physical_calls": len(finished),
        **usage,
        "charged_upper_cny_including_history": str(ledger.charged),
        "remaining_upper_budget_cny": str(Decimal(20) - ledger.charged),
        "qualification": False,
        "limitation": (
            "Byte-identical release smoke; candidate observations may reuse baseline cache."
        ),
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--history-report", type=Path, action="append", required=True)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    key = os.environ.get("CASEFILE_DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    result = smoke(
        args.output,
        live=args.live,
        prices=read_prices(args.prices),
        history_reports=args.history_report,
        api_key=key.strip(),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
