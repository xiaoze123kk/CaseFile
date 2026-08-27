"""Providerless M3.7 deterministic/Fake repository gate."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from casefile.agent_runtime.goal.contracts import (
    FrozenGoal,
    GoalCapability,
    GoalDecisionOutput,
    GoalObligationKind,
    GoalTargetState,
    GoalUnderstandingOutput,
    InvokeCapabilityAction,
)
from casefile.agent_runtime.goal.execution import GoalCapabilityResult, GoalExecutionRunner
from casefile.agent_runtime.goal.filter import goal_candidate_filter
from casefile.agent_runtime.goal.policy import GoalBudget, freeze_goal, qualify_goal, stable_hash
from casefile.agent_runtime.models import CaseFileChatRequest
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.benchmark.chat_goal_suite import load_chat_goal_suite


def run_gate() -> dict[str, int | str]:
    suite = load_chat_goal_suite()
    completed = 0
    filtered = 0
    for task in suite.tasks:
        candidate = goal_candidate_filter(task.message)
        if task.expected_path == "goal":
            if not candidate.candidate:
                raise RuntimeError(f"goal candidate false-negative: {task.task_id}")
            filtered += 1
            understanding = _understanding(
                task.message, task.expected_obligation_kinds, task.expected_target_states
            )
            goal = freeze_goal(understanding, task.message)
            qualification = qualify_goal(understanding, goal, budget=GoalBudget())
            if not qualification.qualified:
                raise RuntimeError(
                    f"goal qualification failed: {task.task_id}: {qualification.reason_codes}"
                )
            decisions = tuple(_decisions(goal))
            provider = FakeProvider(goal_decisions=decisions)
            execution = GoalExecutionRunner(provider).run(
                _request(task.message),
                goal,
                budget=GoalBudget(),
                execute_capability=_capability,
            )
            if not execution.completion.allowed:
                raise RuntimeError(f"goal completion failed: {task.task_id}")
            completed += 1
        elif task.expected_path == "single" and candidate.candidate:
            raise RuntimeError(f"goal candidate false-positive: {task.task_id}")
    return {
        "suite_version": suite.suite_version,
        "tasks": len(suite.tasks),
        "goal_candidates": filtered,
        "goal_completed": completed,
    }


def _understanding(
    message: str,
    kinds: Sequence[GoalObligationKind],
    targets: Sequence[GoalTargetState],
) -> GoalUnderstandingOutput:
    obligations: list[dict[str, Any]] = []
    mutation_index: int | None = None
    for index, (kind, target) in enumerate(zip(kinds, targets, strict=True), start=1):
        dependencies = [index - 1] if index > 1 else []
        obligations.append(
            {
                "kind": kind,
                "target_state": target,
                "source_excerpt": message,
                "depends_on": dependencies,
            }
        )
        if kind == "mutation_proposal":
            mutation_index = index
        if target == "candidate" and mutation_index is None:
            raise RuntimeError("reference candidate obligation has no mutation ancestor")
    return GoalUnderstandingOutput.model_validate(
        {
            "goal": message,
            "obligations": obligations,
            "confidence": 1.0,
            "ambiguous": False,
            "missing_info": [],
        }
    )


def _decisions(goal: FrozenGoal) -> Iterator[GoalDecisionOutput]:
    statuses = {item.obligation_id: "pending" for item in goal.obligations}
    capabilities: dict[GoalObligationKind, GoalCapability] = {
        "analysis": "analyze",
        "audit": "audit",
        "mutation_proposal": "propose_mutation",
    }
    for obligation in goal.obligations:
        yield GoalDecisionOutput.model_validate(
            {
                "plan_items": [
                    {"obligation_id": key, "status": value} for key, value in statuses.items()
                ],
                "action": {
                    "action": "invoke_capability",
                    "capability": capabilities[obligation.kind],
                    "obligation_ids": [obligation.obligation_id],
                    "target_state": obligation.target_state,
                },
            }
        )
        statuses[obligation.obligation_id] = "completed"
    yield GoalDecisionOutput.model_validate(
        {
            "plan_items": [
                {"obligation_id": key, "status": value} for key, value in statuses.items()
            ],
            "action": {"action": "finish"},
        }
    )


def _request(message: str) -> CaseFileChatRequest:
    return CaseFileChatRequest(
        task_run_id=1,
        prompt_version="casefile-chat-v17",
        casefile={"schema_version": "1.0"},
        history=(),
        message=message,
        editable_fields_by_collection={},
        input_hash=stable_hash(message),
        model_id="fake",
        api_key="fake",
        max_turns=12,
        emit=lambda *_args: None,
    )


def _capability(action: InvokeCapabilityAction, action_no: int) -> GoalCapabilityResult:
    candidate_hash = (
        stable_hash("candidate")
        if (action.capability == "propose_mutation" or action.target_state == "candidate")
        else None
    )
    return GoalCapabilityResult(
        summary=f"completed {action.obligation_ids[0]}",
        input_hash=stable_hash(["input", action_no]),
        output_hash=stable_hash(["output", action_no]),
        candidate_hash=candidate_hash,
        mutation_proof_ref=(
            "general_mutation:reference" if action.capability == "propose_mutation" else None
        ),
        mutation_proof=(
            {"candidate_hash": candidate_hash} if action.capability == "propose_mutation" else None
        ),
    )


if __name__ == "__main__":
    print(run_gate())
