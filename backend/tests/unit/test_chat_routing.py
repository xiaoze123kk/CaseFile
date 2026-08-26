"""Pure routing policy tests: profile, budget tightening, hash, R1 fallback."""

from __future__ import annotations

from casefile.agent_runtime.chat_intent import (
    route_allows_suggestions,
    route_suggestion_policy,
)
from casefile.agent_runtime.chat_routing import (
    EXECUTION_PROFILES,
    fallback_route,
    route_hash,
    route_llm_task,
    routing_policy,
)
from casefile.agent_runtime.models import ChatTaskUnderstanding


def analysis_task(**overrides: object) -> ChatTaskUnderstanding:
    values: dict[str, object] = {
        "primary_intent": "analysis",
        "confidence": 1.0,
        "reason_codes": ("rule_preset:inspect",),
    }
    values.update(overrides)
    return ChatTaskUnderstanding(**values)  # type: ignore[arg-type]


def test_analysis_profile_defaults_to_read_only_analysis_budget() -> None:
    route = routing_policy(
        analysis_task(),
        budget={},
        profile="analysis.healthcheck",
        rewrite_strategy="CONTEXTUALIZE",
        route_source="rule_preset",
    )

    assert route.route_source == "rule_preset"
    assert route.routes == (
        {"target_agent_id": "casefile_chat", "profile": "analysis.healthcheck"},
    )
    assert route.execution_profile["prompt_component"] == "analysis"
    assert route.execution_profile["allow_suggestions"] is False
    assert route.execution_profile["max_turns"] == 6
    assert route.execution_profile["max_tool_calls"] == 12
    assert {
        "list_casefile_records",
        "search_casefile",
        "get_casefile_object",
        "get_related_objects",
        "get_validation_issues",
    } <= set(route.execution_profile["toolset"])
    assert route.rewrite_strategy == "CONTEXTUALIZE"
    assert route_suggestion_policy(route) == "deny"
    assert route_allows_suggestions(route) is False


def test_edit_profile_allows_bounded_multi_tool_verification() -> None:
    route = routing_policy(
        ChatTaskUnderstanding(
            primary_intent="edit_request",
            confidence=1.0,
            reason_codes=("rule_capability:general_mutation_delete",),
        ),
        budget={"max_turns": 12},
        profile="edit_request.edit",
        route_source="rule_capability",
    )

    assert route.execution_profile["max_turns"] == 8
    assert route.execution_profile["max_tool_calls"] == 12


def test_budget_only_tightens_never_widens() -> None:
    route = routing_policy(
        analysis_task(),
        budget={"max_turns": 2, "max_tool_calls": 3},
    )

    assert route.execution_profile["max_turns"] == 2
    assert route.execution_profile["max_tool_calls"] == 3

    widened = routing_policy(
        analysis_task(),
        budget={"max_turns": 99, "max_tool_calls": 99},
    )
    assert widened.execution_profile["max_turns"] == EXECUTION_PROFILES["analysis"]["max_turns"]
    assert (
        widened.execution_profile["max_tool_calls"]
        == EXECUTION_PROFILES["analysis"]["max_tool_calls"]
    )


def test_route_hash_is_stable_and_excludes_route_hash_field() -> None:
    first = routing_policy(analysis_task(), budget={}, route_source="rule_preset")
    second = routing_policy(analysis_task(), budget={}, route_source="rule_preset")

    assert first.route_hash == second.route_hash
    assert len(first.route_hash) == 64
    assert route_hash(first) == first.route_hash
    assert "route_hash" not in first.route_hash


def test_different_routes_produce_different_hashes() -> None:
    analysis = routing_policy(analysis_task(), budget={}, route_source="rule_preset")
    gate = routing_policy(
        ChatTaskUnderstanding(
            primary_intent="validate_request",
            confidence=1.0,
            reason_codes=("rule_preset:gate",),
        ),
        budget={},
        profile="validate_request.gate_check",
        route_source="rule_preset",
    )

    assert analysis.route_hash != gate.route_hash


def test_issue_route_allows_suggestions_subject_to_focus_constraints() -> None:
    route = routing_policy(
        ChatTaskUnderstanding(
            primary_intent="explain_issue",
            confidence=1.0,
            reason_codes=("rule_ui:issue_action",),
        ),
        budget={},
        route_source="rule_ui",
    )

    assert route.execution_profile["prompt_component"] == "issue"
    assert route_suggestion_policy(route) == "allow"
    assert route_allows_suggestions(route) is True


def test_logic_audit_profile_allows_suggestions_and_grants_full_review_budget() -> None:
    route = routing_policy(
        ChatTaskUnderstanding(
            primary_intent="logic_audit",
            confidence=1.0,
            reason_codes=("rule_preset:audit",),
        ),
        budget={},
        profile="logic_audit.full_review",
        route_source="rule_preset",
    )

    assert route.execution_profile["prompt_component"] == "audit"
    assert route.execution_profile["allow_suggestions"] is True
    assert route_suggestion_policy(route) == "allow"
    assert route_allows_suggestions(route) is True
    assert route.execution_profile["max_turns"] == 8
    assert route.execution_profile["max_tool_calls"] == 48
    assert "validate_patch_proposal" in route.execution_profile["toolset"]

    tightened = routing_policy(
        ChatTaskUnderstanding(
            primary_intent="logic_audit",
            confidence=1.0,
            reason_codes=("rule_preset:audit",),
        ),
        budget={"max_tool_calls": 4},
    )
    assert tightened.execution_profile["max_tool_calls"] == 4


def test_low_confidence_logic_audit_falls_back_to_question() -> None:
    route = route_llm_task(
        ChatTaskUnderstanding(
            primary_intent="logic_audit",
            confidence=0.5,
            reason_codes=("llm",),
        ),
        budget={},
        rewrite_strategy="CONTEXTUALIZE",
    )

    assert route.route_source == "fallback"
    assert route.execution_profile["primary_intent"] == "question"
    assert route_suggestion_policy(route) == "deny"
    assert route_allows_suggestions(route) is False


def test_r2_fallback_route_denies_suggestions_and_keeps_legacy_prompt_profile() -> None:
    route = fallback_route(reason_codes=("rule_miss",))

    assert route.route_source == "fallback"
    assert route.execution_profile["primary_intent"] == "question"
    assert route.execution_profile["prompt_component"] == "chat"
    assert route.rewrite_strategy == "KEEP"
    assert route.confidence == 0.0
    assert route.reason_codes == ("rule_miss",)
    assert route_suggestion_policy(route) == "deny"
    assert route_allows_suggestions(route) is False


def test_unknown_primary_intent_falls_back_to_question_profile() -> None:
    route = routing_policy(
        ChatTaskUnderstanding(primary_intent="unknown_family", confidence=0.9),
        budget={},
    )

    assert route.execution_profile["primary_intent"] == "unknown_family"
    assert route.execution_profile["prompt_component"] == "chat"


def test_all_read_route_profiles_expose_the_v2_read_surface() -> None:
    expected_read_tools = {
        "list_casefile_records",
        "search_casefile",
        "get_casefile_object",
        "get_related_objects",
    }
    for intent, write_tool in (
        ("question", None),
        ("analysis", "get_validation_issues"),
        ("explain_issue", "get_validation_issues"),
        ("edit_request", "validate_patch_proposal"),
        ("logic_audit", "validate_patch_proposal"),
    ):
        route = routing_policy(
            ChatTaskUnderstanding(
                primary_intent=intent,
                confidence=1.0,
                reason_codes=("test",),
            ),
            budget={},
        )
        toolset = set(route.execution_profile["toolset"])
        assert expected_read_tools <= toolset
        if write_tool is None:
            assert toolset == expected_read_tools
        else:
            assert write_tool in toolset
