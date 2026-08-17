"""Context pipeline engine, policy fallback and legacy manifest tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

import pytest
from casefile.agent_runtime.chat_routing import fallback_route
from casefile.agent_runtime.context import (
    LEGACY_CONTEXT_POLICY_VERSION,
    ContextBlock,
    ContextBudget,
    ContextEngine,
    ContextEngineError,
    ContextPolicy,
    ContextPolicyError,
    ContextPolicyStage,
    ContextRegistry,
    ContextRun,
    StageResult,
    build_chat_context_manifest,
    default_context_registry,
    estimate_conservative_tokens,
    known_context_policy_versions,
    legacy_chat_routing_payload,
    load_context_policy,
)
from casefile.agent_runtime.models import (
    CaseFileChatRequest,
    chat_routing_payload_as_dict,
)


@dataclass(slots=True)
class _EchoStage:
    name: str = "echo_v1"
    version: str = "echo-v1"
    capabilities: frozenset[str] = frozenset({"source"})

    def can_run(self, run: ContextRun) -> bool:
        return True

    def run(self, run: ContextRun) -> StageResult:
        return StageResult(
            added=(
                ContextBlock(
                    id="echo",
                    kind="echo",
                    payload=run.prebuilt_input or "",
                    tokens=3,
                ),
            ),
            metrics={"echo": True},
        )


@dataclass(slots=True)
class _DisabledStage:
    name: str = "disabled_v1"
    version: str = "disabled-v1"
    capabilities: frozenset[str] = frozenset({"source"})

    def can_run(self, run: ContextRun) -> bool:
        return False

    def run(self, run: ContextRun) -> StageResult:
        raise AssertionError("disabled stage must never run")


@dataclass(slots=True)
class _OldBlockStage:
    name: str = "old_block_v1"
    version: str = "old-block-v1"
    capabilities: frozenset[str] = frozenset({"source"})

    def can_run(self, run: ContextRun) -> bool:
        return True

    def run(self, run: ContextRun) -> StageResult:
        return StageResult(added=(ContextBlock(id="old", kind="old", payload=None),))


@dataclass(slots=True)
class _ReplaceStage:
    name: str = "replace_v1"
    version: str = "replace-v1"
    capabilities: frozenset[str] = frozenset({"source"})

    def can_run(self, run: ContextRun) -> bool:
        return True

    def run(self, run: ContextRun) -> StageResult:
        return StageResult(
            replaced_ids=("old",),
            added=(ContextBlock(id="new", kind="new", payload=None, tokens=5),),
        )


@dataclass(slots=True)
class _JumpStage:
    name: str = "jump_v1"
    version: str = "jump-v1"
    capabilities: frozenset[str] = frozenset({"source"})

    def can_run(self, run: ContextRun) -> bool:
        return True

    def run(self, run: ContextRun) -> StageResult:
        return StageResult(
            added=(ContextBlock(id="jumped", kind="jumped", payload=None),),
            next_stage="third",
        )


def _policy(
    *stages: ContextPolicyStage,
    budget: ContextBudget | None = None,
    version: str = "test-context-policy-v1",
) -> ContextPolicy:
    return ContextPolicy(
        schema_version=1,
        version=version,
        task_type="casefile_chat",
        stages=stages,
        budget=budget or ContextBudget(),
    )


def _registry() -> ContextRegistry:
    registry = ContextRegistry()
    registry.register(_EchoStage())
    registry.register(_DisabledStage())
    registry.register(_OldBlockStage())
    registry.register(_ReplaceStage())
    registry.register(_JumpStage())
    return registry


def _chat_request() -> CaseFileChatRequest:
    return CaseFileChatRequest(
        task_run_id=1,
        prompt_version="casefile-chat-v3",
        casefile={},
        history=(),
        message="你好",
        editable_fields_by_collection={},
        input_hash="hash",
        model_id="model",
        api_key=None,
        max_turns=4,
        emit=lambda event_type, stage, payload: None,
    )


def test_legacy_policy_resource_is_valid() -> None:
    policy = load_context_policy(LEGACY_CONTEXT_POLICY_VERSION)
    assert policy.version == LEGACY_CONTEXT_POLICY_VERSION
    assert policy.task_type == "casefile_chat"
    assert policy.stages[0].strategy == "legacy_full_injection_v1"
    assert policy.guardrails["delete_allowed"] is False


def test_known_context_policy_versions_include_legacy() -> None:
    assert LEGACY_CONTEXT_POLICY_VERSION in known_context_policy_versions()


def test_unknown_policy_version_raises() -> None:
    with pytest.raises(ContextPolicyError):
        load_context_policy("missing-context-policy-v9")


def test_default_request_freezes_legacy_context_policy() -> None:
    assert _chat_request().context_policy_version == LEGACY_CONTEXT_POLICY_VERSION


def test_engine_executes_stages_in_declared_order_and_skips_disabled() -> None:
    policy = _policy(
        ContextPolicyStage(id="first", strategy="echo_v1"),
        ContextPolicyStage(id="second", strategy="disabled_v1"),
    )
    assembly = ContextEngine(_registry()).build(
        policy=policy,
        frozen_input={},
        input_hash="hash",
        prebuilt_input="你好",
    )
    assert [block.id for block in assembly.blocks] == ["echo"]
    assert assembly.stage_versions == (
        {"stage_id": "first", "strategy": "echo_v1", "version": "echo-v1"},
    )


def test_engine_applies_replaced_ids_before_added() -> None:
    policy = _policy(
        ContextPolicyStage(id="first", strategy="old_block_v1"),
        ContextPolicyStage(id="second", strategy="replace_v1"),
    )
    assembly = ContextEngine(_registry()).build(
        policy=policy,
        frozen_input={},
        input_hash="hash",
    )
    assert [block.id for block in assembly.blocks] == ["new"]


def test_engine_rejects_duplicate_block_ids() -> None:
    policy = _policy(
        ContextPolicyStage(id="first", strategy="echo_v1"),
        ContextPolicyStage(id="second", strategy="echo_v1"),
    )
    with pytest.raises(ContextEngineError, match="already exists"):
        ContextEngine(_registry()).build(
            policy=policy,
            frozen_input={},
            input_hash="hash",
            prebuilt_input="你好",
        )


def test_engine_rejects_unknown_strategy() -> None:
    policy = _policy(ContextPolicyStage(id="first", strategy="missing_v1"))
    with pytest.raises(ContextEngineError, match="unknown strategies"):
        ContextEngine(_registry()).build(policy=policy, frozen_input={}, input_hash="hash")


def test_engine_marks_budget_exceeded_without_trimming() -> None:
    policy = _policy(
        ContextPolicyStage(id="first", strategy="echo_v1"),
        budget=ContextBudget(total_input_tokens=1, enforce_budget=True),
    )
    assembly = ContextEngine(_registry()).build(
        policy=policy,
        frozen_input={},
        input_hash="hash",
    )
    assert assembly.budget_exceeded is True
    assert len(assembly.blocks) == 1


def test_engine_honors_forward_next_stage_jump() -> None:
    policy = _policy(
        ContextPolicyStage(id="first", strategy="jump_v1"),
        ContextPolicyStage(id="second", strategy="echo_v1"),
        ContextPolicyStage(id="third", strategy="old_block_v1"),
    )
    assembly = ContextEngine(_registry()).build(
        policy=policy,
        frozen_input={},
        input_hash="hash",
    )
    assert [block.id for block in assembly.blocks] == ["jumped", "old"]
    assert [item["stage_id"] for item in assembly.stage_versions] == ["first", "third"]


def test_legacy_manifest_measures_the_rendered_input() -> None:
    result = build_chat_context_manifest(
        policy_version=LEGACY_CONTEXT_POLICY_VERSION,
        frozen_input={},
        input_hash="hash",
        prebuilt_input="你好abcd",
        provider="openai",
        model_id="model",
    )
    assert result.fallback is None
    manifest = result.manifest
    assert manifest.policy_version == LEGACY_CONTEXT_POLICY_VERSION
    assert manifest.blocks[0].id == "legacy_chat_input"
    assert manifest.blocks[0].tokens == estimate_conservative_tokens("你好abcd")
    jsonable = manifest.to_jsonable()
    assert jsonable["total_tokens"] == manifest.total_tokens
    assert jsonable["budget"]["enforce_budget"] is False


def test_unknown_policy_falls_back_to_legacy_with_decision() -> None:
    result = build_chat_context_manifest(
        policy_version="missing-context-policy-v9",
        frozen_input={},
        input_hash="hash",
        prebuilt_input="你好",
    )
    assert result.fallback is not None
    assert result.fallback.code == "context_policy_unknown_fallback"
    assert result.manifest.policy_version == LEGACY_CONTEXT_POLICY_VERSION


def test_default_registry_shipped_with_legacy_stage() -> None:
    registry = default_context_registry()
    assert "legacy_full_injection_v1" in registry.names()


def test_estimator_is_stable_and_conservative() -> None:
    assert estimate_conservative_tokens("") == 0
    assert estimate_conservative_tokens("abcd") == 1
    assert estimate_conservative_tokens("abcde") == 2
    assert estimate_conservative_tokens("你好") == 2
    assert estimate_conservative_tokens("你好abcd") == 3


def test_shared_routing_payload_matches_legacy_alias() -> None:
    request = replace(_chat_request(), route=fallback_route())
    payload = chat_routing_payload_as_dict(request)
    assert payload == legacy_chat_routing_payload(request)
    assert payload is not None
    assert payload["route"]["route_source"] == "fallback"
    assert chat_routing_payload_as_dict(_chat_request()) is None


def test_manifest_jsonable_is_serializable() -> None:
    result = build_chat_context_manifest(
        policy_version=LEGACY_CONTEXT_POLICY_VERSION,
        frozen_input={"history": [{"role": "user", "content": "你好"}]},
        input_hash="hash",
        prebuilt_input="{\"a\": 1}",
    )
    json.loads(json.dumps(result.manifest.to_jsonable(), ensure_ascii=False))
