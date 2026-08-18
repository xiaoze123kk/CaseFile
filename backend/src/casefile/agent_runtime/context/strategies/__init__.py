"""Versioned context pipeline strategy implementations."""

from casefile.agent_runtime.context.strategies.legacy import LegacyChatInputStage
from casefile.agent_runtime.context.strategies.selectors.history_window import (
    HistoryWindowStage,
    select_history_window,
)
from casefile.agent_runtime.context.strategies.sources.casefile_skeleton import (
    CaseFileSkeletonStage,
    build_casefile_skeleton,
)
from casefile.agent_runtime.context.strategies.sources.chat_contract import ChatContractStage
from casefile.agent_runtime.context.strategies.sources.focus_objects import (
    FocusObjectsStage,
    build_focus_objects_payload,
)
from casefile.agent_runtime.context.strategies.transformers.validation_trim import (
    ValidationTrimStage,
    trim_validation_issues,
)

__all__ = [
    "CaseFileSkeletonStage",
    "ChatContractStage",
    "FocusObjectsStage",
    "HistoryWindowStage",
    "LegacyChatInputStage",
    "ValidationTrimStage",
    "build_casefile_skeleton",
    "build_focus_objects_payload",
    "select_history_window",
    "trim_validation_issues",
]
