"""Phase 2 deterministic context engineering tests."""

from __future__ import annotations

import json
from dataclasses import replace

from casefile.agent_runtime.chat_tools import bounded_tool_result_json, fold_tool_results
from casefile.agent_runtime.context import (
    CHAT_CONTEXT_POLICY_VERSION,
    build_casefile_skeleton,
    build_chat_context_manifest,
    build_focus_objects_payload,
    chat_input_payload_from_assembly,
    select_history_window,
    trim_validation_issues,
)
from casefile.agent_runtime.models import (
    CaseFileChatRequest,
    ChatExecutorInputV2,
    RouteDecision,
    chat_routing_payload_as_dict,
)
from casefile.agent_runtime.prompt import render_chat_executor_prompt


def _casefile(record_count: int = 20) -> dict[str, object]:
    entities = [
        {
            "id": f"object:person_{index}",
            "type": "person",
            "name": f"人物{index}",
            "description": f"这是人物{index}的完整描述，" * 12,
        }
        for index in range(record_count)
    ]
    relationships = [
        {
            "id": f"rel_{index}",
            "from_ref": f"object:person_{index}",
            "to_ref": f"object:person_{index + 1}",
            "relationship_type": "acquaintance",
            "description": "彼此认识",
        }
        for index in range(max(0, record_count - 1))
    ]
    return {
        "entities": entities,
        "relationships": relationships,
        "events": [
            {
                "id": "event:fire",
                "title": "仓库失火",
                "description": "2026-07-02 夜间三号库区失火。",
            }
        ],
    }


def _frozen_input() -> dict[str, object]:
    issues = [
        {
            "issue_id": f"issue:{index}",
            "rule_id": "temporal_exclusivity_violation",
            "severity": "S1",
            "title": f"时间冲突 {index}",
            "message": f"事件时间重叠的完整说明 {index}。" + ("详细补充内容。" * 40),
            "object_refs": ["event:fire"],
        }
        for index in range(8)
    ]
    return {
        "casefile": _casefile(),
        "history": [
            message
            for index in range(10)
            for message in (
                {"role": "user", "content": f"第{index}轮：请记住这个约束"},
                {"role": "assistant", "content": f"已记住第{index}轮约束"},
            )
        ],
        "message": "请核对 issue:1 涉及的人物。",
        "focus": {
            "object_ids": ["object:person_0"],
            "event_ids": ["event:fire"],
            "validation_issue_ids": ["issue:1"],
        },
        "validation": {
            "status": "failed",
            "validator": "casefile.contracts.validate_casefile",
            "schema_version": "1.0",
            "issue_count": 8,
            "issues": issues,
        },
        "context_policy_version": CHAT_CONTEXT_POLICY_VERSION,
    }


def _request() -> CaseFileChatRequest:
    frozen = _frozen_input()
    return CaseFileChatRequest(
        task_run_id=1,
        prompt_version="casefile-chat-v4",
        casefile=frozen["casefile"],  # type: ignore[arg-type]
        history=tuple(frozen["history"]),  # type: ignore[arg-type]
        message=str(frozen["message"]),
        editable_fields_by_collection={"entities": ("description",)},
        input_hash="h" * 64,
        model_id="gpt-5.6-sol",
        api_key="key",
        max_turns=6,
        emit=lambda _event_type, _stage, _payload: None,
        validation_issues=tuple(frozen["validation"]["issues"]),  # type: ignore[arg-type]
        validation=frozen["validation"],  # type: ignore[arg-type]
        focus=frozen["focus"],  # type: ignore[arg-type]
        route=RouteDecision(
            execution_profile={"profile": "question", "prompt_component": "chat"}
        ),
        context_policy_version=CHAT_CONTEXT_POLICY_VERSION,
        toolset_version="casefile-chat-tools-v2",
    )


def test_casefile_skeleton_drops_full_text_and_keeps_counts() -> None:
    skeleton = build_casefile_skeleton(_casefile(record_count=5))  # type: ignore[arg-type]
    assert skeleton["collection_counts"]["entities"] == 5
    assert skeleton["collection_counts"]["relationships"] == 4
    records = {record["id"]: record for record in skeleton["records"]}
    entity = records["object:person_0"]
    assert entity["collection"] == "entities"
    assert entity["label"] == "人物0"
    assert "description" not in entity


def test_focus_payload_expands_full_objects_and_summarizes_neighbors() -> None:
    payload = build_focus_objects_payload(
        _casefile(record_count=8),  # type: ignore[arg-type]
        {"object_ids": ["object:person_0", "missing:object"], "event_ids": ["event:fire"]},
    )
    assert len(payload["objects"]) == 2
    assert payload["objects"][0]["object"]["description"].startswith("这是人物0")
    assert payload["neighbors"]
    assert payload["neighbors"][0]["id"] == "object:person_1"
    assert "description" not in payload["neighbors"][0]
    assert payload["unresolved_refs"] == ["missing:object"]


def test_history_window_pins_anchor_and_constraint_messages() -> None:
    history = [
        {"role": "user", "content": "这是线程锚点"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "请务必保留这条约束"},
        {"role": "assistant", "content": "保留"},
    ]
    history.extend(
        message
        for index in range(6)
        for message in (
            {"role": "user", "content": f"普通消息{index}"},
            {"role": "assistant", "content": f"收到{index}"},
        )
    )
    selected = select_history_window(history, max_messages=4)
    assert selected["dropped_count"] == 10
    assert [message["content"] for message in selected["thread_history"]] == [
        "这是线程锚点",
        "请务必保留这条约束",
        "普通消息4",
        "收到4",
        "普通消息5",
        "收到5",
    ]


def test_validation_trim_keeps_focus_and_mention_full_and_compacts_rest() -> None:
    issues = [
        {
            "issue_id": "issue:focus",
            "message": "焦点问题" + ("长" * 500),
        },
        {
            "issue_id": "issue:mention",
            "message": "提及问题" + ("长" * 500),
        },
        {
            "issue_id": "issue:other",
            "message": "其他问题" + ("长" * 500),
        },
    ]
    trimmed = trim_validation_issues(
        issues,
        focus_issue_ids=["issue:focus"],
        author_message="请解释 issue:mention 的原因",
        max_message_chars=200,
    )
    assert trimmed["issues"][0]["message"].endswith("长")
    assert trimmed["issues"][1]["message"].endswith("长")
    assert len(trimmed["issues"][2]["message"]) == 200
    assert trimmed["compacted_count"] == 1


def test_validation_trim_gate_keeps_full_snapshot() -> None:
    issues = [{"issue_id": "issue:1", "message": "全量消息"}]
    trimmed = trim_validation_issues(
        issues,
        focus_issue_ids=[],
        author_message="导出前门禁检查",
        gate=True,
    )
    assert trimmed["mode"] == "full"
    assert trimmed["issues"] == issues


def test_v1_policy_builds_contract_blocks_and_renders_v4_input() -> None:
    request = _request()
    result = build_chat_context_manifest(
        policy_version=CHAT_CONTEXT_POLICY_VERSION,
        frozen_input=_frozen_input(),
        input_hash=request.input_hash,
        routing=chat_routing_payload_as_dict(request),
        extra_input={"editable_fields_by_collection": request.editable_fields_by_collection},
    )
    assert result.fallback is None
    block_ids = {block.id for block in result.assembly.blocks}
    assert {
        "casefile_skeleton",
        "focus_objects",
        "thread_history",
        "validation_issues",
        "author_message",
        "editable_fields_by_collection",
        "focus",
        "validation",
        "routing",
        "input_hash",
    } <= block_ids
    payload = chat_input_payload_from_assembly(result.assembly)
    validated = ChatExecutorInputV2.model_validate(payload)
    assert validated.casefile["collection_counts"]["entities"] == 20
    assert validated.focus_objects["objects"][0]["object"]["id"] == "object:person_0"
    issue_by_id = {issue["issue_id"]: issue for issue in validated.validation_issues}
    assert len(issue_by_id["issue:1"]["message"]) > 200
    assert len(issue_by_id["issue:0"]["message"]) == 200
    bound_request = replace(request, assembled_input=payload)
    instructions, input_text = render_chat_executor_prompt(bound_request)
    assert instructions
    rendered = json.loads(input_text)
    assert rendered["casefile"]["collection_counts"]["entities"] == 20
    assert rendered["focus_objects"]["objects"][0]["object"]["id"] == "object:person_0"


def test_v1_policy_cuts_legacy_executor_tokens_by_more_than_half() -> None:
    request = _request()
    frozen = _frozen_input()
    legacy_result = build_chat_context_manifest(
        policy_version="agent-focus-v1",
        frozen_input=frozen,
        input_hash=request.input_hash,
        routing=chat_routing_payload_as_dict(request),
        prebuilt_input=(
            "请根据以下冻结数据回复作者。\n"
            + json.dumps(
                {
                    "input_hash": request.input_hash,
                    "casefile": frozen["casefile"],
                    "thread_history": frozen["history"],
                    "author_message": frozen["message"],
                    "editable_fields_by_collection": dict(
                        request.editable_fields_by_collection
                    ),
                    "focus": frozen["focus"],
                    "validation": frozen["validation"],
                    "validation_issues": frozen["validation"]["issues"],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ),
    )
    v1_result = build_chat_context_manifest(
        policy_version=CHAT_CONTEXT_POLICY_VERSION,
        frozen_input=frozen,
        input_hash=request.input_hash,
        routing=chat_routing_payload_as_dict(request),
        extra_input={"editable_fields_by_collection": request.editable_fields_by_collection},
    )
    assert v1_result.manifest.total_tokens * 2 < legacy_result.manifest.total_tokens


def test_unknown_policy_still_falls_back_to_legacy() -> None:
    result = build_chat_context_manifest(
        policy_version="missing-context-policy-v9",
        frozen_input=_frozen_input(),
        input_hash="h" * 64,
        prebuilt_input="legacy-text",
    )
    assert result.fallback is not None
    assert result.manifest.policy_version == "agent-focus-v1"


def test_context_policy_defaults_to_v1_with_v2_and_legacy_switches(monkeypatch) -> None:
    from casefile.agent_runtime.context import CHAT_CONTEXT_POLICY_V2_VERSION
    from casefile.application.workflow_service import _chat_context_policy_version

    monkeypatch.delenv("CASEFILE_CHAT_CONTEXT_ROLLOUT", raising=False)
    assert _chat_context_policy_version() == CHAT_CONTEXT_POLICY_VERSION
    monkeypatch.setenv("CASEFILE_CHAT_CONTEXT_ROLLOUT", "agent-focus-v1")
    assert _chat_context_policy_version() == "agent-focus-v1"
    monkeypatch.setenv("CASEFILE_CHAT_CONTEXT_ROLLOUT", CHAT_CONTEXT_POLICY_V2_VERSION)
    assert _chat_context_policy_version() == CHAT_CONTEXT_POLICY_V2_VERSION
    monkeypatch.setenv("CASEFILE_CHAT_CONTEXT_ROLLOUT", "unexpected")
    assert _chat_context_policy_version() == CHAT_CONTEXT_POLICY_VERSION


def test_bounded_tool_result_marks_and_caps_oversized_payloads() -> None:
    small = {"results": [{"id": "a", "label": "小"}]}
    text, truncated = bounded_tool_result_json(small, max_chars=4000)
    assert truncated is False
    assert json.loads(text) == small
    big = {
        "results": [
            {"id": f"object:{index}", "label": "对象" * 300, "snippet": "内容" * 300}
            for index in range(40)
        ]
    }
    text, truncated = bounded_tool_result_json(big, max_chars=4000)
    assert truncated is True
    assert len(text) <= 4000
    loaded = json.loads(text)
    assert loaded.get("truncated") is True


def test_fold_tool_results_collapses_old_results_to_one_line() -> None:
    results = [
        {"tool": "search_casefile", "args": {"query": f"q{index}"}, "status": "ok",
         "payload": {"results": [{"id": f"object:{index}"}]}}
        for index in range(5)
    ]
    folded = fold_tool_results(results, max_recent=2)
    assert len(folded["recent"]) == 2
    assert len(folded["folded"]) == 3
    assert folded["folded"][0]["tool"] == "search_casefile"
    assert folded["folded"][0]["hit_ids"] == ["object:0"]
