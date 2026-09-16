"""Ordered Chat completion policy and required validation hooks.

Shared by ordinary Chat and Goal finalizers. Normalizers retain their existing
order; hooks only validate isolated candidate snapshots. Repair and persistence
remain owned by the caller.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from casefile.agent_runtime.chat_audit_validation import apply_deterministic_audit_gate
from casefile.agent_runtime.chat_intent import (
    apply_route_suggestion_policy,
    suppress_general_mutation_finalizer_suggestions,
)
from casefile.agent_runtime.chat_reference_normalization import normalize_reference_slots
from casefile.agent_runtime.chat_validation import validate_chat_candidate
from casefile.agent_runtime.chat_versions import PUBLIC_LANGUAGE_PROMPT_VERSIONS
from casefile.agent_runtime.models import CaseFileChatRequest, CaseFileChatResult
from casefile.agent_runtime.public_language import (
    normalize_general_mutation_clarification,
    normalize_internal_disclosure_refusal,
    validate_public_language,
)
from casefile.agent_runtime.runtime_hooks import (
    HookBinding,
    HookEvent,
    HookInput,
    HookResult,
    SyncHookDispatcher,
)

CHAT_COMPLETION_HOOKS = (
    HookBinding("chat_candidate", "1", HookEvent.AFTER_ARTIFACT, component="candidate"),
    HookBinding("chat_public_language", "1", HookEvent.AFTER_ARTIFACT, component="public_language"),
)


def _validate_boundary(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
    component: str,
    records: list[dict[str, Any]],
) -> None:
    def candidate(event: HookInput) -> HookResult:
        isolated = replace(
            result, candidate=result.candidate.__class__.model_validate(event.payload["candidate"])
        )
        validate_chat_candidate(request, isolated)
        return HookResult()

    def public_language(event: HookInput) -> HookResult:
        isolated = replace(
            result, candidate=result.candidate.__class__.model_validate(event.payload["candidate"])
        )
        validate_public_language(isolated, sensitive_values=(request.api_key or "",))
        return HookResult()

    dispatcher = SyncHookDispatcher(
        {
            ("chat_candidate", "1"): candidate,
            ("chat_public_language", "1"): public_language,
        }
    )
    try:
        dispatcher.dispatch(
            CHAT_COMPLETION_HOOKS,
            HookInput(
                HookEvent.AFTER_ARTIFACT,
                "chat_completion",
                component,
                {"candidate": result.candidate.model_dump(mode="json")},
            ),
            records=records,
        )
    except Exception as error:
        # Preserve the original validation exception and repair classification.
        error.__dict__["hook_records"] = list(records)
        raise


def validate_chat_public_language(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
    *,
    hook_records: list[dict[str, Any]],
) -> None:
    """Shared public-language gate, including the safe terminal projection."""
    _validate_boundary(request, result, "public_language", hook_records)


def coordinate_chat_candidate_validation(
    request: CaseFileChatRequest,
    result: CaseFileChatResult,
    *,
    hook_records: list[dict[str, Any]] | None = None,
) -> CaseFileChatResult:
    """Shared final candidate boundary for single-task and Goal execution."""

    records = [] if hook_records is None else hook_records
    result = suppress_general_mutation_finalizer_suggestions(request, result)
    result = normalize_reference_slots(request, result)
    result = apply_deterministic_audit_gate(request, result)
    _validate_boundary(request, result, "candidate", records)
    result = apply_route_suggestion_policy(request, result)
    if request.prompt_version in PUBLIC_LANGUAGE_PROMPT_VERSIONS:
        result = normalize_general_mutation_clarification(request, result)
        result = normalize_internal_disclosure_refusal(request, result)
        try:
            validate_chat_public_language(request, result, hook_records=records)
        except Exception:
            if request.feedback is not None:
                request.feedback("message.preview_invalidated", {"discard": True})
            raise
    return result
