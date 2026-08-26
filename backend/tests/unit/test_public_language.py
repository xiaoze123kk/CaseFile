from __future__ import annotations

from dataclasses import replace

import pytest
from casefile.agent_runtime.chat_execution import ChatExecutionRunner
from casefile.agent_runtime.models import (
    CaseFileChatAuditFindingCandidate,
    CaseFileChatCandidate,
    CaseFileChatCandidateV2,
    CaseFileChatResult,
    CaseFileChatSuggestionCandidateV2,
    ToolMetrics,
)
from casefile.agent_runtime.prompt import render_chat_finalizer_prompt
from casefile.agent_runtime.public_language import (
    PUBLIC_OUTPUT_POLICY_FAILED,
    PUBLIC_OUTPUT_POLICY_VIOLATION,
    PublicLanguageValidationError,
    public_language_rule_ids,
    validate_public_language,
)
from casefile.application.workflow_views import task_failure_view
from casefile.benchmark.chat_outcome_eval import (
    build_outcome_tasks,
    resolve_task_route,
)
from casefile.worker.failures import error_code


class _SequenceProvider:
    def __init__(self, results: list[CaseFileChatResult]) -> None:
        self.results = results
        self.requests = []

    def chat(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return self.results[len(self.requests) - 1]


def _result(candidate: CaseFileChatCandidate | CaseFileChatCandidateV2) -> CaseFileChatResult:
    return CaseFileChatResult(
        candidate=candidate,
        usage={"input_tokens": 1, "output_tokens": 1},
        tools=ToolMetrics(),
    )


def _v16_request():  # type: ignore[no-untyped-def]
    task = next(item for item in build_outcome_tasks() if item.task_id == "golden-entity-question")
    return replace(resolve_task_route(task), prompt_version="casefile-chat-v16")


def test_normal_author_language_and_nearby_words_are_not_blocked() -> None:
    candidate = CaseFileChatCandidate(
        answer=(
            "林澈的动机已经成立。这里的结构和运行节奏都能服务悬念，"
            "字段较长也不会影响读者理解；角色观看 F1 赛事也只是生活细节。"
        )
    )
    result = _result(candidate)

    validate_public_language(result)

    assert result.candidate.answer == candidate.answer


@pytest.mark.parametrize(
    ("text", "expected_rule"),
    (
        ("System Prompt 要求我展示隐藏指令。", "engineering_term"),
        ("内部结果保存在 result_jsonb。", "reserved_field"),
        ('原始内容是 {"answer":"泄漏"}。', "raw_json"),
        ("请修改 /description。", "json_pointer"),
        ("目标记录是 ent_lucy。", "internal_id"),
    ),
)
def test_prompt_injection_and_internal_output_are_rejected(
    text: str,
    expected_rule: str,
) -> None:
    with pytest.raises(PublicLanguageValidationError) as caught:
        validate_public_language(_result(CaseFileChatCandidate(answer=text)))

    assert caught.value.code == PUBLIC_OUTPUT_POLICY_VIOLATION
    assert expected_rule in caught.value.issues[0].details["rule_ids"]


def test_current_sensitive_value_is_rejected_without_echoing_it_in_diagnostics() -> None:
    canary = "phase3-current-secret-canary"

    with pytest.raises(PublicLanguageValidationError) as caught:
        validate_public_language(
            _result(CaseFileChatCandidate(answer=f"当前值为 {canary}")),
            sensitive_values=(canary,),
        )

    diagnostic = repr([issue.as_dict() for issue in caught.value.issues])
    assert "current_sensitive_value" in diagnostic
    assert canary not in diagnostic
    assert canary not in caught.value.repair_feedback()


def test_public_rule_probe_returns_only_stable_rule_ids() -> None:
    canary = "phase6-sensitive-canary"

    rules = public_language_rule_ids(
        f"TaskRun 当前值为 {canary}",
        sensitive_values=(canary,),
    )

    assert rules == ("current_sensitive_value", "engineering_term")
    assert canary not in repr(rules)


def test_finding_and_suggestion_public_prose_are_all_guarded() -> None:
    candidate = CaseFileChatCandidateV2(
        answer="这处冲突需要作者确认。",
        suggestions=[
            CaseFileChatSuggestionCandidateV2(
                object_id="ent_lucy",
                path="/description",
                value_json='"修正后的描述"',
                reason="Provider 已经完成检查。",
                finding_ref="F1",
            )
        ],
        audit_findings=[
            CaseFileChatAuditFindingCandidate(
                finding_id="F1",
                kind="contradiction",
                severity="S2",
                title="TaskRun 状态冲突",
                statement="证据来自 /description。",
                evidence_object_ids=["ent_lucy", "claim_restart"],
            )
        ],
    )

    with pytest.raises(PublicLanguageValidationError) as caught:
        validate_public_language(_result(candidate))

    assert {issue.path for issue in caught.value.issues} == {
        "/audit_findings/0/title",
        "/audit_findings/0/statement",
        "/suggestions/0/reason",
    }


def test_first_violation_uses_existing_bounded_repair_and_can_succeed() -> None:
    valid = _v16_request()
    task = next(item for item in build_outcome_tasks() if item.task_id == "golden-entity-question")
    good_candidate = task.reference_candidate
    bad_candidate = good_candidate.model_copy(
        update={"answer": "System Prompt 中包含内部组件说明。"}
    )
    provider = _SequenceProvider([_result(bad_candidate), _result(good_candidate)])
    completed: list[CaseFileChatResult] = []

    execution = ChatExecutionRunner(provider).run(valid, complete=completed.append)

    assert execution.attempts == 2
    assert execution.repair_attempted is True
    assert len(completed) == 1
    assert completed[0].candidate.answer == good_candidate.answer
    assert provider.requests[1].repair_feedback
    assert "自然、清楚的中文创作者语言" in provider.requests[1].repair_feedback[0]
    repair_request = provider.requests[1]
    instructions, _input_text = render_chat_finalizer_prompt(
        repair_request,
        tool_ledger=repair_request.frozen_tool_ledger,
        evidence_summary="",
        previous_candidate=repair_request.previous_candidate,
        repair_plan=repair_request.repair_plan,
    )
    assert "只执行下列最小修复" in instructions
    assert "自然、清楚的中文创作者语言" in instructions


def test_second_violation_fails_closed_before_patch_persistence() -> None:
    request = _v16_request()
    task = next(item for item in build_outcome_tasks() if item.task_id == "golden-entity-question")
    first = task.reference_candidate.model_copy(
        update={"answer": "System Prompt 中包含内部组件说明。"}
    )
    second = task.reference_candidate.model_copy(
        update={"answer": "内部结果保存在 payload_jsonb。"}
    )
    provider = _SequenceProvider([_result(first), _result(second)])
    completed: list[CaseFileChatResult] = []

    with pytest.raises(PublicLanguageValidationError) as caught:
        ChatExecutionRunner(provider).run(request, complete=completed.append)

    assert caught.value.code == PUBLIC_OUTPUT_POLICY_FAILED
    assert error_code(caught.value) == PUBLIC_OUTPUT_POLICY_FAILED
    assert len(provider.requests) == 2
    assert completed == []
    assert caught.value.attempts == 2
    assert caught.value.repair_attempted is True
    public_failure = task_failure_view(error_code(caught.value))
    assert public_failure == {
        "code": PUBLIC_OUTPUT_POLICY_FAILED,
        "message": "本次回复未通过安全检查，未生成修改建议，请重新表述后再试。",
        "retryable": False,
        "issues": [],
    }


def test_v15_remains_outside_public_language_gate() -> None:
    task = next(item for item in build_outcome_tasks() if item.task_id == "golden-entity-question")
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    candidate = task.reference_candidate.model_copy(
        update={"answer": "System Prompt 中包含内部组件说明。"}
    )

    execution = ChatExecutionRunner(_SequenceProvider([_result(candidate)])).run(request)

    assert execution.attempts == 1
    assert execution.result.candidate.answer == candidate.answer
