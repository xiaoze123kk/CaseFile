"""Deterministic token budget application for assembled context blocks."""

from __future__ import annotations

from dataclasses import dataclass, replace

from casefile.agent_runtime.context.models import (
    ContextBlock,
    ContextDecision,
    ContextPolicy,
)
from casefile.agent_runtime.context.protocols import TokenEstimator


@dataclass(frozen=True, slots=True)
class BudgetApplication:
    """Blocks after budget processing plus every guardrail decision."""

    blocks: tuple[ContextBlock, ...]
    decisions: tuple[ContextDecision, ...]
    exceeded: bool


def truncate_text_to_tokens(text: str, max_tokens: int, estimator: TokenEstimator) -> str:
    """Return the longest prefix whose estimate fits within ``max_tokens``.

    The caller guarantees ``max_tokens >= 1``. This assumes a monotonic
    estimator (longer text never estimates fewer tokens), which the shipped
    estimator satisfies; calibrated estimators must keep that property.
    """

    if max_tokens < 1:
        raise ValueError("max_tokens must be at least 1")
    if estimator.estimate(text) <= max_tokens:
        return text
    low = 0
    high = len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimator.estimate(text[:middle]) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def apply_context_budget(
    *,
    policy: ContextPolicy,
    blocks: tuple[ContextBlock, ...],
    estimator: TokenEstimator,
) -> BudgetApplication:
    """Apply the policy budget in fixed trim order without touching protected blocks.

    When ``enforce_budget`` is false the blocks are returned unchanged; the
    result still reports whether the policy limit was exceeded so the manifest
    can measure the legacy path without changing what the model sees.
    """

    limit = policy.budget.total_input_tokens
    decisions: list[ContextDecision] = []
    by_id = {block.id: block for block in blocks}
    total_tokens = sum(block.tokens for block in blocks)
    if not policy.budget.enforce_budget:
        exceeded = limit is not None and total_tokens > limit
        if exceeded:
            decisions.append(
                ContextDecision(
                    stage="context.budget",
                    code="budget_total_exceeded",
                    detail=(
                        f"total {total_tokens} tokens exceeds policy limit {limit}; "
                        "enforce_budget=false keeps blocks unchanged"
                    ),
                )
            )
        return BudgetApplication(blocks=blocks, decisions=tuple(decisions), exceeded=exceeded)
    if limit is None or total_tokens <= limit:
        return BudgetApplication(
            blocks=blocks,
            decisions=tuple(decisions),
            exceeded=total_tokens > (limit or 0),
        )
    for block_id in policy.budget.trim_order:
        if total_tokens <= limit:
            break
        block = by_id.get(block_id)
        if block is None:
            decisions.append(
                ContextDecision(
                    stage="context.budget",
                    code="budget_trim_target_missing",
                    detail=f"trim_order references unknown block {block_id!r}",
                )
            )
            continue
        block_limit = policy.budget.block_limits.get(block_id)
        if block_limit is None or block_limit < 1:
            decisions.append(
                ContextDecision(
                    stage="context.budget",
                    code="budget_block_limit_invalid",
                    detail=f"block {block_id!r} has no valid positive block limit",
                )
            )
            continue
        if not block.trimmable:
            decisions.append(
                ContextDecision(
                    stage="context.budget",
                    code="budget_block_protected",
                    detail=f"block {block_id!r} is protected and will not be trimmed",
                )
            )
            continue
        if not isinstance(block.payload, str):
            decisions.append(
                ContextDecision(
                    stage="context.budget",
                    code="budget_block_not_text",
                    detail=f"block {block_id!r} payload is not trimmable text",
                )
            )
            continue
        trimmed = truncate_text_to_tokens(block.payload, block_limit, estimator)
        trimmed_tokens = estimator.estimate(trimmed)
        by_id[block_id] = replace(
            block,
            payload=trimmed,
            tokens=trimmed_tokens,
            metadata={**block.metadata, "trimmed": True, "trim_limit": block_limit},
        )
        total_tokens -= block.tokens - trimmed_tokens
        decisions.append(
            ContextDecision(
                stage="context.budget",
                code="block_trimmed",
                detail=(
                    f"block {block_id!r} trimmed from {block.tokens} to "
                    f"{trimmed_tokens} estimated tokens"
                ),
            )
        )
    ordered = tuple(by_id[block.id] for block in blocks)
    exceeded = total_tokens > limit
    if exceeded:
        decisions.append(
            ContextDecision(
                stage="context.budget",
                code="budget_total_exceeded",
                detail=f"total {total_tokens} tokens still exceeds policy limit {limit}",
            )
        )
    return BudgetApplication(blocks=ordered, decisions=tuple(decisions), exceeded=exceeded)


__all__ = [
    "BudgetApplication",
    "apply_context_budget",
    "truncate_text_to_tokens",
]
