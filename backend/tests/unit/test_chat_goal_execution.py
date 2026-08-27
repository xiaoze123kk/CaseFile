from __future__ import annotations

import pytest
from casefile.agent_runtime.goal.contracts import (
    GoalDecisionOutput,
    GoalUnderstandingOutput,
)
from casefile.agent_runtime.goal.execution import (
    GoalCapabilityResult,
    GoalExecutionError,
    GoalExecutionRunner,
)
from casefile.agent_runtime.goal.filter import goal_candidate_filter
from casefile.agent_runtime.goal.policy import GoalBudget, freeze_goal, stable_hash
from casefile.agent_runtime.models import CaseFileChatRequest
from casefile.agent_runtime.provider_adapters.fake import FakeProvider

SOURCE = "先分析时间线，再审计矛盾。"


def _goal():
    output = GoalUnderstandingOutput.model_validate(
        {
            "goal": "分析并审计",
            "confidence": 0.95,
            "obligations": [
                {
                    "kind": "analysis",
                    "target_state": "baseline",
                    "source_excerpt": "分析时间线",
                },
                {
                    "kind": "audit",
                    "target_state": "baseline",
                    "source_excerpt": "审计矛盾",
                    "depends_on": [1],
                },
            ],
        }
    )
    return freeze_goal(output, SOURCE)


def _decision(capability: str, obligation_id: str) -> GoalDecisionOutput:
    return GoalDecisionOutput.model_validate(
        {
            "plan_items": [
                {"obligation_id": "obl_1", "status": "pending"},
                {"obligation_id": "obl_2", "status": "pending"},
            ],
            "action": {
                "action": "invoke_capability",
                "capability": capability,
                "obligation_ids": [obligation_id],
                "target_state": "baseline",
            },
        }
    )


def _finish() -> GoalDecisionOutput:
    return GoalDecisionOutput.model_validate(
        {
            "plan_items": [
                {"obligation_id": "obl_1", "status": "completed"},
                {"obligation_id": "obl_2", "status": "completed"},
            ],
            "action": {"action": "finish"},
        }
    )


def _request() -> CaseFileChatRequest:
    return CaseFileChatRequest(
        task_run_id=1,
        prompt_version="casefile-chat-v17",
        casefile={"schema_version": "1.0"},
        history=(),
        message=SOURCE,
        editable_fields_by_collection={},
        input_hash=stable_hash(SOURCE),
        model_id="fake",
        api_key="fake-key",
        max_turns=12,
        emit=lambda *_args: None,
    )


def _capability(action, action_no: int) -> GoalCapabilityResult:
    return GoalCapabilityResult(
        summary=f"完成 {action.obligation_ids[0]}",
        input_hash=stable_hash(["input", action_no]),
        output_hash=stable_hash(["output", action_no]),
    )


def test_goal_loop_completes_with_one_finalizer() -> None:
    provider = FakeProvider(
        goal_decisions=(
            _decision("analyze", "obl_1"),
            _decision("audit", "obl_2"),
            _finish(),
        )
    )
    result = GoalExecutionRunner(provider).run(
        _request(), _goal(), budget=GoalBudget(), execute_capability=_capability
    )
    assert result.completion.allowed
    assert len(result.observations) == 2
    assert result.decision_calls == 3
    assert result.result.candidate.suggestions == []


def test_finish_gets_one_feedback_then_fails_closed() -> None:
    provider = FakeProvider(goal_decisions=(_finish(), _finish()))
    with pytest.raises(GoalExecutionError, match="goal_completion_blocked"):
        GoalExecutionRunner(provider).run(
            _request(), _goal(), budget=GoalBudget(), execute_capability=_capability
        )


def test_repeated_action_is_no_progress() -> None:
    repeated = _decision("analyze", "obl_1")
    provider = FakeProvider(goal_decisions=(repeated, repeated))
    with pytest.raises(GoalExecutionError, match="goal_no_progress"):
        GoalExecutionRunner(provider).run(
            _request(), _goal(), budget=GoalBudget(), execute_capability=_capability
        )


def test_goal_loop_bounds_observation_summary_without_discarding_proof() -> None:
    provider = FakeProvider(
        goal_decisions=(
            _decision("analyze", "obl_1"),
            _decision("audit", "obl_2"),
            _finish(),
        )
    )

    def verbose_capability(action, action_no: int) -> GoalCapabilityResult:
        return GoalCapabilityResult(
            summary="证据" * 100,
            input_hash=stable_hash(["input", action_no]),
            output_hash=stable_hash(["output", action_no]),
            ledger_hash=stable_hash(["ledger", action_no]),
        )

    result = GoalExecutionRunner(provider).run(
        _request(),
        _goal(),
        budget=GoalBudget(max_observation_chars=64),
        execute_capability=verbose_capability,
    )

    assert result.completion.allowed is True
    assert all(len(item.summary) == 64 for item in result.observations)
    assert all(item.output_hash for item in result.observations)
    assert all(item.ledger_hash for item in result.observations)
    assert all("已截断" in item.summary for item in result.observations)


@pytest.mark.parametrize(
    ("message", "entrypoint", "candidate"),
    [
        (SOURCE, "free_text", True),
        ("审计当前时间线。", "free_text", False),
        (SOURCE, "preset", False),
        ("分析后直接修改并自动应用，不要让我确认。", "free_text", False),
        ("分析一下，然后删掉那个不需要的东西。", "free_text", False),
    ],
)
def test_candidate_filter_is_conservative(message: str, entrypoint: str, candidate: bool) -> None:
    assert goal_candidate_filter(message, routing_entrypoint=entrypoint).candidate is candidate
