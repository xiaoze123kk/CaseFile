"""Phase 4 Step 4.1 tests: lifecycle metadata, dashboard, and runtime guardrails."""

from __future__ import annotations

import pytest
from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_V2_VERSION,
    ContextBlock,
    ContextDashboard,
    ContextEngineError,
    build_chat_context_manifest,
    build_context_dashboard,
    chat_input_payload_from_assembly,
    load_context_policy,
    thread_memory_state_to_jsonable,
)
from casefile.agent_runtime.context.dashboard import dashboard_guardrail_decisions
from casefile.agent_runtime.context.models import ContextAssembly
from casefile.agent_runtime.context.thread_memory import ChatThreadMemoryState


def _v2_frozen_input(history_length: int = 4) -> dict[str, object]:
    history = [
        {"role": "user", "content": f"第 {index} 轮问题，必须保留这个约束。"}
        if index % 2 == 0
        else {"role": "assistant", "content": f"第 {index} 轮回答。"}
        for index in range(1, history_length + 1)
    ]
    return {
        "casefile": {
            "entities": [
                {"id": "ent_1", "name": "张三", "description": "研究员。"}
            ],
            "events": [],
        },
        "history": history,
        "message": "继续核对。",
        "focus": {"object_ids": [], "event_ids": [], "validation_issue_ids": []},
        "validation": {"issues": []},
        "context_policy_version": CHAT_CONTEXT_POLICY_V2_VERSION,
    }


def _v2_state() -> dict[str, object]:
    return thread_memory_state_to_jsonable(
        ChatThreadMemoryState(
            constraints=["必须逐字保留该约束。"],
            last_compacted_message_seq=2,
            evidence_refs=["thread://7/message/2"],
        )
    )


def test_blocks_carry_lifecycle_metadata_in_manifest_and_dashboard() -> None:
    result = build_chat_context_manifest(
        policy_version=CHAT_CONTEXT_POLICY_V2_VERSION,
        frozen_input=_v2_frozen_input(),
        input_hash="a" * 64,
        extra_input={"thread_memory_state": _v2_state()},
    )
    summaries = {block.id: block for block in result.manifest.blocks}
    assert summaries["thread_history"].age_turns == 1
    assert summaries["thread_history"].last_access_turn == 4
    assert summaries["thread_memory"].age_turns == 2
    assert summaries["casefile_skeleton"].age_turns == 0
    dashboard = result.dashboard
    assert dashboard["used_tokens"] > 0
    assert dashboard["budget_tokens"] == 100000
    assert dashboard["remaining_tokens"] == 100000 - dashboard["used_tokens"]
    assert dashboard["largest_block"]["id"]
    assert "thread_memory" in dashboard["protected_blocks"]
    assert "thread_history" in dashboard["protected_blocks"]
    assert dashboard["recoverable_evidence_ids"] == ["thread://7/message/2"]
    assert dashboard["guardrail_violations"] == []


def test_render_can_embed_read_only_dashboard_for_v2() -> None:
    result = build_chat_context_manifest(
        policy_version=CHAT_CONTEXT_POLICY_V2_VERSION,
        frozen_input=_v2_frozen_input(),
        input_hash="a" * 64,
        extra_input={"thread_memory_state": _v2_state()},
    )
    payload = chat_input_payload_from_assembly(
        result.assembly,
        require_thread_memory=True,
        dashboard=result.dashboard,
    )
    assert payload["context_dashboard"]["hard_input_tokens"] is None
    assert payload["context_dashboard"]["protected_blocks"]
    without = chat_input_payload_from_assembly(
        result.assembly,
        require_thread_memory=True,
    )
    assert "context_dashboard" not in without


def test_runtime_hard_input_cap_is_enforced_and_cannot_be_relaxed() -> None:
    with pytest.raises(ContextEngineError, match="hard input cap exceeded"):
        build_chat_context_manifest(
            policy_version=CHAT_CONTEXT_POLICY_V2_VERSION,
            frozen_input=_v2_frozen_input(),
            input_hash="a" * 64,
            extra_input={"thread_memory_state": _v2_state()},
            hard_input_tokens=10,
        )


def test_dashboard_detects_guardrail_violations() -> None:
    policy = load_context_policy(CHAT_CONTEXT_POLICY_V2_VERSION)
    assembly = ContextAssembly(
        policy_version=policy.version,
        stage_versions=(),
        blocks=(
            ContextBlock(
                id="archived_tool_result",
                kind="tool_result",
                payload={"text": "旧结果。"},
                tokens=20,
                status="archived",
                recoverable=False,
            ),
            ContextBlock(
                id="pinned",
                kind="constraint",
                payload="必须保留。",
                tokens=5,
                trimmable=True,
                metadata={"protected": True},
            ),
        ),
    )
    dashboard = build_context_dashboard(assembly, policy)
    assert dashboard.hard_cap_exceeded is False
    codes = {
        violation["reason_code"] for violation in dashboard.guardrail_violations
    }
    assert "archived_block_not_recoverable" in codes
    assert "pinned_block_trimming_allowed" in codes
    decisions = dashboard_guardrail_decisions(dashboard)
    assert len(decisions) == 2
    assert decisions[0].code in {
        "archived_block_not_recoverable",
        "pinned_block_trimming_allowed",
    }


def test_dashboard_jsonable_is_serializable_and_read_only() -> None:
    dashboard = ContextDashboard(
        used_tokens=10,
        budget_tokens=100,
        remaining_tokens=90,
        hard_input_tokens=128000,
        largest_block={"id": "thread_history", "kind": "history_window", "tokens": 7},
        protected_blocks=("thread_history",),
        recoverable_evidence_ids=("thread://1/message/2",),
        policy_guardrails={"pinned_immutable": True},
    )
    payload = dashboard.to_jsonable()
    assert payload["remaining_tokens"] == 90
    assert payload["protected_blocks"] == ["thread_history"]
    assert payload["hard_cap_exceeded"] is False
