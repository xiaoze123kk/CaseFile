"""Shared CaseFile Chat execution and bounded completion repair tests."""

from __future__ import annotations

from dataclasses import replace

import pytest

from casefile.agent_runtime.chat_execution import (
    ChatCompletionValidationError,
    ChatExecutionRunner,
    bind_chat_context_input,
    prepare_chat_request_artifacts,
    validate_chat_candidate,
)
from casefile.agent_runtime.context import CHAT_CONTEXT_POLICY_V6_VERSION
from casefile.agent_runtime.models import CaseFileChatResult, ToolMetrics
from casefile.benchmark.chat_outcome_eval import (
    _request_for_task,
    build_outcome_tasks,
    resolve_task_route,
)


class SequenceProvider:
    def __init__(self, results: list[CaseFileChatResult]) -> None:
        self.results = results
        self.requests = []

    def chat(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return self.results[len(self.requests) - 1]


def _result(candidate, tokens: int) -> CaseFileChatResult:  # type: ignore[no-untyped-def]
    return CaseFileChatResult(
        candidate=candidate,
        usage={"input_tokens": tokens, "output_tokens": tokens},
        tools=ToolMetrics(calls=1, valid_calls=1, successful_calls=1),
    )


def test_runner_repairs_one_dangling_reference_and_merges_metrics() -> None:
    task = build_outcome_tasks()[0]
    valid = task.reference_candidate
    invalid = valid.model_copy(update={"referenced_object_ids": ["obj_does_not_exist"]})
    provider = SequenceProvider([_result(invalid, 2), _result(valid, 3)])

    execution = ChatExecutionRunner(provider).run(_request_for_task(task))

    assert execution.attempts == 2
    assert execution.repair_attempted is True
    assert execution.usage["input_tokens"] == 5
    assert execution.tools.calls == 2
    assert execution.diagnostics["repair_history"][0]["attempt"] == 1
    assert execution.diagnostics["repair_history"][0]["suggestion_count"] == 0
    assert provider.requests[1].repair_feedback
    assert "obj_does_not_exist" in provider.requests[1].repair_feedback[0]


def test_runner_stops_after_one_failed_repair() -> None:
    task = build_outcome_tasks()[0]
    invalid = task.reference_candidate.model_copy(
        update={"referenced_object_ids": ["obj_does_not_exist"]}
    )
    provider = SequenceProvider([_result(invalid, 1), _result(invalid, 1)])

    with pytest.raises(ChatCompletionValidationError) as caught:
        ChatExecutionRunner(provider).run(_request_for_task(task))

    assert caught.value.code == "chat_reference_validation_failed"
    assert len(provider.requests) == 2


def test_runner_suppresses_structured_clean_noop_hallucination() -> None:
    task = next(
        item for item in build_outcome_tasks() if item.task_id == "golden-audit-clean-no-op"
    )
    hallucinated_payload = task.reference_candidate.model_dump(mode="json")
    hallucinated_payload.update(
        {
            "audit_findings": [
                {
                    "finding_id": "F1",
                    "kind": "contradiction",
                    "severity": "S2",
                    "title": "误报",
                    "statement": "误报",
                    "evidence_object_ids": ["ent_lucy", "claim_restart"],
                    "evidence_event_ids": [],
                    "evidence_validation_issue_ids": [],
                    "needs_manual_review": False,
                }
            ],
            "suggestions": [],
        }
    )
    hallucinated = task.reference_candidate.__class__.model_validate(
        hallucinated_payload
    )
    provider = SequenceProvider([_result(hallucinated, 1)])

    execution = ChatExecutionRunner(provider).run(
        replace(resolve_task_route(task), prompt_version="casefile-chat-v13")
    )

    assert execution.result.candidate.audit_findings == []
    assert execution.result.candidate.suggestions == []


def test_runner_rejects_duplicate_or_extra_edit_targets() -> None:
    task = next(
        item for item in build_outcome_tasks() if item.task_id == "golden-multi-field-edit"
    )
    candidate = task.reference_candidate.model_copy(
        update={"suggestions": [*task.reference_candidate.suggestions] * 2}
    )
    provider = SequenceProvider([_result(candidate, 1), _result(candidate, 1)])

    with pytest.raises(ChatCompletionValidationError, match="edit_target_manifest_incomplete"):
        ChatExecutionRunner(provider).run(
            replace(resolve_task_route(task), prompt_version="casefile-chat-v13")
        )

    feedback = provider.requests[1].repair_feedback[0]
    assert "preserve" in feedback
    assert "extra" in feedback


def test_runner_repairs_missing_edit_target_with_exact_delta() -> None:
    task = next(
        item for item in build_outcome_tasks() if item.task_id == "golden-multi-field-edit"
    )
    valid = task.reference_candidate
    incomplete = valid.model_copy(update={"suggestions": valid.suggestions[:1]})
    provider = SequenceProvider([_result(incomplete, 1), _result(valid, 1)])

    execution = ChatExecutionRunner(provider).run(
        replace(resolve_task_route(task), prompt_version="casefile-chat-v14")
    )

    assert execution.repair_attempted is True
    plan = provider.requests[1].repair_plan
    assert plan is not None
    assert "ent_lucy:/aliases" in plan["add"]
    assert "ent_lucy:/description" in plan["preserve"]


def test_runner_repairs_incomplete_audit_evidence() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    valid = task.reference_candidate
    broken_finding = valid.audit_findings[0].model_copy(
        update={
            "evidence_object_ids": valid.audit_findings[0].evidence_object_ids[:1],
            "evidence_event_ids": [],
            "evidence_validation_issue_ids": [],
        }
    )
    incomplete = valid.model_copy(update={"audit_findings": [broken_finding]})
    provider = SequenceProvider([_result(incomplete, 1), _result(valid, 1)])

    execution = ChatExecutionRunner(provider).run(
        replace(resolve_task_route(task), prompt_version="casefile-chat-v14")
    )

    assert execution.repair_attempted is True
    assert provider.requests[1].repair_plan is not None
    assert provider.requests[1].repair_plan["fix"][0]["code"] == (
        "audit_finding_evidence_incomplete"
    )


def test_v14_audit_rejects_suggestion_with_unsafe_frozen_simulation() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v14")
    suggestion = task.reference_candidate.suggestions[0]
    result = _result(task.reference_candidate, 1)
    result = replace(
        result,
        tool_ledger={
            "entries": [
                {
                    "tool_name": "simulate_patch_application",
                    "sanitized_arguments": {
                        "object_id": suggestion.object_id,
                        "path": suggestion.path,
                        "value_json": suggestion.value_json,
                    },
                    "bounded_result": {
                        "valid": True,
                        "advice": "introduces_new_issues",
                        "counts": {"new": 1},
                    },
                }
            ]
        },
    )

    with pytest.raises(ChatCompletionValidationError) as caught:
        validate_chat_candidate(request, result)

    assert caught.value.code == "audit_suggestion_simulation_failed"
    assert caught.value.repair_plan.remove == (
        f"{suggestion.object_id}:{suggestion.path}",
    )


def _safe_simulation_ledger(request, suggestions):  # type: ignore[no-untyped-def]
    return {
        "input_hash": request.input_hash,
        "ledger_hash": "a" * 64,
        "entries": [
            {
                "ordinal": index,
                "tool_name": "simulate_patch_application",
                "status": "ok",
                "sanitized_arguments": {
                    "object_id": suggestion.object_id,
                    "path": suggestion.path,
                    "value_json": suggestion.value_json,
                },
                "bounded_result": {
                    "valid": True,
                    "advice": "safe_to_propose",
                    "counts": {"new": 0},
                },
            }
            for index, suggestion in enumerate(suggestions, start=1)
        ],
    }


def test_v15_materializes_unique_safe_patch_without_model_repair() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    frozen = task.reference_candidate.suggestions[0]
    rewritten = frozen.model_copy(
        update={"value_json": '"叛逃后双方已经不再互信。"'}
    )
    candidate = task.reference_candidate.model_copy(update={"suggestions": [rewritten]})
    result = replace(
        _result(candidate, 1),
        tool_ledger=_safe_simulation_ledger(request, [frozen]),
    )
    provider = SequenceProvider([result])

    execution = ChatExecutionRunner(provider).run(request)

    assert execution.attempts == 1
    assert execution.repair_attempted is False
    assert execution.result.candidate.suggestions[0].value_json == frozen.value_json
    assert execution.diagnostics["safe_patch_materializations"] == [
        {
            "suggestion_index": 0,
            "target": f"{frozen.object_id}:{frozen.path}",
            "patch_id": "P1",
            "reason": "unique_safe_target",
        }
    ]


def test_v15_ambiguous_safe_patch_enters_explicit_replace_plan() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    frozen = task.reference_candidate.suggestions[0]
    alternative = frozen.model_copy(update={"value_json": '"同盟已经公开决裂。"'})
    rewritten = frozen.model_copy(update={"value_json": '"双方关系已经变化。"'})
    candidate = task.reference_candidate.model_copy(update={"suggestions": [rewritten]})
    result = replace(
        _result(candidate, 1),
        tool_ledger=_safe_simulation_ledger(request, [frozen, alternative]),
    )

    with pytest.raises(ChatCompletionValidationError) as caught:
        validate_chat_candidate(request, result)

    assert caught.value.code == "audit_suggestion_value_not_frozen"
    assert [item["patch_id"] for item in caught.value.repair_plan.replace] == ["P1", "P2"]
    assert caught.value.repair_plan.remove == ()


def test_runner_moves_known_event_out_of_object_slot() -> None:
    task = next(
        item for item in build_outcome_tasks() if item.task_id == "golden-analysis-inspect"
    )
    candidate = task.reference_candidate.model_copy(
        update={"referenced_object_ids": ["ent_lucy", "evt_restart"]}
    )
    provider = SequenceProvider([_result(candidate, 1)])

    execution = ChatExecutionRunner(provider).run(resolve_task_route(task))

    assert execution.result.candidate.referenced_object_ids == ["ent_lucy"]
    assert execution.result.candidate.referenced_event_ids == ["evt_restart"]


def test_runner_autofills_unique_event_reference_when_slot_is_empty() -> None:
    task = next(
        item for item in build_outcome_tasks() if item.task_id == "golden-event-question"
    )
    candidate = task.reference_candidate.model_copy(update={"referenced_event_ids": []})
    provider = SequenceProvider([_result(candidate, 1)])

    execution = ChatExecutionRunner(provider).run(resolve_task_route(task))

    assert execution.result.candidate.referenced_event_ids == ["evt_restart"]


def test_v13_artifacts_are_present_in_the_bound_executor_payload() -> None:
    task = next(
        item for item in build_outcome_tasks() if item.task_id == "golden-audit-clean-no-op"
    )
    request = prepare_chat_request_artifacts(
        replace(resolve_task_route(task), prompt_version="casefile-chat-v13")
    )
    request = replace(request, context_policy_version=CHAT_CONTEXT_POLICY_V6_VERSION)
    bound = bind_chat_context_input(
        request,
        frozen_input={
            "casefile": task.frozen_casefile,
            "message": task.message,
            "history": list(task.history),
            "focus": dict(task.focus or {}),
            "validation": request.validation,
        },
    )

    assert bound.assembled_input is not None
    assert "audit_evidence_bundle" in bound.assembled_input["validation"]
