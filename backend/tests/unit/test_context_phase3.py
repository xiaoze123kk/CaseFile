"""Phase 3 rolling Thread Memory contract, compactor, policy, and render tests."""

from __future__ import annotations

import pytest
from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_V2_VERSION,
    CHAT_CONTEXT_PROMPT_V2_VERSION,
    ChatThreadMemoryState,
    ThreadCompactionInputV1,
    ThreadMemoryCompactorV1,
    ThreadMemoryDecision,
    ThreadMemoryDelta,
    ThreadMemoryStage,
    ThreadMemoryVerifiedFact,
    build_chat_context_manifest,
    chat_input_payload_from_assembly,
    empty_thread_memory_state,
    known_context_policy_versions,
    load_context_policy,
    preservation_errors,
    thread_memory_input_hash,
    thread_memory_state_from_jsonable,
    thread_memory_state_to_jsonable,
)
from casefile.agent_runtime.context.models import ContextAssembly
from casefile.agent_runtime.context.thread_memory import (
    ThreadCompactionRequest,
    register_compactor,
)
from casefile.agent_runtime.providers import FakeProvider
from pydantic import ValidationError


def _decision(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "decision": "accepted",
        "object_id": "ent_1",
        "field_path": "/description",
        "reason": "作者确认采用。",
        "patch_set_id": 7,
        "thread_ref": "thread://3/message/12",
    }
    payload.update(overrides)
    return payload


def test_thread_memory_state_schema_rejects_invalid_facts_and_pointers() -> None:
    with pytest.raises(ValidationError):
        ThreadMemoryDelta.model_validate(
            {
                "verified_facts": [{"fact": "x", "source_message_id": 0}],
            }
        )
    with pytest.raises(ValidationError):
        ThreadMemoryDelta.model_validate({"evidence_refs": ["not a pointer"]})
    with pytest.raises(ValidationError):
        ThreadMemoryDecision.model_validate(_decision(thread_ref="not a pointer"))


def test_compactor_build_input_is_deterministic_and_typed() -> None:
    compactor = ThreadMemoryCompactorV1()
    old_state = ChatThreadMemoryState(
        constraints=["必须保留原有人物动机。"],
        verified_facts=[
            ThreadMemoryVerifiedFact(fact="林研究员在场。", source_message_id=2),
        ],
        last_compacted_message_seq=2,
    )
    turns = [
        {"role": "user", "content": "补充一个证据。"},
        {"role": "assistant", "content": "已补充。"},
    ]
    first = compactor.build_input(
        old_state=old_state,
        new_turns=turns,
        db_decisions=[],
        from_message_seq=3,
        to_message_seq=4,
    )
    second = compactor.build_input(
        old_state=old_state,
        new_turns=turns,
        db_decisions=[],
        from_message_seq=3,
        to_message_seq=4,
    )
    assert first == second
    assert first["input_hash"] == thread_memory_input_hash(
        {key: value for key, value in first.items() if key != "input_hash"}
    )
    parsed = ThreadCompactionInputV1.model_validate(first)
    assert parsed.to_message_seq == 4
    assert parsed.old_state["constraints"][0] == "必须保留原有人物动机。"


def test_compactor_merge_carries_constraints_decisions_and_facts_forward() -> None:
    compactor = ThreadMemoryCompactorV1()
    old_state = ChatThreadMemoryState(
        constraints=["不得删除事件时间。"],
        decisions=[ThreadMemoryDecision.model_validate(_decision())],
        verified_facts=[
            ThreadMemoryVerifiedFact(fact="旧事实。", source_message_id=2),
        ],
        last_compacted_message_seq=2,
    )
    delta = ThreadMemoryDelta(
        constraints=["不得删除事件时间。"],
        verified_facts=[
            ThreadMemoryVerifiedFact(fact="更新后的旧事实。", source_message_id=2),
            ThreadMemoryVerifiedFact(fact="新事实。", source_message_id=4),
        ],
        evidence_refs=["taskrun://9"],
    )
    merged = compactor.merge(
        old_state,
        delta,
        db_decisions=[_decision(patch_set_id=8, decision="rejected")],
        last_compacted_message_seq=4,
    )
    assert merged.constraints == ["不得删除事件时间。"]
    assert [item.patch_set_id for item in merged.decisions] == [7, 8]
    assert merged.verified_facts[0].fact == "更新后的旧事实。"
    assert merged.verified_facts[1].source_message_id == 4
    assert merged.last_compacted_message_seq == 4
    assert preservation_errors(old_state, merged) == []
    assert compactor.validate(merged) == []


def test_compactor_validate_and_preservation_errors_are_explicit() -> None:
    compactor = ThreadMemoryCompactorV1()
    old_state = ChatThreadMemoryState(constraints=["保留这一条。"])
    bad_state = ChatThreadMemoryState(
        verified_facts=[
            ThreadMemoryVerifiedFact(fact="来源越界。", source_message_id=5),
        ],
        last_compacted_message_seq=4,
    )
    assert compactor.validate(bad_state) == [
        "verified fact source_message_id 5 exceeds last_compacted_message_seq 4"
    ]
    merged = compactor.merge(
        old_state,
        ThreadMemoryDelta(),
        last_compacted_message_seq=4,
    )
    assert preservation_errors(old_state, merged) == []


def test_compactor_registry_rejects_conflicting_versions() -> None:
    registry: dict[str, object] = {}
    register_compactor(ThreadMemoryCompactorV1(), registry)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        register_compactor(  # type: ignore[arg-type]
            ThreadMemoryCompactorV1(version="2"),
            registry,
        )


def test_policy_v2_orders_thread_memory_between_history_and_validation() -> None:
    assert CHAT_CONTEXT_POLICY_V2_VERSION in known_context_policy_versions()
    policy = load_context_policy(CHAT_CONTEXT_POLICY_V2_VERSION)
    strategy_ids = [stage.strategy for stage in policy.stages]
    assert strategy_ids == [
        "casefile_skeleton_v1",
        "focus_objects_v1",
        "history_window_v1",
        "thread_memory_v1",
        "validation_trim_v1",
        "chat_contract_v2",
    ]


def test_thread_memory_stage_emits_countable_block_and_manifest() -> None:
    state = ChatThreadMemoryState(
        constraints=["必须逐字保留。"],
        verified_facts=[
            ThreadMemoryVerifiedFact(fact="已核实。", source_message_id=3),
        ],
        last_compacted_message_seq=3,
    )
    frozen_input = {
        "casefile": {"entities": [], "events": []},
        "history": [
            {"role": "user", "content": "问"},
            {"role": "assistant", "content": "答"},
        ],
        "message": "继续。",
        "focus": {"object_ids": []},
        "validation": {"issues": []},
        "context_policy_version": CHAT_CONTEXT_POLICY_V2_VERSION,
    }
    result = build_chat_context_manifest(
        policy_version=CHAT_CONTEXT_POLICY_V2_VERSION,
        frozen_input=frozen_input,
        input_hash="a" * 64,
        extra_input={
            "thread_memory_state": thread_memory_state_to_jsonable(state),
        },
        provider="fake",
        model_id="fake",
    )
    assert result.fallback is None
    blocks = {block.id: block for block in result.assembly.blocks}
    assert "thread_memory" in blocks
    assert blocks["thread_memory"].payload["constraints"] == ["必须逐字保留。"]
    payload = chat_input_payload_from_assembly(
        result.assembly,
        require_thread_memory=True,
    )
    assert payload["thread_memory"]["last_compacted_message_seq"] == 3


def test_v2_assembly_render_requires_thread_memory_block() -> None:
    assembly = ContextAssembly(
        policy_version=CHAT_CONTEXT_POLICY_V2_VERSION,
        stage_versions=(),
        blocks=(),
    )
    with pytest.raises(ValueError, match="thread_memory"):
        chat_input_payload_from_assembly(assembly, require_thread_memory=True)


def test_state_json_roundtrip_is_lossless() -> None:
    state = ChatThreadMemoryState(
        constraints=["约束"],
        decisions=[ThreadMemoryDecision.model_validate(_decision())],
        last_compacted_message_seq=12,
    )
    roundtripped = thread_memory_state_from_jsonable(
        thread_memory_state_to_jsonable(state)
    )
    assert roundtripped == state
    assert empty_thread_memory_state().last_compacted_message_seq == 0


def test_fake_provider_compactor_is_deterministic_and_valid() -> None:
    provider = FakeProvider()
    events: list[dict[str, object]] = []
    request = ThreadCompactionRequest(
        task_run_id=1,
        prompt_version=CHAT_CONTEXT_PROMPT_V2_VERSION,
        input_hash="a" * 64,
        input_data={"old_state": {"topics": ["t1"]}, "new_turns": []},
        model_id="fake",
        api_key=None,
        network_retries=0,
        max_turns=1,
        emit=lambda event_type, stage, payload: events.append(
            {"event_type": event_type, "stage": stage, "payload": payload}
        ),
    )
    result = provider.compact_thread_memory(request)
    assert result.candidate == ThreadMemoryDelta(topics=["t1"])
    assert events[-1]["stage"] == "compacting"


def test_thread_memory_stage_can_run_only_with_state() -> None:
    stage = ThreadMemoryStage()
    assert stage.name == "thread_memory_v1"
    assert stage.version == "thread-memory-v1"
