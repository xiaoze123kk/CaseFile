"""Stable, persistence-free CaseFile Chat execution entrypoint.

Owns Provider calls, bounded retry state, event callbacks, and diagnostic
aggregation. Does not own request preparation, candidate/audit validation,
context policy, or safe-patch domain rules; those are delegated to pure modules.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

from casefile.agent_runtime.chat_audit_validation import (
    ChatAuditValidationError,
    apply_deterministic_audit_gate,
    audit_repair_integrity,
)
from casefile.agent_runtime.chat_intent import apply_route_suggestion_policy
from casefile.agent_runtime.chat_preparation import (
    bind_chat_context_input as bind_chat_context_input,
)
from casefile.agent_runtime.chat_preparation import prepare_chat_request_artifacts
from casefile.agent_runtime.chat_reference_normalization import normalize_reference_slots
from casefile.agent_runtime.chat_safe_patches import (
    materialize_target_locked_repair,
    materialize_unique_safe_patches,
    server_gate_audit_suggestions,
    target_locked_repair_contract,
)
from casefile.agent_runtime.chat_validation import (
    ChatCompletionValidationError,
    ValidationIssue,
    select_semantic_repair_mode,
    target_label,
    validate_chat_candidate,
)
from casefile.agent_runtime.chat_versions import (
    PUBLIC_LANGUAGE_PROMPT_VERSIONS,
    SAFE_PATCH_PROMPT_VERSIONS,
)
from casefile.agent_runtime.models import CaseFileChatRequest, CaseFileChatResult, ToolMetrics
from casefile.agent_runtime.public_language import (
    PublicLanguageValidationError,
    terminal_public_language_error,
    validate_public_language,
)

MAX_SEMANTIC_REPAIRS = 3
MAX_FINALIZER_ATTEMPTS = 1 + MAX_SEMANTIC_REPAIRS


class ChatProvider(Protocol):
    def chat(self, request: CaseFileChatRequest) -> CaseFileChatResult: ...


@dataclass(frozen=True, slots=True)
class ChatExecutionResult:
    result: CaseFileChatResult
    usage: dict[str, Any]
    tools: ToolMetrics
    attempts: int
    repair_attempted: bool
    diagnostics: dict[str, Any]


def _merge_usage(records: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for record in records:
        for key, value in record.items():
            if isinstance(value, int) and not isinstance(value, bool):
                merged[key] = int(merged.get(key, 0)) + value
            else:
                merged[key] = value
    return merged


def _merge_tools(records: list[ToolMetrics]) -> ToolMetrics:
    merged = ToolMetrics()
    for record in records:
        merged.calls += record.calls
        merged.valid_calls += record.valid_calls
        merged.successful_calls += record.successful_calls
        merged.adopted_results += record.adopted_results
        merged.planned_object_ids.update(record.planned_object_ids)
    return merged


class ChatExecutionRunner:
    """Execute and repair one frozen request without owning persistence."""

    def __init__(self, provider: ChatProvider) -> None:
        self.provider = provider

    def run(
        self,
        request: CaseFileChatRequest,
        *,
        complete: Callable[[CaseFileChatResult], None] | None = None,
        artifacts_prepared: bool = False,
    ) -> ChatExecutionResult:
        if not artifacts_prepared:
            request = prepare_chat_request_artifacts(request)
        usages: list[dict[str, Any]] = []
        tools: list[ToolMetrics] = []
        repair_attempted = False
        repair_history: list[dict[str, Any]] = []
        materialization_history: list[dict[str, Any]] = []
        previous_failure_signature: str | None = None
        public_language_repairs = 0
        for attempt in range(1, MAX_FINALIZER_ATTEMPTS + 1):
            server_gate_issues: tuple[ValidationIssue, ...] = ()
            try:
                result = self.provider.chat(request)
            except Exception as error:
                # Providers may fail after emitting a usage/tool snapshot.
                # Preserve those snapshots for the terminal diagnostic.
                failed_usage = getattr(error, "usage", None)
                failed_tools = getattr(error, "tools", None)
                if isinstance(failed_usage, dict):
                    usages.append(failed_usage)
                if isinstance(failed_tools, ToolMetrics):
                    tools.append(failed_tools)
                _attach_failure_metrics(
                    error,
                    usages,
                    tools,
                    attempts=len(usages),
                    repair_attempted=repair_attempted,
                    repair_history=repair_history,
                    materialization_history=materialization_history,
                )
                raise
            usages.append(result.usage)
            tools.append(result.tools)
            if request.target_locked_repair is not None:
                try:
                    result = materialize_target_locked_repair(request, result)
                except Exception as error:
                    if attempt < MAX_FINALIZER_ATTEMPTS:
                        validation_error = _as_validation_error(error)
                        if validation_error is None:
                            validation_error = ChatCompletionValidationError(
                                code="audit_target_locked_repair_invalid"
                            )
                        _attach_failure_metrics(
                            validation_error,
                            usages,
                            tools,
                            attempts=attempt,
                            repair_attempted=repair_attempted,
                            repair_history=repair_history,
                            materialization_history=materialization_history,
                        )
                        contract = dict(request.target_locked_repair)
                        contract["previous_failure"] = {
                            "value_json": getattr(result.candidate, "value_json", None),
                            "reason_code": validation_error.code,
                            "issue_codes": [validation_error.code],
                        }
                        repair_no = len(repair_history) + 1
                        repair_history.append(
                            {
                                "attempt": attempt,
                                "repair_no": repair_no,
                                "repair_mode": "target_locked",
                                "validation_issues": [],
                                "repair_plan": validation_error.repair_plan.as_dict(),
                                "target_locked_repair": contract,
                            }
                        )
                        request.emit(
                            "model.target_locked_repair_started",
                            "repairing",
                            {
                                "repair_no": repair_no,
                                "max_repairs": MAX_SEMANTIC_REPAIRS,
                                "repair_mode": "target_locked",
                                "target_locked_repair": contract,
                            },
                        )
                        request = replace(
                            request,
                            repair_feedback=(validation_error.repair_feedback(),),
                            previous_candidate=request.previous_candidate,
                            target_locked_repair=contract,
                        )
                        continue
                    _attach_failure_metrics(
                        error,
                        usages,
                        tools,
                        attempts=attempt,
                        repair_attempted=repair_attempted,
                        repair_history=repair_history,
                        materialization_history=materialization_history,
                    )
                    raise
            if (
                request.prompt_version in SAFE_PATCH_PROMPT_VERSIONS
                and request.route is not None
                and request.route.execution_profile.get("primary_intent") == "logic_audit"
            ):
                candidate_payload = result.candidate.model_dump(mode="json")
                raw_suggestions = candidate_payload.get("suggestions")
                if isinstance(raw_suggestions, list):
                    proposals = [item for item in raw_suggestions if isinstance(item, dict)]
                    gate = server_gate_audit_suggestions(request, proposals)
                    ledger = result.tool_ledger or request.frozen_tool_ledger
                    if isinstance(ledger, dict):
                        gate = replace(
                            gate,
                            registry=replace(
                                gate.registry,
                                ledger_hash=str(ledger.get("ledger_hash") or ""),
                            ),
                        )
                    rejected_indexes = {failure.suggestion_index for failure in gate.failures} | {
                        discard.suggestion_index for discard in gate.discards
                    }
                    safe_suggestions = [
                        suggestion
                        for index, suggestion in enumerate(proposals)
                        if index not in rejected_indexes
                    ]
                    materialized, changes = materialize_unique_safe_patches(
                        safe_suggestions,
                        gate.registry,
                    )
                    if materialized != raw_suggestions:
                        candidate_payload["suggestions"] = materialized
                        result = replace(
                            result,
                            candidate=result.candidate.__class__.model_validate(candidate_payload),
                        )
                    result = replace(result, safe_patch_registry=gate.registry.as_dict())
                    request.emit(
                        "model.safe_patch_gated",
                        "validating",
                        {
                            "source": "server_post_finalizer_gate",
                            "safe_count": len(gate.registry.candidates),
                            "rejected": [failure.as_dict() for failure in gate.failures],
                            "discarded": [discard.as_dict() for discard in gate.discards],
                        },
                    )
                    if changes:
                        change_payloads = [change.as_dict() for change in changes]
                        materialization_history.extend(change_payloads)
                        request.emit(
                            "model.safe_patch_materialized",
                            "validating",
                            {
                                "ledger_hash": gate.registry.ledger_hash,
                                "source": gate.registry.source,
                                "changes": change_payloads,
                            },
                        )
                    if gate.failures:
                        preserved = sorted(
                            target_label(item.get("object_id"), item.get("path"))
                            for item in materialized
                        )
                        server_gate_issues = tuple(
                            ValidationIssue(
                                code="audit_suggestion_server_gate_failed",
                                stage="patch",
                                path=f"/suggestions/{failure.suggestion_index}",
                                message="审计建议未通过服务器确定性补丁门禁。",
                                repairable=True,
                                details={
                                    "extra": [failure.target],
                                    "preserve": preserved,
                                    "object_id": failure.object_id,
                                    "path": failure.path,
                                    "reason_code": failure.reason_code,
                                    "value_json": proposals[failure.suggestion_index].get(
                                        "value_json"
                                    ),
                                    "validation": failure.validation,
                                    "simulation": failure.simulation,
                                },
                            )
                            for failure in gate.failures
                        )
            result = normalize_reference_slots(request, result)
            result = apply_deterministic_audit_gate(request, result)
            try:
                if server_gate_issues:
                    candidate_payload = result.candidate.model_dump(mode="json")
                    integrity_issues = audit_repair_integrity(
                        request.validation.get("audit_evidence_bundle"),
                        candidate_payload.get("audit_findings", []),
                        candidate_payload.get("suggestions", []),
                    )
                    server_gate_issues = (*server_gate_issues, *integrity_issues)
                    raise ChatCompletionValidationError(
                        code=server_gate_issues[0].code,
                        issues=server_gate_issues,
                    )
                validate_chat_candidate(request, result)
                result = apply_route_suggestion_policy(request, result)
                if request.prompt_version in PUBLIC_LANGUAGE_PROMPT_VERSIONS:
                    validate_public_language(
                        result,
                        sensitive_values=(request.api_key or "",),
                    )
                if complete is not None:
                    complete(result)
            except Exception as error:
                validation = _as_validation_error(error)
                if validation is None:
                    _attach_failure_metrics(
                        error,
                        usages,
                        tools,
                        attempts=attempt,
                        repair_attempted=repair_attempted,
                        repair_history=repair_history,
                        materialization_history=materialization_history,
                    )
                    raise
                if isinstance(validation, PublicLanguageValidationError):
                    if public_language_repairs >= 1:
                        terminal = terminal_public_language_error(validation)
                        _attach_failure_metrics(
                            terminal,
                            usages,
                            tools,
                            attempts=attempt,
                            repair_attempted=repair_attempted,
                            repair_history=repair_history,
                            materialization_history=materialization_history,
                        )
                        raise terminal from error
                    public_language_repairs += 1
                resolved_target = (
                    target_locked_repair_contract(request, result, validation)
                    if attempt >= 2
                    else None
                )
                target_locked_repair = resolved_target or request.target_locked_repair
                if request.target_locked_repair is not None and resolved_target is not None:
                    identity_keys = ("object_id", "path", "finding_ref")
                    if any(
                        resolved_target.get(key) != request.target_locked_repair.get(key)
                        for key in identity_keys
                    ):
                        target_locked_repair = None
                current_signature = repr(
                    (
                        result.candidate.model_dump(mode="json"),
                        tuple(issue.as_dict().__repr__() for issue in validation.issues),
                    )
                )
                no_progress = (
                    previous_failure_signature is not None
                    and previous_failure_signature == current_signature
                )
                repair_mode = select_semantic_repair_mode(
                    attempt=attempt,
                    repair_plan=validation.repair_plan,
                    has_authoritative_target=target_locked_repair is not None,
                    currently_target_locked=(
                        request.target_locked_repair is not None
                        and target_locked_repair is not None
                    ),
                    no_progress=no_progress,
                    max_attempts=MAX_FINALIZER_ATTEMPTS,
                )
                if repair_mode is None:
                    _attach_failure_metrics(
                        validation,
                        usages,
                        tools,
                        attempts=attempt,
                        repair_attempted=repair_attempted,
                        repair_history=repair_history,
                        materialization_history=materialization_history,
                    )
                    raise validation from error
                repair_attempted = True
                repair_no = len(repair_history) + 1
                repair_record = {
                    "attempt": attempt,
                    "repair_no": repair_no,
                    "repair_mode": repair_mode,
                    "validation_issues": [issue.as_dict() for issue in validation.issues],
                    "repair_plan": validation.repair_plan.as_dict(),
                    "suggestion_count": len(result.candidate.suggestions),
                    "suggestion_targets": [
                        target_label(item.object_id, item.path)
                        for item in result.candidate.suggestions
                    ],
                }
                if target_locked_repair is not None:
                    repair_record["target_locked_repair"] = target_locked_repair
                repair_history.append(repair_record)
                request.emit(
                    (
                        "model.target_locked_repair_started"
                        if target_locked_repair is not None
                        else "model.reference_repair_started"
                    ),
                    "repairing",
                    {
                        "repair_no": repair_no,
                        "max_repairs": MAX_SEMANTIC_REPAIRS,
                        "repair_mode": repair_mode,
                        "unknown_object_ids": list(validation.object_ids),
                        "unknown_event_ids": list(validation.event_ids),
                        "unknown_issue_ids": list(validation.issue_ids),
                        "wrong_slot_object_ids": list(validation.wrong_slot_object_ids),
                        "wrong_slot_event_ids": list(validation.wrong_slot_event_ids),
                        "validation_issues": [issue.as_dict() for issue in validation.issues],
                        "repair_plan": validation.repair_plan.as_dict(),
                        "candidate_summary": {
                            "suggestion_count": repair_record["suggestion_count"],
                            "suggestion_targets": repair_record["suggestion_targets"],
                        },
                        **(
                            {"target_locked_repair": target_locked_repair}
                            if target_locked_repair is not None
                            else {}
                        ),
                    },
                )
                request = replace(
                    request,
                    repair_feedback=(validation.repair_feedback(),),
                    frozen_tool_ledger=result.tool_ledger,
                    safe_patch_registry=result.safe_patch_registry,
                    previous_candidate=result.candidate.model_dump(mode="json"),
                    repair_plan=validation.repair_plan.as_dict(),
                    target_locked_repair=target_locked_repair,
                )
                previous_failure_signature = current_signature
                continue
            return ChatExecutionResult(
                result=result,
                usage=_merge_usage(usages),
                tools=_merge_tools(tools),
                attempts=attempt,
                repair_attempted=repair_attempted,
                diagnostics={
                    "error_code": None,
                    "attempts": attempt,
                    "repair_history": repair_history,
                    "safe_patch_materializations": materialization_history,
                },
            )
        raise AssertionError("unreachable")


def _attach_failure_metrics(
    error: Exception,
    usages: list[dict[str, Any]],
    tools: list[ToolMetrics],
    *,
    attempts: int,
    repair_attempted: bool,
    repair_history: list[dict[str, Any]] | None = None,
    materialization_history: list[dict[str, Any]] | None = None,
) -> None:
    """Best-effort diagnostic attachment without changing public exceptions."""

    try:
        error.__dict__["usage"] = _merge_usage(usages)
        error.__dict__["tools"] = _merge_tools(tools)
        error.__dict__["attempts"] = attempts
        error.__dict__["repair_attempted"] = repair_attempted
        error.__dict__["repair_history"] = list(repair_history or ())
        error.__dict__["safe_patch_materializations"] = list(materialization_history or ())
    except (AttributeError, TypeError):
        return


def _as_validation_error(error: Exception) -> ChatCompletionValidationError | None:
    if isinstance(error, ChatCompletionValidationError):
        return error
    if isinstance(error, ChatAuditValidationError):
        return ChatCompletionValidationError(
            code=error.code,
            issues=(error.issue,),
        )
    object_ids = getattr(error, "object_ids", None)
    event_ids = getattr(error, "event_ids", None)
    issue_ids = getattr(error, "issue_ids", None)
    wrong_slot_object_ids = getattr(error, "wrong_slot_object_ids", None)
    wrong_slot_event_ids = getattr(error, "wrong_slot_event_ids", None)
    if (
        isinstance(object_ids, (list, tuple))
        and isinstance(event_ids, (list, tuple))
        and isinstance(issue_ids, (list, tuple))
    ):
        return ChatCompletionValidationError(
            object_ids=tuple(str(item) for item in object_ids),
            event_ids=tuple(str(item) for item in event_ids),
            issue_ids=tuple(str(item) for item in issue_ids),
            wrong_slot_object_ids=tuple(str(item) for item in (wrong_slot_object_ids or ())),
            wrong_slot_event_ids=tuple(str(item) for item in (wrong_slot_event_ids or ())),
        )
    return None


__all__ = [
    "bind_chat_context_input",
    "ChatCompletionValidationError",
    "ChatExecutionResult",
    "ChatExecutionRunner",
    "prepare_chat_request_artifacts",
    "validate_chat_candidate",
]
