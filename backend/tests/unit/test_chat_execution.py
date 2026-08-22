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
from casefile.agent_runtime.chat_routing import fallback_route
from casefile.agent_runtime.chat_validation import (
    ValidationIssue,
    plan_repairs,
    select_semantic_repair_mode,
)
from casefile.agent_runtime.context import CHAT_CONTEXT_POLICY_V6_VERSION
from casefile.agent_runtime.models import (
    CaseFileChatResult,
    CaseFileChatTargetLockedRepairOutput,
    ToolMetrics,
)
from casefile.agent_runtime.prompt import (
    chat_finalizer_output_type,
    render_chat_finalizer_prompt,
)
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


@pytest.mark.parametrize(
    (
        "attempt",
        "has_authoritative_target",
        "currently_target_locked",
        "no_progress",
        "expected_mode",
    ),
    (
        (1, False, False, False, "minimal"),
        (2, False, False, False, "minimal"),
        (2, False, False, True, None),
        (2, True, False, False, "target_locked"),
        (3, False, False, False, None),
        (3, True, False, True, "target_locked"),
        (3, True, True, True, "target_locked"),
        (4, True, True, False, None),
    ),
)
def test_semantic_repair_policy_freezes_runner_transition_matrix(
    attempt: int,
    has_authoritative_target: bool,
    currently_target_locked: bool,
    no_progress: bool,
    expected_mode: str | None,
) -> None:
    plan = plan_repairs(
        (
            ValidationIssue(
                code="repairable",
                stage="validation",
                path="/suggestions",
                message="需要修复。",
                repairable=True,
                details={"missing": ["ent_target:/description"]},
            ),
        )
    )

    assert (
        select_semantic_repair_mode(
            attempt=attempt,
            repair_plan=plan,
            has_authoritative_target=has_authoritative_target,
            currently_target_locked=currently_target_locked,
            no_progress=no_progress,
        )
        == expected_mode
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


def test_runner_suppresses_denied_route_suggestions_before_completion() -> None:
    question_task = next(
        item for item in build_outcome_tasks() if item.task_id == "golden-entity-question"
    )
    edit_task = next(
        item for item in build_outcome_tasks() if item.task_id == "golden-edit-description"
    )
    candidate = question_task.reference_candidate.model_copy(
        update={"suggestions": [edit_task.reference_candidate.suggestions[0]]}
    )
    events: list[tuple[str, str, dict]] = []
    persisted: list[CaseFileChatResult] = []
    request = replace(
        _request_for_task(question_task),
        route=fallback_route(reason_codes=("confidence_gate_sensitive",)),
        emit=lambda event_type, stage, payload: events.append(
            (event_type, stage, payload)
        ),
    )

    execution = ChatExecutionRunner(SequenceProvider([_result(candidate, 1)])).run(
        request,
        complete=persisted.append,
    )

    assert execution.attempts == 1
    assert execution.result.candidate.suggestions == []
    assert persisted[0].candidate.suggestions == []
    assert len(events) == 1
    event_type, stage, payload = events[0]
    assert event_type == "route.suggestions_suppressed"
    assert stage == "routing"
    assert payload["route_source"] == "fallback"
    assert payload["execution_profile"]["primary_intent"] == "question"
    assert payload["suggestion_policy"] == "deny"
    assert payload["suppressed_count"] == 1
    assert payload["source"] == "shared_execution_runner"


def test_runner_repairs_a_non_audit_suggestion_rejected_by_server_gate() -> None:
    task = next(
        item for item in build_outcome_tasks() if item.task_id == "golden-edit-description"
    )
    invalid_suggestion = task.reference_candidate.suggestions[0].model_copy(
        update={"value_json": "```not-json```"}
    )
    invalid = task.reference_candidate.model_copy(
        update={"suggestions": [invalid_suggestion]}
    )
    provider = SequenceProvider([_result(invalid, 1), _result(task.reference_candidate, 1)])

    execution = ChatExecutionRunner(provider).run(resolve_task_route(task))

    assert execution.attempts == 2
    assert execution.result.candidate.suggestions == task.reference_candidate.suggestions
    repair_plan = provider.requests[1].repair_plan
    assert repair_plan is not None
    assert repair_plan["add"] == ["ent_lucy:/description"]
    assert repair_plan["replace"][0]["reason_code"] == "value_json_wrapped_in_markdown"


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


def test_v15_server_gate_proves_safe_patch_without_tool_simulation() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    proposed = task.reference_candidate.suggestions[0].model_copy(
        update={"value_json": '"叛逃后双方已经不再互信。"'}
    )
    candidate = task.reference_candidate.model_copy(update={"suggestions": [proposed]})
    result = _result(candidate, 1)
    provider = SequenceProvider([result])

    execution = ChatExecutionRunner(provider).run(request)

    assert execution.attempts == 1
    assert execution.repair_attempted is False
    assert execution.result.candidate.suggestions[0].value_json == proposed.value_json
    registry = execution.result.safe_patch_registry
    assert registry is not None
    assert registry["candidates"][0]["source"] == "server_post_finalizer_gate"
    assert registry["candidates"][0]["simulation_passed"] is True
    assert provider.requests[0].frozen_tool_ledger is None


def test_v15_server_gate_quotes_plain_text_and_discards_redundant_target() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    first = task.reference_candidate.suggestions[0].model_copy(
        update={"value_json": "叛逃后双方已经不再互信。"}
    )
    duplicate = task.reference_candidate.suggestions[0].model_copy(
        update={"value_json": '"同盟已经决裂。"'}
    )
    candidate = task.reference_candidate.model_copy(update={"suggestions": [first, duplicate]})

    execution = ChatExecutionRunner(SequenceProvider([_result(candidate, 1)])).run(request)

    assert execution.repair_attempted is False
    assert len(execution.result.candidate.suggestions) == 1
    assert execution.result.candidate.suggestions[0].value_json == '"叛逃后双方已经不再互信。"'
    assert execution.result.safe_patch_registry is not None
    assert len(execution.result.safe_patch_registry["candidates"]) == 1


def test_v15_server_gate_rejects_unsafe_patch_then_repairs() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    unsafe = task.reference_candidate.suggestions[0].model_copy(update={"path": "/id"})
    invalid = task.reference_candidate.model_copy(update={"suggestions": [unsafe]})
    provider = SequenceProvider([_result(invalid, 1), _result(task.reference_candidate, 1)])

    execution = ChatExecutionRunner(provider).run(request)

    assert execution.repair_attempted is True
    repair = execution.diagnostics["repair_history"][0]
    assert repair["validation_issues"][0]["code"] == "audit_suggestion_server_gate_failed"
    assert provider.requests[1].repair_plan is not None
    assert provider.requests[1].repair_plan["remove"] == ["ent_leader:/id"]
    assert provider.requests[1].repair_plan["add"] == ["ent_leader:/description"]


def test_v15_empty_suggestions_with_repairable_finding_enters_repair_plan() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    missing = task.reference_candidate.model_copy(update={"suggestions": []})
    provider = SequenceProvider([_result(missing, 1), _result(task.reference_candidate, 1)])

    execution = ChatExecutionRunner(provider).run(request)

    assert execution.repair_attempted is True
    repair_plan = provider.requests[1].repair_plan
    assert repair_plan is not None
    assert repair_plan["add"] == ["ent_leader:/description"]
    assert repair_plan["fix"][0]["code"] == "audit_repairable_finding_missing_suggestion"


def test_v15_dedupes_same_endpoint_findings_and_rebinds_suggestion() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    first = task.reference_candidate.audit_findings[0]
    duplicate = first.model_copy(
        update={
            "finding_id": "F2",
            "kind": "scope_gap",
            "title": "同一证据对的重复表述",
        }
    )
    suggestion = task.reference_candidate.suggestions[0].model_copy(update={"finding_ref": "F2"})
    candidate = task.reference_candidate.model_copy(
        update={"audit_findings": [first, duplicate], "suggestions": [suggestion]}
    )

    execution = ChatExecutionRunner(SequenceProvider([_result(candidate, 1)])).run(request)

    assert [finding.finding_id for finding in execution.result.candidate.audit_findings] == ["F1"]
    assert execution.result.candidate.suggestions[0].finding_ref == "F1"


def test_v15_adds_event_suggestion_to_the_event_reference_slot() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-vanishing-route"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    finding = task.reference_candidate.audit_findings[0].model_copy(
        update={"evidence_object_ids": ["ent_captain"]}
    )
    candidate = task.reference_candidate.model_copy(
        update={"referenced_event_ids": [], "audit_findings": [finding]}
    )

    execution = ChatExecutionRunner(SequenceProvider([_result(candidate, 1)])).run(request)

    assert "evt_departure" in execution.result.candidate.referenced_event_ids


def test_v15_accepts_one_event_id_for_a_same_event_field_conflict() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-vanishing-route"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")

    execution = ChatExecutionRunner(
        SequenceProvider([_result(task.reference_candidate, 1)])
    ).run(request)

    assert execution.result.candidate.audit_findings[0].evidence_event_ids == ["evt_departure"]


def test_v15_autofills_connected_deterministic_pair_evidence() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-restart-loop"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    finding = task.reference_candidate.audit_findings[0].model_copy(
        update={
            "evidence_object_ids": ["ent_researcher", "claim_backup_trigger"],
        }
    )
    candidate = task.reference_candidate.model_copy(update={"audit_findings": [finding]})

    execution = ChatExecutionRunner(SequenceProvider([_result(candidate, 1)])).run(request)

    assert "ent_backup_system" in execution.result.candidate.audit_findings[0].evidence_object_ids


def test_v15_unbinds_suggestion_from_a_manual_review_finding() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    manual = task.reference_candidate.audit_findings[0].model_copy(
        update={"needs_manual_review": True}
    )
    candidate = task.reference_candidate.model_copy(update={"audit_findings": [manual]})

    execution = ChatExecutionRunner(SequenceProvider([_result(candidate, 1)])).run(request)

    assert execution.result.candidate.suggestions[0].finding_ref is None


def test_v15_dedupes_manual_finding_suggestions_before_unbinding() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    manual = task.reference_candidate.audit_findings[0].model_copy(
        update={"needs_manual_review": True}
    )
    extra = task.reference_candidate.suggestions[0].model_copy(
        update={
            "object_id": "ent_defector",
            "path": "/description",
            "value_json": '"叛逃已经让同盟公开破裂。"',
        }
    )
    candidate = task.reference_candidate.model_copy(
        update={
            "audit_findings": [manual],
            "suggestions": [task.reference_candidate.suggestions[0], extra],
        }
    )

    execution = ChatExecutionRunner(SequenceProvider([_result(candidate, 1)])).run(request)

    assert len(execution.result.candidate.suggestions) == 1
    assert execution.result.candidate.suggestions[0].object_id == "ent_leader"
    assert execution.result.candidate.suggestions[0].finding_ref is None


def test_v15_keeps_one_minimal_suggestion_per_finding() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    extra = task.reference_candidate.suggestions[0].model_copy(
        update={
            "object_id": "ent_defector",
            "path": "/description",
            "value_json": '"叛逃已经让同盟公开破裂。"',
        }
    )
    candidate = task.reference_candidate.model_copy(
        update={"suggestions": [task.reference_candidate.suggestions[0], extra]}
    )

    execution = ChatExecutionRunner(SequenceProvider([_result(candidate, 1)])).run(request)

    assert len(execution.result.candidate.suggestions) == 1
    assert execution.result.candidate.suggestions[0].object_id == "ent_leader"


def test_v15_manual_only_deterministic_pair_requires_an_unbound_repair() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-fractured-alliance"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    manual = task.reference_candidate.audit_findings[0].model_copy(
        update={"needs_manual_review": True}
    )
    incomplete = task.reference_candidate.model_copy(
        update={"audit_findings": [manual], "suggestions": []}
    )
    provider = SequenceProvider([_result(incomplete, 1), _result(task.reference_candidate, 1)])

    execution = ChatExecutionRunner(provider).run(request)

    assert execution.repair_attempted is True
    assert provider.requests[1].repair_plan is not None
    assert provider.requests[1].repair_plan["fix"][0]["code"] == (
        "audit_deterministic_pair_missing_suggestion"
    )
    assert provider.requests[1].repair_plan["fix"][0]["details"]["finding_ref"] is None


def test_v15_repair_expectation_replaces_an_unrelated_safe_target() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-restart-loop"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    unrelated = task.reference_candidate.suggestions[0].model_copy(
        update={
            "object_id": "evt_restart_seven",
            "path": "/truth_status",
            "value_json": '"canon_true"',
        }
    )
    invalid = task.reference_candidate.model_copy(update={"suggestions": [unrelated]})
    provider = SequenceProvider([_result(invalid, 1), _result(task.reference_candidate, 1)])

    execution = ChatExecutionRunner(provider).run(request)

    assert execution.repair_attempted is True
    repair_plan = provider.requests[1].repair_plan
    assert repair_plan is not None
    assert repair_plan["add"] == ["ent_researcher:/description"]
    assert repair_plan["remove"] == ["evt_restart_seven:/truth_status"]
    assert repair_plan["fix"][0]["code"] == "audit_repair_expectation_missing_target"


def test_v15_target_locked_repair_materializes_only_the_server_locked_target() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-restart-loop"
    )
    events: list[tuple[str, str, dict]] = []
    request = replace(
        resolve_task_route(task),
        prompt_version="casefile-chat-v15",
        emit=lambda event_type, stage, payload: events.append((event_type, stage, payload)),
    )
    unrelated = task.reference_candidate.suggestions[0].model_copy(
        update={
            "object_id": "evt_restart_seven",
            "path": "/truth_status",
            "value_json": '"canon_true"',
        }
    )
    invalid = task.reference_candidate.model_copy(update={"suggestions": [unrelated]})
    expected = task.reference_candidate.suggestions[0]
    hard_output = CaseFileChatTargetLockedRepairOutput(
        value_json=expected.value_json,
        reason=expected.reason,
    )
    provider = SequenceProvider(
        [_result(invalid, 1), _result(invalid, 1), _result(hard_output, 1)]
    )

    execution = ChatExecutionRunner(provider).run(request)

    assert execution.attempts == 3
    assert len(provider.requests) == 3
    target_locked_repair = provider.requests[2].target_locked_repair
    assert target_locked_repair == {
        "issue_code": "audit_repair_expectation_missing_target",
        "object_id": "ent_researcher",
        "path": "/description",
        "finding_ref": "F1",
        "preserve": ["F1"],
        "remove": ["evt_restart_seven:/truth_status"],
        "current_value_json": provider.requests[2].validation["audit_evidence_bundle"][
            "repair_expectation"
        ]["candidate_patch_targets"][0]["current_value_json"],
        "value_type": "str",
        "previous_failure": {
            "value_json": None,
            "reason_code": None,
            "issue_codes": ["audit_repair_expectation_missing_target"],
        },
    }
    assert chat_finalizer_output_type(provider.requests[2]) is CaseFileChatTargetLockedRepairOutput
    instructions, _ = render_chat_finalizer_prompt(
        provider.requests[2],
        tool_ledger=provider.requests[2].frozen_tool_ledger,
        evidence_summary="",
        previous_candidate=provider.requests[2].previous_candidate,
        repair_plan=provider.requests[2].repair_plan,
    )
    assert "你只能输出 value_json 和 reason 两个字段" in instructions
    assert [
        (suggestion.object_id, suggestion.path, suggestion.finding_ref)
        for suggestion in execution.result.candidate.suggestions
    ] == [("ent_researcher", "/description", "F1")]
    assert execution.result.candidate.answer == invalid.answer
    assert execution.result.candidate.audit_findings == invalid.audit_findings
    assert execution.diagnostics["repair_history"][1]["repair_mode"] == "target_locked"
    assert events[-1][0] == "model.safe_patch_gated"
    assert any(event[0] == "model.target_locked_repair_started" for event in events)


def test_v15_target_locked_repair_still_fails_closed_when_its_value_is_invalid() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-restart-loop"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    unrelated = task.reference_candidate.suggestions[0].model_copy(
        update={
            "object_id": "evt_restart_seven",
            "path": "/truth_status",
            "value_json": '"canon_true"',
        }
    )
    invalid = task.reference_candidate.model_copy(update={"suggestions": [unrelated]})
    provider = SequenceProvider(
        [
            _result(invalid, 1),
            _result(invalid, 1),
            _result(
                CaseFileChatTargetLockedRepairOutput(
                    value_json="null",
                    reason="类型不匹配的值必须被服务器拦截。",
                ),
                1,
            ),
            _result(
                CaseFileChatTargetLockedRepairOutput(
                    value_json="null",
                    reason="重复的不安全值仍应被服务器拦截。",
                ),
                1,
            ),
        ]
    )

    with pytest.raises(ChatCompletionValidationError) as caught:
        ChatExecutionRunner(provider).run(request)

    assert caught.value.code == "audit_target_locked_repair_no_progress"
    assert len(provider.requests) == 4


def test_v15_third_semantic_repair_rescues_the_same_locked_target() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-restart-loop"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    unrelated = task.reference_candidate.suggestions[0].model_copy(
        update={
            "object_id": "evt_restart_seven",
            "path": "/truth_status",
            "value_json": '"canon_true"',
        }
    )
    invalid = task.reference_candidate.model_copy(update={"suggestions": [unrelated]})
    expected = task.reference_candidate.suggestions[0]
    provider = SequenceProvider(
        [
            _result(invalid, 1),
            _result(invalid, 1),
            _result(
                CaseFileChatTargetLockedRepairOutput(
                    value_json="null",
                    reason="第一次锁定值仍不安全。",
                ),
                1,
            ),
            _result(
                CaseFileChatTargetLockedRepairOutput(
                    value_json=expected.value_json,
                    reason=expected.reason,
                ),
                1,
            ),
        ]
    )

    execution = ChatExecutionRunner(provider).run(request)

    assert execution.attempts == 4
    assert len(provider.requests) == 4
    identity = ("object_id", "path", "finding_ref")
    assert {
        key: provider.requests[2].target_locked_repair[key] for key in identity
    } == {key: provider.requests[3].target_locked_repair[key] for key in identity}
    assert provider.requests[3].target_locked_repair["previous_failure"] == {
        "value_json": "null",
        "reason_code": "simulation_failed",
        "issue_codes": [
            "audit_repairable_finding_missing_suggestion",
            "audit_suggestion_server_gate_failed",
        ],
    }
    assert execution.diagnostics["repair_history"][-1]["repair_no"] == 3


def test_v15_target_locked_repair_rejects_non_json_output_after_final_rescue() -> None:
    task = next(
        item
        for item in build_outcome_tasks()
        if item.task_id == "golden-audit-restart-loop"
    )
    request = replace(resolve_task_route(task), prompt_version="casefile-chat-v15")
    unrelated = task.reference_candidate.suggestions[0].model_copy(
        update={
            "object_id": "evt_restart_seven",
            "path": "/truth_status",
            "value_json": '"canon_true"',
        }
    )
    invalid = task.reference_candidate.model_copy(update={"suggestions": [unrelated]})
    provider = SequenceProvider(
        [
            _result(invalid, 1),
            _result(invalid, 1),
            _result(
                CaseFileChatTargetLockedRepairOutput(
                    value_json="not-json",
                    reason="必须拒绝未编码的字符串。",
                ),
                1,
            ),
            _result(
                CaseFileChatTargetLockedRepairOutput(
                    value_json="still-not-json",
                    reason="最终救援仍必须拒绝未编码字符串。",
                ),
                1,
            ),
        ]
    )

    with pytest.raises(ChatCompletionValidationError) as caught:
        ChatExecutionRunner(provider).run(request)

    assert caught.value.code == "audit_target_locked_repair_value_invalid"
    assert caught.value.attempts == 4
    assert len(provider.requests) == 4


def test_repair_plan_keeps_a_required_target_when_an_invalid_value_is_removed() -> None:
    plan = plan_repairs(
        (
            ValidationIssue(
                code="audit_suggestion_server_gate_failed",
                stage="patch",
                path="/suggestions/0",
                message="invalid",
                repairable=True,
                details={"extra": ["ent_leader:/description"]},
            ),
            ValidationIssue(
                code="audit_repairable_finding_missing_suggestion",
                stage="audit",
                path="/suggestions",
                message="missing",
                repairable=True,
                details={"missing": ["ent_leader:/description"]},
            ),
        )
    )

    assert plan.add == ("ent_leader:/description",)
    assert plan.remove == ()


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
