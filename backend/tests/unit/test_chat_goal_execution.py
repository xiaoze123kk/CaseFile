from __future__ import annotations

import pytest
from casefile.agent_runtime.goal.contracts import (
    GoalDecisionOutput,
    GoalUnderstandingOutput,
)
from casefile.agent_runtime.goal.execution import (
    GoalCapabilityResult,
    GoalCheckpointResult,
    GoalExecutionError,
    GoalExecutionResult,
    GoalExecutionRunner,
)
from casefile.agent_runtime.goal.filter import goal_candidate_filter
from casefile.agent_runtime.goal.policy import GoalBudget, freeze_goal, stable_hash
from casefile.agent_runtime.models import CaseFileChatCandidateV2, CaseFileChatRequest
from casefile.agent_runtime.provider_adapters.fake import FakeProvider
from casefile.agent_runtime.public_language import (
    PUBLIC_GENERAL_MUTATION_SAFE_TERMINAL,
    PUBLIC_GOAL_SAFE_TERMINAL,
)
from casefile.worker.handlers.chat import _goal_ledger_refs

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
    assert result.decision_calls == 2
    assert result.result.candidate.suggestions == []


def test_goal_loop_checkpoints_after_capability_and_resumes_without_repeating_it() -> None:
    first = GoalExecutionRunner(FakeProvider(goal_decisions=(_decision("analyze", "obl_1"),))).run(
        _request(),
        _goal(),
        budget=GoalBudget(),
        execute_capability=_capability,
        should_interrupt=lambda safe_point: safe_point == "after_capability",
    )

    assert isinstance(first, GoalCheckpointResult)
    assert first.safe_point == "after_capability"
    assert [item.obligation_ids for item in first.checkpoint.observations] == [["obl_1"]]

    resumed = GoalExecutionRunner(
        FakeProvider(
            goal_decisions=(
                _decision("audit", "obl_2"),
                _finish(),
            )
        )
    ).run(
        _request(),
        _goal(),
        budget=GoalBudget(),
        execute_capability=_capability,
        checkpoint=first.checkpoint,
    )

    assert isinstance(resumed, GoalExecutionResult)
    assert [item.obligation_ids for item in resumed.observations] == [
        ["obl_1"],
        ["obl_2"],
    ]
    assert resumed.decision_calls == 1


def test_goal_loop_checkpoints_before_finalizer_and_only_finalizes_after_resume() -> None:
    interrupted = GoalExecutionRunner(
        FakeProvider(
            goal_decisions=(
                _decision("analyze", "obl_1"),
                _decision("audit", "obl_2"),
                _finish(),
            )
        )
    ).run(
        _request(),
        _goal(),
        budget=GoalBudget(),
        execute_capability=_capability,
        should_interrupt=lambda safe_point: safe_point == "before_finalizer",
    )

    assert isinstance(interrupted, GoalCheckpointResult)
    assert interrupted.checkpoint.completion is not None
    assert interrupted.checkpoint.completion.allowed is True

    resumed = GoalExecutionRunner(FakeProvider()).run(
        _request(),
        _goal(),
        budget=GoalBudget(),
        execute_capability=_capability,
        checkpoint=interrupted.checkpoint,
    )

    assert isinstance(resumed, GoalExecutionResult)
    assert resumed.decision_calls == 0
    assert resumed.result.candidate.answer


def test_goal_loop_rejects_checkpoint_for_other_obligations() -> None:
    checkpointed = GoalExecutionRunner(FakeProvider()).run(
        _request(),
        _goal(),
        budget=GoalBudget(),
        execute_capability=_capability,
        should_interrupt=lambda safe_point: safe_point == "before_controller",
    )
    assert isinstance(checkpointed, GoalCheckpointResult)
    invalid = checkpointed.checkpoint.model_copy(update={"obligations_hash": "0" * 64})

    with pytest.raises(GoalExecutionError, match="goal_checkpoint_invalid"):
        GoalExecutionRunner(FakeProvider()).run(
            _request(),
            _goal(),
            budget=GoalBudget(),
            execute_capability=_capability,
            checkpoint=invalid,
        )


def test_finish_gets_one_feedback_then_fails_closed() -> None:
    provider = FakeProvider(goal_decisions=(_finish(), _finish()))
    with pytest.raises(GoalExecutionError, match="goal_completion_blocked"):
        GoalExecutionRunner(provider).run(
            _request(), _goal(), budget=GoalBudget(), execute_capability=_capability
        )


def test_repeated_completed_action_advances_to_next_ready_obligation() -> None:
    repeated = _decision("analyze", "obl_1")
    provider = FakeProvider(goal_decisions=(repeated, repeated))
    result = GoalExecutionRunner(provider).run(
        _request(), _goal(), budget=GoalBudget(), execute_capability=_capability
    )

    assert isinstance(result, GoalExecutionResult)
    assert [item.obligation_ids for item in result.observations] == [["obl_1"], ["obl_2"]]


def test_goal_loop_normalizes_incomplete_model_plan_items() -> None:
    incomplete = _decision("analyze", "obl_1").model_copy(
        update={"plan_items": [_decision("analyze", "obl_1").plan_items[0]]}
    )
    provider = FakeProvider(
        goal_decisions=(
            incomplete,
            _decision("audit", "obl_2"),
            _finish(),
        )
    )

    result = GoalExecutionRunner(provider).run(
        _request(), _goal(), budget=GoalBudget(), execute_capability=_capability
    )

    assert result.completion.allowed is True


def test_goal_finalizer_is_prose_only_at_authoritative_boundary() -> None:
    provider = FakeProvider(
        goal_decisions=(
            _decision("analyze", "obl_1"),
            _decision("audit", "obl_2"),
            _finish(),
        ),
        goal_final_candidate=CaseFileChatCandidateV2(
            answer="已完成分析与审计。",
            referenced_object_ids=["invented_object"],
            suggestions=[
                {
                    "object_id": "invented_object",
                    "path": "/title",
                    "value_json": '"错误值"',
                    "reason": "模型建议",
                }
            ],
            audit_findings=[
                {
                    "finding_id": "F1",
                    "kind": "contradiction",
                    "severity": "S2",
                    "title": "未证明发现",
                    "statement": "缺少两端证据。",
                }
            ],
        ),
    )

    result = GoalExecutionRunner(provider).run(
        _request(), _goal(), budget=GoalBudget(), execute_capability=_capability
    )

    assert result.result.candidate.referenced_object_ids == []
    assert result.result.candidate.suggestions == []
    assert result.result.candidate.audit_findings == []


def test_mutation_goal_uses_safe_public_answer_after_language_violation() -> None:
    source = "先分析时间线，再把标题改成夜间系统重启。"
    output = GoalUnderstandingOutput.model_validate(
        {
            "goal": "分析并准备修改",
            "confidence": 1.0,
            "obligations": [
                {
                    "kind": "analysis",
                    "target_state": "baseline",
                    "source_excerpt": "分析时间线",
                },
                {
                    "kind": "mutation_proposal",
                    "target_state": "baseline",
                    "source_excerpt": "把标题改成夜间系统重启",
                    "depends_on": [1],
                },
            ],
        }
    )
    goal = freeze_goal(output, source)
    provider = FakeProvider(
        goal_decisions=(
            _decision("analyze", "obl_1"),
            _decision("propose_mutation", "obl_2"),
            _finish(),
        ),
        goal_final_candidate=CaseFileChatCandidateV2(
            answer="已根据 input_hash 完成修改准备。",
        ),
    )

    def capability(action, action_no: int) -> GoalCapabilityResult:
        if action.capability != "propose_mutation":
            return _capability(action, action_no)
        candidate_hash = stable_hash("candidate")
        return GoalCapabilityResult(
            summary="已形成待审阅建议。",
            input_hash=stable_hash(["input", action_no]),
            output_hash=stable_hash(["output", action_no]),
            candidate_hash=candidate_hash,
            mutation_proof_ref="general_mutation:proof",
            mutation_proof={"candidate_hash": candidate_hash},
        )

    result = GoalExecutionRunner(provider).run(
        _request(), goal, budget=GoalBudget(), execute_capability=capability
    )

    assert result.result.candidate.answer == PUBLIC_GENERAL_MUTATION_SAFE_TERMINAL


def test_read_only_goal_uses_safe_public_answer_after_language_violation() -> None:
    provider = FakeProvider(
        goal_decisions=(
            _decision("analyze", "obl_1"),
            _decision("audit", "obl_2"),
            _finish(),
        ),
        goal_final_candidate=CaseFileChatCandidateV2(
            answer="已根据 input_hash 完成内部 Finalizer 处理。",
        ),
    )

    result = GoalExecutionRunner(provider).run(
        _request(), _goal(), budget=GoalBudget(), execute_capability=_capability
    )

    assert result.result.candidate.answer == PUBLIC_GOAL_SAFE_TERMINAL


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
        ("先解释原因，再给出修复补丁。", "free_text", True),
        ("先分析，再给出补丁，不要自动应用。", "free_text", True),
        ("先分析，不要修改。", "free_text", False),
        ("解释“修改和审计”这个标题。", "free_text", False),
        ("Explain the issue, then repair it.", "free_text", True),
        ("分析后直接修改并自动应用，不要让我确认。", "free_text", False),
        ("分析一下，然后删掉那个不需要的东西。", "free_text", False),
    ],
)
def test_candidate_filter_is_conservative(message: str, entrypoint: str, candidate: bool) -> None:
    assert goal_candidate_filter(message, routing_entrypoint=entrypoint).candidate is candidate


def test_composite_issue_action_requires_bound_focus_before_goal_interpretation() -> None:
    message = "先解释当前问题的原因，再给出可审阅的修复补丁。"
    assert goal_candidate_filter(
        message, routing_entrypoint="issue_action", has_issue_focus=True
    ).candidate
    assert not goal_candidate_filter(message, routing_entrypoint="issue_action").candidate
    assert not goal_candidate_filter(
        "解释当前问题。", routing_entrypoint="issue_action", has_issue_focus=True
    ).candidate


def test_goal_ledger_refs_are_bounded_deduplicated_authority() -> None:
    ledger = {
        "retrieved_object_ids": ["evt_restart", "evt_restart", " ", 1],
        "retrieved_evidence_ids": ["issue_1"],
    }

    assert _goal_ledger_refs(ledger, "retrieved_object_ids") == ("evt_restart",)
    assert _goal_ledger_refs(ledger, "retrieved_evidence_ids") == ("issue_1",)
    assert _goal_ledger_refs({}, "retrieved_object_ids") == ()
