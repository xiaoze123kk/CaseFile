"""Context Dashboard projection and runtime guardrail validation.

The dashboard is a tiny, read-only audit instrument embedded in the executor
payload. It never grants new permissions: it only reports what the runtime
already enforced (used/remaining budget, largest block, protected blocks,
recoverable evidence ids) and every guardrail violation the deterministic
pipeline detected before the provider call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from casefile.agent_runtime.context.models import (
    ContextAssembly,
    ContextBlock,
    ContextDecision,
    ContextPolicy,
)

_PROTECTED_KINDS = frozenset({"chat_input_hash", "author_message"})


@dataclass(frozen=True, slots=True)
class ContextDashboard:
    """Serializable runtime view of one assembled context."""

    used_tokens: int
    budget_tokens: int | None
    remaining_tokens: int | None
    hard_input_tokens: int | None
    largest_block: dict[str, Any] = field(default_factory=dict)
    protected_blocks: tuple[str, ...] = ()
    recoverable_evidence_ids: tuple[str, ...] = ()
    guardrail_violations: tuple[dict[str, str], ...] = ()
    hard_cap_exceeded: bool = False
    policy_guardrails: dict[str, bool] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "used_tokens": self.used_tokens,
            "budget_tokens": self.budget_tokens,
            "remaining_tokens": self.remaining_tokens,
            "hard_input_tokens": self.hard_input_tokens,
            "largest_block": dict(self.largest_block),
            "protected_blocks": list(self.protected_blocks),
            "recoverable_evidence_ids": list(self.recoverable_evidence_ids),
            "guardrail_violations": [
                dict(violation) for violation in self.guardrail_violations
            ],
            "hard_cap_exceeded": self.hard_cap_exceeded,
            "policy_guardrails": dict(self.policy_guardrails),
        }


def _block_evidence_refs(block: ContextBlock) -> tuple[str, ...]:
    """Collect stable evidence pointers declared by one recoverable block."""

    refs: list[str] = []
    declared = block.metadata.get("evidence_refs")
    if isinstance(declared, list):
        refs.extend(str(value) for value in declared if isinstance(value, str) and value)
    payload = block.payload
    if isinstance(payload, dict):
        nested = payload.get("evidence_refs")
        if isinstance(nested, list):
            refs.extend(str(value) for value in nested if isinstance(value, str) and value)
    unique: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            unique.append(ref)
    return tuple(unique)


def _largest_block(blocks: tuple[ContextBlock, ...]) -> dict[str, Any]:
    if not blocks:
        return {}
    block = max(blocks, key=lambda item: item.tokens)
    return {
        "id": block.id,
        "kind": block.kind,
        "tokens": block.tokens,
        "status": block.status,
    }


def build_context_dashboard(
    assembly: ContextAssembly,
    policy: ContextPolicy,
    *,
    hard_input_tokens: int | None = None,
) -> ContextDashboard:
    """Project the deterministic guardrail view for one assembled context.

    This function only reports and validates. Enforcing the hard cap (raising
    before a provider call) is the caller's responsibility when
    ``hard_cap_exceeded`` is true.
    """

    blocks = assembly.blocks
    used_tokens = sum(block.tokens for block in blocks)
    budget_tokens = policy.budget.total_input_tokens
    remaining_tokens = (
        None
        if budget_tokens is None
        else max(0, budget_tokens - used_tokens)
    )
    protected = tuple(
        block.id
        for block in blocks
        if not block.trimmable
        or block.metadata.get("protected") is True
        or block.kind in _PROTECTED_KINDS
    )
    recoverable_refs: list[str] = []
    for block in blocks:
        if block.recoverable:
            recoverable_refs.extend(_block_evidence_refs(block))
    unique_refs: list[str] = []
    seen_refs: set[str] = set()
    for ref in recoverable_refs:
        if ref not in seen_refs:
            seen_refs.add(ref)
            unique_refs.append(ref)

    violations: list[dict[str, str]] = []
    if policy.guardrails.get("pinned_immutable") is True:
        for block in blocks:
            if block.metadata.get("protected") is True and block.trimmable:
                violations.append(
                    {
                        "reason_code": "pinned_block_trimming_allowed",
                        "block_id": block.id,
                        "detail": "pinned block must never be trimmable",
                    }
                )
    if policy.guardrails.get("recent_turns_protected") is True:
        for block in blocks:
            if block.kind == "history_window" and block.trimmable:
                violations.append(
                    {
                        "reason_code": "recent_turns_trimming_allowed",
                        "block_id": block.id,
                        "detail": "recent raw turns must never be trimmable",
                    }
                )
    if policy.guardrails.get("archive_must_be_recoverable") is True:
        for block in blocks:
            if block.status in {"archived", "folded"} and not block.recoverable:
                violations.append(
                    {
                        "reason_code": "archived_block_not_recoverable",
                        "block_id": block.id,
                        "detail": (
                            f"status={block.status} requires a recoverable "
                            "evidence pointer"
                        ),
                    }
                )
    hard_cap_exceeded = hard_input_tokens is not None and used_tokens > hard_input_tokens
    if hard_cap_exceeded:
        violations.append(
            {
                "reason_code": "hard_input_cap_exceeded",
                "block_id": "",
                "detail": (
                    f"used {used_tokens} tokens exceeds runtime hard cap "
                    f"{hard_input_tokens}; policy/model cannot relax this limit"
                ),
            }
        )
    return ContextDashboard(
        used_tokens=used_tokens,
        budget_tokens=budget_tokens,
        remaining_tokens=remaining_tokens,
        hard_input_tokens=hard_input_tokens,
        largest_block=_largest_block(blocks),
        protected_blocks=protected,
        recoverable_evidence_ids=tuple(unique_refs),
        guardrail_violations=tuple(violations),
        hard_cap_exceeded=hard_cap_exceeded,
        policy_guardrails=dict(policy.guardrails),
    )


def dashboard_guardrail_decisions(
    dashboard: ContextDashboard,
) -> tuple[ContextDecision, ...]:
    """Map dashboard violations to audit decisions for TaskEvents."""

    return tuple(
        ContextDecision(
            stage="context.guardrails",
            code=str(violation.get("reason_code") or "context_guardrail_violation"),
            detail=(
                f"{violation.get('block_id')}: {violation.get('detail')}"
                if violation.get("block_id")
                else str(violation.get("detail"))
            ),
        )
        for violation in dashboard.guardrail_violations
    )


__all__ = [
    "ContextDashboard",
    "build_context_dashboard",
    "dashboard_guardrail_decisions",
]
