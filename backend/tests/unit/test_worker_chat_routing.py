"""Worker-side R2 rule/LLM route resolution and routing event payload tests."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

from casefile.agent_runtime.models import (
    CaseFileChatRequest,
    ChatTaskUnderstanding,
    RouteDecision,
    RouteSpecificRewriteRequest,
)
from casefile.agent_runtime.prompt import (
    casefile_chat_input,
    render_chat_executor_prompt,
    render_chat_rewrite_prompt,
    render_chat_router_prompt,
)
from casefile.agent_runtime.providers import FakeProvider
from casefile.worker.executors.chat import (
    ChatTaskExecutorMixin,
    chat_intent_event_payload,
    chat_rewrite_event_payload,
    resolve_chat_route,
)


class _GeneralMutationGateExecutor(ChatTaskExecutorMixin):
    def __init__(self) -> None:
        self.config = SimpleNamespace(general_mutation_mode="suggest")
        self.events: list[tuple[str, str, dict]] = []

    def _emit(self, _task_run_id: int, event_type: str, stage: str, payload: dict) -> None:
        self.events.append((event_type, stage, payload))


class _CountingMutationProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.general_mutation_calls = 0

    def plan_general_mutation(self, request):  # type: ignore[no-untyped-def]
        self.general_mutation_calls += 1
        return super().plan_general_mutation(request)


def make_request(
    *,
    hint: dict | None,
    message: str = "检查卷宗。",
    prompt_version: str = "casefile-chat-v2",
) -> CaseFileChatRequest:
    return CaseFileChatRequest(
        task_run_id=1,
        prompt_version=prompt_version,
        casefile={"entities": [{"id": "ent_1", "name": "Lucy"}]},
        history=(),
        message=message,
        editable_fields_by_collection={"entities": ("description",)},
        input_hash="a" * 64,
        model_id="fake",
        api_key=None,
        max_turns=6,
        emit=lambda _event_type, _stage, _payload: None,
        focus={
            "object_ids": ["ent_1"],
            "event_ids": [],
            "validation_issue_ids": ["validator:issue-1"],
        },
        routing_hint=hint,
    )


def test_preset_hint_resolves_route_rewrite_and_task_state_before_model_call() -> None:
    resolved = resolve_chat_route(
        make_request(hint={"entrypoint": "preset", "preset_id": "inspect"})
    )

    assert resolved.task_understanding is not None
    assert resolved.task_understanding.primary_intent == "analysis"
    assert resolved.route is not None
    assert resolved.route.route_source == "rule_preset"
    assert resolved.route.routes[0]["profile"] == "analysis.healthcheck"
    assert resolved.route.rewrite_strategy == "CONTEXTUALIZE"
    assert resolved.rewrite is not None
    assert resolved.rewrite.rewrite_decision == "CONTEXTUALIZE"


def test_issue_action_hint_resolves_to_explain_issue_profile() -> None:
    resolved = resolve_chat_route(
        make_request(
            hint={"entrypoint": "issue_action"},
            message="请处理当前焦点中的验证问题。",
        )
    )

    assert resolved.task_understanding is not None
    assert resolved.task_understanding.primary_intent == "explain_issue"
    assert resolved.route is not None
    assert resolved.route.route_source == "rule_ui"
    assert resolved.route.routes[0]["profile"] == "explain_issue.issue_fix"
    assert resolved.rewrite is not None
    assert resolved.rewrite.rewrite_decision == "KEEP"


def test_audit_preset_resolves_to_logic_audit_profile_with_suggestions_allowed() -> None:
    resolved = resolve_chat_route(
        make_request(hint={"entrypoint": "preset", "preset_id": "audit"})
    )

    assert resolved.task_understanding is not None
    assert resolved.task_understanding.primary_intent == "logic_audit"
    assert resolved.route is not None
    assert resolved.route.route_source == "rule_preset"
    assert resolved.route.routes[0]["profile"] == "logic_audit.full_review"
    assert resolved.route.execution_profile["prompt_component"] == "audit"
    assert resolved.route.execution_profile["suggestion_policy"] == "allow"
    assert resolved.route.rewrite_strategy == "CONTEXTUALIZE"
    assert resolved.rewrite is not None
    assert resolved.rewrite.rewrite_decision == "CONTEXTUALIZE"
    assert "全卷逻辑漏洞复查" in resolved.rewrite.canonical_query


def test_free_text_without_router_falls_back_and_denies_suggestions() -> None:
    resolved = resolve_chat_route(
        make_request(hint={"entrypoint": "free_text"}, message="帮我看看 Lucy。")
    )

    assert resolved.task_understanding is not None
    assert resolved.task_understanding.primary_intent == "question"
    assert resolved.route is not None
    assert resolved.route.route_source == "fallback"
    assert resolved.route.reason_codes == ("router_unavailable",)
    assert resolved.route.execution_profile["prompt_component"] == "chat"
    assert resolved.route.execution_profile["suggestion_policy"] == "deny"
    assert resolved.rewrite is not None
    assert resolved.rewrite.rewrite_decision == "KEEP"


def test_free_text_llm_question_routes_through_confidence_gate() -> None:
    resolved = resolve_chat_route(
        make_request(hint={"entrypoint": "free_text"}, message="帮我看看 Lucy。"),
        provider=FakeProvider(),
    )

    assert resolved.task_understanding is not None
    assert resolved.task_understanding.primary_intent == "question"
    assert resolved.route is not None
    assert resolved.route.route_source == "llm"
    assert resolved.route.confidence == 0.9
    assert resolved.route.execution_profile["prompt_component"] == "chat"
    assert resolved.route.execution_profile["suggestion_policy"] == "deny"
    assert resolved.rewrite is not None
    assert resolved.rewrite.rewrite_decision == "KEEP"


def test_free_text_llm_edit_resolves_mentions_and_contextualizes() -> None:
    resolved = resolve_chat_route(
        make_request(
            hint={"entrypoint": "free_text"},
            message="它的描述太夸张，改得克制点，但别动时间线。",
        ),
        provider=FakeProvider(),
    )

    assert resolved.task_understanding is not None
    assert resolved.task_understanding.primary_intent == "edit_request"
    assert resolved.task_understanding.entities["object_mentions"] == [
        {"resolved_ref": "ent_1", "text": "它"}
    ]
    assert resolved.route is not None
    assert resolved.route.route_source == "llm"
    assert resolved.route.execution_profile["prompt_component"] == "edit"
    assert resolved.route.rewrite_strategy == "CONTEXTUALIZE"
    assert resolved.rewrite is not None
    assert resolved.rewrite.rewrite_decision == "CONTEXTUALIZE"
    assert "别动时间线" in resolved.rewrite.canonical_query


def test_create_capability_routes_without_calling_the_intent_model() -> None:
    resolved = resolve_chat_route(
        make_request(
            hint={"entrypoint": "free_text"},
            message="创建一个人物实体，名称为夜班观察员。",
        ),
        allow_general_mutation_create=True,
    )

    assert resolved.task_understanding is not None
    assert resolved.task_understanding.primary_intent == "edit_request"
    assert resolved.route is not None
    assert resolved.route.route_source == "rule_capability"
    assert resolved.route.execution_profile["suggestion_policy"] == "allow"


def test_known_object_field_update_routes_without_calling_the_intent_model() -> None:
    resolved = resolve_chat_route(
        make_request(
            hint={"entrypoint": "free_text"},
            message="把 ent_1 的 description 改为夜班研究员。",
        ),
        allow_general_mutation_update=True,
    )

    assert resolved.task_understanding is not None
    assert resolved.task_understanding.primary_intent == "edit_request"
    assert resolved.route is not None
    assert resolved.route.route_source == "rule_capability"
    assert resolved.route.execution_profile["suggestion_policy"] == "allow"


def test_low_confidence_sensitive_edit_hits_the_gate_and_falls_back() -> None:
    resolved = resolve_chat_route(
        make_request(
            hint={"entrypoint": "free_text"},
            message="这段描述低置信度地改一下。",
        ),
        provider=FakeProvider(),
    )

    assert resolved.route is not None
    assert resolved.route.route_source == "fallback"
    assert resolved.route.reason_codes == ("confidence_gate_sensitive",)
    assert resolved.route.execution_profile["suggestion_policy"] == "deny"
    assert resolved.rewrite is not None
    assert resolved.rewrite.rewrite_decision == "KEEP"


def test_general_mutation_planner_obeys_final_deny_route() -> None:
    provider = _CountingMutationProvider()
    executor = _GeneralMutationGateExecutor()
    request = replace(
        make_request(
            hint={"entrypoint": "free_text"},
            message="把 ent_1 的 description 改为夜班研究员。",
        ),
        task_understanding=ChatTaskUnderstanding(primary_intent="edit_request"),
        route=RouteDecision(
            execution_profile={
                "primary_intent": "edit_request",
                "suggestion_policy": "deny",
            }
        ),
    )

    envelope, usage = executor._execute_general_mutation(
        SimpleNamespace(id=1, input_hash="a" * 64),
        request,
        provider,
        "test-key",
    )

    assert provider.general_mutation_calls == 0
    assert envelope == {"status": "blocked"}
    assert usage == {}
    blocked = next(event for event in executor.events if event[0] == "general_mutation.blocked")
    assert blocked[2] == {
        "reason_code": "general_mutation_route_denied",
        "failure_layer": "routing",
    }


def test_analysis_route_selects_multi_query_and_calls_post_route_rewrite() -> None:
    events: list[tuple[str, str, dict]] = []

    def emit(event_type: str, stage: str, payload: dict) -> None:
        events.append((event_type, stage, payload))

    request = make_request(
        hint={"entrypoint": "free_text"},
        message="对比一下候选解释。",
    )
    resolved = resolve_chat_route(
        replace(request, emit=emit),
        provider=FakeProvider(),
    )

    assert resolved.route is not None
    assert resolved.route.execution_profile["primary_intent"] == "analysis"
    assert resolved.route.rewrite_strategy == "MULTI_QUERY"
    assert resolved.rewrite is not None
    assert resolved.rewrite.rewrite_decision == "MULTI_QUERY"
    assert resolved.rewrite.retrieval_queries
    assert any(
        event_type == "agent.model_call.started"
        and payload.get("component_id") == "query_rewriter"
        for event_type, _stage, payload in events
    )


def test_no_hint_keeps_the_legacy_request_untouched() -> None:
    request = make_request(hint=None)
    resolved = resolve_chat_route(request)

    assert resolved is request
    assert resolved.route is None
    assert resolved.task_understanding is None
    assert resolved.rewrite is None


def test_v2_prompt_package_renders_router_executor_and_rewrite_components() -> None:
    request = make_request(
        hint={"entrypoint": "free_text"},
        message="对比一下候选解释。",
    )

    router_instructions, router_input = render_chat_router_prompt(request)
    assert "意图理解组件" in router_instructions
    assert json.loads(router_input)["author_message"] == "对比一下候选解释。"

    resolved = resolve_chat_route(request, provider=FakeProvider())
    executor_instructions, executor_input = render_chat_executor_prompt(resolved)
    assert "本路由组件为只读分析" in executor_instructions
    executor_payload = json.loads(executor_input)
    assert executor_payload["routing"]["route"]["route_source"] == "llm"
    assert executor_payload["routing"]["rewrite"]["rewrite_decision"] == "MULTI_QUERY"

    rewrite_request = RouteSpecificRewriteRequest(
        task_run_id=1,
        prompt_version="casefile-chat-v2",
        original_query="对比一下候选解释。",
        normalized_query="对比一下候选解释。",
        conservative_canonical_query="对比一下候选解释。",
        primary_intent="analysis",
        sub_intents=("compare_candidates",),
        constraints={"output_format": "answer"},
        rewrite_strategy="MULTI_QUERY",
        route_profile="analysis.inspect",
        input_hash="a" * 64,
        model_id="fake",
        api_key=None,
        max_turns=6,
        emit=lambda _event_type, _stage, _payload: None,
        network_retries=1,
    )
    rewrite_instructions, rewrite_input = render_chat_rewrite_prompt(rewrite_request)
    assert "路由后改写组件" in rewrite_instructions
    assert json.loads(rewrite_input)["rewrite_strategy"] == "MULTI_QUERY"


def test_v1_tasks_keep_the_legacy_render_path() -> None:
    request = make_request(
        hint=None,
        prompt_version="casefile-chat-v1",
    )

    instructions, input_text = render_chat_executor_prompt(request)
    assert "工作台预设指令规则" in instructions
    assert json.loads(input_text.split("\n", 1)[1])["author_message"] == request.message


def test_v3_prompt_package_renders_the_v2_read_tool_guidance() -> None:
    request = make_request(
        hint={"entrypoint": "preset", "preset_id": "inspect"},
        prompt_version="casefile-chat-v3",
    )
    resolved = resolve_chat_route(request)

    instructions, input_text = render_chat_executor_prompt(resolved)

    assert "本路由组件为只读分析" in instructions
    assert "`list_casefile_records`" in instructions
    assert "`get_related_objects`" in instructions
    payload = json.loads(input_text)
    assert payload["routing"]["route"]["route_source"] == "rule_preset"


def test_v8_prompt_package_renders_the_logic_audit_executor() -> None:
    request = make_request(
        hint={"entrypoint": "preset", "preset_id": "audit"},
        prompt_version="casefile-chat-v8",
    )
    resolved = resolve_chat_route(request)

    instructions, input_text = render_chat_executor_prompt(resolved)

    assert "全卷逻辑漏洞复查" in instructions
    assert "`simulate_patch_application`" in instructions
    assert "未发现可取证漏洞" in instructions
    payload = json.loads(input_text)
    execution_profile = payload["routing"]["route"]["execution_profile"]
    assert execution_profile["prompt_component"] == "audit"
    assert execution_profile["profile"] == "logic_audit.full_review"


def test_routing_event_payloads_are_json_serializable_and_small() -> None:
    resolved = resolve_chat_route(
        make_request(hint={"entrypoint": "preset", "preset_id": "gate"})
    )

    intent_payload = chat_intent_event_payload(resolved)
    rewrite_payload = chat_rewrite_event_payload(resolved)

    assert intent_payload["primary_intent"] == "validate_request"
    assert intent_payload["confidence"] == 1.0
    assert rewrite_payload["rewrite_decision"] == "CONTEXTUALIZE"
    assert rewrite_payload["preservation_checks"]["negations_preserved"] is True
    assert json.dumps(intent_payload, ensure_ascii=False)
    assert json.dumps(rewrite_payload, ensure_ascii=False)
    assert "casefile" not in intent_payload
    assert "casefile" not in rewrite_payload


def test_chat_input_renders_routing_block_only_when_route_exists() -> None:
    legacy_text = casefile_chat_input(make_request(hint=None))
    legacy_payload = json.loads(legacy_text.split("\n", 1)[1])

    routed_text = casefile_chat_input(
        resolve_chat_route(
            make_request(hint={"entrypoint": "preset", "preset_id": "inspect"})
        )
    )
    routed_payload = json.loads(routed_text.split("\n", 1)[1])

    assert "routing" not in legacy_payload
    assert routed_payload["routing"]["route"]["route_source"] == "rule_preset"
    assert routed_payload["routing"]["task_understanding"]["primary_intent"] == "analysis"
    assert routed_payload["routing"]["rewrite"]["rewrite_decision"] == "CONTEXTUALIZE"
