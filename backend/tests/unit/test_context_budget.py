"""Deterministic budget trimming and estimator registry tests."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from casefile.agent_runtime.context import (
    CONSERVATIVE_TOKEN_ESTIMATOR,
    ContextBlock,
    ContextBudget,
    ContextDecision,
    ContextEngine,
    ContextPolicy,
    ContextPolicyStage,
    ContextRegistry,
    ContextRun,
    StageResult,
    TokenEstimatorRegistry,
    TokenEstimatorRegistryError,
    UsageTokenSample,
    apply_context_budget,
    build_chat_context_manifest,
    build_context_manifest,
    default_token_estimator_registry,
    estimate_conservative_tokens,
    truncate_text_to_tokens,
    usage_calibration_ratio,
)


@dataclass(slots=True)
class _LongTextStage:
    name: str = "long_text_v1"
    version: str = "long-text-v1"
    capabilities: frozenset[str] = frozenset({"source"})
    text: str = "x" * 400
    trimmable: bool = True
    block_id: str = "long_text"

    def can_run(self, run: ContextRun) -> bool:
        return True

    def run(self, run: ContextRun) -> StageResult:
        return StageResult(
            added=(
                ContextBlock(
                    id=self.block_id,
                    kind="long_text",
                    payload=self.text,
                    tokens=run.estimator.estimate(self.text),
                    trimmable=self.trimmable,
                ),
            ),
        )


def _policy(
    *stages: ContextPolicyStage,
    budget: ContextBudget,
    version: str = "budget-test-policy-v1",
) -> ContextPolicy:
    return ContextPolicy(
        schema_version=1,
        version=version,
        task_type="casefile_chat",
        stages=stages,
        budget=budget,
    )


def _text_block(
    block_id: str,
    text: str,
    *,
    trimmable: bool = True,
) -> ContextBlock:
    return ContextBlock(
        id=block_id,
        kind="text",
        payload=text,
        tokens=estimate_conservative_tokens(text),
        trimmable=trimmable,
    )


def test_truncate_text_to_tokens_is_deterministic_and_monotonic() -> None:
    estimator = CONSERVATIVE_TOKEN_ESTIMATOR
    assert truncate_text_to_tokens("abcd", 1, estimator) == "abcd"
    assert truncate_text_to_tokens("abcde", 1, estimator) == "abcd"
    assert truncate_text_to_tokens("abcde", 2, estimator) == "abcde"
    assert truncate_text_to_tokens("你好世界", 2, estimator) == "你好"
    assert truncate_text_to_tokens("", 1, estimator) == ""
    with pytest.raises(ValueError):
        truncate_text_to_tokens("text", 0, estimator)


def test_measure_only_policy_never_changes_blocks() -> None:
    policy = _policy(
        ContextPolicyStage(id="first", strategy="long_text_v1"),
        budget=ContextBudget(total_input_tokens=1, enforce_budget=False),
    )
    registry = ContextRegistry()
    registry.register(_LongTextStage())
    assembly = ContextEngine(registry).build(
        policy=policy,
        frozen_input={},
        input_hash="hash",
    )
    assert assembly.budget_exceeded is True
    assert assembly.blocks[0].payload == "x" * 400
    assert [decision.code for decision in assembly.decisions] == [
        "budget_total_exceeded"
    ]


def test_budget_trims_trimmable_text_blocks_in_trim_order() -> None:
    long_block = _text_block("long_text", "x" * 400)
    policy = _policy(
        ContextPolicyStage(id="first", strategy="long_text_v1"),
        budget=ContextBudget(
            total_input_tokens=20,
            enforce_budget=True,
            block_limits={"long_text": 10},
            trim_order=("long_text",),
        ),
    )
    application = apply_context_budget(
        policy=policy,
        blocks=(long_block,),
        estimator=CONSERVATIVE_TOKEN_ESTIMATOR,
    )
    assert application.exceeded is False
    assert application.blocks[0].tokens == 10
    assert application.blocks[0].payload == "x" * 40
    assert application.blocks[0].metadata["trimmed"] is True
    assert application.decisions[0].code == "block_trimmed"


def test_budget_protects_untrimmable_blocks() -> None:
    protected = _text_block("pinned", "必须保留的内容" * 50, trimmable=False)
    policy = _policy(
        ContextPolicyStage(id="first", strategy="long_text_v1"),
        budget=ContextBudget(
            total_input_tokens=10,
            enforce_budget=True,
            block_limits={"pinned": 10},
            trim_order=("pinned",),
        ),
    )
    application = apply_context_budget(
        policy=policy,
        blocks=(protected,),
        estimator=CONSERVATIVE_TOKEN_ESTIMATOR,
    )
    assert application.blocks[0].payload == protected.payload
    assert [decision.code for decision in application.decisions] == [
        "budget_block_protected",
        "budget_total_exceeded",
    ]


def test_budget_reports_invalid_targets_and_non_text_blocks() -> None:
    structured = ContextBlock(
        id="structured",
        kind="structured",
        payload={"ids": [1, 2, 3]},
        tokens=20,
        trimmable=True,
    )
    zero_limit = _text_block("zero_limit", "abc", trimmable=True)
    policy = _policy(
        ContextPolicyStage(id="first", strategy="long_text_v1"),
        budget=ContextBudget(
            total_input_tokens=10,
            enforce_budget=True,
            block_limits={"structured": 10, "zero_limit": 0},
            trim_order=("unknown_block", "structured", "zero_limit"),
        ),
    )
    application = apply_context_budget(
        policy=policy,
        blocks=(structured, zero_limit),
        estimator=CONSERVATIVE_TOKEN_ESTIMATOR,
    )
    assert [decision.code for decision in application.decisions] == [
        "budget_trim_target_missing",
        "budget_block_not_text",
        "budget_block_limit_invalid",
        "budget_total_exceeded",
    ]


def test_engine_manifest_reflects_budget_trimming() -> None:
    policy = _policy(
        ContextPolicyStage(id="first", strategy="long_text_v1"),
        budget=ContextBudget(
            total_input_tokens=20,
            enforce_budget=True,
            block_limits={"long_text": 10},
            trim_order=("long_text",),
        ),
    )
    registry = ContextRegistry()
    registry.register(_LongTextStage())
    assembly = ContextEngine(registry).build(
        policy=policy,
        frozen_input={},
        input_hash="hash",
    )
    assert assembly.budget_exceeded is False
    assert assembly.blocks[0].tokens == 10
    assert assembly.metrics["context.budget"]["enforce_budget"] is True
    assert assembly.decisions[0].code == "block_trimmed"


def test_estimator_registry_selects_first_supported_estimator() -> None:
    registry = default_token_estimator_registry()
    assert registry.names() == ("char_conservative_v1",)
    assert registry.select("openai", "gpt-5.6-sol").name == "char_conservative_v1"
    assert registry.select("deepseek", "deepseek-v4-flash").name == "char_conservative_v1"


def test_estimator_registry_rejects_duplicate_names() -> None:
    registry = TokenEstimatorRegistry()
    registry.register(CONSERVATIVE_TOKEN_ESTIMATOR)
    with pytest.raises(TokenEstimatorRegistryError, match="already registered"):
        registry.register(CONSERVATIVE_TOKEN_ESTIMATOR)


def test_empty_estimator_registry_reports_missing_support() -> None:
    registry = TokenEstimatorRegistry()
    with pytest.raises(TokenEstimatorRegistryError, match="No token estimator supports"):
        registry.select("openai", "gpt-5.6-sol")


def test_usage_calibration_ratio_uses_median() -> None:
    assert usage_calibration_ratio([]) is None
    samples = [
        UsageTokenSample("openai", "gpt", 10, 12),
        UsageTokenSample("openai", "gpt", 10, 20),
        UsageTokenSample("openai", "gpt", 0, 12),
        UsageTokenSample("deepseek", "deepseek", 5, 10),
    ]
    assert usage_calibration_ratio(samples) == 2.0


def test_build_manifest_uses_default_estimator_registry() -> None:
    result = build_chat_context_manifest(
        policy_version="agent-focus-v1",
        frozen_input={},
        input_hash="hash",
        prebuilt_input="你好abcd",
        provider="deepseek",
        model_id="deepseek-v4-flash",
    )
    assert result.manifest.total_tokens == estimate_conservative_tokens("你好abcd")


def test_manifest_includes_block_limits_and_trim_order() -> None:
    policy = _policy(
        ContextPolicyStage(id="first", strategy="long_text_v1"),
        budget=ContextBudget(
            total_input_tokens=20,
            enforce_budget=True,
            block_limits={"long_text": 10},
            trim_order=("long_text",),
        ),
    )
    registry = ContextRegistry()
    registry.register(_LongTextStage())
    assembly = ContextEngine(registry).build(
        policy=policy,
        frozen_input={},
        input_hash="hash",
    )
    jsonable = build_context_manifest(assembly, policy).to_jsonable()
    assert jsonable["budget"]["block_limits"] == {"long_text": 10}
    assert jsonable["budget"]["trim_order"] == ["long_text"]


def test_context_decision_is_audit_safe() -> None:
    decision = ContextDecision(stage="context.budget", code="block_trimmed", detail="x")
    assert decision.stage == "context.budget"
