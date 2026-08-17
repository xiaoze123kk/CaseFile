"""Extensible, policy-driven context engineering substrate for CaseFile chat."""

from casefile.agent_runtime.context.budget import (
    BudgetApplication,
    apply_context_budget,
    truncate_text_to_tokens,
)
from casefile.agent_runtime.context.engine import (
    ContextEngine,
    ContextEngineError,
    build_chat_context_manifest,
)
from casefile.agent_runtime.context.estimators import (
    CONSERVATIVE_TOKEN_ESTIMATOR,
    CharTokenEstimator,
    TokenEstimatorRegistry,
    TokenEstimatorRegistryError,
    UsageTokenSample,
    default_token_estimator_registry,
    estimate_conservative_tokens,
    usage_calibration_ratio,
)
from casefile.agent_runtime.context.evidence import (
    EvidenceRef,
    EvidenceRegistry,
    EvidenceResolver,
)
from casefile.agent_runtime.context.manifest import build_context_manifest
from casefile.agent_runtime.context.models import (
    ContextAssembly,
    ContextBlock,
    ContextBlockStatus,
    ContextBlockSummary,
    ContextBudget,
    ContextBuildResult,
    ContextDecision,
    ContextManifest,
    ContextPolicy,
    ContextPolicyStage,
    StageResult,
)
from casefile.agent_runtime.context.policies.loader import (
    CONTEXT_POLICY_SCHEMA_VERSION,
    ContextPolicyError,
    known_context_policy_versions,
    load_context_policy,
)
from casefile.agent_runtime.context.protocols import (
    ContextRun,
    ContextStage,
    TokenEstimator,
)
from casefile.agent_runtime.context.registry import (
    ContextRegistry,
    ContextRegistryError,
    default_context_registry,
)
from casefile.agent_runtime.context.strategies.legacy import (
    LegacyChatInputStage,
    legacy_chat_routing_payload,
)
from casefile.agent_runtime.models import LEGACY_CONTEXT_POLICY_VERSION

__all__ = [
    "CONSERVATIVE_TOKEN_ESTIMATOR",
    "CONTEXT_POLICY_SCHEMA_VERSION",
    "LEGACY_CONTEXT_POLICY_VERSION",
    "BudgetApplication",
    "CharTokenEstimator",
    "ContextAssembly",
    "ContextBlock",
    "ContextBlockStatus",
    "ContextBlockSummary",
    "ContextBudget",
    "ContextBuildResult",
    "ContextDecision",
    "ContextEngine",
    "ContextEngineError",
    "ContextManifest",
    "ContextPolicy",
    "ContextPolicyError",
    "ContextPolicyStage",
    "ContextRegistry",
    "ContextRegistryError",
    "ContextRun",
    "ContextStage",
    "EvidenceRef",
    "EvidenceRegistry",
    "EvidenceResolver",
    "LegacyChatInputStage",
    "StageResult",
    "TokenEstimator",
    "TokenEstimatorRegistry",
    "TokenEstimatorRegistryError",
    "UsageTokenSample",
    "apply_context_budget",
    "build_chat_context_manifest",
    "build_context_manifest",
    "default_context_registry",
    "default_token_estimator_registry",
    "estimate_conservative_tokens",
    "known_context_policy_versions",
    "legacy_chat_routing_payload",
    "load_context_policy",
    "truncate_text_to_tokens",
    "usage_calibration_ratio",
]
