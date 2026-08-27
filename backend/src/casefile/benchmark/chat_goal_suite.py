"""Tracked M3.7 Goal benchmark suite loader and deterministic fingerprint."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from casefile.agent_runtime.goal.policy import stable_hash
from casefile.agent_runtime.models import StrictAgentOutput

CHAT_GOAL_SUITE_VERSION = "casefile-chat-goal-benchmark-v2"
DEFAULT_CHAT_GOAL_SUITE = (
    Path(__file__).parents[4] / "fixtures" / "chat_goal_benchmark" / "v2" / "suite.json"
)


class ChatGoalBenchmarkTask(StrictAgentOutput):
    task_id: str = Field(pattern=r"^goal_[a-z0-9_]+$")
    family: Literal[
        "read_only",
        "mutation_create",
        "mutation_update",
        "mutation_delete",
        "candidate_review",
        "safety_stop",
        "single_neighbor",
    ]
    message: str = Field(min_length=1, max_length=4_000)
    expected_path: Literal["goal", "single", "reject"]
    expected_obligation_kinds: list[
        Literal["analysis", "audit", "mutation_proposal"]
    ] = Field(default_factory=list, max_length=6)
    expected_target_states: list[Literal["baseline", "candidate"]] = Field(
        default_factory=list, max_length=6
    )
    patch_expectation: Literal["required", "optional", "none"] | None = None
    oracle: dict[str, Any] | None = None


class ChatGoalBenchmarkSuite(StrictAgentOutput):
    suite_version: Literal[
        "casefile-chat-goal-benchmark-v1", "casefile-chat-goal-benchmark-v2"
    ]
    trials_per_task: Literal[3] = 3
    tasks: list[ChatGoalBenchmarkTask] = Field(min_length=24, max_length=24)


def load_chat_goal_suite(path: Path = DEFAULT_CHAT_GOAL_SUITE) -> ChatGoalBenchmarkSuite:
    suite = ChatGoalBenchmarkSuite.model_validate_json(path.read_text(encoding="utf-8"))
    ids = [task.task_id for task in suite.tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("chat goal benchmark task ids must be unique")
    return suite


def chat_goal_suite_fingerprint(suite: ChatGoalBenchmarkSuite) -> str:
    return stable_hash(suite.model_dump(mode="json"))


def reference_decisions(suite: ChatGoalBenchmarkSuite) -> dict[str, dict[str, object]]:
    """Providerless reference used by the deterministic/Fake repository gate."""

    return {
        task.task_id: {
            "expected_path": task.expected_path,
            "obligation_count": len(task.expected_obligation_kinds),
            "families": list(task.expected_obligation_kinds),
            "targets": list(task.expected_target_states),
            "patch_expectation": task.patch_expectation,
        }
        for task in suite.tasks
    }


__all__ = [
    "CHAT_GOAL_SUITE_VERSION",
    "ChatGoalBenchmarkSuite",
    "ChatGoalBenchmarkTask",
    "chat_goal_suite_fingerprint",
    "load_chat_goal_suite",
    "reference_decisions",
]
