"""Explicit, budgeted three-arm live evaluation for CaseFile Chat subagents."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from casefile.agent_runtime.chat_tools import (
    CHAT_TOOLSET_V6_VERSION,
    CHAT_TOOLSET_V8_VERSION,
    CHAT_TOOLSET_V9_VERSION,
)
from casefile.agent_runtime.models import CaseFileChatRequest
from casefile.agent_runtime.provider_adapters.shared import (
    run_chat_subagent_component_batch,
)
from casefile.agent_runtime.structured_output import merge_usage
from casefile.benchmark.chat_live_eval import _provider, _saved_provider_credential
from casefile.benchmark.chat_outcome_eval import _request_for_task
from casefile.benchmark.chat_outcome_live_eval import (
    _atomic_json_write,
    run_live_chat_outcome_eval,
)
from casefile.benchmark.chat_subagent_qualification import (
    MODEL_ID,
    TRIALS,
    build_suite_tasks,
)
from casefile.benchmark.prose_cost_budget import PriceSnapshot


class BudgetExhausted(RuntimeError):
    pass


def load_prices(path: Path) -> PriceSnapshot:
    values = json.loads(path.read_text(encoding="utf-8"))
    for key in ("cached_per_million", "uncached_per_million", "output_per_million"):
        values[key] = Decimal(str(values[key]))
    return PriceSnapshot(**values)


def _prices_payload(prices: PriceSnapshot) -> dict[str, Any]:
    payload = asdict(prices)
    for key in ("cached_per_million", "uncached_per_million", "output_per_million"):
        payload[key] = str(payload[key])
    return payload


class EvaluationBudget:
    def __init__(
        self,
        prices: PriceSnapshot,
        limit: Decimal,
        *,
        max_input_tokens_per_trial: int,
        max_output_tokens_per_trial: int,
    ) -> None:
        if limit <= 0:
            raise ValueError("budget must be positive")
        self.prices = prices
        self.limit = limit
        self.charged = Decimal(0)
        self.reserved = Decimal(0)
        self.upper_per_trial = self.cost(
            max_input_tokens_per_trial,
            max_output_tokens_per_trial,
        )

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (
            Decimal(input_tokens) * self.prices.uncached_per_million
            + Decimal(output_tokens) * self.prices.output_per_million
        ) / Decimal(1_000_000)

    def reserve(self) -> None:
        if self.charged + self.reserved + self.upper_per_trial > self.limit:
            raise BudgetExhausted("chat subagent evaluation budget exhausted before next trial")
        self.reserved += self.upper_per_trial

    def settle(self, row: dict[str, Any]) -> None:
        actual = self.cost(int(row.get("input_tokens", 0)), int(row.get("output_tokens", 0)))
        self.reserved -= self.upper_per_trial
        if actual > self.upper_per_trial:
            raise RuntimeError("actual trial usage exceeded the explicit reservation ceiling")
        self.charged += actual


def _transform(arm: str, request: CaseFileChatRequest) -> CaseFileChatRequest:
    route = request.route
    if route is None:
        return request
    profile = dict(route.execution_profile)
    if arm == "matched_single":
        profile["max_tool_calls"] = int(profile.get("max_tool_calls", 0)) + 12
        profile["max_turns"] = int(profile.get("max_turns", 0)) + 8
    return replace(
        request,
        route=replace(route, execution_profile=profile),
        toolset_version=(
            CHAT_TOOLSET_V9_VERSION
            if arm == "subagents_v4"
            else CHAT_TOOLSET_V8_VERSION
            if arm == "subagents"
            else CHAT_TOOLSET_V6_VERSION
        ),
    )


def run_component_smoke(
    output_path: Path,
    *,
    api_key: str,
    prices: PriceSnapshot,
    budget_cny: Decimal,
    max_input_tokens: int,
    max_output_tokens: int,
    case_id: str,
) -> dict[str, Any]:
    tasks = {task.task_id: task for task in build_suite_tasks()}
    task = tasks.get(case_id)
    if task is None:
        raise ValueError(f"unknown subagent benchmark case: {case_id}")
    budget = EvaluationBudget(
        prices,
        budget_cny,
        max_input_tokens_per_trial=max_input_tokens,
        max_output_tokens_per_trial=max_output_tokens,
    )
    events: list[dict[str, Any]] = []

    def emit(event_type: str, stage: str, payload: dict[str, Any]) -> None:
        events.append({"event_type": event_type, "stage": stage, "payload": payload})

    request = replace(
        _request_for_task(task, prompt_version="casefile-chat-v27"),
        model_id=MODEL_ID,
        api_key=api_key,
        emit=emit,
        toolset_version=CHAT_TOOLSET_V8_VERSION,
    )
    provider, _ = _provider("deepseek", api_key)
    budget.reserve()
    model = provider.create_model(request)

    async def execute() -> dict[str, Any]:
        return await run_chat_subagent_component_batch(
            request,
            role="investigate",
            tasks=(
                {
                    "question": "读取并核对 clm_restart 与 info_restart_log 的直接内容",
                    "scope": "只检查 clm_restart 和 info_restart_log",
                    "preserve_constraints": [
                        "不得补造不存在的证据",
                        "完成指定两项读取后直接返回",
                    ],
                },
                {
                    "question": "读取并核对 evt_restart 与 rel_lucy_restart 的直接内容",
                    "scope": "只检查 evt_restart 和 rel_lucy_restart",
                    "preserve_constraints": [
                        "事实和推断必须分开",
                        "完成指定两项读取后直接返回",
                    ],
                },
            ),
            model=model,
            tracing_disabled=True,
        )

    batch = asyncio.run(execute())
    usage = merge_usage(
        [item for item in batch.get("_usage_records", []) if isinstance(item, dict)]
    )
    usage_row = {
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
    }
    budget.settle(usage_row)
    metrics = batch.get("_metrics", {})
    event_counts: dict[str, int] = {}
    for event in events:
        event_type = str(event["event_type"])
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
    passed = (
        len(batch.get("tasks", [])) == 2
        and all(item.get("status") == "completed" for item in batch.get("tasks", []))
        and event_counts.get("model.subagent.started") == 2
        and event_counts.get("model.subagent.completed") == 2
    )
    report = {
        "status": "passed" if passed else "failed",
        "model_id": MODEL_ID,
        "toolset_version": CHAT_TOOLSET_V8_VERSION,
        "case_id": case_id,
        "task_results": batch.get("tasks", []),
        "tool_metrics": metrics,
        "usage": usage,
        "event_counts": event_counts,
        "budget_limit_cny": str(budget_cny),
        "budget_charged_cny": str(budget.charged),
    }
    _atomic_json_write(output_path, report)
    return report


def run(
    output_dir: Path,
    *,
    api_key: str,
    prices: PriceSnapshot,
    budget_cny: Decimal,
    max_input_tokens_per_trial: int,
    max_output_tokens_per_trial: int,
    case_ids: tuple[str, ...] = (),
    arms: tuple[str, ...] = ("baseline", "matched_single", "subagents"),
    trials: int = TRIALS,
    message_override: str | None = None,
    suite_version: str = "v1",
) -> dict[str, Any]:
    trial_grader = None
    if suite_version == "v3":
        from casefile.benchmark.chat_subagent_suite_v3 import (
            build_suite_tasks as build_v3_tasks,
        )
        from casefile.benchmark.chat_subagent_suite_v3 import (
            grade_trial as grade_v3_trial,
        )

        tasks = build_v3_tasks()
        trial_grader = grade_v3_trial
    elif suite_version == "v2":
        from casefile.benchmark.chat_subagent_suite_v2 import (
            build_suite_tasks as build_v2_tasks,
        )
        from casefile.benchmark.chat_subagent_suite_v2 import (
            grade_trial,
        )

        tasks = build_v2_tasks()
        trial_grader = grade_trial
    elif suite_version == "v1":
        tasks = build_suite_tasks()
    else:
        raise ValueError("unsupported benchmark suite version")
    if case_ids:
        selected = set(case_ids)
        missing = sorted(selected - {task.task_id for task in tasks})
        if missing:
            raise ValueError(f"unknown subagent benchmark cases: {missing}")
        tasks = tuple(task for task in tasks if task.task_id in selected)
    if message_override is not None:
        if len(tasks) != 1 or not message_override.strip():
            raise ValueError("message override requires exactly one selected task")
        tasks = (replace(tasks[0], message=message_override.strip()),)
    if not tasks or trials < 1:
        raise ValueError("at least one task and one trial are required")
    if not arms or any(
        arm not in {"baseline", "matched_single", "subagents", "subagents_v4"} for arm in arms
    ):
        raise ValueError("invalid evaluation arms")
    budget = EvaluationBudget(
        prices,
        budget_cny,
        max_input_tokens_per_trial=max_input_tokens_per_trial,
        max_output_tokens_per_trial=max_output_tokens_per_trial,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    completed_arms: list[str] = []
    try:
        for arm in arms:
            rows: list[dict[str, Any]] = []

            def before_trial(_task: Any, _trial_no: int) -> None:
                budget.reserve()

            def on_trial(
                row: dict[str, Any],
                rows: list[dict[str, Any]] = rows,
                arm_name: str = arm,
            ) -> None:
                budget.settle(row)
                rows.append(row)
                _atomic_json_write(
                    output_dir / f"{arm_name}.partial.json",
                    {
                        "status": "running",
                        "arm": arm_name,
                        "model_id": MODEL_ID,
                        "trials": trials,
                        "rows": rows,
                        "budget_charged_cny": str(budget.charged),
                    },
                )

            def provider_factory() -> Any:
                provider, _ = _provider("deepseek", api_key)
                return provider

            def transform(request: CaseFileChatRequest, arm_name: str = arm) -> CaseFileChatRequest:
                return _transform(arm_name, request)

            report = run_live_chat_outcome_eval(
                provider_factory,
                provider_name="deepseek",
                model_id=MODEL_ID,
                api_key=api_key,
                tasks=tasks,
                trials=trials,
                on_trial=on_trial,
                prompt_version="casefile-chat-v27",
                request_transform=transform,
                before_trial=before_trial,
                trial_grader=trial_grader,
                retain_candidate=suite_version in {"v2", "v3"},
            )
            payload = {
                **report.as_dict(),
                "arm": arm,
                "budget_charged_cny": str(budget.charged),
                "prices": _prices_payload(prices),
                "suite_version": f"casefile-chat-subagent-{suite_version}",
            }
            _atomic_json_write(output_dir / f"{arm}.json", payload)
            completed_arms.append(arm)
    except BudgetExhausted:
        status = "incomplete_budget_exhausted"
    else:
        status = "completed"
    summary = {
        "status": status,
        "model_id": MODEL_ID,
        "suite_version": f"casefile-chat-subagent-{suite_version}",
        "trials": trials,
        "case_ids": [task.task_id for task in tasks],
        "requested_arms": list(arms),
        "completed_arms": completed_arms,
        "budget_limit_cny": str(budget_cny),
        "budget_charged_cny": str(budget.charged),
        "remaining_upper_budget_cny": str(budget_cny - budget.charged),
    }
    _atomic_json_write(output_dir / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--budget-cny", type=Decimal, required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--actor-id", type=int, default=1)
    parser.add_argument("--max-input-tokens-per-trial", type=int, required=True)
    parser.add_argument("--max-output-tokens-per-trial", type=int, required=True)
    parser.add_argument("--case-ids", default="")
    parser.add_argument("--arms", default="baseline,matched_single,subagents")
    parser.add_argument("--trials", type=int, default=TRIALS)
    parser.add_argument("--message-override", default=None)
    parser.add_argument("--component-smoke", action="store_true")
    args = parser.parse_args()
    prices = load_prices(args.prices)
    saved = _saved_provider_credential(
        database_url=args.database_url,
        actor_id=args.actor_id,
        provider_name="deepseek",
        requested_model=MODEL_ID,
    )
    if saved is None or saved[1] != MODEL_ID:
        raise SystemExit("saved deepseek-flash credential is required")
    case_ids = tuple(value.strip() for value in args.case_ids.split(",") if value.strip())
    if args.component_smoke:
        if len(case_ids) != 1:
            raise SystemExit("--component-smoke requires exactly one --case-ids value")
        summary = run_component_smoke(
            args.output_dir / "component-smoke.json",
            api_key=saved[0],
            prices=prices,
            budget_cny=args.budget_cny,
            max_input_tokens=args.max_input_tokens_per_trial,
            max_output_tokens=args.max_output_tokens_per_trial,
            case_id=case_ids[0],
        )
    else:
        summary = run(
            args.output_dir,
            api_key=saved[0],
            prices=prices,
            budget_cny=args.budget_cny,
            max_input_tokens_per_trial=args.max_input_tokens_per_trial,
            max_output_tokens_per_trial=args.max_output_tokens_per_trial,
            case_ids=case_ids,
            arms=tuple(value.strip() for value in args.arms.split(",") if value.strip()),
            trials=args.trials,
            message_override=args.message_override,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
