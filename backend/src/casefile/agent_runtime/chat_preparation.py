"""Prepare frozen CaseFile Chat requests without calling a provider.

Owns request artifacts and context binding. Does not own provider calls,
candidate validation, repair policy, or persistence.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from casefile.agent_runtime.chat_intent import build_edit_target_manifest
from casefile.agent_runtime.context import build_chat_context_manifest
from casefile.agent_runtime.context.assembly_render import chat_input_payload_from_assembly
from casefile.agent_runtime.context.audit_evidence import build_audit_evidence_bundle
from casefile.agent_runtime.context.thread_memory import (
    empty_thread_memory_state,
    thread_memory_state_to_jsonable,
)
from casefile.agent_runtime.models import (
    LEGACY_CONTEXT_POLICY_VERSION,
    CaseFileChatRequest,
    chat_routing_payload_as_dict,
)


def prepare_chat_request_artifacts(request: CaseFileChatRequest) -> CaseFileChatRequest:
    """Freeze v13 Bundle/manifest before context rendering and provider I/O."""

    if request.prompt_version not in {
        "casefile-chat-v13",
        "casefile-chat-v14",
        "casefile-chat-v15",
        "casefile-chat-v16",
    }:
        return request
    validation = dict(request.validation)
    intent = (
        None
        if request.route is None
        else request.route.execution_profile.get("primary_intent")
    )
    if intent == "logic_audit" and "audit_evidence_bundle" not in validation:
        validation["audit_evidence_bundle"] = build_audit_evidence_bundle(
            request.casefile,
            editable_fields_by_collection=request.editable_fields_by_collection,
        ).payload
    if intent == "edit_request" and "edit_target_manifest" not in validation:
        validation["edit_target_manifest"] = build_edit_target_manifest(request).as_list()
    return replace(request, validation=validation)


def bind_chat_context_input(
    request: CaseFileChatRequest,
    *,
    frozen_input: dict[str, Any],
    thread_memory_state: dict[str, Any] | None = None,
) -> CaseFileChatRequest:
    """Bind the same policy-rendered executor payload used by Worker paths."""

    if (
        request.assembled_input is not None
        or request.context_policy_version == LEGACY_CONTEXT_POLICY_VERSION
    ):
        return request
    policy_requires_thread_memory = (
        request.context_policy_version != LEGACY_CONTEXT_POLICY_VERSION
    )
    if thread_memory_state is None and policy_requires_thread_memory:
        thread_memory_state = thread_memory_state_to_jsonable(empty_thread_memory_state())
    result = build_chat_context_manifest(
        policy_version=request.context_policy_version,
        frozen_input={**frozen_input, "validation": dict(request.validation)},
        input_hash=request.input_hash,
        routing=chat_routing_payload_as_dict(request),
        extra_input={
            "editable_fields_by_collection": request.editable_fields_by_collection,
            **({"thread_memory_state": thread_memory_state} if thread_memory_state else {}),
        },
        provider="deepseek" if request.model_id.startswith("deepseek") else "openai",
        model_id=request.model_id,
        hard_input_tokens=128_000,
    )
    if result.fallback is not None:
        return request
    return replace(
        request,
        assembled_input=chat_input_payload_from_assembly(
            result.assembly,
            require_thread_memory=policy_requires_thread_memory,
            dashboard=result.dashboard,
        ),
    )


__all__ = ["bind_chat_context_input", "prepare_chat_request_artifacts"]
