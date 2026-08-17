"""Deterministic policy-driven context assembly engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from casefile.agent_runtime.context.estimators import CONSERVATIVE_TOKEN_ESTIMATOR
from casefile.agent_runtime.context.manifest import build_context_manifest
from casefile.agent_runtime.context.models import (
    ContextAssembly,
    ContextBuildResult,
    ContextDecision,
    ContextPolicy,
)
from casefile.agent_runtime.context.policies.loader import (
    ContextPolicyError,
    load_context_policy,
)
from casefile.agent_runtime.context.protocols import (
    ContextRun,
    TokenEstimator,
)
from casefile.agent_runtime.context.registry import (
    ContextRegistry,
    default_context_registry,
)
from casefile.agent_runtime.models import LEGACY_CONTEXT_POLICY_VERSION


class ContextEngineError(RuntimeError):
    """A stage contract violation surfaced while assembling context."""


@dataclass(slots=True)
class ContextEngine:
    """Execute one policy's ordered stages and collect the resulting blocks."""

    registry: ContextRegistry

    def build(
        self,
        *,
        policy: ContextPolicy,
        frozen_input: dict[str, Any],
        input_hash: str,
        routing: dict[str, Any] | None = None,
        prebuilt_input: str | None = None,
        estimator: TokenEstimator | None = None,
        state: dict[str, Any] | None = None,
    ) -> ContextAssembly:
        missing = self.registry.missing_strategies(policy)
        if missing:
            raise ContextEngineError(
                f"Context policy {policy.version!r} references unknown strategies: "
                f"{sorted(missing)!r}"
            )
        resolved_estimator = estimator or CONSERVATIVE_TOKEN_ESTIMATOR
        run = ContextRun(
            policy=policy,
            frozen_input=frozen_input,
            input_hash=input_hash,
            estimator=resolved_estimator,
            routing=routing,
            prebuilt_input=prebuilt_input,
            state=dict(state or {}),
        )
        stage_ids = [policy_stage.id for policy_stage in policy.stages]
        stage_versions: list[dict[str, str]] = []
        decisions: list[ContextDecision] = []
        metrics: dict[str, Any] = {}
        index = 0
        while index < len(policy.stages):
            policy_stage = policy.stages[index]
            stage = self.registry.get(policy_stage.strategy)
            if stage.can_run(run):
                result = stage.run(run)
                for block_id in result.replaced_ids:
                    run.blocks.pop(block_id, None)
                for block in result.added:
                    if block.id in run.blocks:
                        raise ContextEngineError(
                            f"Context block {block.id!r} already exists; "
                            "declare it in replaced_ids before adding it again"
                        )
                    run.blocks[block.id] = block
                decisions.extend(result.decisions)
                metrics[policy_stage.id] = result.metrics
                stage_versions.append(
                    {
                        "stage_id": policy_stage.id,
                        "strategy": stage.name,
                        "version": stage.version,
                    }
                )
                if result.next_stage is not None:
                    if result.next_stage not in stage_ids:
                        raise ContextEngineError(
                            f"Stage {policy_stage.id!r} requested unknown next stage: "
                            f"{result.next_stage!r}"
                        )
                    next_index = stage_ids.index(result.next_stage)
                    if next_index <= index:
                        raise ContextEngineError(
                            f"Stage {policy_stage.id!r} requested non-forward jump to "
                            f"{result.next_stage!r}"
                        )
                    index = next_index
                    continue
            index += 1
        blocks = tuple(run.blocks[block_id] for block_id in run.blocks)
        total_tokens = sum(block.tokens for block in blocks)
        budget_limit = policy.budget.total_input_tokens
        budget_exceeded = budget_limit is not None and total_tokens > budget_limit
        return ContextAssembly(
            policy_version=policy.version,
            stage_versions=tuple(stage_versions),
            blocks=blocks,
            decisions=tuple(decisions),
            metrics=metrics,
            budget_exceeded=budget_exceeded,
        )


def build_chat_context_manifest(
    *,
    policy_version: str,
    frozen_input: dict[str, Any],
    input_hash: str,
    routing: dict[str, Any] | None = None,
    prebuilt_input: str | None = None,
    provider: str = "openai",
    model_id: str = "",
    estimator: TokenEstimator | None = None,
    registry: ContextRegistry | None = None,
) -> ContextBuildResult:
    """Load one policy, assemble context, and project the audit manifest.

    Unknown policy versions never fail the chat task: they fall back to the
    legacy policy and record a guardrail decision so the caller can emit
    ``context.guardrail`` before ``context.built``.
    """

    try:
        policy = load_context_policy(policy_version)
        fallback: ContextDecision | None = None
    except ContextPolicyError as error:
        policy = load_context_policy(LEGACY_CONTEXT_POLICY_VERSION)
        fallback = ContextDecision(
            stage="context.policy",
            code="context_policy_unknown_fallback",
            detail=f"{policy_version}: {error}",
        )
    resolved_estimator = estimator or CONSERVATIVE_TOKEN_ESTIMATOR
    if not resolved_estimator.supports(provider, model_id):
        raise ContextEngineError(
            f"Token estimator {resolved_estimator.name!r} does not support "
            f"provider {provider!r} model {model_id!r}"
        )
    engine = ContextEngine(registry or default_context_registry())
    assembly = engine.build(
        policy=policy,
        frozen_input=frozen_input,
        input_hash=input_hash,
        routing=routing,
        prebuilt_input=prebuilt_input,
        estimator=resolved_estimator,
    )
    return ContextBuildResult(
        manifest=build_context_manifest(assembly, policy),
        fallback=fallback,
    )


__all__ = [
    "ContextEngine",
    "ContextEngineError",
    "build_chat_context_manifest",
]
