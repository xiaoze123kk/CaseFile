"""Read-only CaseFile Chat subagent contracts and orchestration tests."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from casefile.agent_runtime.chat_subagents import (
    MAX_SUBAGENT_TOOL_CALLS,
    MAX_SUBAGENT_TURNS,
    SubagentOutput,
    normalize_subagent_output,
    subagent_prompt_hash,
    subagent_soft_gate_decision,
    validate_delegation_tasks,
)
from casefile.agent_runtime.chat_tools import (
    ChatToolContext,
    ChatToolMetrics,
    chat_subagent_manifest,
)
from casefile.agent_runtime.models import CaseFileChatRequest, RouteDecision
from casefile.agent_runtime.provider_adapters import shared
from casefile.agent_runtime.provider_adapters.protocols import ProviderProtocolError


def _request() -> CaseFileChatRequest:
    return CaseFileChatRequest(
        task_run_id=7,
        prompt_version="casefile-chat-v27",
        casefile={"entities": [{"id": "person:1", "name": "甲"}]},
        history=(),
        message="查证",
        editable_fields_by_collection={},
        input_hash="a" * 64,
        model_id="fake",
        api_key=None,
        max_turns=4,
        emit=lambda *_args: None,
        route=RouteDecision(execution_profile={"primary_intent": "analysis", "max_tool_calls": 12}),
    )


def _output(conclusion: str) -> SubagentOutput:
    return SubagentOutput.model_validate(
        {
            "status": "completed",
            "conclusion": conclusion,
            "verified_facts": [],
            "inferences": [],
            "findings": [],
            "coverage": [conclusion],
            "unresolved": [],
        }
    )


def test_subagent_prompts_have_stable_role_specific_hashes() -> None:
    assert len(subagent_prompt_hash("investigate")) == 64
    assert len(subagent_prompt_hash("audit")) == 64
    assert subagent_prompt_hash("investigate") != subagent_prompt_hash("audit")


def test_soft_gate_only_selects_analysis_and_audit_routes() -> None:
    assert subagent_soft_gate_decision(primary_intent="question", message="查一下标题") is None
    assert (
        subagent_soft_gate_decision(
            primary_intent="question",
            message="统计这个大卷宗的对象数量",
            complexity="high",
        )
        is None
    )
    assert (
        subagent_soft_gate_decision(
            primary_intent="question",
            message="把所有与欠压有关的对象列出来",
            complexity="high",
        )
        is None
    )
    for intent in ("analysis", "logic_audit", "explain_issue"):
        assert (
            subagent_soft_gate_decision(
                primary_intent=intent,
                message="分别核对证据链、证词与角色认知",
                complexity="high",
                multi_step=True,
            )
            is None
        )


def test_subagent_tool_surface_is_read_only_and_has_no_recursive_delegation() -> None:
    investigate = {tool.name for tool in chat_subagent_manifest("investigate")}
    audit = {tool.name for tool in chat_subagent_manifest("audit")}

    assert investigate == {
        "get_casefile_object",
        "get_related_objects",
        "get_character_knowledge",
    }
    assert audit == {*investigate, "get_validation_issues"}
    assert (
        not {
            "investigate_case",
            "audit_case",
            "simulate_patch_application",
            "validate_patch_proposal",
            "retrieve_thread_evidence",
            "request_thread_compaction",
        }
        & audit
    )


def test_subagent_reference_validation_rejects_unread_ids() -> None:
    request = _request()
    context = ChatToolContext(request=request, route=request.route)
    output = SubagentOutput.model_validate(
        {
            "status": "completed",
            "conclusion": "发现事实",
            "verified_facts": [{"statement": "甲在场", "evidence_ids": ["event:invented"]}],
        }
    )

    with pytest.raises(ProviderProtocolError, match="unbound evidence references"):
        shared._validate_subagent_references(output, context)


@pytest.mark.parametrize(
    "object_ids,gap", [([], "查证"), (["unknown"], "查证"), (["person:1"], " ")]
)
def test_delegation_requires_known_anchors_and_a_specific_gap(
    object_ids: list[str], gap: str
) -> None:
    assert (
        validate_delegation_tasks(({"object_ids": object_ids, "evidence_gap": gap},), {"person:1"})
        == "subagent_requires_located_objects_and_evidence_gap"
    )


def test_duplicate_tasks_are_rejected_but_different_gaps_can_share_an_anchor() -> None:
    task = {"object_ids": ["person:1"], "evidence_gap": "是否在场"}
    assert validate_delegation_tasks((task, task), {"person:1"}) == "subagent_duplicate_task"
    assert (
        validate_delegation_tasks((task, {**task, "evidence_gap": "是否知情"}), {"person:1"})
        is None
    )


@pytest.mark.parametrize("kind", ["fact", "conflict"])
def test_unbound_authoritative_claims_are_downgraded_to_partial(kind: str) -> None:
    output = _output("局部结果")
    payload = output.model_dump()
    if kind == "fact":
        payload["verified_facts"] = [{"statement": "甲在场", "evidence_ids": []}]
    else:
        payload["findings"] = [
            {
                "classification": "confirmed_conflict",
                "statement": "时间冲突",
                "evidence_ids": [],
                "impact": "无法同时成立",
            }
        ]
    normalized = normalize_subagent_output(SubagentOutput.model_validate(payload))
    assert normalized.status == "partial"
    assert not normalized.verified_facts
    assert not normalized.findings
    assert normalized.unresolved


def test_unknown_references_are_removed_after_protocol_repair_fallback() -> None:
    request = _request()
    context = ChatToolContext(request=request, route=request.route)
    context.metrics.retrieved_object_ids.append("person:1")
    output = SubagentOutput.model_validate(
        {
            "status": "completed",
            "conclusion": "甲在场",
            "verified_facts": [
                {
                    "statement": "甲在场",
                    "evidence_ids": ["person:1", "event:unknown"],
                }
            ],
        }
    )

    normalized = shared._sanitize_subagent_references(output, context)

    assert normalized.status == "partial"
    assert normalized.verified_facts[0].evidence_ids == ["person:1"]
    assert normalized.unresolved == ["已移除未绑定引用：event:unknown"]


def test_subagent_budget_adds_one_turn_and_two_hard_guard_calls() -> None:
    assert MAX_SUBAGENT_TURNS == 5
    assert MAX_SUBAGENT_TOOL_CALLS == 8


def test_batch_returns_frozen_source_records_alongside_child_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(*_args: Any, **_kwargs: Any) -> Any:
        output = SubagentOutput.model_validate(
            {
                "status": "partial",
                "conclusion": "仍需核对时间",
                "verified_facts": [{"statement": "角色名为甲", "evidence_ids": ["person:1"]}],
                "unresolved": ["事件时间未读取"],
            }
        )
        return output, {}, ChatToolMetrics(), SimpleNamespace(ledger_hash="ledger")

    monkeypatch.setattr(shared, "_run_one_chat_subagent", fake_run)
    result = asyncio.run(
        shared._run_chat_subagent_batch(
            _request(),
            role="investigate",
            tasks=({"question": "甲是谁"},),
            model=SimpleNamespace(),
            model_settings=SimpleNamespace(),
            tracing_disabled=True,
        )
    )
    assert result["tasks"][0]["source_records"] == [{"id": "person:1", "name": "甲"}]
    assert result["tasks"][0]["status"] == "partial"
    assert result["tasks"][0]["unresolved"] == ["事件时间未读取"]


def test_child_receives_frozen_anchors_without_spending_lookup_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(_agent: Any, input_text: str, **kwargs: Any) -> Any:
        payload = json.loads(input_text)
        assert payload["task"]["source_records"] == [{"id": "person:1", "name": "甲"}]
        assert payload["execution_limits"] == {
            "max_turns": 5,
            "hard_max_tool_calls": 8,
            "finalize_after_tool_calls": 6,
        }
        assert "casefile_skeleton" not in payload
        assert "validation_issue_count" not in payload
        assert "focus" not in payload
        assert kwargs["context"].metrics.calls == 0
        return SimpleNamespace(
            context_wrapper=SimpleNamespace(usage=SimpleNamespace()),
            final_output=json.dumps(
                {
                    "status": "completed",
                    "conclusion": "角色为甲",
                    "verified_facts": [{"statement": "角色为甲", "evidence_ids": ["person:1"]}],
                }
            ),
        )

    monkeypatch.setattr(shared.Runner, "run", fake_run)
    output, _, metrics, _ = asyncio.run(
        shared._run_one_chat_subagent(
            _request(),
            role="investigate",
            ordinal=1,
            task={"object_ids": ["person:1"], "source_records": [{"id": "forged"}]},
            model="fake",  # type: ignore[arg-type]
            model_settings=shared.ModelSettings(),
            tracing_disabled=True,
        )
    )
    assert output.status == "completed"
    assert metrics.retrieved_object_ids == ["person:1"]
    assert metrics.calls == 0


def test_subagent_batch_overlaps_execution_and_returns_input_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    peak = 0

    async def fake_run(
        _request: CaseFileChatRequest,
        *,
        task: dict[str, Any],
        ordinal: int,
        **_kwargs: Any,
    ) -> tuple[SubagentOutput, dict[str, Any], ChatToolMetrics, Any]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01 if ordinal == 1 else 0)
        active -= 1
        return (
            _output(str(task["question"])),
            {"input_tokens": ordinal, "output_tokens": 1},
            ChatToolMetrics(calls=1, valid_calls=1, successful_calls=1),
            SimpleNamespace(ledger_hash=f"ledger-{ordinal}"),
        )

    monkeypatch.setattr(shared, "_run_one_chat_subagent", fake_run)
    result = asyncio.run(
        shared._run_chat_subagent_batch(
            _request(),
            role="investigate",
            tasks=({"question": "第一项"}, {"question": "第二项"}),
            model=SimpleNamespace(),  # type: ignore[arg-type]
            model_settings=SimpleNamespace(),  # type: ignore[arg-type]
            tracing_disabled=True,
        )
    )

    assert peak == 2
    assert [item["conclusion"] for item in result["tasks"]] == ["第一项", "第二项"]
    assert [item["ledger_hash"] for item in result["tasks"]] == ["ledger-1", "ledger-2"]


def test_subagent_batch_keeps_failed_usage_metrics_and_error_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metrics = ChatToolMetrics(calls=2, valid_calls=1, successful_calls=1)

    async def fake_run(*_args: Any, **_kwargs: Any) -> Any:
        raise shared._ChatSubagentExecutionFailure(
            ProviderProtocolError("invalid child payload"),
            {"requests": 1, "input_tokens": 12, "output_tokens": 3},
            metrics,
        )

    monkeypatch.setattr(shared, "_run_one_chat_subagent", fake_run)
    result = asyncio.run(
        shared._run_chat_subagent_batch(
            _request(),
            role="investigate",
            tasks=({"question": "失败项"},),
            model=SimpleNamespace(),  # type: ignore[arg-type]
            model_settings=SimpleNamespace(),  # type: ignore[arg-type]
            tracing_disabled=True,
        )
    )

    assert result["tasks"][0]["unresolved"] == ["ProviderProtocolError: invalid child payload"]
    assert result["_usage_records"] == [{"requests": 1, "input_tokens": 12, "output_tokens": 3}]
    assert result["_metrics"]["calls"] == 2
    assert result["_metrics"]["successful_calls"] == 1
