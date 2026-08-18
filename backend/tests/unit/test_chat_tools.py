"""Deterministic chat tool contract and route toolset tests (R3 bounded loop)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

from agents import RunContextWrapper
from casefile.agent_runtime.chat_tools import (
    CHAT_TOOLSET_V3_VERSION,
    CHAT_TOOLSET_VERSION,
    LEGACY_CHAT_TOOLSET_VERSION,
    ChatToolContext,
    chat_tool_manifest,
    find_casefile_object,
    get_casefile_object,
    get_related_objects,
    get_validation_issues,
    list_casefile_collections,
    list_casefile_records,
    page_casefile_records,
    related_casefile_objects,
    request_thread_compaction,
    retrieve_thread_evidence,
    search_casefile,
    search_casefile_records,
    validate_patch_proposal,
)
from casefile.agent_runtime.models import CaseFileChatRequest, RouteDecision


def make_casefile() -> dict[str, Any]:
    return {
        "resolution_specs": [],
        "entities": [
            {
                "id": "object:person_1",
                "name": "张三",
                "description": "仓储管理员，负责三号库区。",
            },
            {"id": "object:company_1", "name": "远洋物流", "description": "承运方。"},
        ],
        "relationships": [
            {
                "id": "rel_1",
                "from_ref": "object:person_1",
                "to_ref": "object:company_1",
                "description": "张三受雇于远洋物流",
            }
        ],
        "locations": [{"id": "loc_1", "name": "三号库区", "description": "火灾现场"}],
        "events": [{"id": "event:known", "title": "三号库区失火", "description": "2026-07-02"}],
        "information_units": [{"id": "info_1", "description": "目击者看到张三在库区门口"}],
        "claims": [{"id": "claim_1", "statement": "张三主张当天不在库区"}],
        "hypotheses": [{"id": "hyp_1", "statement": "外来火源引燃包装材料"}],
        "reasoning_paths": [{"id": "path_1", "description": "由入库记录推知张三曾进入库区"}],
        "constraints": [],
        "structure_locks": [],
    }


def make_request(
    *,
    toolset: list[str],
    max_tool_calls: int,
    editable_fields_by_collection: dict[str, tuple[str, ...]] | None = None,
    validation_issues: tuple[dict[str, Any], ...] = (),
    casefile: dict[str, Any] | None = None,
    emit: Any | None = None,
    toolset_version: str = LEGACY_CHAT_TOOLSET_VERSION,
    thread_evidence_resolver: Any | None = None,
) -> CaseFileChatRequest:
    return CaseFileChatRequest(
        task_run_id=1,
        prompt_version="casefile-chat-v2",
        casefile=casefile or make_casefile(),
        history=(),
        message="查询",
        editable_fields_by_collection=(
            editable_fields_by_collection or {"entities": ("name", "description")}
        ),
        input_hash="h",
        model_id="fake",
        api_key=None,
        max_turns=4,
        emit=emit if emit is not None else (lambda _event_type, _stage, _payload: None),
        validation_issues=validation_issues,
        route=RouteDecision(
            execution_profile={"toolset": toolset, "max_tool_calls": max_tool_calls}
        ),
        toolset_version=toolset_version,
        thread_evidence_resolver=thread_evidence_resolver,
    )


def invoke(tool: Any, context: ChatToolContext, arguments: dict[str, Any]) -> Any:
    wrapper = RunContextWrapper(context)
    wrapper.tool_name = tool.name  # type: ignore[attr-defined]
    wrapper.run_config = None  # type: ignore[attr-defined]
    return asyncio.run(
        tool.on_invoke_tool(
            wrapper,
            json.dumps(arguments, ensure_ascii=False),
        )
    )


def test_manifest_only_exposes_route_selected_tools() -> None:
    request = make_request(toolset=["search_casefile"], max_tool_calls=2)

    manifest = chat_tool_manifest(request.route)

    assert [tool.name for tool in manifest] == ["search_casefile"]
    assert [
        tool.name
        for tool in chat_tool_manifest(
            RouteDecision(execution_profile={"toolset": [], "max_tool_calls": 0})
        )
    ] == []


def test_search_finds_exact_id_substring_and_chinese_bigram_overlap() -> None:
    casefile = make_casefile()

    exact = search_casefile_records(casefile, "object:person_1")[0]["id"]
    assert exact == "object:person_1"
    assert "object:person_1" in {
        result["id"] for result in search_casefile_records(casefile, "张三")
    }
    assert "loc_1" in {
        result["id"] for result in search_casefile_records(casefile, "三号库区")
    }
    assert search_casefile_records(casefile, "") == []


def test_search_tool_records_retrieved_ids_and_emits_completed_events() -> None:
    events: list[tuple[str, str, dict[str, Any]]] = []
    request = make_request(
        toolset=["search_casefile"],
        max_tool_calls=2,
        emit=lambda event_type, stage, payload: events.append((event_type, stage, payload)),
    )
    context = ChatToolContext(request=request, route=request.route)
    output = json.loads(invoke(search_casefile, context, {"query": "张三"}))

    assert output["results"]
    assert "object:person_1" in context.metrics.retrieved_object_ids
    assert context.metrics.calls == 1
    assert context.metrics.successful_calls == 1
    assert events[0][0] == "tool.started"
    assert events[-1][0] == "tool.completed"
    assert events[-1][2]["result_count"] >= 1


def test_budget_gate_rejects_tool_calls_once_exhausted() -> None:
    request = make_request(toolset=["get_validation_issues"], max_tool_calls=1)
    context = ChatToolContext(request=request, route=request.route)

    first = json.loads(invoke(get_validation_issues, context, {}))
    second = json.loads(invoke(get_validation_issues, context, {}))

    assert "issues" in first
    assert second == {"error": "tool_budget_exhausted", "issues": []}
    assert context.metrics.calls == 2
    assert context.metrics.successful_calls == 1
    assert context.metrics.budget_exhausted == 1


def test_get_object_rejects_unknown_ids_without_scanning_other_contract_data() -> None:
    request = make_request(toolset=["get_casefile_object"], max_tool_calls=2)
    context = ChatToolContext(request=request, route=request.route)

    found = json.loads(
        invoke(get_casefile_object, context, {"object_id": "object:person_1"})
    )
    missing = json.loads(
        invoke(get_casefile_object, context, {"object_id": "object:missing"})
    )

    assert found["object_id"] == "object:person_1"
    assert found["collection"] == "entities"
    assert missing == {"error": "object_not_found", "object_id": "object:missing"}
    assert find_casefile_object(request.casefile, "object:missing") is None


def test_validate_patch_proposal_enforces_collection_field_whitelist() -> None:
    request = make_request(toolset=["validate_patch_proposal"], max_tool_calls=5)
    context = ChatToolContext(request=request, route=request.route)

    valid = json.loads(
        invoke(
            validate_patch_proposal,
            context,
            {
                "object_id": "object:person_1",
                "path": "/description",
                "value_json": json.dumps("新的描述", ensure_ascii=False),
            },
        )
    )
    forbidden = json.loads(
        invoke(
            validate_patch_proposal,
            context,
            {
                "object_id": "object:person_1",
                "path": "/id",
                "value_json": json.dumps("object:other"),
            },
        )
    )
    missing = json.loads(
        invoke(
            validate_patch_proposal,
            context,
            {
                "object_id": "object:missing",
                "path": "/description",
                "value_json": json.dumps("值"),
            },
        )
    )
    markdown = json.loads(
        invoke(
            validate_patch_proposal,
            context,
            {
                "object_id": "object:person_1",
                "path": "/description",
                "value_json": "```json\n\"值\"\n```",
            },
        )
    )

    assert valid["valid"] is True
    assert forbidden["reason_code"] == "field_not_editable"
    assert forbidden["allowed_fields"] == ["name", "description"]
    assert missing["reason_code"] == "object_not_found"
    assert markdown["reason_code"] == "value_json_wrapped_in_markdown"


FULL_V2_TOOLSET = [
    "list_casefile_records",
    "search_casefile",
    "get_casefile_object",
    "get_related_objects",
    "get_validation_issues",
    "validate_patch_proposal",
]


def test_manifest_gates_v2_tools_by_frozen_toolset_version() -> None:
    request = make_request(
        toolset=FULL_V2_TOOLSET,
        max_tool_calls=12,
        toolset_version=CHAT_TOOLSET_VERSION,
    )

    v2_names = [tool.name for tool in chat_tool_manifest(
        request.route,
        toolset_version=CHAT_TOOLSET_VERSION,
    )]
    legacy_names = [tool.name for tool in chat_tool_manifest(
        request.route,
        toolset_version=LEGACY_CHAT_TOOLSET_VERSION,
    )]

    assert v2_names == FULL_V2_TOOLSET
    assert "list_casefile_records" not in legacy_names
    assert "get_related_objects" not in legacy_names
    assert "search_casefile" in legacy_names


def test_list_collections_reports_every_frozen_collection_count() -> None:
    manifest = list_casefile_collections(make_casefile())

    assert {entry["collection"] for entry in manifest} == {
        "resolution_specs",
        "entities",
        "relationships",
        "locations",
        "events",
        "information_units",
        "claims",
        "hypotheses",
        "reasoning_paths",
        "constraints",
        "structure_locks",
    }
    assert {entry["collection"]: entry["count"] for entry in manifest}["entities"] == 2


def test_list_records_paginates_deterministically() -> None:
    casefile = make_casefile()

    first_page = page_casefile_records(casefile, "entities", offset=0, limit=1)
    second_page = page_casefile_records(casefile, "entities", offset=1, limit=1)

    assert first_page["total"] == 2
    assert first_page["records"][0]["id"] == "object:person_1"
    assert first_page["records"][0]["label"] == "张三"
    assert second_page["records"][0]["id"] == "object:company_1"
    assert page_casefile_records(casefile, "entities", offset=99, limit=1)["records"] == []


def test_list_tool_supports_manifest_page_and_rejects_unknown_collections() -> None:
    events: list[tuple[str, str, dict[str, Any]]] = []
    request = make_request(
        toolset=["list_casefile_records"],
        max_tool_calls=4,
        emit=lambda event_type, stage, payload: events.append((event_type, stage, payload)),
        toolset_version=CHAT_TOOLSET_VERSION,
    )
    context = ChatToolContext(request=request, route=request.route)

    manifest = json.loads(invoke(list_casefile_records, context, {}))
    page = json.loads(
        invoke(list_casefile_records, context, {"collection": "entities", "limit": 1})
    )
    missing = json.loads(
        invoke(list_casefile_records, context, {"collection": "unknown_collection"})
    )

    assert manifest["total"] == 9
    assert page["records"][0]["id"] == "object:person_1"
    assert missing == {"error": "unknown_collection", "collection": "unknown_collection"}
    assert events[-1][2]["reason_code"] == "unknown_collection"
    assert context.metrics.calls == 3
    assert context.metrics.successful_calls == 2


def test_related_objects_expand_one_hop_with_summaries() -> None:
    casefile = make_casefile()

    payload = related_casefile_objects(
        casefile,
        ["object:person_1"],
        limit=10,
    )

    assert [relationship["id"] for relationship in payload["relationships"]] == ["rel_1"]
    assert payload["relationships"][0]["relationship_type"] is None or isinstance(
        payload["relationships"][0]["relationship_type"], str
    )
    assert [obj["id"] for obj in payload["objects"]] == ["object:company_1"]
    assert payload["objects"][0]["label"] == "远洋物流"
    assert payload["unresolved_refs"] == []


def test_related_objects_filters_relation_types_and_reports_dangling_refs() -> None:
    casefile = make_casefile()
    casefile["relationships"].append(
        {
            "id": "rel_2",
            "title": "雇佣关系",
            "from_ref": "object:person_1",
            "to_ref": "object:missing_1",
            "relationship_type": "employs",
            "direction": "directed",
            "truth_status": "canon_true",
        }
    )

    filtered = related_casefile_objects(
        casefile,
        ["object:person_1"],
        relation_types=["employs"],
        limit=10,
    )
    unfiltered = related_casefile_objects(
        casefile,
        ["object:person_1", "object:unknown"],
        limit=10,
    )

    assert [item["id"] for item in filtered["relationships"]] == ["rel_2"]
    assert filtered["unresolved_refs"] == ["object:missing_1"]
    assert "object:unknown" in unfiltered["unresolved_refs"]


def test_related_tool_validates_depth_and_seed_bounds() -> None:
    request = make_request(
        toolset=["get_related_objects"],
        max_tool_calls=4,
        toolset_version=CHAT_TOOLSET_VERSION,
    )
    context = ChatToolContext(request=request, route=request.route)

    invalid_depth = json.loads(
        invoke(
            get_related_objects,
            context,
            {"object_ids": ["object:person_1"], "max_depth": 2},
        )
    )
    empty = json.loads(invoke(get_related_objects, context, {"object_ids": []}))
    too_many = json.loads(
        invoke(
            get_related_objects,
            context,
            {"object_ids": [f"object:{i}" for i in range(9)]},
        )
    )
    ok = json.loads(
        invoke(
            get_related_objects,
            context,
            {"object_ids": ["object:person_1"], "limit": 5},
        )
    )

    assert invalid_depth["error"] == "invalid_depth"
    assert empty["error"] == "object_ids_empty"
    assert too_many["error"] == "too_many_seeds"
    assert ok["relationships"][0]["id"] == "rel_1"
    assert context.metrics.calls == 4
    assert context.metrics.successful_calls == 1
    assert context.metrics.budget_exhausted == 0


def test_related_tool_obeys_the_same_budget_gate() -> None:
    request = make_request(
        toolset=["get_related_objects"],
        max_tool_calls=1,
        toolset_version=CHAT_TOOLSET_VERSION,
    )
    context = ChatToolContext(request=request, route=request.route)

    first = json.loads(
        invoke(get_related_objects, context, {"object_ids": ["object:person_1"]})
    )
    second = json.loads(
        invoke(get_related_objects, context, {"object_ids": ["object:person_1"]})
    )

    assert first["objects"][0]["id"] == "object:company_1"
    assert second == {
        "error": "tool_budget_exhausted",
        "relationships": [],
        "objects": [],
    }
    assert context.metrics.budget_exhausted == 1


def test_v3_context_tools_are_gated_by_toolset_and_route_profile() -> None:
    route = RouteDecision(
        execution_profile={
            "toolset": ["search_casefile"],
            "context_tools": [
                "retrieve_thread_evidence",
                "request_thread_compaction",
            ],
            "max_tool_calls": 4,
        }
    )
    assert [tool.name for tool in chat_tool_manifest(route)] == ["search_casefile"]
    assert [tool.name for tool in chat_tool_manifest(
        route,
        toolset_version=CHAT_TOOLSET_VERSION,
    )] == ["search_casefile"]
    v3_names = [
        tool.name
        for tool in chat_tool_manifest(
            route,
            toolset_version=CHAT_TOOLSET_V3_VERSION,
        )
    ]
    assert v3_names == [
        "search_casefile",
        "retrieve_thread_evidence",
        "request_thread_compaction",
    ]


def test_retrieve_thread_evidence_resolves_and_records_metrics() -> None:
    events: list[tuple[str, str, dict[str, Any]]] = []
    request = make_request(
        toolset=["search_casefile"],
        max_tool_calls=2,
        emit=lambda event_type, stage, payload: events.append((event_type, stage, payload)),
        thread_evidence_resolver=lambda evidence_id: (
            {"evidence_id": evidence_id, "content": "原始消息。", "role": "user"}
        ),
    )
    request = replace(
        request,
        assembled_input={
            "context_dashboard": {
                "recoverable_evidence_ids": ["thread://7/message/2"],
            },
        },
    )
    context = ChatToolContext(request=request, route=request.route)
    result = json.loads(
        invoke(
            retrieve_thread_evidence,
            context,
            {"evidence_id": "thread://7/message/2"},
        )
    )
    assert result["valid"] is True
    assert result["evidence"]["content"] == "原始消息。"
    assert context.metrics.retrieved_evidence_ids == ["thread://7/message/2"]
    assert events[0][0] == "tool.started"
    assert events[1][2]["valid"] is True


def test_retrieve_thread_evidence_fails_closed_without_resolver() -> None:
    request = make_request(toolset=["search_casefile"], max_tool_calls=1)
    request = replace(
        request,
        assembled_input={
            "context_dashboard": {
                "recoverable_evidence_ids": ["thread://7/message/2"],
            },
        },
    )
    context = ChatToolContext(request=request, route=request.route)
    result = json.loads(
        invoke(
            retrieve_thread_evidence,
            context,
            {"evidence_id": "thread://7/message/2"},
        )
    )
    assert result == {
        "valid": False,
        "reason_code": "thread_evidence_unavailable",
        "evidence_id": "thread://7/message/2",
    }
    assert context.metrics.successful_calls == 0


def test_retrieve_thread_evidence_rejects_undeclared_pointers() -> None:
    request = make_request(
        toolset=["search_casefile"],
        max_tool_calls=1,
        thread_evidence_resolver=lambda evidence_id: (
            {"evidence_id": evidence_id, "content": "不应返回。"}
        ),
    )
    request = replace(
        request,
        assembled_input={
            "context_dashboard": {
                "recoverable_evidence_ids": ["thread://7/message/2"],
            },
        },
    )
    context = ChatToolContext(request=request, route=request.route)
    result = json.loads(
        invoke(
            retrieve_thread_evidence,
            context,
            {"evidence_id": "thread://7/message/3"},
        )
    )
    assert result["valid"] is False
    assert result["reason_code"] == "evidence_ref_not_declared"
    assert context.metrics.successful_calls == 0


def test_request_thread_compaction_is_queued_not_executed() -> None:
    request = make_request(toolset=["search_casefile"], max_tool_calls=1)
    context = ChatToolContext(request=request, route=request.route)
    result = json.loads(invoke(request_thread_compaction, context, {}))
    assert result == {"valid": True, "requested": True, "queued": "after_reply"}
    assert context.metrics.requested_thread_compaction == 1
    assert context.metrics.calls == 1
