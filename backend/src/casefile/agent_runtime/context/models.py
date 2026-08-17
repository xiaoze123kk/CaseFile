"""Versioned context blocks, policies, assemblies and audit manifests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ContextBlockStatus = Literal["visible", "folded", "archived", "retrieved"]


@dataclass(frozen=True, slots=True)
class ContextDecision:
    """One audit-safe decision recorded by a stage, a guardrail or a validator."""

    stage: str
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ContextBlock:
    """A single countable, lifecycle-managed unit of working context."""

    id: str
    kind: str
    payload: Any
    tokens: int = 0
    status: ContextBlockStatus = "visible"
    recoverable: bool = False
    trimmable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StageResult:
    """Output of one context pipeline stage."""

    added: tuple[ContextBlock, ...] = ()
    replaced_ids: tuple[str, ...] = ()
    decisions: tuple[ContextDecision, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
    next_stage: str | None = None


@dataclass(frozen=True, slots=True)
class ContextPolicyStage:
    """One declared pipeline step inside a versioned context policy."""

    id: str
    strategy: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Policy-level token budget.

    ``block_limits`` and ``trim_order`` drive deterministic trimming only when
    ``enforce_budget`` is true; blocks without the ``trimmable`` flag are never
    silently shortened.
    """

    total_input_tokens: int | None = None
    enforce_budget: bool = False
    block_limits: dict[str, int] = field(default_factory=dict)
    trim_order: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContextPolicy:
    """Immutable policy document describing how to assemble one task's context."""

    schema_version: int
    version: str
    task_type: str
    stages: tuple[ContextPolicyStage, ...]
    budget: ContextBudget = field(default_factory=ContextBudget)
    guardrails: dict[str, bool] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContextAssembly:
    """Final block collection plus the decisions that produced it."""

    policy_version: str
    stage_versions: tuple[dict[str, str], ...]
    blocks: tuple[ContextBlock, ...]
    decisions: tuple[ContextDecision, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
    budget_exceeded: bool = False


@dataclass(frozen=True, slots=True)
class ContextBlockSummary:
    """Token-level ledger entry for one assembled block; payload stays out."""

    id: str
    kind: str
    tokens: int
    status: str
    recoverable: bool


@dataclass(frozen=True, slots=True)
class ContextManifest:
    """Serializable context audit manifest persisted with TaskEvents."""

    policy_version: str
    stage_versions: tuple[dict[str, str], ...]
    blocks: tuple[ContextBlockSummary, ...]
    total_tokens: int
    decisions: tuple[ContextDecision, ...]
    budget: dict[str, Any]

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "stage_versions": [dict(item) for item in self.stage_versions],
            "blocks": [
                {
                    "id": block.id,
                    "kind": block.kind,
                    "tokens": block.tokens,
                    "status": block.status,
                    "recoverable": block.recoverable,
                }
                for block in self.blocks
            ],
            "total_tokens": self.total_tokens,
            "decisions": [
                {
                    "stage": decision.stage,
                    "code": decision.code,
                    "detail": decision.detail,
                }
                for decision in self.decisions
            ],
            "budget": dict(self.budget),
        }


@dataclass(frozen=True, slots=True)
class ContextBuildResult:
    """Manifest, assembled blocks, plus a fallback decision when unavailable."""

    manifest: ContextManifest
    assembly: ContextAssembly
    fallback: ContextDecision | None = None


__all__ = [
    "ContextAssembly",
    "ContextBlock",
    "ContextBlockStatus",
    "ContextBlockSummary",
    "ContextBudget",
    "ContextBuildResult",
    "ContextDecision",
    "ContextManifest",
    "ContextPolicy",
    "ContextPolicyStage",
    "StageResult",
]
