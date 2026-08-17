"""Deterministic manifest projection for an assembled chat context."""

from __future__ import annotations

from casefile.agent_runtime.context.models import (
    ContextAssembly,
    ContextBlockSummary,
    ContextManifest,
    ContextPolicy,
)


def build_context_manifest(
    assembly: ContextAssembly,
    policy: ContextPolicy,
) -> ContextManifest:
    """Project an assembly into an audit-safe ledger without block payloads."""

    summaries = tuple(
        ContextBlockSummary(
            id=block.id,
            kind=block.kind,
            tokens=block.tokens,
            status=block.status,
            recoverable=block.recoverable,
        )
        for block in assembly.blocks
    )
    return ContextManifest(
        policy_version=assembly.policy_version,
        stage_versions=assembly.stage_versions,
        blocks=summaries,
        total_tokens=sum(block.tokens for block in summaries),
        decisions=assembly.decisions,
        budget={
            "total_input_tokens": policy.budget.total_input_tokens,
            "enforce_budget": policy.budget.enforce_budget,
            "exceeded": assembly.budget_exceeded,
        },
    )


__all__ = ["build_context_manifest"]
