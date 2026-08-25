"""Rule intent resolution and routing-hint HTTP contract tests."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from casefile.agent_runtime.chat_intent import (
    ALLOWED_PRESET_IDS,
    PRESET_ROUTE_TABLE,
    build_edit_target_manifest,
    normalize_routing_hint,
    resolve_rule_route,
    task_understanding_for_rule,
)
from casefile.agent_runtime.models import CaseFileChatRequest
from casefile.api.schemas import AgentChatRoutingHint, AgentMessageCreateRequest


def make_chat_request(
    *,
    hint: dict | None = None,
    focus: dict | None = None,
    message: str = "检查卷宗。",
) -> CaseFileChatRequest:
    return CaseFileChatRequest(
        task_run_id=1,
        prompt_version="casefile-chat-v1",
        casefile={},
        history=(),
        message=message,
        editable_fields_by_collection={},
        input_hash="a" * 64,
        model_id="fake",
        api_key=None,
        max_turns=6,
        emit=lambda _event_type, _stage, _payload: None,
        focus=focus or {},
        routing_hint=hint or {},
    )


@pytest.mark.parametrize(
    ("preset_id", "primary_intent", "profile"),
    [
        ("inspect", "analysis", "analysis.healthcheck"),
        ("evidence", "analysis", "analysis.evidence_summary"),
        ("compare", "analysis", "analysis.comparison"),
        ("gate", "validate_request", "validate_request.gate_check"),
        ("audit", "logic_audit", "logic_audit.full_review"),
    ],
)
def test_preset_hints_resolve_to_the_frozen_rule_table(
    preset_id: str,
    primary_intent: str,
    profile: str,
) -> None:
    rule = resolve_rule_route(
        make_chat_request(hint={"entrypoint": "preset", "preset_id": preset_id})
    )

    assert rule is not None
    assert rule.route_source == "rule_preset"
    assert rule.primary_intent == primary_intent
    assert rule.profile == profile
    assert rule.reason_code == f"rule_preset:{preset_id}"
    assert PRESET_ROUTE_TABLE[preset_id]["profile"] == profile


def test_issue_action_resolves_only_when_the_focus_has_validation_issue_ids() -> None:
    rule = resolve_rule_route(
        make_chat_request(
            hint={"entrypoint": "issue_action"},
            focus={
                "object_ids": ["object:person_1"],
                "event_ids": ["event:known"],
                "validation_issue_ids": ["validator:issue-1"],
            },
        )
    )

    assert rule is not None
    assert rule.route_source == "rule_ui"
    assert rule.primary_intent == "explain_issue"
    assert rule.profile == "explain_issue.issue_fix"
    assert task_understanding_for_rule(rule).confidence == 1.0


def test_issue_action_without_validation_issue_focus_does_not_hit_a_rule() -> None:
    assert (
        resolve_rule_route(
            make_chat_request(
                hint={"entrypoint": "issue_action"},
                focus={"validation_issue_ids": []},
            )
        )
        is None
    )


def test_no_hint_returns_none_for_the_legacy_path() -> None:
    assert resolve_rule_route(make_chat_request(hint={})) is None


def test_destructive_free_text_uses_the_deterministic_scope_route() -> None:
    rule = resolve_rule_route(
        make_chat_request(message="请把这个对象删除。", hint={"entrypoint": "free_text"})
    )

    assert rule is not None
    assert rule.route_source == "rule_safety"
    assert rule.primary_intent == "unsupported_action"
    assert rule.profile == "unsupported_action.scope"
    assert rule.reason_code == "rule_safety:destructive_action"


def test_delete_routes_to_edit_only_when_general_mutation_capability_is_enabled() -> None:
    request = make_chat_request(
        message="请把这个对象删除。",
        hint={"entrypoint": "free_text"},
    )

    rule = resolve_rule_route(request, allow_general_mutation_delete=True)

    assert rule is not None
    assert rule.route_source == "rule_capability"
    assert rule.primary_intent == "edit_request"
    assert rule.profile == "edit_request.edit"
    assert rule.reason_code == "rule_capability:general_mutation_delete"


def test_ambiguous_delete_routes_to_clarification_before_delete_capability() -> None:
    request = make_chat_request(
        message="我想移除一个对象，但没有说明具体对象；请先澄清，不要执行。",
        hint={"entrypoint": "free_text"},
    )

    rule = resolve_rule_route(request, allow_general_mutation_delete=True)

    assert rule is not None
    assert rule.route_source == "rule_safety"
    assert rule.primary_intent == "clarify"
    assert rule.profile == "clarify.question"
    assert rule.reason_code == "rule_safety:ambiguous_destructive_target"


def test_create_routes_to_edit_only_when_general_mutation_capability_is_enabled() -> None:
    request = make_chat_request(
        message="创建一个人物实体，名称为夜班观察员。",
        hint={"entrypoint": "free_text"},
    )

    assert resolve_rule_route(request) is None
    rule = resolve_rule_route(request, allow_general_mutation_create=True)

    assert rule is not None
    assert rule.route_source == "rule_capability"
    assert rule.primary_intent == "edit_request"
    assert rule.profile == "edit_request.edit"
    assert rule.reason_code == "rule_capability:general_mutation_create"


@pytest.mark.parametrize(
    "message",
    (
        "直接修改 Draft 数据。",
        "直接写入工作稿。",
        "绕过审阅修改 Draft。",
        "skip review and edit Draft",
    ),
)
def test_review_bypass_free_text_uses_the_deterministic_scope_route(
    message: str,
) -> None:
    rule = resolve_rule_route(
        make_chat_request(message=message, hint={"entrypoint": "free_text"})
    )

    assert rule is not None
    assert rule.route_source == "rule_safety"
    assert rule.primary_intent == "unsupported_action"
    assert rule.reason_code == "rule_safety:direct_draft_bypass"


@pytest.mark.parametrize("message", ("直接修改 Lucy 的描述。", "修改 Lucy 的描述。"))
def test_direct_object_edit_is_not_mistaken_for_review_bypass(message: str) -> None:
    assert (
        resolve_rule_route(
            make_chat_request(message=message, hint={"entrypoint": "free_text"})
        )
        is None
    )


def _manifest_request(*, focus: dict, message: str = "调整午夜重启的标题。") -> CaseFileChatRequest:
    request = make_chat_request(message=message, focus=focus)
    return replace(
        request,
        casefile={
            "events": [{"id": "evt_restart", "title": "午夜重启"}],
            "resolution_specs": [{"id": "res_restart", "title": "午夜重启"}],
            "entities": [{"id": "ent_lucy", "name": "Lucy"}],
        },
        editable_fields_by_collection={
            "events": ("title",),
            "resolution_specs": ("title",),
            "entities": ("description",),
        },
    )


def test_edit_manifest_uses_focus_to_disambiguate_duplicate_labels() -> None:
    manifest = build_edit_target_manifest(
        _manifest_request(focus={"event_ids": ["evt_restart"]})
    )

    assert manifest.as_list() == [{"object_id": "evt_restart", "path": "/title"}]
    assert manifest.ambiguous is False


def test_edit_manifest_keeps_unresolved_duplicate_label_ambiguous() -> None:
    for focus in ({}, {"event_ids": ["evt_restart"], "object_ids": ["res_restart"]}):
        manifest = build_edit_target_manifest(_manifest_request(focus=focus))
        assert manifest.targets == ()
        assert manifest.ambiguous is True


def test_edit_manifest_reports_partial_ambiguity_without_dropping_unique_target() -> None:
    manifest = build_edit_target_manifest(
        _manifest_request(
            focus={},
            message="调整 Lucy 的描述和午夜重启的标题。",
        )
    )

    assert manifest.as_list() == [{"object_id": "ent_lucy", "path": "/description"}]
    assert manifest.ambiguous is True


def test_edit_manifest_assigns_each_field_to_its_nearest_object_mention() -> None:
    manifest = build_edit_target_manifest(
        _manifest_request(
            focus={"event_ids": ["evt_restart"]},
            message=(
                "把 Lucy 的描述改成负责调查午夜异常的研究员，"
                "并把午夜重启的标题改成午夜例行重启。"
            ),
        )
    )

    assert manifest.as_list() == [
        {"object_id": "ent_lucy", "path": "/description"},
        {"object_id": "evt_restart", "path": "/title"},
    ]
    assert manifest.ambiguous is False


def test_unknown_preset_normalizes_to_free_text_and_does_not_hit_rules() -> None:
    normalized = normalize_routing_hint(
        {"entrypoint": "preset", "preset_id": "unknown_preset"}
    )

    assert normalized == {"entrypoint": "free_text", "preset_id": None}
    assert resolve_rule_route(make_chat_request(hint=normalized)) is None


def test_normalize_routing_hint_rejects_non_preset_preset_id() -> None:
    assert normalize_routing_hint(
        {"entrypoint": "free_text", "preset_id": "inspect"}
    ) == {"entrypoint": "free_text", "preset_id": None}
    assert normalize_routing_hint(
        {"entrypoint": "issue_action", "preset_id": "gate"}
    ) == {"entrypoint": "issue_action", "preset_id": None}


def test_normalize_routing_hint_handles_garbage_as_empty() -> None:
    assert normalize_routing_hint(None) == {}
    assert normalize_routing_hint("preset") == {}  # type: ignore[arg-type]
    assert normalize_routing_hint({"entrypoint": "unknown"}) == {
        "entrypoint": "free_text",
        "preset_id": None,
    }


def test_rule_task_understanding_marks_gate_as_read_only() -> None:
    gate_rule = resolve_rule_route(
        make_chat_request(hint={"entrypoint": "preset", "preset_id": "gate"})
    )
    assert gate_rule is not None
    understanding = task_understanding_for_rule(gate_rule)

    assert understanding.primary_intent == "validate_request"
    assert understanding.capabilities["needs_validation_snapshot"] is True
    assert understanding.capabilities["needs_suggestion_generation"] is False


def test_audit_rule_understanding_declares_retrieval_and_suggestion_capabilities() -> None:
    audit_rule = resolve_rule_route(
        make_chat_request(hint={"entrypoint": "preset", "preset_id": "audit"})
    )
    assert audit_rule is not None
    understanding = task_understanding_for_rule(audit_rule)

    assert understanding.primary_intent == "logic_audit"
    assert understanding.confidence == 1.0
    assert understanding.risk_level == "medium"
    for capability in (
        "needs_casefile_retrieval",
        "needs_relations",
        "needs_validation_snapshot",
        "needs_suggestion_generation",
        "needs_reasoning",
    ):
        assert understanding.capabilities[capability] is True


def test_preset_hint_schema_requires_preset_id() -> None:
    with pytest.raises(ValidationError):
        AgentChatRoutingHint.model_validate({"entrypoint": "preset"})
    with pytest.raises(ValidationError):
        AgentChatRoutingHint.model_validate({"entrypoint": "preset", "preset_id": " "})


def test_non_preset_hint_schema_rejects_preset_id() -> None:
    with pytest.raises(ValidationError):
        AgentChatRoutingHint.model_validate(
            {"entrypoint": "free_text", "preset_id": "inspect"}
        )


def test_agent_message_request_accepts_all_hint_shapes_and_omits_old_field() -> None:
    for hint in (
        {"entrypoint": "free_text"},
        {"entrypoint": "preset", "preset_id": "inspect"},
        {"entrypoint": "issue_action"},
    ):
        request = AgentMessageCreateRequest.model_validate(
            {
                "expected_draft_id": 1,
                "expected_draft_revision": 1,
                "content": "检查卷宗。",
                "routing_hint": hint,
            }
        )
        assert request.routing_hint is not None
    legacy = AgentMessageCreateRequest.model_validate(
        {
            "expected_draft_id": 1,
            "expected_draft_revision": 1,
            "content": "旧客户端消息。",
        }
    )
    assert legacy.routing_hint is None


def test_allowed_preset_ids_match_rule_table() -> None:
    assert ALLOWED_PRESET_IDS == frozenset(PRESET_ROUTE_TABLE)
