"""Single-Agent Brief runtime with swappable model providers."""

from casefile.agent_runtime.models import (
    BriefAnchorExtractCandidate,
    BriefAnchorExtractRequest,
    BriefAnchorExtractResult,
    BriefPolishCandidate,
    BriefPolishRequest,
    BriefPolishResult,
    CaseFileChatCandidate,
    CaseFileChatRequest,
    CaseFileChatResult,
    CaseFileChatSuggestionCandidate,
    GenerationRequest,
    GenerationResult,
    ToolMetrics,
)
from casefile.agent_runtime.providers import (
    AgentProvider,
    DeepSeekAgentsProvider,
    FakeProvider,
    OpenAIAgentsProvider,
)

__all__ = [
    "AgentProvider",
    "BriefAnchorExtractCandidate",
    "BriefAnchorExtractRequest",
    "BriefAnchorExtractResult",
    "BriefPolishCandidate",
    "BriefPolishRequest",
    "BriefPolishResult",
    "CaseFileChatCandidate",
    "CaseFileChatRequest",
    "CaseFileChatResult",
    "CaseFileChatSuggestionCandidate",
    "DeepSeekAgentsProvider",
    "FakeProvider",
    "GenerationRequest",
    "GenerationResult",
    "OpenAIAgentsProvider",
    "ToolMetrics",
]
