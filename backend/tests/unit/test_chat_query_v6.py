"""Version identity, server pagination and request-local read reuse regressions."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import Mock

from agents import RunContextWrapper
from agents.tool_context import ToolContext
from test_chat_tools import make_request

from casefile.agent_runtime.chat_tools import (
    CHAT_TOOLSET_V5_VERSION,
    CHAT_TOOLSET_V6_VERSION,
    ChatToolContext,
    chat_tool_manifest,
    compare_draft_revisions_v6,
)
from casefile.agent_runtime.prompt import render_chat_executor_prompt, render_chat_finalizer_prompt


def request_with_history(resolver: Mock):
    return replace(
        make_request(toolset=["get_casefile_object"], max_tool_calls=4),
        prompt_version="casefile-chat-v27",
        toolset_version=CHAT_TOOLSET_V6_VERSION,
        draft_id=4,
        frozen_draft_revision=8,
        revision_history_resolver=resolver,
        focus={"draft_revision_context": {"frozen_revision": 999}},
    )


def test_trusted_draft_identity_reaches_executor_and_finalizer() -> None:
    request = request_with_history(Mock())
    for render in (
        lambda req: render_chat_executor_prompt(req),
        lambda req: render_chat_finalizer_prompt(req, tool_ledger=None, evidence_summary=""),
    ):
        _, raw = render(request)
        payload = json.loads(raw)
        identity = payload["focus"]["draft_revision_context"]
        assert identity["draft_id"] == 4
        assert identity["frozen_revision"] == 8
        assert identity["history_available"] is True
    _, raw = render_chat_executor_prompt(request)
    assembled = json.loads(raw)
    assembled["focus"]["draft_revision_context"]["frozen_revision"] = 999
    _, raw = render_chat_executor_prompt(replace(request, assembled_input=assembled))
    assert json.loads(raw)["focus"]["draft_revision_context"]["frozen_revision"] == 8


def test_v6_page_size_is_server_owned_without_changing_v5_schema() -> None:
    request = request_with_history(Mock())
    old = next(
        tool
        for tool in chat_tool_manifest(request.route, toolset_version=CHAT_TOOLSET_V5_VERSION)
        if tool.name == "compare_draft_revisions"
    )
    new = next(
        tool
        for tool in chat_tool_manifest(request.route, toolset_version=CHAT_TOOLSET_V6_VERSION)
        if tool.name == "compare_draft_revisions"
    )
    assert "limit" in old.params_json_schema["properties"]
    assert "limit" not in new.params_json_schema["properties"]


def invoke(context: ChatToolContext, offset: int = 0):
    arguments = json.dumps({"from_revision": 1, "to_revision": 8, "offset": offset})
    wrapper = ToolContext.from_agent_context(
        RunContextWrapper(context),
        "call",
        tool_name="compare_draft_revisions",
        tool_arguments=arguments,
    )
    return compare_draft_revisions_v6.on_invoke_tool(wrapper, arguments)


def test_concurrent_duplicates_reuse_read_without_consuming_budget() -> None:
    resolver = Mock(return_value={"results": [{"operation_id": 1}], "next_offset": 1})
    request = request_with_history(resolver)
    context = ChatToolContext(request=request, route=request.route)

    async def run():
        return await asyncio.gather(invoke(context), invoke(context))

    results = [json.loads(raw) for raw in asyncio.run(run())]
    assert results[0]["results"] == results[1]["results"]
    resolver.assert_called_once_with(1, 8, 0, 10)
    assert context.metrics.calls == 1
    assert context.metrics.query_cache_hits == 1
    assert len(context.recent_tool_results) == 2
    asyncio.run(invoke(context, offset=1))
    assert resolver.call_count == 2
    new_context = ChatToolContext(request=request, route=request.route)
    asyncio.run(invoke(new_context))
    assert resolver.call_count == 3


def test_failed_reads_are_not_cached() -> None:
    resolver = Mock(return_value={"error": "revision_history_unavailable"})
    request = request_with_history(resolver)
    context = ChatToolContext(request=request, route=request.route)
    asyncio.run(invoke(context))
    asyncio.run(invoke(context))
    assert resolver.call_count == 2
    assert context.metrics.query_cache_hits == 0
