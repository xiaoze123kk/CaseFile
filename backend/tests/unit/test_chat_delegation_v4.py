"""V4 production entry, scope isolation, reserved finalization and usage accounting."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from agents import ModelSettings
from agents.exceptions import MaxTurnsExceeded
from pydantic import ValidationError

from casefile.agent_runtime.chat_delegation import (
    TOOLSET_VERSION,
    DelegationPlan,
    orientation_records,
    runtime_manifest,
    scoped_document,
)
from casefile.agent_runtime.chat_tools import ChatToolContext, chat_tool_manifest
from casefile.agent_runtime.models import CaseFileChatRequest, RouteDecision
from casefile.agent_runtime.provider_adapters import chat_delegation as adapter
from casefile.agent_runtime.provider_adapters import shared
from casefile.agent_runtime.provider_adapters.chat_delegation import run_child
from casefile.benchmark.chat_subagent_live_eval import _transform


def request() -> CaseFileChatRequest:
    return CaseFileChatRequest(
        task_run_id=1,
        prompt_version="casefile-chat-v27",
        toolset_version=TOOLSET_VERSION,
        casefile={
            "entities": [
                {"id": "a", "name": "甲", "related_ref": "b"},
                {"id": "b", "name": "乙", "related_ref": "c"},
                {"id": "c", "name": "丙"},
                {"id": "unrelated", "name": "不相关人物"},
            ]
        },
        history=(),
        message="核对人物关系与证据",
        editable_fields_by_collection={},
        input_hash="a" * 64,
        model_id="fake",
        api_key=None,
        max_turns=5,
        emit=lambda *_: None,
        route=RouteDecision(
            execution_profile={
                "primary_intent": "analysis",
                "max_tool_calls": 12,
                "toolset": ["get_casefile_object"],
            }
        ),
    )


def task() -> dict[str, Any]:
    return {
        "question": "甲与乙的关系是否有记录",
        "scope": "甲的一跳关系",
        "object_ids": ["a"],
        "evidence_gap": "乙的记录",
        "completion_criteria": "读取甲乙并核对引用",
        "independence_reason": "局部关系可独立核对",
    }


def result(value: Any, tokens: int = 10) -> Any:
    return SimpleNamespace(
        final_output=value,
        context_wrapper=SimpleNamespace(
            usage=SimpleNamespace(
                requests=1,
                input_tokens=tokens,
                output_tokens=2,
                total_tokens=tokens + 2,
            )
        ),
    )


def output() -> str:
    return json.dumps(
        {
            "status": "completed",
            "conclusion": "甲有记录",
            "verified_facts": [{"statement": "甲有记录", "evidence_ids": ["a"]}],
        }
    )


def test_one_hop_scope_excludes_recursive_and_unrelated_records() -> None:
    document, ids, truncated = scoped_document(request().casefile, ["a"])
    assert ids == ["a", "b"]
    assert not truncated
    assert [row["id"] for row in document["entities"]] == ids


def test_plan_requires_tasks_and_explicit_completion_criteria() -> None:
    with pytest.raises(ValidationError):
        DelegationPlan(decision="delegate", reason="复杂", tasks=[])
    invalid = task()
    del invalid["completion_criteria"]
    with pytest.raises(ValidationError):
        DelegationPlan.model_validate(
            {"decision": "delegate", "reason": "复杂", "tasks": [invalid]}
        )
    assert len(runtime_manifest()["skill_sha256"]) == 64


def test_v4_production_entry_plans_dispatches_and_charges_all_phases(monkeypatch: Any) -> None:
    req = _transform("subagents_v4", request())
    assert req.toolset_version == TOOLSET_VERSION
    context = ChatToolContext(request=req, route=req.route)
    seen: list[str] = []

    async def fake_run(agent: Any, *_args: Any, **kwargs: Any) -> Any:
        seen.append(agent.name)
        if agent.name == "CaseFile Delegation Planner":
            return result(
                json.dumps({"decision": "delegate", "reason": "独立缺口", "tasks": [task()]})
            )
        if agent.name == "CaseFile Scoped Investigator":
            child = kwargs["context"]
            assert {r["id"] for r in child.request.casefile["entities"]} == {"a", "b"}
            assert kwargs["max_turns"] == 5
            return result("甲有记录")
        if agent.name == "CaseFile Subagent Finalizer":
            assert not agent.tools and kwargs["max_turns"] == 1
            return result(output())
        assert agent.name == "CaseFile Evidence Agent"
        assert "source_records" in _args[0]
        return result("原始记录 a 核对完成")

    monkeypatch.setattr(shared.Runner, "run", fake_run)
    # Use the exact imported alias called by the real DeepSeek adapter.
    from casefile.agent_runtime.provider_adapters.deepseek import _run_chat_tool_agent

    manifest = chat_tool_manifest(req.route, toolset_version=TOOLSET_VERSION)
    assert not {tool.name for tool in manifest} & {"investigate_case", "audit_case"}
    ledger, usage = asyncio.run(
        _run_chat_tool_agent(
            req,
            model="fake",
            model_settings=ModelSettings(),
            instructions="查证",
            input_text="输入",
            tools=manifest,
            context=context,
            max_turns=5,
            tracing_disabled=True,
        )
    )
    assert len(seen) == 4
    assert usage["input_tokens"] == 40
    assert context.metrics.subagent_completed == 1
    assert "a" in ledger.retrieved_object_ids


def test_turn_limit_preserves_usage_and_still_finalizes(monkeypatch: Any) -> None:
    calls = []

    async def fake_run(agent: Any, *_args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs["max_turns"])
        if len(calls) == 1:
            error = MaxTurnsExceeded("limit")
            error.run_data = SimpleNamespace(context_wrapper=result("", 40).context_wrapper)
            raise error
        return result(output(), 20)

    monkeypatch.setattr(shared.Runner, "run", fake_run)
    completed, usage, _, _ = asyncio.run(
        run_child(
            request(),
            role="investigate",
            task=task(),
            ordinal=1,
            model="fake",
            model_settings=ModelSettings(),
            tracing_disabled=True,
            max_tool_calls=10,
        )
    )
    assert calls == [5, 1]
    assert usage["input_tokens"] == 60
    assert completed.status == "completed"


def test_invalid_citation_drops_unsupported_conclusion(monkeypatch: Any) -> None:
    async def fake_run(agent: Any, *_args: Any, **_kwargs: Any) -> Any:
        if agent.name == "CaseFile Subagent Finalizer":
            return result(output().replace('"a"', '"invented"'))
        return result("笔记")

    monkeypatch.setattr(shared.Runner, "run", fake_run)
    completed, _, _, _ = asyncio.run(
        run_child(
            request(),
            role="investigate",
            task=task(),
            ordinal=1,
            model="fake",
            model_settings=ModelSettings(),
            tracing_disabled=True,
            max_tool_calls=10,
        )
    )
    assert completed.status == "partial"
    assert completed.verified_facts == []
    assert completed.conclusion != "甲有记录"


def test_explicit_ids_and_focus_precede_similar_history() -> None:
    history = [{"id": f"old-{i}", "title": "核对目标时间证据历史"} for i in range(30)]
    document = {"events": history + [{"id": "event-1"}, {"id": "event-10"}, {"id": "focus"}]}
    records = orientation_records(document, "核对目标时间证据event-10", {"event_ids": ["focus"]})
    assert [record["id"] for record in records[:2]] == ["event-10", "focus"]
    assert len(records) == 16


def test_investigation_07_locates_target_and_direct_evidence() -> None:
    from casefile.benchmark.chat_subagent_suite_v3 import build_suite_tasks

    case = next(case for case in build_suite_tasks() if case.task_id == "investigation-07")
    records = orientation_records(case.casefile, case.message, case.focus)
    ids = [record["id"] for record in records]
    assert ids[0] == "evt_c07_1"
    assert set(case.expectations.expected_object_ids) <= set(ids)
    assert max(ids.index(key) for key in case.expectations.expected_object_ids) < 6


@pytest.mark.parametrize("failure", ["schema", "unknown", "repeat"])
def test_invalid_plan_gets_one_diagnostic_repair(monkeypatch: Any, failure: str) -> None:
    events: list[Any] = []
    req = replace(request(), emit=lambda *args: events.append(args))
    context = ChatToolContext(request=req, route=req.route)
    calls: list[Any] = []
    dispatched: list[Any] = []
    invalid_task = {**task(), "object_ids": ["invented"]}
    invalid = (
        "{}"
        if failure == "schema"
        else json.dumps({"decision": "delegate", "reason": "查证", "tasks": [invalid_task]})
    )

    async def fake_run(agent: Any, payload: str, **kwargs: Any) -> Any:
        calls.append(json.loads(payload))
        if len(calls) == 1:
            return result(invalid)
        assert not agent.tools and kwargs["max_turns"] == 1
        feedback = calls[-1]["validation_feedback"]
        if failure == "schema":
            assert feedback["code"] == "invalid_plan_schema"
            assert feedback["errors"][0]["loc"]
        else:
            assert feedback["unknown_object_ids"] == ["invented"]
        return result(
            invalid
            if failure == "repeat"
            else json.dumps({"decision": "delegate", "reason": "已修正", "tasks": [task()]})
        )

    async def batch(_context: Any, **kwargs: Any) -> str:
        dispatched.append(kwargs)
        return "查证结果"

    monkeypatch.setattr(adapter.Runner, "run", fake_run)
    monkeypatch.setattr(adapter, "run_subagent_batch", batch)
    asyncio.run(
        adapter.plan_and_delegate(
            context, model="fake", model_settings=ModelSettings(), tracing_disabled=True
        )
    )
    assert len(calls) == 2
    assert len(context.subagent_usage_records) == 2
    assert len(dispatched) == (0 if failure == "repeat" else 1)
    assert any(event[2].get("diagnostic") for event in events)


def test_real_anchor_outside_orientation_is_bound_before_dispatch(monkeypatch: Any) -> None:
    req = request()
    context = ChatToolContext(request=req, route=req.route)
    monkeypatch.setattr(adapter, "orientation_records", lambda *_: [{"id": "unrelated"}])

    async def fake_run(*_args: Any, **_kwargs: Any) -> Any:
        return result(json.dumps({"decision": "delegate", "reason": "查证", "tasks": [task()]}))

    async def batch(ctx: Any, **_kwargs: Any) -> str:
        assert "a" in ctx.metrics.retrieved_object_ids
        assert ctx.recent_tool_results[-1]["payload"]["source_records"][0]["id"] == "a"
        return "ok"

    monkeypatch.setattr(adapter.Runner, "run", fake_run)
    monkeypatch.setattr(adapter, "run_subagent_batch", batch)
    assert "ok" in asyncio.run(
        adapter.plan_and_delegate(
            context, model="fake", model_settings=ModelSettings(), tracing_disabled=True
        )
    )


def test_planner_reads_missing_anchor_with_bounded_budget(monkeypatch: Any) -> None:
    from agents import RunContextWrapper

    req = request()
    context = ChatToolContext(request=req, route=req.route)

    async def fake_run(agent: Any, *_args: Any, **_kwargs: Any) -> Any:
        tool = agent.tools[0]
        wrapper = RunContextWrapper(context=None)
        for _ in range(6):
            read = json.loads(await tool.on_invoke_tool(wrapper, '{"object_id":"c"}'))
            assert read["source_records"][0]["id"] == "c"
        read = json.loads(await tool.on_invoke_tool(wrapper, '{"object_id":"a"}'))
        assert read["error"] == "delegation_read_budget_exhausted"
        return result('{"decision":"single","reason":"单次读取可解决","tasks":[]}')

    monkeypatch.setattr(adapter.Runner, "run", fake_run)
    asyncio.run(
        adapter.plan_and_delegate(
            context, model="fake", model_settings=ModelSettings(), tracing_disabled=True
        )
    )
    assert context.metrics.subagent_soft_gate_triggered == 0
